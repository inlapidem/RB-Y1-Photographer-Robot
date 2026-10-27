"""Capture five robot joint angles and a phone camera frame with Space.

Run on the Jetson, open /phone on the phone and /operator on the Jetson
browser. The phone page needs HTTPS because mobile browsers only expose the
camera on a secure origin.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geekseek.pose_capture import (  # noqa: E402
    AxisPose,
    FeedbackSnapshot,
    FeedbackStore,
    PoseCaptureError,
    save_pose_sample,
    snapshot_dict,
    validate_snapshot,
)
from geekseek.perception import (  # noqa: E402
    MediaPipePersonSensor,
    WebcamFrameSource,
    is_approaching,
    is_positioned,
)


class PhoneLink:
    def __init__(self) -> None:
        self.socket: WebSocket | None = None
        self.pending: asyncio.Future[bytes] | None = None
        self.latest_frame: bytes | None = None
        self.latest_frame_monotonic = 0.0

    @property
    def connected(self) -> bool:
        return self.socket is not None

    async def capture(self, timeout_seconds: float) -> bytes:
        if self.socket is None:
            raise PoseCaptureError("휴대폰 카메라가 연결되지 않았습니다")
        if self.pending is not None and not self.pending.done():
            raise PoseCaptureError("이미 캡처 중입니다")
        self.pending = asyncio.get_running_loop().create_future()
        await self.socket.send_text("capture")
        try:
            return await asyncio.wait_for(self.pending, timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise PoseCaptureError("휴대폰 이미지 수신 시간이 초과되었습니다") from exc
        finally:
            self.pending = None

    def receive(self, data: bytes) -> None:
        self.latest_frame = data
        self.latest_frame_monotonic = time.monotonic()
        if self.pending is not None and not self.pending.done():
            self.pending.set_result(data)

    def latest(self, max_age_seconds: float) -> bytes:
        if self.socket is None:
            raise PoseCaptureError("휴대폰 카메라가 연결되지 않았습니다")
        if self.latest_frame is None:
            raise PoseCaptureError("휴대폰의 첫 실시간 프레임을 기다리는 중입니다")
        age = time.monotonic() - self.latest_frame_monotonic
        if age > max_age_seconds:
            raise PoseCaptureError(f"휴대폰 실시간 프레임이 오래되었습니다 ({age:.2f}초)")
        return self.latest_frame


class RosFeedbackThread:
    def __init__(self, store: FeedbackStore, axis_indices: tuple[int, ...]) -> None:
        self.store = store
        self.axis_indices = axis_indices
        self.thread: threading.Thread | None = None
        self.rclpy = None
        self.node = None

    def start(self) -> None:
        ros_log_dir = ROOT / "log" / "pose_capture"
        ros_log_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("ROS_LOG_DIR", str(ros_log_dir))
        try:
            import rclpy
            from agx_msgs.msg import PhorceFeedback
            from rclpy.executors import ExternalShutdownException
            from rclpy.node import Node
            from rclpy.qos import qos_profile_sensor_data
        except ImportError as exc:
            raise RuntimeError(
                "ROS 2/agx_msgs를 불러올 수 없습니다. /opt/ros/humble/setup.bash를 source하세요"
            ) from exc

        owner = self

        class FeedbackNode(Node):
            def __init__(self) -> None:
                super().__init__("geekseek_pose_capture")
                self.create_subscription(
                    PhorceFeedback,
                    "/phorce/feedback",
                    self.on_feedback,
                    qos_profile_sensor_data,
                )

            def on_feedback(self, msg) -> None:
                if any(index < 0 or index >= len(msg.axis) for index in owner.axis_indices):
                    return
                axes = tuple(
                    AxisPose(
                        index=index,
                        position_rad=float(msg.axis[index].position_rad),
                        velocity_rad_s=float(msg.axis[index].velocity_rad_s),
                        valid=bool(msg.axis[index].valid),
                        pos_ref_echo_rad=(
                            float(msg.axis[index].pos_ref_echo_rad)
                            if hasattr(msg.axis[index], "pos_ref_echo_rad")
                            else None
                        ),
                    )
                    for index in owner.axis_indices
                )
                owner.store.update(
                    FeedbackSnapshot(
                        captured_at=datetime.now().astimezone().isoformat(timespec="milliseconds"),
                        monotonic_seconds=time.monotonic(),
                        axes=axes,
                    )
                )

        # Do not let rclpy parse this script's --axes/--port arguments.
        rclpy.init(args=[])
        self.rclpy = rclpy
        self.node = FeedbackNode()

        def spin() -> None:
            try:
                rclpy.spin(self.node)
            except ExternalShutdownException:
                pass

        self.thread = threading.Thread(target=spin, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.rclpy is not None and self.rclpy.ok():
            self.rclpy.shutdown()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.node is not None:
            self.node.destroy_node()


@dataclass(frozen=True)
class WebcamPoseFrame:
    skeleton_jpeg: bytes
    monotonic_seconds: float
    metadata: dict[str, object]


class WebcamPoseThread:
    """Continuously prepares a webcam frame and its MediaPipe overlay."""

    def __init__(
        self,
        camera_index: int,
        capture_fps: float,
        inference_fps: float,
        frame_width: int,
        frame_height: int,
    ) -> None:
        self.camera_index = camera_index
        self.capture_fps = capture_fps
        self.inference_fps = inference_fps
        self.frame_width = frame_width
        self.frame_height = frame_height
        self._lock = threading.Lock()
        self._latest: WebcamPoseFrame | None = None
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._source: WebcamFrameSource | None = None
        self._sensor: MediaPipePersonSensor | None = None

    def start(self) -> None:
        self._source = WebcamFrameSource(
            camera_index=self.camera_index,
            target_fps=self.capture_fps,
            frame_width=self.frame_width,
            frame_height=self.frame_height,
        )
        try:
            self._sensor = MediaPipePersonSensor()
        except Exception:
            self._source.close()
            self._source = None
            raise

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="webcam-pose")
        self._thread.start()

    def _loop(self) -> None:
        assert self._source is not None
        assert self._sensor is not None
        interval = 1.0 / max(self.inference_fps, 0.1)
        while not self._stop.is_set():
            started = time.monotonic()
            frame = self._source()
            if frame is not None:
                try:
                    frame = frame.copy()
                    signal = self._sensor.sense(frame)
                    skeleton_jpeg = self._sensor.annotate_jpeg(
                        frame,
                        signal,
                        is_approaching(signal),
                        is_positioned(signal),
                    )
                    if not skeleton_jpeg:
                        raise RuntimeError("JPEG encode failed")
                    metadata: dict[str, object] = {
                        "camera_index": self.camera_index,
                        "width": int(frame.shape[1]),
                        "height": int(frame.shape[0]),
                        "detected": signal.detected,
                        "size_ratio": round(signal.size_ratio, 6),
                        "center_x": round(signal.center_x, 6),
                        "center_y": round(signal.center_y, 6),
                        "hand_raised": signal.hand_raised,
                        "delegate": self._sensor.delegate_name,
                        "inference_fps": round(self._sensor.inference_fps, 3),
                    }
                    bundle = WebcamPoseFrame(
                        skeleton_jpeg=skeleton_jpeg,
                        monotonic_seconds=time.monotonic(),
                        metadata=metadata,
                    )
                    with self._lock:
                        self._latest = bundle
                        self._error = None
                except Exception as exc:
                    with self._lock:
                        self._error = str(exc)
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def status(self) -> dict[str, object]:
        with self._lock:
            latest = self._latest
            error = self._error
        age = None if latest is None else time.monotonic() - latest.monotonic_seconds
        return {
            "ready": latest is not None,
            "frame_age_seconds": None if age is None else round(age, 3),
            "error": error,
            "inference": None if latest is None else latest.metadata,
        }

    def latest(self, max_age_seconds: float) -> WebcamPoseFrame:
        with self._lock:
            latest = self._latest
            error = self._error
        if latest is None:
            suffix = "" if error is None else f": {error}"
            raise PoseCaptureError(f"웹캠 skeleton 첫 프레임을 기다리는 중입니다{suffix}")
        age = time.monotonic() - latest.monotonic_seconds
        if age > max_age_seconds:
            raise PoseCaptureError(f"웹캠 skeleton 프레임이 오래되었습니다 ({age:.2f}초)")
        return latest

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._sensor is not None:
            self._sensor.close()
        if self._source is not None:
            self._source.close()


def parse_axes(value: str) -> tuple[int, ...]:
    try:
        axes = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("축은 0,1,2,3,4 형식이어야 합니다") from exc
    if len(axes) != 5 or len(set(axes)) != 5 or any(axis < 0 or axis > 11 for axis in axes):
        raise argparse.ArgumentTypeError("서로 다른 축 5개를 0..11 범위에서 지정하세요")
    return axes


def load_zero_offsets(path: Path, axis_indices: tuple[int, ...]) -> tuple[dict[int, float], str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        source_axes = tuple(int(value) for value in data["axis_indices"])
        positions = tuple(float(value) for value in data["position_rad"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"영점 offset 파일을 읽을 수 없습니다: {path}: {exc}") from exc
    if source_axes != axis_indices or len(positions) != len(axis_indices):
        raise RuntimeError(
            f"영점 offset 축 {source_axes}가 실행 축 {axis_indices}와 일치하지 않습니다"
        )
    return dict(zip(source_axes, positions)), str(data.get("source_sample", path.name))


def create_app(args: argparse.Namespace) -> FastAPI:
    store = FeedbackStore()
    phone = PhoneLink()
    feedback = RosFeedbackThread(store, args.axes)
    webcam = WebcamPoseThread(
        args.webcam_index,
        args.webcam_fps,
        args.webcam_inference_fps,
        args.webcam_width,
        args.webcam_height,
    )
    capture_lock = asyncio.Lock()
    zero_offsets_rad, zero_reference = load_zero_offsets(args.zero_offset_file, args.axes)
    args.csv_file.parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        feedback.start()
        try:
            webcam.start()
        except Exception:
            feedback.stop()
            raise
        try:
            yield
        finally:
            webcam.stop()
            feedback.stop()

    app = FastAPI(title="GeekSeek pose capture", lifespan=lifespan)
    app.mount("/samples", StaticFiles(directory=args.csv_file.parent), name="samples")

    @app.get("/phone", include_in_schema=False)
    async def phone_page() -> FileResponse:
        return FileResponse(ROOT / "web" / "phone_pose_stream.html")

    @app.get("/operator", include_in_schema=False)
    async def operator_page() -> FileResponse:
        return FileResponse(ROOT / "web" / "pose_capture_operator.html")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return await operator_page()

    @app.websocket("/phone-ws")
    async def phone_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        phone.socket = websocket
        try:
            while True:
                phone.receive(await websocket.receive_bytes())
        except WebSocketDisconnect:
            pass
        finally:
            if phone.socket is websocket:
                phone.socket = None

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        latest = store.latest()
        return {
            "phone_connected": phone.connected,
            "phone_frame_ready": phone.latest_frame is not None,
            "phone_frame_age_seconds": (
                None
                if phone.latest_frame is None
                else round(time.monotonic() - phone.latest_frame_monotonic, 3)
            ),
            "feedback_ready": latest is not None,
            "feedback_age_seconds": (
                None if latest is None else round(time.monotonic() - latest.monotonic_seconds, 3)
            ),
            "axes": list(args.axes),
            "zero_reference": zero_reference,
            "feedback": None if latest is None else snapshot_dict(latest, zero_offsets_rad),
            "webcam": webcam.status(),
        }

    @app.get("/api/live-frame", include_in_schema=False)
    async def live_frame() -> Response:
        try:
            image = phone.latest(args.max_phone_frame_age)
        except PoseCaptureError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(
            image,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/webcam-skeleton", include_in_schema=False)
    async def webcam_skeleton() -> Response:
        try:
            frame = webcam.latest(args.max_webcam_frame_age)
        except PoseCaptureError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(
            frame.skeleton_jpeg,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.post("/api/capture")
    async def capture() -> dict[str, object]:
        async with capture_lock:
            try:
                trigger = validate_snapshot(
                    store.latest(),
                    max_age_seconds=args.max_feedback_age,
                    max_speed_deg_s=args.max_speed,
                )
                image = await phone.capture(args.phone_timeout)
                validate_snapshot(
                    store.latest(),
                    max_age_seconds=args.max_feedback_age,
                    max_speed_deg_s=args.max_speed,
                )
                webcam_frame = webcam.latest(args.max_webcam_frame_age)
                metadata = save_pose_sample(
                    args.csv_file,
                    image,
                    webcam_frame.skeleton_jpeg,
                    trigger,
                    zero_offsets_rad,
                )
            except PoseCaptureError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        return metadata

    @app.post("/api/capture-latest")
    async def capture_latest() -> dict[str, object]:
        """Save the frame already visible in the Pygame live preview."""
        async with capture_lock:
            try:
                trigger = validate_snapshot(
                    store.latest(),
                    max_age_seconds=args.max_feedback_age,
                    max_speed_deg_s=args.max_speed,
                )
                image = phone.latest(args.max_phone_frame_age)
                validate_snapshot(
                    store.latest(),
                    max_age_seconds=args.max_feedback_age,
                    max_speed_deg_s=args.max_speed,
                )
                webcam_frame = webcam.latest(args.max_webcam_frame_age)
                metadata = save_pose_sample(
                    args.csv_file,
                    image,
                    webcam_frame.skeleton_jpeg,
                    trigger,
                    zero_offsets_rad,
                )
            except PoseCaptureError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        return metadata

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # Verified on the five-axis hackathon arm: valid_mask=0x147 -> 0,1,2,6,8.
    # Keep --axes available because a different PCM axis map may differ.
    parser.add_argument("--axes", type=parse_axes, default=parse_axes("0,1,2,6,8"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8444)
    parser.add_argument(
        "--csv-file",
        type=Path,
        default=ROOT / "calibration" / "pose_samples.csv",
    )
    parser.add_argument(
        "--zero-offset-file",
        type=Path,
        default=ROOT / "config" / "pose-zero-offsets.json",
    )
    parser.add_argument("--max-speed", type=float, default=0.5, help="deg/s")
    parser.add_argument("--max-feedback-age", type=float, default=0.2, help="seconds")
    parser.add_argument("--phone-timeout", type=float, default=5.0, help="seconds")
    parser.add_argument("--max-phone-frame-age", type=float, default=1.0, help="seconds")
    parser.add_argument("--webcam-index", type=int, default=0)
    parser.add_argument("--webcam-fps", type=float, default=15.0)
    parser.add_argument("--webcam-inference-fps", type=float, default=5.0)
    parser.add_argument("--webcam-width", type=int, default=1280)
    parser.add_argument("--webcam-height", type=int, default=960)
    parser.add_argument("--max-webcam-frame-age", type=float, default=1.0, help="seconds")
    parser.add_argument("--keyfile", type=Path, default=ROOT / "certs" / "key.pem")
    parser.add_argument("--certfile", type=Path, default=ROOT / "certs" / "cert.pem")
    args = parser.parse_args()

    if not args.keyfile.exists() or not args.certfile.exists():
        parser.error("휴대폰 카메라용 HTTPS 인증서가 certs/에 없습니다")

    print(f"operator: https://127.0.0.1:{args.port}/operator")
    print(f"phone:    https://<JETSON-IP>:{args.port}/phone")
    print(f"axes:     {args.axes}")
    print(f"csv:      {args.csv_file}")
    uvicorn.run(
        create_app(args),
        host=args.host,
        port=args.port,
        ssl_keyfile=str(args.keyfile),
        ssl_certfile=str(args.certfile),
        access_log=False,
    )


if __name__ == "__main__":
    main()

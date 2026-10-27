"""RBY1 encoder + iPhone-camera teaching tool.

Run this server on the Jetson, open ``/phone`` on the iPhone, and open
``/operator`` on the laptop.  Space on the laptop requests one iPhone frame
and stores it with a fresh RB-Y1 right-arm state.  This tool never commands
the robot.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import threading
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parents[1]
SESSION_SCHEMA = "geekseek.rby1.capture-session/v1"
KEYPOSE_SCHEMA = "geekseek.rby1.keyposes/v1"
MAX_IMAGE_BYTES = 15 * 1024 * 1024
CAPTURE_CSV_FIELDS = [
    "sample_id",
    "kind",
    "server_captured_at",
    "iphone_captured_at",
    "iphone_image",
    "rgb_image",
    "template_id",
    "framing_positioned",
    *[f"right_arm_{index}_rad" for index in range(1, 8)],
    *[f"right_arm_{index}_deg" for index in range(1, 8)],
    "ee_right_transform_json",
    "force_n_json",
    "torque_nm_json",
    "max_speed_deg_s",
]
FRAMING_JOINTS = {
    11: "left_shoulder",
    12: "right_shoulder",
    23: "left_hip",
    24: "right_hip",
}
FRAMING_CSV_FIELDS = [
    "sample_id",
    "label",
    "captured_at",
    "rgb_image",
    "skeleton_image",
    "people_count",
    *[
        f"{name}_{axis}"
        for name in FRAMING_JOINTS.values()
        for axis in ("x", "y", "visibility")
    ],
    "landmarks_json",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def validate_jpeg(image: bytes) -> None:
    if not image:
        raise ValueError("카메라 이미지가 비어 있습니다")
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError(f"이미지가 너무 큽니다 ({len(image)} bytes)")
    if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
        raise ValueError("JPEG 이미지가 아닙니다")


class PhoneLink:
    """Phorce Studio-compatible iPhone WebSocket camera link."""

    def __init__(self) -> None:
        self.socket: WebSocket | None = None
        self.pending: asyncio.Future[bytes] | None = None
        self.latest_frame: bytes | None = None
        self.latest_frame_monotonic = 0.0
        self.latest_frame_received_at = ""
        self.metadata: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        return self.socket is not None

    async def capture(self, timeout_seconds: float) -> tuple[bytes, str]:
        if self.socket is None:
            raise ValueError("iPhone 카메라가 연결되지 않았습니다")
        if self.pending is not None and not self.pending.done():
            raise ValueError("이미 iPhone 캡처 중입니다")
        self.pending = asyncio.get_running_loop().create_future()
        await self.socket.send_text("capture")
        try:
            image = await asyncio.wait_for(self.pending, timeout_seconds)
            return image, self.latest_frame_received_at
        except asyncio.TimeoutError as exc:
            raise ValueError("iPhone 이미지 수신 시간이 초과되었습니다") from exc
        finally:
            self.pending = None

    def receive_frame(self, data: bytes) -> None:
        validate_jpeg(data)
        self.latest_frame = data
        self.latest_frame_monotonic = asyncio.get_running_loop().time()
        self.latest_frame_received_at = now_iso()
        if self.pending is not None and not self.pending.done():
            self.pending.set_result(data)

    def receive_metadata(self, text: str) -> None:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return
        if isinstance(value, dict) and value.get("type") == "camera_metadata":
            self.metadata = value

    def latest(self, max_age_seconds: float) -> bytes:
        if self.socket is None:
            raise ValueError("iPhone 카메라가 연결되지 않았습니다")
        if self.latest_frame is None:
            raise ValueError("iPhone의 첫 실시간 프레임을 기다리는 중입니다")
        age = asyncio.get_running_loop().time() - self.latest_frame_monotonic
        if age > max_age_seconds:
            raise ValueError(f"iPhone 실시간 프레임이 오래되었습니다 ({age:.2f}초)")
        return self.latest_frame


class SkeletonBridge:
    """Read the already-running kiosk's MediaPipe result without reopening its camera."""

    def __init__(self, base_url: str, timeout_seconds: float = 1.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _read(self, path: str) -> tuple[bytes, str]:
        request = urllib.request.Request(
            f"{self.base_url}{path}", headers={"Cache-Control": "no-store"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read(), response.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValueError(f"skeleton 서버에 연결할 수 없습니다: {exc}") from exc

    def status(self, template_id: str) -> dict[str, Any]:
        body, _ = self._read(f"/api/debug/teaching-framing/{template_id}")
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("skeleton 서버 응답이 JSON이 아닙니다") from exc
        if not isinstance(value, dict):
            raise ValueError("skeleton 서버 응답 형식이 잘못되었습니다")
        return value

    def frame(self, template_id: str) -> bytes:
        body, content_type = self._read(
            f"/api/debug/teaching-framing-frame/{template_id}"
        )
        if content_type != "image/jpeg":
            raise ValueError("skeleton 서버 프레임이 JPEG가 아닙니다")
        validate_jpeg(body)
        return body

    def rgb_frame(self) -> bytes:
        body, content_type = self._read("/api/debug/rgb-frame")
        if content_type != "image/jpeg":
            raise ValueError("RGB 서버 프레임이 JPEG가 아닙니다")
        validate_jpeg(body)
        return body


class FramingCalibrationSession:
    """Store RGB/skeleton pairs and pose landmarks for later range fitting."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.csv_path = directory / "framing_samples.csv"
        self.lock = threading.Lock()
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
                csv.DictWriter(handle, fieldnames=FRAMING_CSV_FIELDS).writeheader()

    def summary(self) -> dict[str, Any]:
        with self.lock:
            with self.csv_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            return {
                "directory": str(self.directory),
                "csv": str(self.csv_path),
                "count": len(rows),
                "full_body_count": sum(row["label"] == "full_body" for row in rows),
                "upper_body_count": sum(row["label"] == "upper_body" for row in rows),
                "last_sample": rows[-1] if rows else None,
            }

    def save(
        self,
        label: str,
        rgb_image: bytes,
        skeleton_image: bytes,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        if label not in {"full_body", "upper_body"}:
            raise ValueError(f"지원하지 않는 라벨입니다: {label}")
        validate_jpeg(rgb_image)
        validate_jpeg(skeleton_image)
        if status.get("people_count") != 1:
            raise ValueError("카메라에 한 명만 보이게 해주세요")
        landmarks = {
            int(item["index"]): item
            for item in status.get("landmarks", [])
            if isinstance(item, dict) and "index" in item
        }
        missing = [name for index, name in FRAMING_JOINTS.items() if index not in landmarks]
        if missing:
            raise ValueError(f"기준 관절을 찾지 못했습니다: {', '.join(missing)}")

        with self.lock:
            with self.csv_path.open("r", encoding="utf-8", newline="") as handle:
                sample_number = sum(1 for _ in csv.DictReader(handle)) + 1
            sample_id = f"sample_{sample_number:04d}"
            stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
            rgb_name = f"{sample_id}_{label}_{stamp}_rgb.jpg"
            skeleton_name = f"{sample_id}_{label}_{stamp}_skeleton.jpg"
            (self.directory / rgb_name).write_bytes(rgb_image)
            (self.directory / skeleton_name).write_bytes(skeleton_image)
            captured_at = now_iso()
            row: dict[str, Any] = {
                "sample_id": sample_id,
                "label": label,
                "captured_at": captured_at,
                "rgb_image": rgb_name,
                "skeleton_image": skeleton_name,
                "people_count": status["people_count"],
                "landmarks_json": json.dumps(status["landmarks"], separators=(",", ":")),
            }
            for index, name in FRAMING_JOINTS.items():
                point = landmarks[index]
                for axis in ("x", "y", "visibility"):
                    row[f"{name}_{axis}"] = round(float(point[axis]), 6)
            with self.csv_path.open("a", encoding="utf-8", newline="") as handle:
                csv.DictWriter(handle, fieldnames=FRAMING_CSV_FIELDS).writerow(row)
            return row


class Rby1StateReader:
    def __init__(
        self,
        address: str,
        model_name: str,
        max_speed_deg_s: float,
        joint_limit_tolerance_deg: float,
    ) -> None:
        import rby1_sdk as rby

        self.rby = rby
        self.robot = rby.create_robot(address, model_name)
        if not self.robot.connect():
            raise RuntimeError(f"RB-Y1에 연결하지 못했습니다: {address}")
        self.model = self.robot.model()
        self.indices = list(self.model.right_arm_idx)
        if len(self.indices) != 7:
            raise RuntimeError(f"오른팔 축 수가 7이 아닙니다: {len(self.indices)}")
        self.dynamics = self.robot.get_dynamics()
        self.dynamics_state = self.dynamics.make_state(
            ["base", "ee_right"], self.model.robot_joint_names
        )
        lower = self.dynamics.get_limit_q_lower(self.dynamics_state)
        upper = self.dynamics.get_limit_q_upper(self.dynamics_state)
        self.lower = [float(lower[index]) for index in self.indices]
        self.upper = [float(upper[index]) for index in self.indices]
        self.max_speed_deg_s = max_speed_deg_s
        self.joint_limit_tolerance_rad = math.radians(joint_limit_tolerance_deg)
        self.lock = threading.Lock()

    def snapshot(self, *, require_stopped: bool) -> dict[str, Any]:
        with self.lock:
            state = self.robot.get_state()
            position = [float(state.position[index]) for index in self.indices]
            velocity = [float(state.velocity[index]) for index in self.indices]
            fastest = max((abs(math.degrees(value)) for value in velocity), default=0.0)
            violations = [
                {
                    "joint": self.model.robot_joint_names[index],
                    "position_deg": math.degrees(value),
                    "lower_deg": math.degrees(lower),
                    "upper_deg": math.degrees(upper),
                }
                for index, value, lower, upper in zip(
                    self.indices, position, self.lower, self.upper
                )
                if value < lower - self.joint_limit_tolerance_rad
                or value > upper + self.joint_limit_tolerance_rad
            ]
            if require_stopped and fastest > self.max_speed_deg_s:
                raise ValueError(
                    f"오른팔이 움직이는 중입니다 (최대 {fastest:.3f} deg/s, "
                    f"허용 {self.max_speed_deg_s:.3f} deg/s)"
                )
            if require_stopped and violations:
                names = ", ".join(item["joint"] for item in violations)
                raise ValueError(f"관절 허용 범위를 벗어났습니다: {names}")
            self.dynamics_state.set_q(state.position)
            self.dynamics.compute_forward_kinematics(self.dynamics_state)
            transform = self.dynamics.compute_transformation(self.dynamics_state, 0, 1)
            ft = state.ft_sensor_right
            control = self.robot.get_control_manager_state()
            return {
                "server_captured_at": now_iso(),
                "right_arm_joint_names": [self.model.robot_joint_names[index] for index in self.indices],
                "right_arm_rad": position,
                "right_arm_deg": [math.degrees(value) for value in position],
                "right_arm_velocity_rad_s": velocity,
                "right_arm_velocity_deg_s": [math.degrees(value) for value in velocity],
                "max_speed_deg_s": fastest,
                "joint_limit_violations": violations,
                "ee_right_transform": [
                    [float(cell) for cell in row] for row in transform
                ],
                "ft_sensor_right": {
                    "force_n": [float(value) for value in ft.force],
                    "torque_nm": [float(value) for value in ft.torque],
                },
                "control_manager": {
                    "state": str(control.state),
                    "control_state": str(getattr(control, "control_state", "unknown")),
                    "enabled_joint_idx": [
                        int(index) for index in getattr(control, "enabled_joint_idx", [])
                    ],
                },
            }

    def close(self) -> None:
        if hasattr(self.robot, "disconnect"):
            self.robot.disconnect()


class CaptureSession:
    def __init__(self, directory: Path, address: str, model: str) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.document_path = directory / "session.json"
        self.csv_path = directory / "captures.csv"
        self.lock = threading.Lock()
        if self.document_path.exists():
            self.document = json.loads(self.document_path.read_text(encoding="utf-8"))
            if self.document.get("schema") != SESSION_SCHEMA:
                raise RuntimeError(f"지원하지 않는 session 파일입니다: {self.document_path}")
        else:
            self.document = {
                "schema": SESSION_SCHEMA,
                "started_at": now_iso(),
                "robot": {"address": address, "model": model},
                "home": None,
                "anchors": [],
            }
            self._write()
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
                csv.DictWriter(handle, fieldnames=CAPTURE_CSV_FIELDS).writeheader()

    def _write(self) -> None:
        temporary = self.document_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.document_path)

    def summary(self) -> dict[str, Any]:
        with self.lock:
            anchors = self.document["anchors"]
            return {
                "directory": str(self.directory),
                "home_saved": self.document["home"] is not None,
                "anchor_count": len(anchors),
                "last_sample": None if not anchors else anchors[-1],
            }

    def save(
        self,
        kind: str,
        iphone_image: bytes,
        robot_snapshot: dict[str, Any],
        iphone_captured_at: str,
        capture_client: dict[str, Any] | None = None,
        framing: dict[str, Any] | None = None,
        rgb_image: bytes | None = None,
    ) -> dict[str, Any]:
        validate_jpeg(iphone_image)
        if rgb_image is not None:
            validate_jpeg(rgb_image)
        if kind not in {"anchor", "home"}:
            raise ValueError(f"지원하지 않는 capture kind입니다: {kind}")
        with self.lock:
            if kind == "anchor":
                label = f"wp{len(self.document['anchors']) + 1:02d}"
            else:
                label = "home"
            stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
            iphone_name = f"{label}_{stamp}_iphone.jpg"
            rgb_name = f"{label}_{stamp}_rgb.jpg" if rgb_image is not None else ""
            (self.directory / iphone_name).write_bytes(iphone_image)
            if rgb_image is not None:
                (self.directory / rgb_name).write_bytes(rgb_image)
            sample = {
                "label": label,
                "kind": kind,
                "browser_captured_at": iphone_captured_at,
                "iphone_captured_at": iphone_captured_at,
                "capture_client": capture_client or {},
                "framing": framing,
                "reference_image": iphone_name,
                "iphone_image": iphone_name,
                "rgb_image": rgb_name or None,
                **robot_snapshot,
            }
            if kind == "anchor":
                self.document["anchors"].append(sample)
            else:
                self.document["home"] = sample
            self._write()
            self._append_csv(sample)
            return sample

    def _append_csv(self, sample: dict[str, Any]) -> None:
        arm_rad = sample.get("right_arm_rad", [])
        arm_deg = sample.get("right_arm_deg", [])
        ft = sample.get("ft_sensor_right", {})
        framing = sample.get("framing") or {}
        row: dict[str, Any] = {
            "sample_id": sample["label"],
            "kind": sample["kind"],
            "server_captured_at": sample.get("server_captured_at", ""),
            "iphone_captured_at": sample.get("iphone_captured_at", ""),
            "iphone_image": sample.get("iphone_image", ""),
            "rgb_image": sample.get("rgb_image", ""),
            "template_id": framing.get("template_id", ""),
            "framing_positioned": framing.get("positioned", ""),
            "ee_right_transform_json": json.dumps(sample.get("ee_right_transform", [])),
            "force_n_json": json.dumps(ft.get("force_n", [])),
            "torque_nm_json": json.dumps(ft.get("torque_nm", [])),
            "max_speed_deg_s": sample.get("max_speed_deg_s", ""),
        }
        row.update({f"right_arm_{index}_rad": value for index, value in enumerate(arm_rad, 1)})
        row.update({f"right_arm_{index}_deg": value for index, value in enumerate(arm_deg, 1)})
        write_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with self.csv_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CAPTURE_CSV_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    def export_keyposes(self, grasp_id: str, capture_count: int) -> Path:
        with self.lock:
            home = self.document.get("home")
            anchors = self.document.get("anchors", [])
            if not isinstance(home, dict):
                raise ValueError("H를 눌러 Home을 먼저 저장하세요")
            if len(anchors) < 2:
                raise ValueError("anchor가 최소 2개 필요합니다")
            if capture_count < len(anchors):
                raise ValueError("capture_count는 anchor 수보다 작을 수 없습니다")

            def pose(sample: dict[str, Any]) -> dict[str, Any]:
                return {
                    "right_arm_rad": sample["right_arm_rad"],
                    "ee_right_transform": sample["ee_right_transform"],
                    "ft_sensor_right": sample["ft_sensor_right"],
                    "reference_image": sample["reference_image"],
                    "browser_captured_at": sample["browser_captured_at"],
                    "server_captured_at": sample["server_captured_at"],
                }

            output = {
                "schema": KEYPOSE_SCHEMA,
                "name": f"RBY1 browser teaching {self.document['started_at']}",
                "tool": {
                    "grasp_id": grasp_id,
                    "camera_transform_status": "uncalibrated",
                },
                "home": pose(home),
                "anchors": [
                    {"label": sample["label"], "enabled": True, **pose(sample)}
                    for sample in anchors
                ],
                "planning": {
                    "capture_count": capture_count,
                    "max_joint_speed_rad_s": 0.10,
                    "dwell_seconds": 1.5,
                    "min_travel_seconds": 1.0,
                    "entry_seconds": 6.0,
                    "home_seconds": 6.0,
                    "blocked_edges": [],
                },
            }
            path = self.directory / "keyposes.json"
            path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return path


def create_app(args: argparse.Namespace) -> FastAPI:
    session = CaptureSession(args.session_dir, args.address, args.model)
    phone = PhoneLink()
    skeleton = SkeletonBridge(args.skeleton_base_url)
    framing_directory = getattr(args, "framing_session_dir", None)
    if framing_directory is None:
        stamp = datetime.now().astimezone().strftime("session_%Y%m%d_%H%M%S")
        framing_directory = ROOT / "calibration" / "rby1" / "framing_samples" / stamp
    framing_session = FramingCalibrationSession(Path(framing_directory))
    reader: Rby1StateReader | None = None
    capture_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal reader
        reader = await asyncio.to_thread(
            Rby1StateReader,
            args.address,
            args.model,
            args.max_speed_deg_s,
            args.joint_limit_tolerance_deg,
        )
        try:
            yield
        finally:
            if reader is not None:
                await asyncio.to_thread(reader.close)

    app = FastAPI(title="GeekSeek RBY1 pose capture", lifespan=lifespan)
    app.mount("/samples", StaticFiles(directory=session.directory), name="samples")
    app.mount(
        "/framing-samples",
        StaticFiles(directory=framing_session.directory),
        name="framing-samples",
    )

    @app.get("/operator", include_in_schema=False)
    async def operator_page() -> FileResponse:
        return FileResponse(ROOT / "web" / "rby1_pose_capture.html")

    @app.get("/framing-calibration", include_in_schema=False)
    async def framing_calibration_page() -> FileResponse:
        return FileResponse(ROOT / "web" / "rby1_framing_calibration.html")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(ROOT / "web" / "rby1_pose_capture_index.html")

    @app.get("/phone", include_in_schema=False)
    async def phone_page() -> FileResponse:
        # Keep the Phorce Studio phone sender unchanged.
        return FileResponse(ROOT / "web" / "phone_pose_stream.html")

    @app.websocket("/phone-ws")
    async def phone_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        if phone.socket is not None:
            await phone.socket.close(code=1000, reason="new iPhone connection")
        phone.socket = websocket
        try:
            while True:
                message = await websocket.receive()
                if message.get("bytes") is not None:
                    phone.receive_frame(message["bytes"])
                elif message.get("text") is not None:
                    phone.receive_metadata(message["text"])
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if phone.socket is websocket:
                phone.socket = None

    @app.get("/api/live-frame", include_in_schema=False)
    async def live_frame() -> Response:
        try:
            image = phone.latest(args.max_phone_frame_age)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(
            image,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/framing/{template_id}", include_in_schema=False)
    async def framing_status(template_id: str) -> dict[str, Any]:
        if template_id not in {"full_body", "upper_body"}:
            raise HTTPException(status_code=422, detail="지원하지 않는 촬영 모드입니다")
        try:
            return await asyncio.to_thread(skeleton.status, template_id)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/framing-frame/{template_id}", include_in_schema=False)
    async def framing_frame(template_id: str) -> Response:
        if template_id not in {"full_body", "upper_body"}:
            raise HTTPException(status_code=422, detail="지원하지 않는 촬영 모드입니다")
        try:
            image = await asyncio.to_thread(skeleton.frame, template_id)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(
            image,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/rgb-frame", include_in_schema=False)
    async def rgb_frame() -> Response:
        try:
            image = await asyncio.to_thread(skeleton.rgb_frame)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(
            image,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/framing-calibration/status", include_in_schema=False)
    async def framing_calibration_status(label: str = "full_body") -> dict[str, Any]:
        if label not in {"full_body", "upper_body"}:
            raise HTTPException(status_code=422, detail="지원하지 않는 라벨입니다")
        try:
            pose = await asyncio.to_thread(skeleton.status, label)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"session": framing_session.summary(), "pose": pose}

    @app.post("/api/framing-calibration/capture", include_in_schema=False)
    async def capture_framing_sample(label: str = "full_body") -> dict[str, Any]:
        if label not in {"full_body", "upper_body"}:
            raise HTTPException(status_code=422, detail="지원하지 않는 라벨입니다")
        async with capture_lock:
            try:
                status = await asyncio.to_thread(skeleton.status, label)
                rgb_image, skeleton_image = await asyncio.gather(
                    asyncio.to_thread(skeleton.rgb_frame),
                    asyncio.to_thread(skeleton.frame, label),
                )
                sample = await asyncio.to_thread(
                    framing_session.save,
                    label,
                    rgb_image,
                    skeleton_image,
                    status,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "sample": sample,
            "session": framing_session.summary(),
            "rgb_url": f"/framing-samples/{sample['rgb_image']}",
            "skeleton_url": f"/framing-samples/{sample['skeleton_image']}",
            "csv_url": "/framing-samples/framing_samples.csv",
        }

    @app.get("/local-ca.crt", include_in_schema=False)
    async def local_ca_certificate() -> FileResponse:
        path = ROOT / "certs" / "local-ca.crt"
        if not path.exists():
            raise HTTPException(status_code=404, detail="로컬 CA 인증서가 없습니다")
        return FileResponse(
            path,
            media_type="application/x-x509-ca-cert",
            filename="geekseek-rby1-local-ca.crt",
        )

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        assert reader is not None
        try:
            robot = await asyncio.to_thread(reader.snapshot, require_stopped=False)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        age = None
        if phone.latest_frame is not None:
            age = asyncio.get_running_loop().time() - phone.latest_frame_monotonic
        return {
            "robot": robot,
            "session": session.summary(),
            "phone_connected": phone.connected,
            "phone_frame_ready": phone.latest_frame is not None,
            "phone_frame_age_seconds": None if age is None else round(age, 3),
            "phone_metadata": phone.metadata,
        }

    @app.post("/api/capture/{kind}")
    async def capture(kind: str, template_id: str = "full_body") -> dict[str, Any]:
        assert reader is not None
        async with capture_lock:
            try:
                framing = {"template_id": template_id, "configured": False}
                await asyncio.to_thread(reader.snapshot, require_stopped=True)
                snapshot_result, phone_result, rgb_image = await asyncio.gather(
                    asyncio.to_thread(reader.snapshot, require_stopped=True),
                    phone.capture(args.phone_timeout),
                    asyncio.to_thread(skeleton.rgb_frame),
                )
                snapshot = snapshot_result
                image, phone_captured_at = phone_result
                after = await asyncio.to_thread(reader.snapshot, require_stopped=True)
                movement = [
                    abs(math.degrees(end - start))
                    for start, end in zip(snapshot["right_arm_rad"], after["right_arm_rad"])
                ]
                sample = await asyncio.to_thread(
                    session.save,
                    kind,
                    image,
                    snapshot,
                    phone_captured_at,
                    {"type": "iphone-websocket", **phone.metadata},
                    framing,
                    rgb_image,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "sample": sample,
            "image_url": f"/samples/{sample['reference_image']}",
            "session": session.summary(),
            "movement_during_capture_deg": movement,
        }

    @app.post("/api/export")
    async def export() -> dict[str, str]:
        try:
            path = await asyncio.to_thread(
                session.export_keyposes, args.grasp_id, args.capture_count
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"path": str(path), "url": f"/samples/{path.name}"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="192.168.30.1:50051")
    parser.add_argument("--model", choices=("a", "m"), default="a")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8455)
    parser.add_argument("--max-speed-deg-s", type=float, default=0.5)
    parser.add_argument("--joint-limit-tolerance-deg", type=float, default=1.0)
    parser.add_argument("--phone-timeout", type=float, default=5.0)
    parser.add_argument("--max-phone-frame-age", type=float, default=1.0)
    parser.add_argument("--skeleton-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--capture-count", type=int, default=30)
    parser.add_argument("--grasp-id", default="phone_grasp_v1")
    parser.add_argument("--session-dir", type=Path)
    parser.add_argument("--framing-session-dir", type=Path)
    parser.add_argument("--keyfile", type=Path, default=ROOT / "certs" / "key.pem")
    parser.add_argument("--certfile", type=Path, default=ROOT / "certs" / "cert.pem")
    args = parser.parse_args()
    if args.session_dir is None:
        stamp = datetime.now().astimezone().strftime("session_%Y%m%d_%H%M%S")
        args.session_dir = ROOT / "calibration" / "rby1" / "capture_sessions" / stamp
    if args.framing_session_dir is None:
        stamp = datetime.now().astimezone().strftime("session_%Y%m%d_%H%M%S")
        args.framing_session_dir = ROOT / "calibration" / "rby1" / "framing_samples" / stamp
    if (
        args.max_speed_deg_s <= 0
        or args.joint_limit_tolerance_deg < 0
        or args.capture_count < 2
        or args.phone_timeout <= 0
        or args.max_phone_frame_age <= 0
    ):
        parser.error("속도 한계는 0보다 크고 capture-count는 2 이상이어야 합니다")
    if not args.keyfile.exists() or not args.certfile.exists():
        parser.error("iPhone 카메라용 HTTPS 인증서가 certs/에 없습니다")
    print(f"laptop operator: https://<JETSON-WIFI-IP>:{args.port}/operator")
    print(f"iPhone sender:   https://<JETSON-WIFI-IP>:{args.port}/phone")
    print(f"session: {args.session_dir}")
    print(f"framing calibration: {args.framing_session_dir}")
    print("SPACE=anchor, H=home, E=export; this server never commands the robot")
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

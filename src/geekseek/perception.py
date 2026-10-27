from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "pose_landmarker_lite.task"
LOGGER = logging.getLogger(__name__)
PERFORMANCE_LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class PersonSignal:
    detected: bool
    people_count: int = 0
    size_ratio: float = 0.0  # visible-landmark bbox area / frame area, 0..1
    center_x: float = 0.5  # normalized 0..1, bbox center
    center_y: float = 0.5
    hand_raised: bool = False  # 손목이 어깨보다 위에 있는 사람이 한 명이라도 있으면 True
    left_hand_raised: bool = False
    right_hand_raised: bool = False


class PersonSensor(Protocol):
    def sense(self, frame: object) -> PersonSignal: ...


# MediaPipe PoseLandmarker indices (BlazePose 33-point topology).
_LEFT_SHOULDER, _RIGHT_SHOULDER = 11, 12
_LEFT_WRIST, _RIGHT_WRIST = 15, 16


def _raised_hands(landmarks_list: list, min_visibility: float, margin: float = 0.05) -> tuple[bool, bool]:
    """Return whether an anatomical left/right wrist is above its shoulder."""
    left_raised = False
    right_raised = False
    for landmarks in landmarks_list:
        for side, shoulder_index, wrist_index in (
            ("left", _LEFT_SHOULDER, _LEFT_WRIST),
            ("right", _RIGHT_SHOULDER, _RIGHT_WRIST),
        ):
            shoulder, wrist = landmarks[shoulder_index], landmarks[wrist_index]
            if shoulder.visibility < min_visibility or wrist.visibility < min_visibility:
                continue
            if wrist.y < shoulder.y - margin:
                if side == "left":
                    left_raised = True
                else:
                    right_raised = True
    return left_raised, right_raised


def _any_hand_raised(landmarks_list: list, min_visibility: float, margin: float = 0.05) -> bool:
    return any(_raised_hands(landmarks_list, min_visibility, margin))


def create_pose_landmarker(
    model_path: Path = MODEL_PATH,
    max_people: int = 2,
    min_detection_confidence: float = 0.5,
    prefer_gpu: bool = True,
) -> tuple[object, object, str]:
    """Create a two-person pose landmarker, preferring the GPU delegate.

    Some MediaPipe Python wheels expose the GPU enum but are compiled with GPU
    calculators disabled. Keep the kiosk usable on those machines by logging
    the exact failure and retrying with the explicit CPU delegate.
    """
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python import vision

    delegates = [BaseOptions.Delegate.GPU, BaseOptions.Delegate.CPU] if prefer_gpu else [BaseOptions.Delegate.CPU]
    for delegate in delegates:
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path), delegate=delegate),
            running_mode=vision.RunningMode.IMAGE,
            num_poses=max_people,
            min_pose_detection_confidence=min_detection_confidence,
        )
        try:
            landmarker = vision.PoseLandmarker.create_from_options(options)
        except (NotImplementedError, RuntimeError) as exc:
            if delegate is BaseOptions.Delegate.GPU:
                LOGGER.warning("MediaPipe GPU delegate unavailable; falling back to CPU: %s", exc)
                continue
            raise
        delegate_name = delegate.name.lower()
        LOGGER.info("MediaPipe pose landmarker initialized: delegate=%s, max_people=%d", delegate_name, max_people)
        return mp, landmarker, delegate_name
    raise RuntimeError("could not initialize MediaPipe pose landmarker")


class FakePersonSensor:
    """Returns a scripted sequence of signals, one per sense() call (repeats the last)."""

    def __init__(self, signals: list[PersonSignal] | None = None) -> None:
        self.signals = signals or [PersonSignal(detected=False)]
        self._index = 0

    def sense(self, frame: object) -> PersonSignal:
        signal = self.signals[min(self._index, len(self.signals) - 1)]
        self._index += 1
        return signal


class MediaPipePersonSensor:
    """Wraps MediaPipe's PoseLandmarker (IMAGE mode) to derive a coarse
    "how close / how centered" signal from a single BGR frame (e.g. from
    cv2.VideoCapture) — enough for the scenario's 2단계(접근 감지)와
    4→5단계(정위치 확인). Full landmark keypoints stay on the raw result if a
    later feature (AR 가이드 등) needs them; this class only exposes the
    reduced signal."""

    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        min_detection_confidence: float = 0.5,
        min_landmark_visibility: float = 0.5,
        min_visible_landmarks: int = 8,
        max_people: int = 2,
    ) -> None:
        from mediapipe.tasks.python import vision

        self._mp, self._landmarker, self.delegate_name = create_pose_landmarker(
            model_path=model_path,
            max_people=max_people,
            min_detection_confidence=min_detection_confidence,
        )
        self._min_landmark_visibility = min_landmark_visibility
        self._min_visible_landmarks = min_visible_landmarks
        self._connections = vision.PoseLandmarksConnections().POSE_LANDMARKS
        self._last_landmarks_list: list = []
        self.loop_fps = 0.0
        self.inference_fps = 0.0
        self._last_sense_at: float | None = None
        self._next_performance_log = time.perf_counter() + 5.0

    def sense(self, frame: object) -> PersonSignal:
        started = time.perf_counter()
        if self._last_sense_at is not None:
            instant_loop_fps = 1.0 / max(started - self._last_sense_at, 1e-6)
            self.loop_fps = instant_loop_fps if self.loop_fps == 0.0 else self.loop_fps * 0.9 + instant_loop_fps * 0.1
        self._last_sense_at = started
        # Channel reversal produces a negative-stride NumPy view. MediaPipe's
        # ARM pybind accepts only contiguous image buffers, so materialize it.
        rgb = frame[:, :, ::-1].copy()  # cv2 gives BGR; mediapipe wants RGB
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)
        instant_inference_fps = 1.0 / max(time.perf_counter() - started, 1e-6)
        self.inference_fps = (
            instant_inference_fps
            if self.inference_fps == 0.0
            else self.inference_fps * 0.9 + instant_inference_fps * 0.1
        )
        if started >= self._next_performance_log:
            PERFORMANCE_LOGGER.info(
                "MediaPipe pose performance: delegate=%s, loop_fps=%.1f, inference_fps=%.1f",
                self.delegate_name,
                self.loop_fps,
                self.inference_fps,
            )
            self._next_performance_log = started + 5.0
        if not result.pose_landmarks:
            self._last_landmarks_list = []
            return PersonSignal(detected=False, people_count=len(result.pose_landmarks))

        self._last_landmarks_list = list(result.pose_landmarks)

        # Low-confidence poses can place unseen joints anywhere, including
        # far outside the frame (x/y outside [0, 1]) — drop those landmarks
        # and require enough confidently-visible ones left to trust the box.
        # Pool every visible landmark across every detected person into one
        # union bbox — "두 분" approaching/positioned is a single group signal,
        # not per-person.
        xs: list[float] = []
        ys: list[float] = []
        for landmarks in result.pose_landmarks:
            for point in landmarks:
                if point.visibility >= self._min_landmark_visibility:
                    xs.append(min(1.0, max(0.0, point.x)))
                    ys.append(min(1.0, max(0.0, point.y)))

        if len(xs) < self._min_visible_landmarks:
            return PersonSignal(detected=False)

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        left_hand_raised, right_hand_raised = _raised_hands(
            result.pose_landmarks, self._min_landmark_visibility
        )
        return PersonSignal(
            detected=True,
            people_count=len(result.pose_landmarks),
            size_ratio=max(0.0, max_x - min_x) * max(0.0, max_y - min_y),
            center_x=(min_x + max_x) / 2,
            center_y=(min_y + max_y) / 2,
            hand_raised=left_hand_raised or right_hand_raised,
            left_hand_raised=left_hand_raised,
            right_hand_raised=right_hand_raised,
        )

    @property
    def latest_landmarks(self) -> tuple:
        """Pose landmarks from the latest ``sense`` call.

        The tuple is read-only at the container level and lets guest-facing
        guide tools use the full skeleton without depending on a private
        implementation attribute.
        """
        return tuple(self._last_landmarks_list)

    def annotate_jpeg(self, frame: object, signal: PersonSignal, approaching: bool, positioned: bool) -> bytes:
        """Draws every detected person's skeleton (from the most recent
        sense() call) plus a status readout onto a copy of frame, for a live
        debug view — never called from the hot sense() path, only by the
        coordinator's debug stream."""
        import cv2

        annotated = frame.copy()
        height, width = annotated.shape[:2]
        for landmarks in self._last_landmarks_list:
            points = [
                (int(p.x * width), int(p.y * height))
                if p.visibility >= self._min_landmark_visibility
                else None
                for p in landmarks
            ]
            for connection in self._connections:
                start, end = points[connection.start], points[connection.end]
                if start is not None and end is not None:
                    cv2.line(annotated, start, end, (80, 220, 120), 2)
            for point in points:
                if point is not None:
                    cv2.circle(annotated, point, 4, (60, 140, 255), -1)

        lines = [
            f"detected={signal.detected}  people={len(self._last_landmarks_list)}  delegate={self.delegate_name}",
            f"loop={self.loop_fps:.1f}fps  inference={self.inference_fps:.1f}fps",
            f"size_ratio={signal.size_ratio:.3f}  center=({signal.center_x:.2f}, {signal.center_y:.2f})",
            f"approaching={approaching}  positioned={positioned}",
            f"hands: left={signal.left_hand_raised}  right={signal.right_hand_raised}",
        ]
        for index, line in enumerate(lines):
            y = 28 + index * 26
            cv2.putText(annotated, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            color = (120, 255, 120) if index == 2 and (approaching or positioned) else (255, 255, 255)
            cv2.putText(annotated, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1)

        ok, buffer = cv2.imencode(".jpg", annotated)
        return buffer.tobytes() if ok else b""

    def mirror_jpeg(self, frame: object) -> bytes:
        """Center-cropped 3:4 JPEG for the guest-facing iPad preview.

        The 1280x960 inference/storage frame stays untouched.  The iPad gets
        its native-density center 720x960 crop, with no stretching or resize.
        Not actually mirrored despite the historical method name: mirroring
        makes text and numbers in the scene read backwards.
        """
        return encode_jpeg(center_crop_to_aspect(frame, 3, 4))

    def close(self) -> None:
        self._landmarker.close()


class WebcamFrameSource:
    """Grabs frames from a cv2 webcam on a background thread so the async
    sense loop (Coordinator) never blocks the event loop on cv2's blocking
    capture.read(). Call the instance to get the most recent frame (or None
    before the first frame arrives). Capped at target_fps — reading faster
    than the sense loop (5Hz by default) or the MJPEG streams (~6.7Hz) can
    consume just wastes a full CPU core on frames nobody looks at."""

    def __init__(
        self,
        camera_index: int = 0,
        target_fps: float = 15.0,
        frame_width: int = 1280,
        frame_height: int = 960,
        fourcc: str = "MJPG",
        device_fps: float | None = None,
        camera_name: str = "",
    ) -> None:
        import cv2

        if len(fourcc) != 4:
            raise ValueError(f"camera FOURCC must be four characters: {fourcc!r}")
        self._camera_index = camera_index
        self._camera_name = camera_name
        self._target_fps = target_fps
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._fourcc = fourcc
        self._device_fps = device_fps
        self._capture = self._open_capture(cv2)
        self._latest: object | None = None
        self._running = True
        self._frame_interval = 1.0 / target_fps if target_fps > 0 else 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _open_capture(self, cv2: object) -> object:
        errors: list[str] = []
        for candidate in camera_device_candidates(self._camera_name, self._camera_index):
            # Resolve the stable by-id symlink only when opening. If USB
            # enumeration changes later, a reconnect resolves it again.
            device = str(candidate.resolve()) if isinstance(candidate, Path) else candidate
            capture = cv2.VideoCapture(device)
            if not capture.isOpened():
                errors.append(str(candidate))
                capture.release()
                continue
            # UVC cameras often deliver useful resolutions only in compressed
            # MJPEG mode. Select FOURCC before dimensions and device FPS.
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self._fourcc))
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._frame_width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._frame_height)
            capture.set(cv2.CAP_PROP_FPS, self._device_fps or self._target_fps)
            LOGGER.info("camera opened: %s -> %s", candidate, device)
            return capture
        description = f"name {self._camera_name!r}" if self._camera_name else f"index {self._camera_index}"
        raise RuntimeError(f"could not open camera {description}; candidates={errors}")

    def _loop(self) -> None:
        import cv2
        import time

        while self._running:
            started = time.monotonic()
            ok, frame = self._capture.read()
            if ok:
                self._latest = frame
            else:
                LOGGER.warning("camera frame read failed; reconnecting by device name")
                self._capture.release()
                while self._running:
                    try:
                        self._capture = self._open_capture(cv2)
                    except RuntimeError as exc:
                        LOGGER.warning("camera reconnect waiting: %s", exc)
                        time.sleep(1.0)
                    else:
                        LOGGER.info("camera reconnected")
                        break
            remaining = self._frame_interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)

    def __call__(self) -> object | None:
        return self._latest

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=1.0)
        self._capture.release()


def _normalized_camera_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def camera_device_candidates(
    camera_name: str,
    camera_index: int,
    *,
    by_id_dir: Path = Path("/dev/v4l/by-id"),
    sysfs_dir: Path = Path("/sys/class/video4linux"),
    dev_dir: Path = Path("/dev"),
) -> tuple[Path | int, ...]:
    """Resolve a UVC capture stream without depending on ``/dev/videoN``.

    UVC devices commonly expose ``video-index0`` (capture) and ``index1``
    (metadata). The by-id link includes the USB product/serial and remains
    stable when the kernel assigns a different video number after reconnect.
    """
    if not camera_name:
        return (camera_index,)

    wanted = _normalized_camera_name(camera_name)
    stable = tuple(
        path
        for path in sorted(by_id_dir.glob("*-video-index0"))
        if wanted in _normalized_camera_name(path.name)
    )
    if stable:
        return stable

    devices: list[Path] = []
    for entry in sorted(sysfs_dir.glob("video*"), key=lambda path: path.name):
        try:
            product_name = (entry / "name").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if wanted in _normalized_camera_name(product_name):
            devices.append(dev_dir / entry.name)
    if devices:
        return tuple(devices)
    raise RuntimeError(f"camera named {camera_name!r} was not found")


def encode_jpeg(frame: object) -> bytes:
    """Plain JPEG encode, no overlay/mirror — for handing a frame to something
    outside cv2's world (e.g. the VLM greeter)."""
    import cv2

    ok, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes() if ok else b""


def center_crop_to_aspect(frame: object, aspect_width: int, aspect_height: int) -> object:
    """Return a centered view at the requested aspect without resizing.

    For example, a 1280x960 4:3 frame cropped to 3:4 becomes 720x960.  The
    returned slice keeps the source resolution and never distorts geometry.
    """
    if aspect_width <= 0 or aspect_height <= 0:
        raise ValueError("aspect dimensions must be positive")
    height, width = frame.shape[:2]
    target_ratio = aspect_width / aspect_height
    source_ratio = width / height
    if math.isclose(source_ratio, target_ratio, rel_tol=0.0, abs_tol=1e-9):
        return frame
    if source_ratio > target_ratio:
        cropped_width = max(1, round(height * target_ratio))
        left = (width - cropped_width) // 2
        return frame[:, left : left + cropped_width]
    cropped_height = max(1, round(width / target_ratio))
    top = (height - cropped_height) // 2
    return frame[top : top + cropped_height, :]


def is_approaching(signal: PersonSignal, size_threshold: float = 0.12) -> bool:
    """시나리오 2단계: skeleton이 일정 크기 이상이면 접근으로 판단."""
    return signal.detected and signal.size_ratio >= size_threshold


def is_ready_signal(signal: PersonSignal) -> bool:
    """시나리오 5단계: 카운트다운 시작 전, 손을 들어 준비됐음을 알리는 신호."""
    return signal.detected and signal.hand_raised


def is_positioned(
    signal: PersonSignal,
    center_x_range: tuple[float, float] = (0.35, 0.65),
    center_y_range: tuple[float, float] = (0.3, 0.85),
) -> bool:
    """시나리오 4→5단계: 표시된 위치(화면 중앙 하단 영역)로 이동했는지 판단."""
    if not signal.detected:
        return False
    return center_x_range[0] <= signal.center_x <= center_x_range[1] and (
        center_y_range[0] <= signal.center_y <= center_y_range[1]
    )

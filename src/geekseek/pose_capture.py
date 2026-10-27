from __future__ import annotations

import csv
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class AxisPose:
    index: int
    position_rad: float
    velocity_rad_s: float
    valid: bool
    pos_ref_echo_rad: float | None = None


@dataclass(frozen=True)
class FeedbackSnapshot:
    captured_at: str
    monotonic_seconds: float
    axes: tuple[AxisPose, ...]


class FeedbackStore:
    """Thread-safe handoff from the rclpy feedback thread to FastAPI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: FeedbackSnapshot | None = None

    def update(self, snapshot: FeedbackSnapshot) -> None:
        with self._lock:
            self._latest = snapshot

    def latest(self) -> FeedbackSnapshot | None:
        with self._lock:
            return self._latest


class PoseCaptureError(RuntimeError):
    pass


def validate_snapshot(
    snapshot: FeedbackSnapshot | None,
    *,
    max_age_seconds: float,
    max_speed_deg_s: float,
) -> FeedbackSnapshot:
    if snapshot is None:
        raise PoseCaptureError("아직 /phorce/feedback 데이터가 없습니다")

    age = time.monotonic() - snapshot.monotonic_seconds
    if age > max_age_seconds:
        raise PoseCaptureError(f"로봇 피드백이 오래되었습니다 ({age:.2f}초)")

    invalid = [axis.index for axis in snapshot.axes if not axis.valid]
    if invalid:
        raise PoseCaptureError(f"valid가 아닌 축이 있습니다: {invalid}")

    fastest = max((abs(math.degrees(axis.velocity_rad_s)) for axis in snapshot.axes), default=0.0)
    if fastest > max_speed_deg_s:
        raise PoseCaptureError(
            f"로봇팔이 아직 움직이는 중입니다 (최대 {fastest:.2f} deg/s, "
            f"허용 {max_speed_deg_s:.2f} deg/s)"
        )
    return snapshot


def wrapped_delta(current_rad: float, zero_rad: float) -> float:
    return math.atan2(math.sin(current_rad - zero_rad), math.cos(current_rad - zero_rad))


def snapshot_dict(
    snapshot: FeedbackSnapshot,
    zero_offsets_rad: dict[int, float] | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "captured_at": snapshot.captured_at,
        "axis_indices": [axis.index for axis in snapshot.axes],
        "position_rad": [round(axis.position_rad, 9) for axis in snapshot.axes],
        "position_deg": [round(math.degrees(axis.position_rad), 6) for axis in snapshot.axes],
        "velocity_rad_s": [round(axis.velocity_rad_s, 9) for axis in snapshot.axes],
        "velocity_deg_s": [round(math.degrees(axis.velocity_rad_s), 6) for axis in snapshot.axes],
        "pos_ref_echo_rad": [
            None if axis.pos_ref_echo_rad is None else round(axis.pos_ref_echo_rad, 9)
            for axis in snapshot.axes
        ],
        "valid": [axis.valid for axis in snapshot.axes],
    }
    if zero_offsets_rad is not None:
        offsets = [zero_offsets_rad[axis.index] for axis in snapshot.axes]
        zeroed = [wrapped_delta(axis.position_rad, zero_offsets_rad[axis.index]) for axis in snapshot.axes]
        data.update(
            {
                "zero_offset_rad": [round(value, 9) for value in offsets],
                "zero_offset_deg": [round(math.degrees(value), 6) for value in offsets],
                "zeroed_position_rad": [round(value, 9) for value in zeroed],
                "zeroed_position_deg": [round(math.degrees(value), 6) for value in zeroed],
            }
        )
    return data


def save_pose_sample(
    csv_file: Path,
    image: bytes,
    webcam_image: bytes,
    snapshot: FeedbackSnapshot,
    zero_offsets_rad: dict[int, float] | None = None,
) -> dict[str, object]:
    if not image:
        raise PoseCaptureError("휴대폰에서 빈 이미지가 도착했습니다")
    if not webcam_image:
        raise PoseCaptureError("웹캠 skeleton 이미지가 비어 있습니다")
    if len(snapshot.axes) != 5:
        raise PoseCaptureError(f"5축 pose가 필요합니다 (현재 {len(snapshot.axes)}축)")

    timestamp = datetime.now().astimezone()
    stem = timestamp.strftime("pose_%Y%m%d_%H%M%S_%f")
    iphone_name = f"{stem}_iphone.jpg"
    webcam_name = f"{stem}_webcam.jpg"
    axis_fields = [f"axis_{axis.index}_deg" for axis in snapshot.axes]
    fieldnames = [*axis_fields, "webcam_image", "iphone_image"]

    if zero_offsets_rad is None:
        positions_deg = [math.degrees(axis.position_rad) for axis in snapshot.axes]
    else:
        positions_deg = [
            math.degrees(wrapped_delta(axis.position_rad, zero_offsets_rad[axis.index]))
            for axis in snapshot.axes
        ]
    row: dict[str, object] = {
        **dict(zip(axis_fields, (round(value, 6) for value in positions_deg))),
        "webcam_image": webcam_name,
        "iphone_image": iphone_name,
    }

    csv_file.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_file.exists() or csv_file.stat().st_size == 0
    if not write_header:
        with csv_file.open("r", encoding="utf-8", newline="") as handle:
            existing_header = next(csv.reader(handle), [])
        if existing_header != fieldnames:
            raise PoseCaptureError(
                f"CSV 축/열 구성이 현재 로봇과 다릅니다: {existing_header}"
            )

    (csv_file.parent / iphone_name).write_bytes(image)
    (csv_file.parent / webcam_name).write_bytes(webcam_image)
    with csv_file.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return row

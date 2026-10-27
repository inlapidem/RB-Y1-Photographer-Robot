from __future__ import annotations

import csv
import math
import time

import pytest

from geekseek.pose_capture import (
    AxisPose,
    FeedbackSnapshot,
    PoseCaptureError,
    save_pose_sample,
    validate_snapshot,
    wrapped_delta,
)
from scripts.pose_dataset_viewer import load_samples


def snapshot(*, speed_deg_s: float = 0.0) -> FeedbackSnapshot:
    return FeedbackSnapshot(
        captured_at="2026-08-08T12:00:00.000+09:00",
        monotonic_seconds=time.monotonic(),
        axes=tuple(
            AxisPose(
                index=index,
                position_rad=math.radians(index * 10),
                velocity_rad_s=math.radians(speed_deg_s),
                valid=True,
            )
            for index in range(5)
        ),
    )


def test_rejects_moving_robot() -> None:
    with pytest.raises(PoseCaptureError, match="움직이는 중"):
        validate_snapshot(snapshot(speed_deg_s=1.0), max_age_seconds=0.2, max_speed_deg_s=0.5)


def read_rows(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_appends_paired_images_and_pose_to_csv(tmp_path) -> None:
    csv_file = tmp_path / "trial.csv"
    first = save_pose_sample(csv_file, b"iphone-1", b"webcam-1", snapshot())
    second = save_pose_sample(csv_file, b"iphone-2", b"webcam-2", snapshot())

    assert (tmp_path / str(first["iphone_image"])).read_bytes() == b"iphone-1"
    assert (tmp_path / str(first["webcam_image"])).read_bytes() == b"webcam-1"
    rows = read_rows(csv_file)
    assert len(rows) == 2
    assert list(rows[0]) == [
        "axis_0_deg",
        "axis_1_deg",
        "axis_2_deg",
        "axis_3_deg",
        "axis_4_deg",
        "webcam_image",
        "iphone_image",
    ]
    assert [float(rows[0][f"axis_{index}_deg"]) for index in range(5)] == [
        0.0,
        10.0,
        20.0,
        30.0,
        40.0,
    ]
    assert rows[1]["iphone_image"] == second["iphone_image"]

    samples = load_samples(csv_file)
    assert len(samples) == 2
    assert samples[0].positions_deg == (0.0, 10.0, 20.0, 30.0, 40.0)
    assert samples[0].iphone_path.name == first["iphone_image"]
    assert samples[0].webcam_path.name == first["webcam_image"]


def test_applies_wrapped_zero_offsets_to_csv(tmp_path) -> None:
    current = snapshot()
    offsets = {axis.index: axis.position_rad for axis in current.axes}
    csv_file = tmp_path / "trial.csv"
    save_pose_sample(csv_file, b"iphone", b"webcam", current, offsets)

    row = read_rows(csv_file)[0]
    assert [float(row[f"axis_{index}_deg"]) for index in range(5)] == [0.0] * 5
    assert math.isclose(wrapped_delta(-math.pi + 0.1, math.pi - 0.1), 0.2)

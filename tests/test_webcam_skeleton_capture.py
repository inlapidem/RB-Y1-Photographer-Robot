from datetime import datetime, timezone

import pytest

from scripts.webcam_skeleton_capture import save_pair


def test_save_pair_uses_one_timestamp_for_both_images(tmp_path) -> None:
    captured_at = datetime(2026, 8, 8, 8, 30, 15, 123456, tzinfo=timezone.utc)
    webcam_path, skeleton_path = save_pair(
        tmp_path,
        b"webcam-jpeg",
        b"skeleton-jpeg",
        captured_at=captured_at,
    )

    assert webcam_path.name == "webcam_20260808_083015_123456_webcam.jpg"
    assert skeleton_path.name == "webcam_20260808_083015_123456_skeleton.jpg"
    assert webcam_path.read_bytes() == b"webcam-jpeg"
    assert skeleton_path.read_bytes() == b"skeleton-jpeg"


def test_save_pair_rejects_missing_frame(tmp_path) -> None:
    with pytest.raises(ValueError, match="모두 준비"):
        save_pair(tmp_path, b"", b"skeleton")

import csv

from scripts.framing_image_labeler import (
    FULL_BODY,
    UNUSED,
    discover_pairs,
    load_labels,
    save_labels,
)


def test_discovers_only_complete_image_pairs(tmp_path) -> None:
    (tmp_path / "webcam_a_webcam.jpg").write_bytes(b"raw-a")
    (tmp_path / "webcam_a_skeleton.jpg").write_bytes(b"skel-a")
    (tmp_path / "webcam_b_webcam.jpg").write_bytes(b"orphan")

    pairs = discover_pairs(tmp_path)

    assert [pair.sample_id for pair in pairs] == ["webcam_a"]


def test_saves_and_updates_labels_csv(tmp_path) -> None:
    (tmp_path / "webcam_a_webcam.jpg").write_bytes(b"raw-a")
    (tmp_path / "webcam_a_skeleton.jpg").write_bytes(b"skel-a")
    pairs = discover_pairs(tmp_path)
    csv_file = tmp_path / "framing_labels.csv"

    save_labels(csv_file, pairs, {"webcam_a": FULL_BODY})
    assert load_labels(csv_file) == {"webcam_a": FULL_BODY}

    save_labels(csv_file, pairs, {"webcam_a": UNUSED})
    assert load_labels(csv_file) == {"webcam_a": UNUSED}
    with csv_file.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["webcam_image"] == "webcam_a_webcam.jpg"
    assert row["skeleton_image"] == "webcam_a_skeleton.jpg"

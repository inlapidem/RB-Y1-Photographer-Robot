#!/usr/bin/env python3
"""Fail fast unless MediaPipe PoseLandmarker can initialize its GPU delegate."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "models" / "pose_landmarker_lite.task",
    )
    args = parser.parse_args()

    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(
            model_asset_path=str(args.model.resolve()),
            delegate=BaseOptions.Delegate.GPU,
        ),
        running_mode=vision.RunningMode.IMAGE,
        num_poses=2,
    )
    landmarker = vision.PoseLandmarker.create_from_options(options)
    blank = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=__import__("numpy").zeros((480, 640, 3), dtype="uint8"),
    )
    result = landmarker.detect(blank)
    landmarker.close()
    print(f"OK: GPU delegate initialized; num_poses=2; detections={len(result.pose_landmarks)}")


if __name__ == "__main__":
    main()


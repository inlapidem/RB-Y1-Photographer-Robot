"""Isolated smoke test for the laptop-webcam-as-C270 perception link.

Opens a local webcam, runs MediaPipePersonSensor on live frames, and prints
detected/size_ratio/center + approach/positioned flags so you can confirm
person detection actually works before wiring it into the workflow state
machine (that integration is a separate, not-yet-decided step).

Usage:
  python scripts/test_webcam_perception.py [camera-index] [seconds]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cv2

from geekseek.perception import MediaPipePersonSensor, is_approaching, is_positioned


def main() -> None:
    camera_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        print(f"could not open camera index {camera_index}")
        raise SystemExit(1)

    sensor = MediaPipePersonSensor()
    print(f"reading camera {camera_index} for {seconds:.0f}s — stand in frame to test detection")

    start = time.monotonic()
    frames = 0
    detections = 0
    try:
        while time.monotonic() - start < seconds:
            ok, frame = capture.read()
            if not ok:
                print("frame read failed")
                break
            frames += 1
            signal = sensor.sense(frame)
            if signal.detected:
                detections += 1
                print(
                    f"detected size_ratio={signal.size_ratio:.3f} "
                    f"center=({signal.center_x:.2f},{signal.center_y:.2f}) "
                    f"approaching={is_approaching(signal)} positioned={is_positioned(signal)}"
                )
    finally:
        capture.release()
        sensor.close()

    print(f"done: {frames} frames read, {detections} had a detected person")


if __name__ == "__main__":
    main()

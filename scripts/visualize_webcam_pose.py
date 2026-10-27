"""Grabs a few frames from a local webcam, draws the MediaPipe pose skeleton
on each, and saves them as PNGs so the detection quality can be checked
visually (rather than just reading size_ratio/center numbers).

Usage:
  python scripts/visualize_webcam_pose.py [camera-index] [num-frames] [out-dir]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cv2

from mediapipe.tasks.python import vision

from geekseek.perception import MODEL_PATH, create_pose_landmarker

CONNECTIONS = vision.PoseLandmarksConnections().POSE_LANDMARKS


def draw_skeleton(frame, landmarks, min_visibility: float = 0.5) -> None:
    height, width = frame.shape[:2]
    points = []
    for point in landmarks:
        if point.visibility >= min_visibility:
            points.append((int(point.x * width), int(point.y * height)))
        else:
            points.append(None)

    for connection in CONNECTIONS:
        start, end = points[connection.start], points[connection.end]
        if start is not None and end is not None:
            cv2.line(frame, start, end, (80, 220, 120), 2)

    for point in points:
        if point is not None:
            cv2.circle(frame, point, 4, (60, 140, 255), -1)


def main() -> None:
    camera_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    num_frames = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(__file__).resolve().parents[1] / "photos"
    out_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        print(f"could not open camera index {camera_index}")
        raise SystemExit(1)

    # PersonSensor.sense() only returns the reduced signal, so use the shared
    # raw two-person landmarker to get full keypoints for drawing.
    mp, landmarker, delegate_name = create_pose_landmarker(MODEL_PATH, max_people=2)

    saved = []
    try:
        # warm up / let auto-exposure settle
        for _ in range(5):
            capture.read()

        for i in range(num_frames):
            ok, frame = capture.read()
            if not ok:
                print("frame read failed")
                break

            rgb = frame[:, :, ::-1]
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect(mp_image)

            for landmarks in result.pose_landmarks:
                draw_skeleton(frame, landmarks)
            people = len(result.pose_landmarks)
            label = f"people={people}  delegate={delegate_name}" if people else f"no person  delegate={delegate_name}"
            cv2.putText(frame, label, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)

            path = out_dir / f"pose_check_{i + 1}.png"
            cv2.imwrite(str(path), frame)
            saved.append(path)
            print(f"saved {path} ({label})")
            time.sleep(1.0)
    finally:
        capture.release()
        landmarker.close()

    print("done:", ", ".join(str(p) for p in saved))


if __name__ == "__main__":
    main()

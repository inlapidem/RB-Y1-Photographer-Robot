"""Live webcam window with the MediaPipe pose skeleton drawn on top, for
eyeballing detection quality in real time.

Usage:
  python scripts/live_webcam_pose.py [camera-index]

Press 'q' or Esc in the window to quit.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cv2
from mediapipe.tasks.python import vision

from geekseek.perception import MODEL_PATH, create_pose_landmarker

CONNECTIONS = vision.PoseLandmarksConnections().POSE_LANDMARKS


def draw_skeleton(frame, landmarks, min_visibility: float = 0.5) -> None:
    height, width = frame.shape[:2]
    points = [
        (int(p.x * width), int(p.y * height)) if p.visibility >= min_visibility else None
        for p in landmarks
    ]
    for connection in CONNECTIONS:
        start, end = points[connection.start], points[connection.end]
        if start is not None and end is not None:
            cv2.line(frame, start, end, (80, 220, 120), 2)
    for point in points:
        if point is not None:
            cv2.circle(frame, point, 4, (60, 140, 255), -1)


def main() -> None:
    camera_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        print(f"could not open camera index {camera_index}")
        raise SystemExit(1)

    mp, landmarker, delegate_name = create_pose_landmarker(MODEL_PATH, max_people=2)

    window = "geekseek pose preview (q/Esc to quit)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    try:
        while True:
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

            cv2.imshow(window, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        capture.release()
        landmarker.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

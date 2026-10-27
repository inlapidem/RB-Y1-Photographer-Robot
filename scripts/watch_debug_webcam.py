"""Native local window showing the running kiosk server's webcam debug view
(skeleton + detection status overlay) — pulls JPEG frames over HTTP from the
server's own /debug/webcam stream instead of opening the camera device a
second time (which would fail: the server already holds it exclusively).

Run this in a separate terminal *while* `python3 -m geekseek --config ...`
is already running.

Usage:
  python scripts/watch_debug_webcam.py [server-url]
  (default: https://127.0.0.1:8443/debug/webcam)

Press 'q' or Esc in the window to quit.
"""

from __future__ import annotations

import sys
import time

import cv2
import httpx
import numpy as np


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://127.0.0.1:8443/debug/webcam"
    # Auto-launched alongside the server (see __main__.py) — the server needs
    # a moment to bind the port and load the mediapipe model, so retry a few
    # times before giving up instead of failing on the very first attempt.
    max_attempts = 20
    retry_seconds = 1.0

    window = "geekseek debug webcam (q/Esc to quit)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    for attempt in range(1, max_attempts + 1):
        buffer = b""
        try:
            with httpx.Client(verify=False, timeout=10.0) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        buffer += chunk
                        while True:
                            start = buffer.find(b"\xff\xd8")
                            end = buffer.find(b"\xff\xd9", start)
                            if start == -1 or end == -1:
                                break
                            jpeg = buffer[start : end + 2]
                            buffer = buffer[end + 2 :]
                            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                            if frame is not None:
                                cv2.imshow(window, frame)
                            key = cv2.waitKey(1) & 0xFF
                            if key in (ord("q"), 27):
                                cv2.destroyAllWindows()
                                return
            break  # stream ended cleanly (server shut down) — stop retrying
        except httpx.HTTPStatusError as exc:
            print(f"server returned {exc.response.status_code} — is person_sensor: mediapipe set?")
            break
        except httpx.RequestError as exc:
            if attempt == max_attempts:
                print(f"could not reach {url} after {max_attempts} attempts: {exc}")
                break
            time.sleep(retry_seconds)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

"""Isolated smoke test for the Jetson<->phone capture link.

Does not touch the FastAPI app, coordinator, or workflow state machine —
just hits the phone's snapshot endpoint once and saves the result, so you
can confirm the network link works before wiring it into the full app.

Prerequisites:
  - Phone (Android) has the "IP Webcam" app installed, server started.
  - Phone and this machine are on the same Wi-Fi/hotspot.
  - The app screen shows an IP like http://192.168.x.x:8080 — use that IP.

Usage:
  python scripts/test_phone_capture.py <phone-ip> [port]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from geekseek.capture import HttpSnapshotCapture  # noqa: E402


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    ip = sys.argv[1]
    port = sys.argv[2] if len(sys.argv) > 2 else "8080"
    url = f"http://{ip}:{port}/photo.jpg"
    save_dir = Path(__file__).resolve().parents[1] / "photos"

    capture = HttpSnapshotCapture(snapshot_url=url, save_dir=save_dir)
    print(f"GET {url} ...")
    result = await capture.capture()
    saved_path = save_dir / Path(result.photo_url).name
    print(f"OK: saved {saved_path} ({saved_path.stat().st_size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())

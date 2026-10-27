"""Standalone receiver for the iPhone Shortcuts+Pushcut capture test.

Starts a tiny HTTP server that accepts a photo POSTed by the Shortcuts
automation and saves it locally. Decoupled from the full app/coordinator —
this only exists to confirm the phone -> this machine upload path works.

Usage:
  python scripts/receive_photo.py [port]

Then in the iPhone Shortcuts automation, add a "Get Contents of URL" action:
  URL:    http://<this-machine-ip>:<port>/upload
  Method: POST
  Request Body: Form
    field name: photo   ->  value: (the photo taken earlier in the shortcut)
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, UploadFile

SAVE_DIR = Path(__file__).resolve().parents[1] / "photos"

app = FastAPI()


@app.post("/upload")
async def upload(photo: UploadFile) -> dict[str, str]:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SAVE_DIR / f"iphone_{timestamp}.jpg"
    path.write_bytes(await photo.read())
    print(f"received: {path} ({path.stat().st_size} bytes)")
    return {"status": "ok", "saved": path.name}


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    print(f"listening on 0.0.0.0:{port} — POST photo to /upload")
    uvicorn.run(app, host="0.0.0.0", port=port)

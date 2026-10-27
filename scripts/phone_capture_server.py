"""Standalone HTTPS server for the "phone as webapp camera" capture test.

Decoupled from the full app/coordinator — this only exists to confirm the
web app link works: phone opens a page in Safari, grants camera access
once, then this server can remote-trigger a frame capture at any time
(no tap needed on the phone) and receive it back over the same socket.

iOS Safari only allows getUserMedia (camera access) on secure (HTTPS)
origins, so this needs the self-signed cert in certs/ (see
scripts/generate_dev_cert.sh or the README note below).

Usage:
  1. python scripts/phone_capture_server.py [port]
  2. On the phone, open https://<this-machine-ip>:<port>/  in Safari,
     accept the "not private" warning once, allow camera access.
  3. From another terminal on this machine:
       curl -X POST https://<this-machine-ip>:<port>/trigger -k
     Each call grabs one frame from the phone and saves it to photos/.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
SAVE_DIR = ROOT / "photos"
CERT_DIR = ROOT / "certs"

app = FastAPI()
_phone_socket: WebSocket | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "phone_capture.html")


@app.websocket("/phone-ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    global _phone_socket
    await websocket.accept()
    _phone_socket = websocket
    print("phone connected")
    try:
        while True:
            data = await websocket.receive_bytes()
            SAVE_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = SAVE_DIR / f"phone_{timestamp}.jpg"
            path.write_bytes(data)
            print(f"received: {path} ({len(data)} bytes)")
    except WebSocketDisconnect:
        print("phone disconnected")
        _phone_socket = None


@app.post("/trigger")
async def trigger() -> dict[str, str]:
    if _phone_socket is None:
        return {"status": "error", "detail": "no phone connected"}
    await _phone_socket.send_text("capture")
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    keyfile = CERT_DIR / "key.pem"
    certfile = CERT_DIR / "cert.pem"
    if not keyfile.exists() or not certfile.exists():
        print(f"missing cert files in {CERT_DIR} — generate with openssl first (see repo notes)")
        raise SystemExit(1)

    print(f"listening on https://0.0.0.0:{port} — open on phone, then POST /trigger to capture")
    uvicorn.run(app, host="0.0.0.0", port=port, ssl_keyfile=str(keyfile), ssl_certfile=str(certfile))

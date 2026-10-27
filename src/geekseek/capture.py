from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class CaptureResult:
    photo_url: str
    # 갤러리는 URL이 아니라 파일에서 바로 읽는다. fake 캡처처럼 실제 파일이
    # 없는 구현은 None을 둔다.
    path: Path | None = None


class CaptureDevice(Protocol):
    async def capture(self) -> CaptureResult: ...


class FakeCapture:
    def __init__(self, capture_seconds: float = 0.2) -> None:
        self.capture_seconds = capture_seconds
        self.count = 0

    async def capture(self) -> CaptureResult:
        await asyncio.sleep(self.capture_seconds)
        self.count += 1
        return CaptureResult(photo_url=f"/api/fake-photo/{self.count}.svg")


class _TextSender(Protocol):
    async def send_text(self, data: str) -> None: ...


class WebAppCapture:
    """Capture device backed by a phone running the Safari webapp page
    (web/phone_capture.html). The live WebSocket connection is owned by the
    /phone-ws route in web.py, which calls bind()/unbind()/on_frame() on this
    object; capture() only does the trigger-then-await-one-frame handshake."""

    def __init__(self, save_dir: Path, timeout_seconds: float = 5.0) -> None:
        self.save_dir = save_dir
        self.timeout_seconds = timeout_seconds
        self.count = 0
        self._socket: _TextSender | None = None
        self._pending: asyncio.Future[bytes] | None = None
        self.camera_metadata: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def bind(self, socket: _TextSender) -> None:
        self._socket = socket

    def unbind(self, socket: _TextSender) -> None:
        if self._socket is socket:
            self._socket = None

    def on_frame(self, data: bytes) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(data)

    def update_camera_metadata(self, metadata: dict[str, Any]) -> None:
        self.camera_metadata = dict(metadata)

    async def capture(self) -> CaptureResult:
        if self._socket is None:
            raise RuntimeError("no phone connected")

        self._pending = asyncio.get_running_loop().create_future()
        await self._socket.send_text("capture")
        data = await asyncio.wait_for(self._pending, timeout=self.timeout_seconds)

        self.count += 1
        self.save_dir.mkdir(parents=True, exist_ok=True)
        # A per-process counter alone restarts at 1 every launch and quietly
        # overwrites the previous session's photos — and a sweep now saves
        # dozens per guest, so a restart used to cost a whole shoot.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.save_dir / f"phone_{stamp}.jpg"
        path.write_bytes(data)
        return CaptureResult(photo_url=f"/photos/{path.name}", path=path)


class HttpSnapshotCapture:
    """Captures a still frame over HTTP from a phone running a snapshot server
    (e.g. the Android "IP Webcam" app's /photo.jpg endpoint)."""

    def __init__(self, snapshot_url: str, save_dir: Path, timeout_seconds: float = 5.0) -> None:
        self.snapshot_url = snapshot_url
        self.save_dir = save_dir
        self.timeout_seconds = timeout_seconds
        self.count = 0

    async def capture(self) -> CaptureResult:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(self.snapshot_url)
            response.raise_for_status()

        self.count += 1
        self.save_dir.mkdir(parents=True, exist_ok=True)
        path = self.save_dir / f"capture_{self.count}.jpg"
        path.write_bytes(response.content)
        return CaptureResult(photo_url=f"/photos/{path.name}", path=path)

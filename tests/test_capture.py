import asyncio
import unittest

from geekseek.capture import WebAppCapture


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


class WebAppCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_without_phone_raises(self) -> None:
        capture = WebAppCapture(save_dir=None)  # type: ignore[arg-type]
        with self.assertRaises(RuntimeError):
            await capture.capture()

    async def test_capture_triggers_and_saves_returned_frame(self) -> None:
        capture = WebAppCapture(save_dir=self._tmp_dir())
        socket = FakeSocket()
        capture.bind(socket)

        async def deliver_frame() -> None:
            await asyncio.sleep(0)
            capture.on_frame(b"jpeg-bytes")

        asyncio.ensure_future(deliver_frame())
        result = await capture.capture()

        self.assertEqual(socket.sent, ["capture"])
        # 파일명은 타임스탬프 기반 — 서버를 다시 띄워도 앞 세션 사진을 덮지 않는다.
        saved = list(capture.save_dir.glob("phone_*.jpg"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(result.photo_url, f"/photos/{saved[0].name}")
        self.assertEqual(saved[0].read_bytes(), b"jpeg-bytes")

    async def test_capture_times_out_if_no_frame_arrives(self) -> None:
        capture = WebAppCapture(save_dir=self._tmp_dir(), timeout_seconds=0.05)
        capture.bind(FakeSocket())
        with self.assertRaises(asyncio.TimeoutError):
            await capture.capture()

    async def test_unbind_ignores_stale_socket(self) -> None:
        capture = WebAppCapture(save_dir=self._tmp_dir())
        first, second = FakeSocket(), FakeSocket()
        capture.bind(first)
        capture.bind(second)
        capture.unbind(first)
        self.assertTrue(capture.connected)
        capture.unbind(second)
        self.assertFalse(capture.connected)

    async def test_camera_metadata_is_replaced_by_latest_report(self) -> None:
        capture = WebAppCapture(save_dir=self._tmp_dir())
        capture.update_camera_metadata({"video_width": 1920, "video_height": 1440})
        capture.update_camera_metadata({"video_width": 1440, "video_height": 1920})
        self.assertEqual(
            capture.camera_metadata,
            {"video_width": 1440, "video_height": 1920},
        )

    def _tmp_dir(self):
        import tempfile
        from pathlib import Path

        return Path(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()

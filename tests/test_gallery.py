from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from geekseek.capture import CaptureResult, FakeCapture
from geekseek.coordinator import Coordinator
from geekseek.gallery import Gallery, create_gallery_app, qr_svg
from geekseek.robot import FakeRobot
from geekseek.web import create_app
from geekseek.workflow import EventType, State


class FileCapture(FakeCapture):
    """FakeCapture, but it writes a real file — the gallery serves bytes off
    disk, not the URLs the kiosk shows."""

    def __init__(self, save_dir: Path) -> None:
        super().__init__(0)
        self.save_dir = save_dir

    async def capture(self) -> CaptureResult:
        result = await super().capture()
        path = self.save_dir / f"photo_{self.count}.jpg"
        path.write_bytes(b"\xff\xd8jpeg" + str(self.count).encode())
        return CaptureResult(photo_url=result.photo_url, path=path)


class GalleryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.photos = []
        for index in range(3):
            path = self.dir / f"p{index}.jpg"
            path.write_bytes(f"jpeg-{index}".encode())
            self.photos.append(path)
        self.gallery = Gallery(base_url="https://example.ts.net")
        self.client = TestClient(create_gallery_app(self.gallery))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_disabled_without_a_base_url(self) -> None:
        """기존 설정들은 gallery_base_url이 없으므로 갤러리가 꺼져 있어야 한다."""
        self.assertFalse(Gallery().enabled)
        self.assertTrue(self.gallery.enabled)

    def test_each_shoot_gets_its_own_unguessable_link(self) -> None:
        first = self.gallery.publish(self.photos[:2])
        second = self.gallery.publish(self.photos[2:])
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 16)
        self.assertEqual(len(self.gallery.photos(first)), 2)
        self.assertEqual(len(self.gallery.photos(second)), 1)

    def test_one_session_cannot_see_anothers_photos(self) -> None:
        mine = self.gallery.publish(self.photos[:1])
        theirs = self.gallery.publish(self.photos[1:])
        self.assertEqual(self.client.get(f"/g/{mine}/0.jpg").content, b"jpeg-0")
        # 내 링크로는 내 사진 개수만큼만 열린다.
        self.assertEqual(self.client.get(f"/g/{mine}/1.jpg").status_code, 404)
        self.assertEqual(self.client.get(f"/g/{theirs}/0.jpg").content, b"jpeg-1")

    def test_unknown_token_is_not_found(self) -> None:
        self.assertEqual(self.client.get("/g/nope").status_code, 404)
        self.assertEqual(self.client.get("/g/nope/0.jpg").status_code, 404)

    def test_page_lists_every_photo(self) -> None:
        token = self.gallery.publish(self.photos)
        html = self.client.get(f"/g/{token}").text
        self.assertIn("사진 3장 모두 저장", html)
        self.assertIn(token, html)

    def test_page_carries_both_save_paths(self) -> None:
        """저장 통로가 플랫폼마다 다르다 — 한쪽만 남으면 다른 쪽은 사진첩에
        넣지 못한다. iOS 공유시트에는 '이미지 N개 저장'이 있지만 안드로이드에는
        없어서, 안드로이드에서 share를 쓰면 '다른 앱으로 보내기'만 뜬다."""
        html = self.client.get(f"/g/{self.gallery.publish(self.photos)}").text
        self.assertIn("navigator.share", html)   # iOS
        self.assertIn("link.download", html)     # 안드로이드·데스크톱
        self.assertIn("iPad|iPhone|iPod", html)  # 기능 감지가 아니라 플랫폼 분기

    def test_old_sessions_are_evicted(self) -> None:
        gallery = Gallery(base_url="https://example.ts.net", max_sessions=2)
        first = gallery.publish(self.photos[:1])
        gallery.publish(self.photos[1:2])
        gallery.publish(self.photos[2:])
        with self.assertRaises(KeyError):
            gallery.photos(first)

    def test_qr_encodes_the_real_link(self) -> None:
        """장식용 그림이 아니라 실제로 스캔되는 코드여야 한다."""
        import io

        import cv2
        import numpy as np
        import segno

        token = self.gallery.publish(self.photos)
        url = self.gallery.url_for(token)
        self.assertTrue(url.startswith("https://example.ts.net/g/"))
        self.assertIn(b"<svg", qr_svg(url)[:200])

        buffer = io.BytesIO()
        segno.make(url, error="m").save(buffer, kind="png", scale=8, border=4)
        image = cv2.imdecode(np.frombuffer(buffer.getvalue(), np.uint8), cv2.IMREAD_COLOR)
        decoded, *_ = cv2.QRCodeDetector().detectAndDecode(image)
        self.assertEqual(decoded, url)


class KioskGalleryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.gallery = Gallery(base_url="https://example.ts.net")
        self.coordinator = Coordinator(
            FakeRobot(0),
            FileCapture(Path(self._tmp.name)),
            greeting_seconds=0.05,
            countdown_seconds=0,
            preview_seconds=0.05,
            farewell_seconds=0.05,
            gallery=self.gallery,
        )
        self.context = TestClient(create_app(self.coordinator))
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self._tmp.cleanup()

    def _emit(self, event_type: EventType, **data: object) -> None:
        self.client.portal.call(self.coordinator.emit, event_type, **data)

    def _run_capture(self) -> dict:
        self._emit(EventType.PERSON_APPROACHED)
        self.client.portal.call(self.coordinator.wait_for_state, State.DECIDING, 5.0)
        self.client.post("/api/capture-started", json={"template_id": "upper_body"})
        self.client.portal.call(self.coordinator.wait_for_state, State.GUIDING, 5.0)
        self._emit(EventType.POSITION_REACHED)
        self.client.portal.call(self.coordinator.wait_for_state, State.PREVIEWING, 10.0)
        return self.client.get("/api/state").json()

    def test_no_qr_before_there_are_photos(self) -> None:
        self.assertEqual(self.client.get("/api/state").json()["gallery_url"], "")
        self.assertEqual(self.client.get("/api/qr.svg").status_code, 404)

    def test_capture_publishes_a_gallery_link_and_qr(self) -> None:
        state = self._run_capture()
        self.assertTrue(state["gallery_url"].startswith("https://example.ts.net/g/"))

        response = self.client.get("/api/qr.svg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/svg+xml")
        self.assertIn(b"<svg", response.content[:200])

        token = state["gallery_url"].rsplit("/", 1)[-1]
        self.assertEqual(len(self.gallery.photos(token)), 3)

    def test_link_is_cleared_when_the_guest_leaves(self) -> None:
        self._run_capture()
        self.client.portal.call(self.coordinator.wait_for_state, State.ASKING, 10.0)
        self.client.post("/api/liked")
        self.client.portal.call(self.coordinator.wait_for_state, State.WAITING, 10.0)
        self.assertEqual(self.client.get("/api/state").json()["gallery_url"], "")

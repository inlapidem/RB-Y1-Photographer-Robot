import unittest

from fastapi.testclient import TestClient

from geekseek.capture import FakeCapture
from geekseek.coordinator import Coordinator
from geekseek.robot import FakeRobot
from geekseek.web import create_app
from geekseek.workflow import EventType


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = Coordinator(
            FakeRobot(0),
            FakeCapture(0),
            greeting_seconds=0.05,
            countdown_seconds=0,
            preview_seconds=0.05,
            farewell_seconds=0.05,
        )
        self.client_context = TestClient(create_app(self.coordinator))
        self.client = self.client_context.__enter__()
        # PERSON_APPROACHED/POSITION_REACHED are webcam-driven in production (no HTTP
        # route for them); tests reach into the coordinator via the TestClient's portal
        # so the call runs on the same event loop as the app instead of a stray one.
        self._emit(EventType.PERSON_APPROACHED)
        self._wait_for("deciding")

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)

    def _emit(self, event_type: EventType, **data: object) -> None:
        self.client.portal.call(self.coordinator.emit, event_type, **data)

    def test_pages_and_initial_state(self) -> None:
        self.assertEqual(self.client.get("/face").status_code, 200)
        self.assertEqual(self.client.get("/guide").status_code, 200)
        self.assertEqual(self.client.get("/standby").status_code, 200)
        self.assertEqual(self.client.get("/debug").status_code, 200)
        self.assertEqual(self.client.get("/api/state").json()["state"], "deciding")

    def test_full_api_flow(self) -> None:
        self.assertEqual(
            self.client.post("/api/capture-started", json={"template_id": "upper_body"}).status_code,
            200,
        )
        self._wait_for("guiding")

        self._emit(EventType.POSITION_REACHED)
        previewing = self._wait_for("previewing")
        self.assertEqual(len(previewing["photos"]), 3)
        for photo_url in previewing["photos"]:
            self.assertTrue(photo_url.endswith(".svg"))
            self.assertEqual(self.client.get(photo_url).status_code, 200)

        asking = self._wait_for("asking")
        self.assertEqual(self.client.post("/api/replay").status_code, 200)
        self._wait_for("previewing")
        self._wait_for("asking")

        self.assertEqual(self.client.post("/api/liked").status_code, 200)
        farewell = self._wait_for("farewell")
        self.assertEqual(farewell["photos"], asking["photos"])

        waiting = self._wait_for("waiting")
        self.assertEqual(waiting["photos"], [])
        self.assertIsNone(waiting["template_id"])

    def test_decline_returns_to_waiting(self) -> None:
        self.assertEqual(self.client.post("/api/decline").status_code, 200)
        self._wait_for("waiting")

    def test_invalid_action_returns_conflict(self) -> None:
        response = self.client.post("/api/liked")
        self.assertEqual(response.status_code, 409)

    def test_unknown_template_id_rejected(self) -> None:
        response = self.client.post("/api/capture-started", json={"template_id": "bogus"})
        self.assertEqual(response.status_code, 422)

    def _wait_for(self, state: str) -> dict[str, object]:
        for _ in range(200):
            value = self.client.get("/api/state").json()
            if value["state"] == state:
                return value
        self.fail(f"state did not become {state}")

    def test_guide_offers_a_button_per_framing(self) -> None:
        """구도 선택 단계의 두 버튼이 각자 다른 슬롯으로 이어져야 한다."""
        html = self.client.get("/guide").text
        self.assertIn('data-template="full_body"', html)
        self.assertIn('data-template="upper_body"', html)
        self.assertIn("전신샷", html)
        self.assertIn("상반신샷", html)
        # 옛 단일 버튼이 남아 있으면 JS가 둘 중 하나만 연결한다.
        self.assertNotIn('id="start-capture"', html)

    def test_standby_combines_face_and_guide_controls(self) -> None:
        html = self.client.get("/standby").text
        self.assertIn('data-page="standby"', html)
        self.assertIn('id="face"', html)
        self.assertIn('id="guide-stage"', html)
        self.assertIn('data-template="full_body"', html)
        self.assertIn('data-template="upper_body"', html)
        self.assertIn("왼손을 1초 들어주세요", html)
        self.assertIn("오른손을 1초 들어주세요", html)

    def test_debug_hand_buttons_select_the_mapped_framing(self) -> None:
        html = self.client.get("/debug").text
        self.assertIn('id="gesture-left"', html)
        self.assertIn('id="gesture-right"', html)
        response = self.client.post("/api/debug/gesture-left")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["template_id"], "full_body")

    def test_debug_keeps_ipad_links_and_adds_standby(self) -> None:
        html = self.client.get("/debug").text
        self.assertIn('href="/face"', html)
        self.assertIn('href="/guide"', html)
        self.assertIn('href="/standby"', html)

    def _start_capture(self, template: str) -> None:
        response = self.client.post("/api/capture-started", json={"template_id": template})
        self.assertEqual(response.status_code, 200)
        state = self.client.get("/api/state").json()
        self.assertEqual(state["template_id"], template)
        self.assertEqual(state["state"], "guiding")

    def test_full_body_button_starts_a_full_body_capture(self) -> None:
        self._start_capture("full_body")

    def test_upper_body_button_starts_an_upper_body_capture(self) -> None:
        self._start_capture("upper_body")

    def test_state_carries_the_expected_photo_count(self) -> None:
        """가이드 화면의 'n / N' 진행률이 쓰는 값."""
        self.assertIn("photo_target", self.client.get("/api/state").json())

    def test_state_carries_live_framing_fields(self) -> None:
        state = self.client.get("/api/state").json()
        self.assertIn("framing_message", state)
        self.assertIn("framing_direction", state)
        self.assertIn("framing_positioned", state)

    def test_debug_page_offers_a_skip_for_every_recognition_gate(self) -> None:
        """사람이 몰리면 세 인식이 다 흔들린다. 운영자가 각각 넘길 수 있어야 한다."""
        html = self.client.get("/debug").text
        for button_id in ("skip-approach", "skip-position", "skip-ready"):
            self.assertIn(f'id="{button_id}"', html)

    def test_debug_script_wires_every_skip_button(self) -> None:
        """버튼만 있고 핸들러가 없으면 눌러도 조용히 아무 일도 안 난다."""
        script = self.client.get("/static/app.js").text
        for selector, path in (
            ("#skip-approach", "/api/debug/person-approached"),
            ("#skip-position", "/api/debug/position-reached"),
            ("#skip-ready", "/api/debug/ready-signal"),
        ):
            self.assertIn(f'bind("{selector}", "{path}")', script)

    def test_skip_approach_advances_from_waiting(self) -> None:
        self.assertEqual(self.client.post("/api/decline").status_code, 200)
        self._wait_for("waiting")
        self.assertEqual(self.client.post("/api/debug/person-approached").status_code, 200)
        self._wait_for("deciding")

    def test_skip_approach_rejected_outside_waiting(self) -> None:
        self.assertEqual(self.client.post("/api/debug/person-approached").status_code, 409)

    def test_skip_position_advances_from_guiding(self) -> None:
        self._start_capture("full_body")
        self.assertEqual(self.client.post("/api/debug/position-reached").status_code, 200)
        self._wait_for("previewing")

    def test_skip_position_rejected_outside_guiding(self) -> None:
        self.assertEqual(self.client.post("/api/debug/position-reached").status_code, 409)

    def test_skip_ready_rejected_when_nothing_is_waiting_on_it(self) -> None:
        self.assertEqual(self.client.post("/api/debug/ready-signal").status_code, 409)

    def test_skip_ready_releases_the_hand_raise_wait(self) -> None:
        """실제 촬영에서는 person_sensor가 있어야 이 대기가 생긴다 — 그 상태를
        직접 만들어 두고, 버튼이 12초 타임아웃을 실제로 끊는지 본다."""
        self.coordinator.context.awaiting_ready = True
        self.assertEqual(self.client.post("/api/debug/ready-signal").status_code, 200)
        self.assertTrue(self.coordinator._ready_event.is_set())


if __name__ == "__main__":
    unittest.main()

import unittest

from geekseek.workflow import Event, EventType, InvalidTransition, State, WorkflowContext, apply_event


class WorkflowTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        context = WorkflowContext()
        events = [
            Event(EventType.SYSTEM_READY),
            Event(EventType.PERSON_APPROACHED),
            Event(EventType.GREETING_DONE),
            Event(EventType.CAPTURE_STARTED, {"template_id": "upper_body"}),
            Event(EventType.POSITION_REACHED),
            Event(EventType.BURST_COMPLETE, {"photos": ["/photos/1.jpg", "/photos/2.jpg"]}),
            Event(EventType.PREVIEW_DONE),
            Event(EventType.PHOTO_LIKED),
            Event(EventType.FAREWELL_DONE),
        ]
        for event in events:
            apply_event(context, event)

        self.assertEqual(context.state, State.WAITING)
        self.assertIsNone(context.template_id)
        self.assertEqual(context.photos, [])
        self.assertEqual(context.revision, len(events))

    def test_rejects_event_from_wrong_state(self) -> None:
        with self.assertRaises(InvalidTransition):
            apply_event(WorkflowContext(), Event(EventType.POSITION_REACHED))

    def test_burst_complete_stores_photos(self) -> None:
        context = WorkflowContext(state=State.CAPTURING, template_id="full_body")
        apply_event(context, Event(EventType.BURST_COMPLETE, {"photos": ["/photos/1.jpg"]}))
        self.assertEqual(context.state, State.PREVIEWING)
        self.assertEqual(context.photos, ["/photos/1.jpg"])

    def test_capture_failed_moves_to_error_with_reason(self) -> None:
        context = WorkflowContext(state=State.CAPTURING)
        apply_event(context, Event(EventType.CAPTURE_FAILED, {"reason": "no phone connected"}))
        self.assertEqual(context.state, State.ERROR)
        self.assertEqual(context.error, "no phone connected")

    def test_replay_returns_to_previewing(self) -> None:
        context = WorkflowContext(state=State.ASKING, photos=["/photos/1.jpg"])
        apply_event(context, Event(EventType.REPLAY_REQUESTED))
        self.assertEqual(context.state, State.PREVIEWING)
        self.assertEqual(context.photos, ["/photos/1.jpg"])

    def test_decline_clears_template_and_returns_to_waiting(self) -> None:
        context = WorkflowContext(state=State.DECIDING, template_id="full_body")
        apply_event(context, Event(EventType.DECLINED))
        self.assertEqual(context.state, State.WAITING)
        self.assertIsNone(context.template_id)

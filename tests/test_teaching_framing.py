from types import SimpleNamespace

from geekseek.capture import FakeCapture
from geekseek.coordinator import Coordinator
from geekseek.framing_guide import FULL_BODY, JointBand, JointPoint, SilhouetteTemplate
from geekseek.perception import PersonSignal
from geekseek.robot import FakeRobot


def landmarks(points):
    values = [SimpleNamespace(x=0.0, y=0.0, visibility=0.0) for _ in range(33)]
    for index, point in points.items():
        values[index] = SimpleNamespace(x=point.x, y=point.y, visibility=1.0)
    return values


def template():
    centers = {
        11: JointPoint(0.42, 0.35),
        12: JointPoint(0.58, 0.35),
        23: JointPoint(0.44, 0.62),
        24: JointPoint(0.56, 0.62),
    }
    return SilhouetteTemplate(
        mode=FULL_BODY,
        joints={index: JointBand(point, 0.08, 0.08) for index, point in centers.items()},
        sample_count=3,
    ), centers


def test_teaching_position_requires_two_stable_skeleton_frames():
    framing_template, centers = template()
    sensor = SimpleNamespace(latest_landmarks=(landmarks(centers),))
    coordinator = Coordinator(
        FakeRobot(0),
        FakeCapture(0),
        person_sensor=sensor,
        framing_templates={FULL_BODY: framing_template},
    )
    signal = PersonSignal(detected=True)

    coordinator._update_teaching_framing(signal)
    assert coordinator.teaching_framing_snapshot("full_body")["positioned"] is False

    coordinator._update_teaching_framing(signal)
    snapshot = coordinator.teaching_framing_snapshot("full_body")
    assert snapshot["positioned"] is True
    assert snapshot["stable_frames"] == 2
    assert snapshot["stable_required"] == 2


def test_teaching_position_rejects_multiple_people():
    framing_template, centers = template()
    pose = landmarks(centers)
    sensor = SimpleNamespace(latest_landmarks=(pose, pose))
    coordinator = Coordinator(
        FakeRobot(0),
        FakeCapture(0),
        person_sensor=sensor,
        framing_templates={FULL_BODY: framing_template},
    )
    coordinator._update_teaching_framing(PersonSignal(detected=True))
    snapshot = coordinator.teaching_framing_snapshot("full_body")
    assert snapshot["positioned"] is False
    assert snapshot["people_count"] == 2
    assert "한 명" in snapshot["message"]


def test_teaching_falls_back_to_center_and_size_without_dataset():
    _, centers = template()
    sensor = SimpleNamespace(latest_landmarks=(landmarks(centers),))
    coordinator = Coordinator(FakeRobot(0), FakeCapture(0), person_sensor=sensor)
    signal = PersonSignal(detected=True, size_ratio=0.18, center_x=0.5, center_y=0.5)
    for _ in range(2):
        coordinator._update_teaching_framing(signal)
    snapshot = coordinator.teaching_framing_snapshot("full_body")
    assert snapshot["algorithm"] == "center_size"
    assert snapshot["positioned"] is True

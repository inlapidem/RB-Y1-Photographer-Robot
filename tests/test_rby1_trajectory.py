import asyncio
import json
from pathlib import Path

import pytest

from geekseek.robot import Rby1Robot, load_rby1_trajectory


def replay_module():
    from importlib.util import module_from_spec, spec_from_file_location
    import sys

    path = Path(__file__).parents[1] / "scripts" / "replay_rby1_encoder.py"
    spec = spec_from_file_location("replay_rby1_encoder", path)
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def test_rby1_trajectory_keeps_dwell_timing(tmp_path):
    source = tmp_path / "full-body.json"
    source.write_text(
        json.dumps(
            {
                "name": "full body",
                "segments": [
                    {"kind": "entry", "duration_s": 1.0, "right_arm_rad": [0] * 7},
                    {"kind": "dwell", "label": "wp1", "duration_s": 2.0, "right_arm_rad": [0] * 7},
                    {"kind": "home", "duration_s": 3.0, "right_arm_rad": [0] * 7},
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = load_rby1_trajectory(source).sweep_plan()

    assert plan.total_seconds == 6.0
    assert plan.windows == [("wp1", 1.0, 3.0)]


def test_rby1_trajectory_rejects_non_seven_axis_target(tmp_path):
    source = tmp_path / "broken.json"
    source.write_text(
        json.dumps({"segments": [{"kind": "dwell", "duration_s": 1, "right_arm_rad": [0] * 5}]}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="7축"):
        load_rby1_trajectory(source)


def test_rby1_loader_compiles_keyposes_without_changing_calibration_path(tmp_path):
    source = tmp_path / "frame.full_body.json"
    source.write_text(
        json.dumps(
            {
                "schema": "geekseek.rby1.keyposes/v1",
                "home": {"right_arm_rad": [0.0] * 7},
                "anchors": [
                    {"label": "wp01", "right_arm_rad": [0.1] * 7},
                    {"label": "wp02", "right_arm_rad": [0.2] * 7},
                ],
                "planning": {"capture_count": 5},
            }
        ),
        encoding="utf-8",
    )

    trajectory = load_rby1_trajectory(source)

    assert len(trajectory.sweep_plan().windows) == 2
    assert sum(len(segment.shot_ratios) for segment in trajectory.segments) == 3
    assert trajectory.segments[-1].kind == "home"


def test_partial_replay_starts_at_base_and_returns_to_base():
    replay = replay_module()
    segments = [
        {"kind": "home", "label": "start-home", "duration_s": 6.0, "right_arm_rad": [0.0] * 7},
        {"kind": "entry", "label": "entry", "duration_s": 4.0, "right_arm_rad": [0.2] * 7},
        {"kind": "dwell", "label": "wp01", "duration_s": 1.5, "right_arm_rad": [0.2] * 7},
        {"kind": "travel", "label": "next", "duration_s": 4.0, "right_arm_rad": [0.4] * 7},
        {"kind": "dwell", "label": "wp02", "duration_s": 1.5, "right_arm_rad": [0.4] * 7},
        {"kind": "home", "label": "home", "duration_s": 6.0, "right_arm_rad": [0.0] * 7},
    ]
    result = replay.limited_test_path(segments, 1)
    assert [item["label"] for item in result] == ["start-home", "entry", "wp01", "test-return-home"]
    assert result[-1]["right_arm_rad"] == [0.0] * 7
    assert result[-1]["duration_s"] == pytest.approx(6.0)


def test_base_only_replay_does_not_visit_a_photo_pose():
    replay = replay_module()
    segments = [
        {"kind": "home", "label": "start-home", "duration_s": 6.0, "right_arm_rad": [0.0] * 7},
        {"kind": "dwell", "label": "wp01", "duration_s": 1.0, "right_arm_rad": [0.2] * 7},
        {"kind": "home", "label": "home", "duration_s": 6.0, "right_arm_rad": [0.0] * 7},
    ]
    assert replay.limited_test_path(segments, 0) == [segments[0]]


def test_hardware_adapter_refuses_old_continuous_recording(tmp_path):
    source = tmp_path / "frame.full_body.json"
    source.write_text(
        json.dumps(
            {
                "segments": [
                    {"kind": "dwell", "duration_s": 1.0, "right_arm_rad": [0.0] * 7}
                ]
            }
        ),
        encoding="utf-8",
    )
    robot = Rby1Robot({"frame.full_body": source}, "unused")

    with pytest.raises(RuntimeError, match="연속 녹화"):
        robot.trajectory_for("frame.full_body", 30)


def test_move_home_sends_only_the_calibrated_start_home(tmp_path):
    source = tmp_path / "frame.full_body.json"
    source.write_text(
        json.dumps(
            {
                "schema": "geekseek.rby1.keyposes/v1",
                "home": {"right_arm_rad": [0.0] * 7, "head_rad": [0.0, 0.1745329252]},
                "anchors": [
                    {"label": "wp01", "right_arm_rad": [0.1] * 7},
                    {"label": "wp02", "right_arm_rad": [0.2] * 7},
                ],
                "planning": {"capture_count": 5},
            }
        ),
        encoding="utf-8",
    )
    robot = Rby1Robot({"frame.full_body": source}, "unused")
    sent = []

    async def preflight():
        return None

    robot.preflight = preflight
    robot._retime_trajectory = lambda trajectory: trajectory
    robot._send_segment = sent.append

    asyncio.run(robot.move_home())

    assert robot.moves == ["home"]
    assert len(sent) == 1
    assert sent[0].kind == "home"
    assert sent[0].label == "start-home"
    assert sent[0].head_rad == pytest.approx((0.0, 0.1745329252))

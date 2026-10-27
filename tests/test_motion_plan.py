from __future__ import annotations

import csv
import io
import json
import unittest
from pathlib import Path

import pytest

from geekseek.capture import FakeCapture
from geekseek.coordinator import Coordinator
from geekseek.workflow import EventType, State

from geekseek.motion_plan import (
    P_VECTOR_COLUMNS,
    MotionPlanError,
    SweepPlan,
    Waypoint,
    build_schedule,
    joint_distance,
    optimize_order,
    plan_shot_times,
    render_motion_csv,
    shot_times_seconds,
)

AXES = (0, 1, 2, 6, 8)
HOME = (0.0, 0.0, 0.0, 0.0, 0.0)


def waypoints(*rows: tuple[float, ...]) -> list[Waypoint]:
    return [Waypoint(label=str(i + 1), angles_deg=row) for i, row in enumerate(rows)]


def schedule_for(points: list[Waypoint], seconds: float = 28.0):
    return build_schedule(
        points,
        optimize_order(points, HOME),
        home_deg=HOME,
        total_seconds=seconds,
        travel_deg_per_s=18.0,
        transit_deg_per_s=25.0,
    )


def test_optimize_order_shortens_the_route():
    # Two tight clusters interleaved, so visiting in capture order zig-zags.
    points = waypoints(
        (0.0, 0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, -50.0, 0.0, 0.0),
        (1.0, 0.0, 0.0, 0.0, 0.0),
        (1.0, 0.0, -50.0, 0.0, 0.0),
    )
    original = list(range(len(points)))
    best = optimize_order(points, HOME)

    def cost(order):
        angles = [points[i].angles_deg for i in order]
        return (
            joint_distance(HOME, angles[0])
            + sum(joint_distance(a, b) for a, b in zip(angles, angles[1:]))
            + joint_distance(angles[-1], HOME)
        )

    assert cost(best) < cost(original)
    assert sorted(best) == original


def test_schedule_hits_the_requested_duration_exactly():
    points = waypoints((5.0,) * 5, (-5.0,) * 5, (10.0,) * 5)
    segments = schedule_for(points, seconds=28.0)
    assert sum(segment.ticks for segment in segments) == 28_000
    assert sum(1 for segment in segments if segment.is_shot) == len(points)


def test_schedule_rejects_a_budget_too_small_to_pause_in():
    points = waypoints(*[(float(i) * 20,) * 5 for i in range(8)])
    with pytest.raises(MotionPlanError):
        schedule_for(points, seconds=2.0)


def test_every_axis_shares_one_ltraj_total():
    """The PCM's own files hold this invariant; a motion set whose MDs disagree
    would desynchronise the axes."""
    points = waypoints((5.0, 1.0, -30.0, 20.0, 3.0), (-5.0, 2.0, -60.0, 40.0, -3.0))
    text = render_motion_csv(schedule_for(points), ms_id=4, ms_name="T", axes=AXES)
    rows = list(csv.reader(io.StringIO(text)))

    totals = set()
    for row in rows[4:]:
        cells = [cell for cell in row[3:23] if cell and cell != "-"]
        if cells:
            totals.add(sum(float(cell.split(",")[1]) for cell in cells))
    assert totals == {28_000.0}


def test_rendered_csv_matches_the_shape_on_the_sd_card():
    points = waypoints((5.0, 1.0, -30.0, 20.0, 3.0), (-5.0, 2.0, -60.0, 40.0, -3.0))
    text = render_motion_csv(schedule_for(points), ms_id=4, ms_name="PHOTO", axes=AXES)
    rows = list(csv.reader(io.StringIO(text)))

    assert "\r" not in text
    assert len(rows) == 16  # 4 header rows + MD0..MD11
    assert {len(row) for row in rows} == {23}
    assert rows[0][:2] == ["robot_id", "1"]
    assert rows[1][:2] == ["file_version", "3.0.0"]
    assert rows[3][3:] == [str(i) for i in range(P_VECTOR_COLUMNS)]
    assert [row[2] for row in rows[4:]] == [f"MD{i}" for i in range(12)]
    # MD index is the feedback axis index; everything else is a bare dash.
    assert all(row[3] == "-" for row in rows[4:] if int(row[2][2:]) not in AXES)
    assert all(row[3] != "-" for row in rows[4:] if int(row[2][2:]) in AXES)


def test_render_refuses_more_pvectors_than_the_slot_holds():
    points = waypoints(*[(float(i),) * 5 for i in range(12)])
    with pytest.raises(MotionPlanError):
        schedule_for(points, seconds=120.0)


def sweep_plan(window_count: int = 4, width: float = 2.0) -> SweepPlan:
    windows = [(f"wp{i + 1}", 1.0 + i * 4.0, 1.0 + i * 4.0 + width) for i in range(window_count)]
    return SweepPlan(slot=4, name="T", total_seconds=windows[-1][2] + 2.0, windows=windows)


def test_every_pause_gets_one_guaranteed_shot():
    plan = sweep_plan()
    cues = plan_shot_times(plan, target_count=40)
    guaranteed = [cue for cue in cues if cue.waypoint]
    assert [cue.waypoint for cue in guaranteed] == [label for label, _, _ in plan.windows]
    for cue, (_, start, end) in zip(guaranteed, plan.windows):
        assert cue.at_seconds == pytest.approx((start + end) / 2)


def test_shots_never_exceed_the_rate_limit_even_across_pauses():
    plan = sweep_plan(window_count=6, width=1.2)
    cues = plan_shot_times(plan, target_count=60, max_rate_hz=2.0)
    gaps = [b.at_seconds - a.at_seconds for a, b in zip(cues, cues[1:])]
    assert min(gaps) >= 0.5 - 1e-9


def test_shot_count_stays_within_budget():
    plan = sweep_plan(window_count=6)
    assert len(plan_shot_times(plan, target_count=20)) <= 20


def test_generated_slots_on_disk_stay_playable():
    """The committed sweeps must survive `phorce play`'s 30 s default wait."""
    slots = Path(__file__).resolve().parents[1] / "calibration" / "slots"
    schedules = sorted(slots.glob("motion_*.schedule.json"))
    if not schedules:
        pytest.skip("생성된 슬롯이 없습니다 — scripts/build_motion_slots.py 먼저 실행")
    for schedule in schedules:
        plan = SweepPlan.from_schedule(json.loads(schedule.read_text()))
        assert plan.total_seconds <= 29.0, f"{schedule.name}: {plan.total_seconds}s"
        assert plan.windows
        for _, start, end in plan.windows:
            assert 0.0 < start < end < plan.total_seconds


def test_shot_windows_line_up_with_the_pause_segments():
    points = waypoints((5.0,) * 5, (-20.0,) * 5, (10.0,) * 5)
    segments = schedule_for(points)
    windows = shot_times_seconds(segments)
    assert len(windows) == len(points)
    for (_, start, end), segment in zip(windows, [s for s in segments if s.is_shot]):
        assert end - start == pytest.approx(segment.ticks / 1000)


class StubSweepRobot:
    """Stands in for PhorceRobot: records the pose and fires every cue."""

    def __init__(self, plan: SweepPlan) -> None:
        self.plan = plan
        self.moves: list[str] = []
        self.cues: list = []

    async def move_to(self, pose: str) -> None:  # pragma: no cover - not used
        raise AssertionError("sweep robot should not fall back to move_to")

    async def sweep(self, pose: str, shoot, target_count: int = 40) -> None:
        self.moves.append(pose)
        for cue in plan_shot_times(self.plan, target_count=target_count):
            self.cues.append(cue)
            await shoot(cue)


class CoordinatorSweepTests(unittest.IsolatedAsyncioTestCase):
    async def test_sweeps_instead_of_moving_pose_by_pose(self) -> None:
        robot = StubSweepRobot(sweep_plan())
        coordinator = Coordinator(
            robot=robot,
            capture=FakeCapture(0),
            greeting_seconds=0.05,
            countdown_seconds=0,
            preview_seconds=0.05,
            farewell_seconds=0.05,
            photo_target_count=12,
        )
        await coordinator.start()
        try:
            await coordinator.emit(EventType.PERSON_APPROACHED)
            await coordinator.wait_for_state(State.DECIDING)
            await coordinator.emit(EventType.CAPTURE_STARTED, template_id="upper_body")
            await coordinator.wait_for_state(State.GUIDING)
            await coordinator.emit(EventType.POSITION_REACHED)
            await coordinator.wait_for_state(State.PREVIEWING, timeout=5.0)

            # One slot covers the whole burst, so the arm is addressed once.
            self.assertEqual(robot.moves, ["frame.upper_body"])
            self.assertEqual(len(coordinator.context.photos), 12)
            self.assertEqual(
                sum(1 for cue in robot.cues if cue.waypoint), len(robot.plan.windows)
            )
        finally:
            await coordinator.stop()

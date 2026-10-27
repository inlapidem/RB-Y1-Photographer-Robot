"""PhorceRobot against a stand-in for the phorce SDK.

The real SDK needs the two-terminal ROS stack and a powered arm, so the launch
rules that matter most — never shoot at an arm that rejected the goal, never
send while the previous motion is still settling — are the ones that are
hardest to rehearse. This fake reproduces the SDK's actual contract (rejection
sets the result immediately; acceptance leaves it pending; status has to be
fresh *and* contract-active before it means anything) so those rules can be.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory


class FakePhorceError(Exception):
    pass


class FakeMotionBusy(FakePhorceError):
    pass


class FakeMotionRejected(FakePhorceError):
    pass


@dataclass
class FakeStatus:
    """실제 Status의 판정 필드를 그대로 흉내낸다. state_name은 파생값이지
    독립 필드가 아니다 — 그걸 필드로 두면 '완료 후 COMPLETED에 머문다'는
    실제 동작을 테스트가 재현하지 못한다."""

    contract_active: bool = True
    age_ms: int = 5
    boot_id: int = 7
    primary_state: str = "IDLE"   # IDLE | EXECUTING | COMPLETED | FAILED | UNKNOWN
    active: bool = False
    queue_count: int = 0
    physical_idle: bool = True
    recovery_required: bool = False
    active_motion_id: int = 0

    @property
    def is_fresh(self) -> bool:
        return self.age_ms < 500

    @property
    def state_name(self) -> str:
        if not self.contract_active:
            return "CONTRACT_INACTIVE"
        if not self.is_fresh:
            return "STALE"
        if self.boot_id == 0:
            return "UNKNOWN"
        if self.primary_state == "IDLE":
            if not self.active and self.queue_count == 0 and self.physical_idle:
                return "IDLE"
            return "RECOVERY_REQUIRED" if self.recovery_required else "UNKNOWN"
        return self.primary_state


@dataclass
class FakeResult:
    ok: bool = True
    status_name: str = "SUCCEEDED"
    detail: str = ""
    physical_idle: bool = True
    recovery_required: bool = False


@dataclass
class FakeDoctor:
    ok: bool = True
    action_server_identities: list[str] = field(default_factory=lambda: ["/motion_action_server"])
    issues: list[str] = field(default_factory=list)

    @property
    def duplicate_action_server(self) -> bool:
        return len(self.action_server_identities) > 1


@dataclass
class FakeMotion:
    id: int


class FakeHandle:
    def __init__(self, robot: "FakeRobotClient", slot: int) -> None:
        self._robot = robot
        self.slot = slot
        self.cancelled = False
        # Each goal carries its own outcome, decided when it is sent — the SDK
        # sets a rejected goal's result immediately, while an accepted one
        # stays pending for the whole motion.
        self.rejection = robot.take_rejection()
        self.done = self.rejection is not None

    def wait(self, timeout: float | None = None) -> FakeResult:
        if self.rejection is not None:
            raise self.rejection
        return self._robot.result

    def cancel(self) -> None:
        self.cancelled = True


class FakeMotions:
    def __init__(self, robot: "FakeRobotClient") -> None:
        self._robot = robot

    def list(self, timeout: float = 5.0):
        return [FakeMotion(slot) for slot in self._robot.loaded_slots]


class FakeRobotClient:
    def __init__(self) -> None:
        self.doctor_report = FakeDoctor()
        self.loaded_slots = [1, 2, 3, 4, 5]
        self.statuses: list[FakeStatus] = []
        self.reject: Exception | None = None
        self.reject_times: int | None = None  # None = 계속 거절
        self.result = FakeResult()
        self.played: list[int] = []
        self.status_calls = 0
        self.closed = False
        self.motions = FakeMotions(self)

    def take_rejection(self) -> Exception | None:
        if self.reject is None:
            return None
        if self.reject_times is None:
            return self.reject
        if self.reject_times <= 0:
            return None
        self.reject_times -= 1
        return self.reject

    def doctor(self, timeout: float = 2.0) -> FakeDoctor:
        return self.doctor_report

    def status(self, timeout: float = 2.0) -> FakeStatus:
        self.status_calls += 1
        if self.statuses:
            return self.statuses.pop(0)
        return FakeStatus()

    def play_async(self, *ids, **kwargs) -> FakeHandle:
        slot = int(ids[0])
        self.played.append(slot)
        return FakeHandle(self, slot)

    def close(self) -> None:
        self.closed = True


def install_fake_phorce(client: FakeRobotClient) -> None:
    module = types.ModuleType("phorce")
    module.connect = lambda **kwargs: client
    module.PhorceError = FakePhorceError
    module.PhorceUnavailable = FakePhorceError
    module.MotionBusy = FakeMotionBusy
    module.MotionRejected = FakeMotionRejected
    sys.modules["phorce"] = module


SCHEDULE = {
    "slot": 4,
    "name": "PHOTO_FULLBODY",
    "total_seconds": 1.0,
    "shots": [
        {"label": "wp1", "window_start_s": 0.05, "window_end_s": 0.15},
        {"label": "wp2", "window_start_s": 0.30, "window_end_s": 0.40},
    ],
}


class PhorceRobotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = FakeRobotClient()
        install_fake_phorce(self.client)
        self._tmp = TemporaryDirectory()
        self.slot_dir = Path(self._tmp.name)
        (self.slot_dir / "motion_04.schedule.json").write_text(json.dumps(SCHEDULE))

    def tearDown(self) -> None:
        self._tmp.cleanup()
        sys.modules.pop("phorce", None)

    def make_robot(self, **kwargs):
        from geekseek.robot import PhorceRobot

        defaults = dict(
            motion_ids={"frame.full_body": 4},
            slot_dir=self.slot_dir,
            busy_retry_seconds=0,
            accept_grace_seconds=0.05,
            cooldown_seconds=0,
            idle_timeout_seconds=0.5,
            idle_poll_seconds=0.01,
        )
        defaults.update(kwargs)
        return PhorceRobot(**defaults)

    # ── 사전 검증 ──

    async def test_constructing_does_not_touch_the_robot(self):
        """The kiosk web server must come up even with the stack down."""
        robot = self.make_robot()
        self.assertEqual(self.client.played, [])
        self.assertEqual(self.client.status_calls, 0)
        self.assertIsNone(robot._robot)

    async def test_preflight_runs_doctor_and_catalog(self):
        robot = self.make_robot()
        await robot.preflight()
        self.assertIsNotNone(robot._robot)

    async def test_preflight_rejects_a_not_ready_doctor(self):
        self.client.doctor_report = FakeDoctor(ok=False, issues=["Action 서버 없음"])
        with self.assertRaises(RuntimeError) as caught:
            await self.make_robot().preflight()
        self.assertIn("Action 서버 없음", str(caught.exception))
        self.assertTrue(self.client.closed)

    async def test_preflight_rejects_two_action_servers(self):
        self.client.doctor_report = FakeDoctor(action_server_identities=["/a", "/b"])
        with self.assertRaises(RuntimeError) as caught:
            await self.make_robot().preflight()
        self.assertIn("둘 이상", str(caught.exception))

    async def test_preflight_rejects_a_slot_the_robot_has_not_loaded(self):
        """The catalog of record is the PCM's, not the Jetson's files."""
        self.client.loaded_slots = [1, 2, 3]
        with self.assertRaises(RuntimeError) as caught:
            await self.make_robot().preflight()
        self.assertIn("적재되지 않은 슬롯", str(caught.exception))

    # ── 발사 게이트 ──

    async def test_a_rejected_goal_fires_no_shots(self):
        self.client.reject = FakeMotionBusy("busy")
        robot = self.make_robot(busy_retries=1)
        shots: list = []

        with self.assertRaises(FakeMotionBusy):
            await robot.sweep("frame.full_body", lambda cue: _record(shots, cue))

        self.assertEqual(shots, [])
        self.assertEqual(len(self.client.played), 2)  # 최초 + 재시도 1회

    async def test_busy_clearing_between_retries_lets_the_sweep_run(self):
        """코드 5(BUSY)는 기다리면 풀리는 유일한 거절이다."""
        self.client.reject = FakeMotionBusy("busy")
        self.client.reject_times = 1  # 첫 발사만 거절
        robot = self.make_robot(busy_retries=2)
        shots: list = []

        await robot.sweep("frame.full_body", lambda cue: _record(shots, cue))

        self.assertEqual(len(self.client.played), 2)
        self.assertEqual(
            [cue.waypoint for cue in shots if cue.waypoint],
            [shot["label"] for shot in SCHEDULE["shots"]],
        )

    async def test_launch_waits_for_a_fresh_idle_reading(self):
        self.client.statuses = [
            FakeStatus(primary_state="EXECUTING", active=True, physical_idle=False, active_motion_id=4),
            FakeStatus(age_ms=9_999),  # stale — idle로 인정하면 안 된다
            FakeStatus(contract_active=False),
            FakeStatus(),
        ]
        robot = self.make_robot()
        await robot.move_to("frame.full_body")
        self.assertEqual(self.client.status_calls, 4)
        self.assertEqual(self.client.played, [4])

    async def test_stale_status_alone_never_counts_as_idle(self):
        self.client.statuses = [FakeStatus(age_ms=9_999)] * 50
        robot = self.make_robot()
        with self.assertRaises(RuntimeError) as caught:
            await robot.move_to("frame.full_body")
        self.assertIn("IDLE", str(caught.exception))
        self.assertEqual(self.client.played, [])

    async def test_a_completed_motion_still_counts_as_ready(self):
        """모션이 끝나면 게이트웨이는 IDLE이 아니라 COMPLETED에 머문다 —
        PlayResult.ok가 요구하는 상태가 바로 그것이다. 문자열 'IDLE'만
        통과시키면 첫 발사 뒤 모든 발사가 막힌다."""
        self.client.statuses = [FakeStatus(primary_state="COMPLETED", active_motion_id=5)] * 4
        robot = self.make_robot()
        await robot.move_to("frame.full_body")
        self.assertEqual(self.client.played, [4])

    async def test_not_ready_while_the_arm_is_still_settling(self):
        """physical_idle이 False면 아직 정착 중이다 — 큐가 없으니 지금 보내면 버려진다."""
        self.client.statuses = [FakeStatus(primary_state="COMPLETED", physical_idle=False)] * 50
        robot = self.make_robot()
        with self.assertRaises(RuntimeError) as caught:
            await robot.move_to("frame.full_body")
        self.assertIn("physical_idle=False", str(caught.exception))
        self.assertEqual(self.client.played, [])

    async def test_recovery_required_is_not_retried(self):
        """12/13은 기다려도 풀리지 않는다 — 사람이 버튼을 눌러야 한다."""
        self.client.statuses = [FakeStatus(primary_state="COMPLETED", recovery_required=True)]
        robot = self.make_robot()
        with self.assertRaises(RuntimeError) as caught:
            await robot.move_to("frame.full_body")
        self.assertIn("RECOVERY_REQUIRED", str(caught.exception))
        self.assertEqual(self.client.played, [])

    # ── 완료 판정 ──

    async def test_a_cancelled_motion_is_not_reported_as_success(self):
        self.client.result = FakeResult(ok=False, status_name="CANCELED", detail="사용자 취소")
        robot = self.make_robot()
        with self.assertRaises(RuntimeError) as caught:
            await robot.move_to("frame.full_body")
        self.assertIn("CANCELED", str(caught.exception))

    async def test_a_sweep_whose_shots_all_fail_is_an_error(self):
        robot = self.make_robot()

        async def broken(cue):
            raise RuntimeError("no phone connected")

        with self.assertRaises(RuntimeError) as caught:
            await robot.sweep("frame.full_body", broken)
        self.assertIn("모두 실패", str(caught.exception))

    # ── 연속 운전 ──

    async def test_consecutive_sweeps_each_wait_for_idle_and_rest(self):
        robot = self.make_robot(cooldown_seconds=0.2)
        shots: list = []

        loop = asyncio.get_running_loop()
        start = loop.time()
        for _ in range(3):
            await robot.sweep("frame.full_body", lambda cue: _record(shots, cue))
        elapsed = loop.time() - start

        self.assertEqual(self.client.played, [4, 4, 4])
        self.assertEqual(len(shots), 3 * len(SCHEDULE["shots"]))
        # 첫 재생 앞에는 쉼이 없고, 이후 두 번 앞에만 붙는다.
        self.assertGreaterEqual(elapsed, 2 * 0.2)

    async def test_every_waypoint_still_gets_its_guaranteed_shot(self):
        robot = self.make_robot()
        shots: list = []
        await robot.sweep("frame.full_body", lambda cue: _record(shots, cue))
        self.assertEqual(
            [cue.waypoint for cue in shots if cue.waypoint],
            [shot["label"] for shot in SCHEDULE["shots"]],
        )

    async def test_unknown_pose_never_reaches_the_robot(self):
        robot = self.make_robot()
        with self.assertRaises(RuntimeError):
            await robot.move_to("frame.product_closeup")
        self.assertEqual(self.client.played, [])


async def _record(shots: list, cue) -> None:
    shots.append(cue)

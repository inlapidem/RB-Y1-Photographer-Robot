from __future__ import annotations

import asyncio
import json
import math
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .motion_plan import ShotCue, SweepPlan, load_sweep_plan, plan_shot_times
from .rby1_keyposes import SCHEMA as RBY1_KEYPOSE_SCHEMA, compile_keyposes

ShotCallback = Callable[[ShotCue], Awaitable[None]]


class Robot(Protocol):
    async def move_to(self, pose: str) -> None: ...


class SweepingRobot(Robot, Protocol):
    """A robot that can play one long motion while the shutter fires on a
    schedule, instead of the move-then-shoot-once loop."""

    async def sweep(self, pose: str, shoot: ShotCallback, target_count: int = 40) -> None: ...


class FakeRobot:
    def __init__(self, move_seconds: float = 0.2) -> None:
        self.move_seconds = move_seconds
        self.moves: list[str] = []

    async def move_to(self, pose: str) -> None:
        self.moves.append(pose)
        await asyncio.sleep(self.move_seconds)


class RvizRobot(FakeRobot):
    """ROS boundary that keeps a single rclpy node alive for the whole
    session, instead of shelling out to a brand-new `ros2 topic echo`/`pub`
    process on every single move.

    That per-move subprocess approach needed a full DDS discovery handshake
    (a cold-start participant finding the fake_robot_node's publisher/
    subscriber) to finish inside a few seconds, every single time — and
    /geekseek/fake_robot/target|status only carry a couple of messages per
    move, so there's nothing keeping discovery "warm" between calls the way
    a continuous topic like /joint_states does. Measured directly against
    phorce: repeated one-shot `ros2 topic info` queries against that same
    topic failed most of the time even though the node was healthy the whole
    time, which is exactly the "did not complete" failure this class used to
    raise intermittently. A persistent node/publisher/subscription discovers
    the graph exactly once, at startup, and reuses that connection for every
    move — so there is no repeated discovery race left to lose.
    """

    _ROS_SITE_PACKAGES = (
        f"/opt/ros/humble/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages",
        f"/opt/ros/humble/local/lib/python{sys.version_info.major}.{sys.version_info.minor}/dist-packages",
    )

    def __init__(
        self,
        move_seconds: float = 1.2,
        node_name: str = "geekseek_robot_bridge",
        min_status_timeout: float = 5.0,
    ) -> None:
        super().__init__(move_seconds)
        self.min_status_timeout = min_status_timeout
        self._bootstrap_ros_sys_path()

        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from std_msgs.msg import String

        self._String = String
        self._rclpy = rclpy
        if not rclpy.ok():
            rclpy.init()
        self._node = rclpy.create_node(node_name)
        self._target_publisher = self._node.create_publisher(String, "/geekseek/fake_robot/target", 10)
        self._status_subscription = self._node.create_subscription(
            String, "/geekseek/fake_robot/status", self._on_status, 10
        )

        self._awaited_pose: str | None = None
        self._completed_event: asyncio.Event | None = None
        self._completed_loop: asyncio.AbstractEventLoop | None = None

        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._spin_thread.start()
        self._wait_for_discovery()

    def _wait_for_discovery(self, timeout_seconds: float = 5.0, poll_seconds: float = 0.05) -> None:
        """Blocks (briefly, once, at startup — not on the asyncio loop yet)
        until fake_robot_node's target subscriber and status publisher have
        actually been found over DDS. Without this, the very first move_to()
        call publishes/listens before discovery finishes and times out even
        though every later call (once discovery has caught up) works fine —
        this trades that one guaranteed-visible stall for a startup delay
        nobody's watching."""
        import time

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if (
                self._target_publisher.get_subscription_count() >= 1
                and self._node.count_publishers("/geekseek/fake_robot/status") >= 1
            ):
                return
            time.sleep(poll_seconds)

    @classmethod
    def _bootstrap_ros_sys_path(cls) -> None:
        """Make `import rclpy` work even if the launching shell never
        sourced /opt/ros/humble/setup.bash — mutates this process's own
        sys.path since (unlike the old subprocess approach) there is no
        child process whose env we can patch instead."""
        for path in cls._ROS_SITE_PACKAGES:
            if path not in sys.path:
                sys.path.append(path)

    def _on_status(self, message: object) -> None:
        """Runs on the executor's spin thread, not the asyncio loop — only
        touch thread-safe primitives here."""
        if self._awaited_pose is None or message.data != f"completed:{self._awaited_pose}":  # type: ignore[attr-defined]
            return
        event, loop = self._completed_event, self._completed_loop
        if event is not None and loop is not None:
            loop.call_soon_threadsafe(event.set)

    async def move_to(self, pose: str) -> None:
        self.moves.append(pose)
        self._completed_event = asyncio.Event()
        self._completed_loop = asyncio.get_running_loop()
        self._awaited_pose = pose
        try:
            self._target_publisher.publish(self._String(data=pose))
            try:
                await asyncio.wait_for(
                    self._completed_event.wait(), timeout=max(self.min_status_timeout, self.move_seconds + 3.0)
                )
            except asyncio.TimeoutError:
                raise RuntimeError(f"RViz fake robot did not complete {pose}")
        finally:
            self._awaited_pose = None

    def close(self) -> None:
        self._executor.shutdown()
        self._node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()
        self._spin_thread.join(timeout=1.0)


@dataclass(frozen=True)
class Rby1Segment:
    """One right-arm target in an RB-Y1 camera sweep.

    The values are deliberately RB-Y1 joint angles in radians, rather than a
    conversion of the old Phorce values.  The two arms have different
    kinematics, so copying Phorce joint values would not preserve the camera
    path (and could violate a joint limit).
    """

    kind: str
    duration_s: float
    right_arm_rad: tuple[float, ...] | None = None
    head_rad: tuple[float, float] | None = None
    ee_right_transform: tuple[tuple[float, ...], ...] | None = None
    label: str = ""
    shot_ratios: tuple[float, ...] = ()


@dataclass(frozen=True)
class Rby1Trajectory:
    name: str
    segments: tuple[Rby1Segment, ...]

    def sweep_plan(self) -> SweepPlan:
        elapsed = 0.0
        windows: list[tuple[str, float, float]] = []
        for index, segment in enumerate(self.segments, start=1):
            end = elapsed + segment.duration_s
            if segment.kind == "dwell":
                windows.append((segment.label or f"wp{index}", elapsed, end))
            elapsed = end
        return SweepPlan(slot=0, name=self.name, total_seconds=elapsed, windows=windows)


def load_rby1_trajectory(path: Path, target_count: int | None = None) -> Rby1Trajectory:
    """Load a deliberately small, reviewable RB-Y1 right-arm trajectory.

    ``scripts/build_rby1_trajectory.py`` creates this file from the original
    photo-sweep timing.  A calibrated 7-DoF target must be provided for every
    segment before the file can be executed on hardware.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") == RBY1_KEYPOSE_SCHEMA:
            raw = compile_keyposes(raw, target_count)
        segments = tuple(
            Rby1Segment(
                kind=str(item["kind"]),
                duration_s=float(item["duration_s"]),
                right_arm_rad=(
                    tuple(float(value) for value in item["right_arm_rad"])
                    if "right_arm_rad" in item
                    else None
                ),
                head_rad=(
                    tuple(float(value) for value in item["head_rad"])
                    if "head_rad" in item
                    else None
                ),
                ee_right_transform=(
                    tuple(tuple(float(value) for value in row) for row in item["ee_right_transform"])
                    if "ee_right_transform" in item
                    else None
                ),
                label=str(item.get("label", "")),
                shot_ratios=tuple(float(value) for value in item.get("shot_ratios", ())),
            )
            for item in raw["segments"]
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"RB-Y1 trajectory를 읽을 수 없습니다: {path}: {exc}") from exc

    if not segments:
        raise RuntimeError(f"RB-Y1 trajectory에 구간이 없습니다: {path}")
    for index, segment in enumerate(segments, start=1):
        if segment.kind not in {"entry", "travel", "dwell", "home"}:
            raise RuntimeError(f"RB-Y1 trajectory {path}의 {index}번 구간 kind가 올바르지 않습니다")
        if segment.duration_s <= 0:
            raise RuntimeError(f"RB-Y1 trajectory {path}의 {index}번 구간 시간이 0 이하여서는 안 됩니다")
        if (segment.right_arm_rad is None) == (segment.ee_right_transform is None):
            raise RuntimeError(
                f"RB-Y1 trajectory {path}의 {index}번 구간에는 right_arm_rad 또는 "
                "ee_right_transform 중 하나만 필요합니다"
            )
        if segment.right_arm_rad is not None and len(segment.right_arm_rad) != 7:
            raise RuntimeError(f"RB-Y1 trajectory {path}의 {index}번 구간은 오른팔 7축 값이 필요합니다")
        if segment.head_rad is not None and len(segment.head_rad) != 2:
            raise RuntimeError(f"RB-Y1 trajectory {path}의 {index}번 구간은 목 2축 값이 필요합니다")
        if segment.ee_right_transform is not None and (
            len(segment.ee_right_transform) != 4 or any(len(row) != 4 for row in segment.ee_right_transform)
        ):
            raise RuntimeError(f"RB-Y1 trajectory {path}의 {index}번 구간은 4x4 ee_right_transform이 필요합니다")
        if any(not 0.0 < ratio < 1.0 for ratio in segment.shot_ratios):
            raise RuntimeError(f"RB-Y1 trajectory {path}의 {index}번 이동 촬영 비율은 0과 1 사이여야 합니다")
        if tuple(sorted(segment.shot_ratios)) != segment.shot_ratios:
            raise RuntimeError(f"RB-Y1 trajectory {path}의 {index}번 이동 촬영 비율은 오름차순이어야 합니다")
    return Rby1Trajectory(name=str(raw.get("name", path.stem)), segments=segments)


class Rby1Robot:
    """RB-Y1 SDK adapter for GeekSeek's precomputed photo sweeps.

    This uses RB-Y1's non-realtime right-arm joint-position command stream.
    It intentionally refuses to power on or enable servos itself: those are
    physical, operator-confirmed steps in the RB-Y1 Web UI / bring-up flow.
    """

    def __init__(
        self,
        trajectories: dict[str, Path],
        address: str,
        model: str = "a",
        command_priority: int = 1,
        speed_ratio: float | None = None,
        acceleration_ratio: float = 0.5,
        min_travel_seconds: float = 0.25,
        moving_shot_interval: float = 0.35,
    ) -> None:
        if speed_ratio is not None and not 0 < speed_ratio <= 0.70:
            raise ValueError("rby1_speed_ratio는 0보다 크고 0.70 이하여야 합니다")
        if not 0 < acceleration_ratio <= 1.0:
            raise ValueError("rby1_acceleration_ratio는 0보다 크고 1 이하여야 합니다")
        if min_travel_seconds <= 0:
            raise ValueError("rby1_min_travel_seconds는 0보다 커야 합니다")
        if moving_shot_interval <= 0:
            raise ValueError("rby1_moving_shot_interval은 0보다 커야 합니다")
        self.trajectories = {name: Path(path) for name, path in trajectories.items()}
        self.address = address
        self.model = model
        self.command_priority = command_priority
        self.speed_ratio = speed_ratio
        self.acceleration_ratio = acceleration_ratio
        self.min_travel_seconds = min_travel_seconds
        self.moving_shot_interval = moving_shot_interval
        self.moves: list[str] = []
        self._sdk: object | None = None
        self._robot: object | None = None
        self._connect_lock = asyncio.Lock()
        self._joint_velocity_limits: tuple[float, ...] | None = None
        self._joint_acceleration_limits: tuple[float, ...] | None = None

    @staticmethod
    def _minimum_motion_time(
        start: tuple[float, ...],
        target: tuple[float, ...],
        velocity_limits: tuple[float, ...],
        acceleration_limits: tuple[float, ...],
    ) -> float:
        """Minimum synchronized time under per-axis trapezoidal limits."""
        durations = []
        for current, goal, velocity, acceleration in zip(
            start, target, velocity_limits, acceleration_limits
        ):
            distance = abs(goal - current)
            ramp_distance = velocity * velocity / acceleration
            if distance <= ramp_distance:
                durations.append(2.0 * math.sqrt(distance / acceleration))
            else:
                durations.append(distance / velocity + velocity / acceleration)
        return max(durations, default=0.0)

    def _retime_trajectory(self, trajectory: Rby1Trajectory) -> Rby1Trajectory:
        if (
            self._robot is None
            or self._joint_velocity_limits is None
            or self._joint_acceleration_limits is None
        ):
            return trajectory
        model = self._robot.model()
        state = self._robot.get_state()
        previous = tuple(float(state.position[index]) for index in model.right_arm_idx)
        output: list[Rby1Segment] = []
        for segment in trajectory.segments:
            target = segment.right_arm_rad
            if target is None or segment.kind == "dwell":
                output.append(segment)
            else:
                duration = self._minimum_motion_time(
                    previous,
                    target,
                    self._joint_velocity_limits,
                    self._joint_acceleration_limits,
                )
                # Small margin absorbs controller/RPC scheduling variation so
                # dwell windows remain aligned with actual arrival.
                capture_floor = (
                    (len(segment.shot_ratios) + 1) * self.moving_shot_interval
                    if segment.shot_ratios
                    else 0.0
                )
                output.append(
                    replace(
                        segment,
                        duration_s=max(self.min_travel_seconds, duration * 1.10, capture_floor),
                    )
                )
            if target is not None:
                previous = target
        return Rby1Trajectory(name=trajectory.name, segments=tuple(output))

    def trajectory_for(self, pose: str, target_count: int | None = None) -> Rby1Trajectory:
        try:
            path = self.trajectories[pose]
        except KeyError as exc:
            raise RuntimeError(f"{pose}에 대응하는 RB-Y1 trajectory가 없습니다") from exc
        try:
            schema = json.loads(path.read_text(encoding="utf-8")).get("schema")
        except (OSError, AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"RB-Y1 keypose 파일을 읽을 수 없습니다: {path}: {exc}") from exc
        if schema != RBY1_KEYPOSE_SCHEMA:
            raise RuntimeError(
                f"RB-Y1 실기에서는 연속 녹화 trajectory를 실행하지 않습니다: {path}. "
                "휴대폰을 그리퍼로 고정한 뒤 record_rby1_right_arm.py로 11개 keypose를 다시 기록하세요"
            )
        return self._retime_trajectory(load_rby1_trajectory(path, target_count))

    def _connect_and_verify(self) -> None:
        try:
            import rby1_sdk as rby
        except ImportError as exc:
            raise RuntimeError("RB-Y1 실행에는 `pip install -e '.[rby1]'`가 필요합니다") from exc
        robot = rby.create_robot(self.address, self.model)
        if not robot.connect():
            raise RuntimeError(f"RB-Y1에 연결하지 못했습니다: {self.address}")
        state = robot.get_control_manager_state().state
        enabled = rby.ControlManagerState.State.Enabled
        if state != enabled:
            raise RuntimeError(
                "RB-Y1 Control Manager가 ENABLE 상태가 아닙니다. "
                "Web UI에서 주변 안전을 확인한 뒤 전원·서보·Control Manager를 준비하세요."
            )
        if self.speed_ratio is not None:
            model = robot.model()
            dynamics = robot.get_dynamics()
            dynamics_state = dynamics.make_state([], model.robot_joint_names)
            self._joint_velocity_limits = tuple(
                float(value) * self.speed_ratio
                for value in dynamics.get_limit_qdot_upper(dynamics_state)[model.right_arm_idx]
            )
            self._joint_acceleration_limits = tuple(
                float(value) * self.acceleration_ratio
                for value in dynamics.get_limit_qddot_upper(dynamics_state)[model.right_arm_idx]
            )
        self._sdk = rby
        self._robot = robot

    async def preflight(self) -> None:
        async with self._connect_lock:
            if self._robot is None:
                await asyncio.to_thread(self._connect_and_verify)

    async def move_home(self) -> None:
        """Move only to the taught base pose used by every photo sweep.

        This deliberately sends the first ``start-home`` segment and nothing
        else, so server startup cannot accidentally enter a photo waypoint.
        The segment is retimed from the robot's live joint state and includes
        the calibrated head angle when one was recorded.
        """
        await self.preflight()
        if not self.trajectories:
            raise RuntimeError("RB-Y1 base pose를 읽을 trajectory가 없습니다")
        pose = next(iter(self.trajectories))
        trajectory = self.trajectory_for(pose)
        home = trajectory.segments[0]
        if home.kind != "home" or home.label != "start-home":
            raise RuntimeError(f"RB-Y1 trajectory {trajectory.name}이 start-home으로 시작하지 않습니다")
        self.moves.append("home")
        await asyncio.to_thread(self._send_segment, home)

    def _send_segment(self, segment: Rby1Segment) -> None:
        if self._robot is None or self._sdk is None:
            raise RuntimeError("RB-Y1이 연결되지 않았습니다")
        rby = self._sdk
        if segment.ee_right_transform is not None:
            # Cartesian positions are recorded in the robot's `base` frame
            # through SDK FK while the torso/base remain fixed during teleop.
            # Conservative limits keep replay below the generic SDK examples.
            arm_command = (
                rby.CartesianCommandBuilder()
                .add_target(
                    "base",
                    "ee_right",
                    [list(row) for row in segment.ee_right_transform],
                    0.20,
                    0.50,
                    0.30,
                )
                .set_minimum_time(segment.duration_s)
                .set_stop_position_tracking_error(0.003)
                .set_stop_orientation_tracking_error(0.03)
            )
        else:
            arm_command = (
                rby.JointPositionCommandBuilder()
                .set_minimum_time(segment.duration_s)
                .set_position(list(segment.right_arm_rad or ()))
            )
            if self._joint_velocity_limits is not None:
                arm_command.set_velocity_limit(list(self._joint_velocity_limits))
            if self._joint_acceleration_limits is not None:
                arm_command.set_acceleration_limit(list(self._joint_acceleration_limits))
        component = rby.ComponentBasedCommandBuilder().set_body_command(
            rby.BodyComponentBasedCommandBuilder().set_right_arm_command(arm_command)
        )
        if segment.head_rad is not None:
            component.set_head_command(
                rby.JointPositionCommandBuilder()
                .set_minimum_time(segment.duration_s)
                .set_position(list(segment.head_rad))
            )
        command = rby.RobotCommandBuilder().set_command(component)
        feedback = self._robot.send_command(command, self.command_priority).get()
        if feedback.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
            raise RuntimeError(f"RB-Y1 {segment.label or segment.kind} 실행 실패: {feedback.finish_code}")

    async def _execute(self, trajectory: Rby1Trajectory) -> None:
        # Compiled keyposes are sparse, intentional moves and capture dwells.
        # Waiting for each SDK result makes arrival and the final base return
        # explicit; canceling a command stream can race with the next command
        # at the same priority and leave the arm at the last photo pose.
        for segment in trajectory.segments:
            await asyncio.to_thread(self._send_segment, segment)

    async def move_to(self, pose: str) -> None:
        await self.preflight()
        self.moves.append(pose)
        await self._execute(self.trajectory_for(pose))

    async def sweep(self, pose: str, shoot: ShotCallback, target_count: int = 40) -> None:
        await self.preflight()
        trajectory = self.trajectory_for(pose, target_count)
        capture_count = sum(segment.kind == "dwell" for segment in trajectory.segments) + sum(
            len(segment.shot_ratios) for segment in trajectory.segments
        )
        if capture_count != target_count:
            raise RuntimeError(
                f"RB-Y1 trajectory {trajectory.name}의 촬영 수가 다릅니다: "
                f"{capture_count} != {target_count}"
            )
        self.moves.append(pose)
        started_at = time.monotonic()
        pending_shot: asyncio.Task[None] | None = None

        async def trigger_shot(cue: ShotCue) -> None:
            """Keep phone captures serial without holding the arm at a pose."""
            nonlocal pending_shot
            if pending_shot is not None:
                await pending_shot
            pending_shot = asyncio.create_task(shoot(cue))

        try:
            # Fire from the actual completed command sequence instead of a
            # wall-clock schedule.  At high speed, accumulated RPC latency can
            # otherwise move later shots outside their stopped dwell windows.
            for segment in trajectory.segments:
                segment_started = time.monotonic()
                execution = asyncio.create_task(asyncio.to_thread(self._send_segment, segment))
                if segment.kind == "dwell":
                    await trigger_shot(
                        ShotCue(
                            at_seconds=time.monotonic() - started_at,
                            waypoint=segment.label or None,
                        )
                    )
                for shot_index, ratio in enumerate(segment.shot_ratios, start=1):
                    delay = segment_started + segment.duration_s * ratio - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await trigger_shot(
                        ShotCue(
                            at_seconds=time.monotonic() - started_at,
                            waypoint=f"{segment.label}:moving-{shot_index}",
                        )
                    )
                await execution
            if pending_shot is not None:
                await pending_shot
        except Exception:
            # Canceling the await does not imply a physical stop.  Ask the SDK
            # to cancel its current control stream, then surface the failure.
            if self._robot is not None:
                await asyncio.to_thread(self._robot.cancel_control)
            if "execution" in locals():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
            if pending_shot is not None:
                pending_shot.cancel()
                await asyncio.gather(pending_shot, return_exceptions=True)
            raise

    def close(self) -> None:
        if self._robot is not None and hasattr(self._robot, "disconnect"):
            self._robot.disconnect()
        self._robot = None
        self._sdk = None


class PhorceRobot:
    """Real arm, driven the only way the hackathon SDK allows: `play(slot)` on
    a motion pre-loaded onto the PCM's SD card.

    There is no way to steer the arm mid-motion and no progress signal worth
    keying off — the action's `pvector_index` is transfer progress, not
    playback progress, and its own docstring says so. What we do have is an
    exact schedule: `scripts/build_motion_slots.py` chose every Ltraj itself, so
    the pause windows are known to the millisecond before playback starts. So
    the sweep fires the shutter off a clock started when the goal is accepted,
    and the arm's guaranteed dead-stop at each waypoint is what keeps those
    shots sharp.
    """

    def __init__(
        self,
        motion_ids: dict[str, int],
        slot_dir: Path,
        timeout_seconds: float = 45.0,
        busy_retries: int = 2,
        busy_retry_seconds: float = 1.0,
        target: str = "robot",
        connect_timeout: float = 10.0,
        idle_timeout_seconds: float = 15.0,
        idle_poll_seconds: float = 0.2,
        accept_grace_seconds: float = 1.5,
        late_shot_tolerance: float = 0.35,
        cooldown_seconds: float = 5.0,
    ) -> None:
        self.motion_ids = motion_ids
        self.slot_dir = Path(slot_dir)
        self.timeout_seconds = timeout_seconds
        self.busy_retries = busy_retries
        self.busy_retry_seconds = busy_retry_seconds
        self.target = target
        self.connect_timeout = connect_timeout
        self.idle_timeout_seconds = idle_timeout_seconds
        self.idle_poll_seconds = idle_poll_seconds
        self.accept_grace_seconds = accept_grace_seconds
        self.late_shot_tolerance = late_shot_tolerance
        self.cooldown_seconds = cooldown_seconds
        self.moves: list[str] = []
        RvizRobot._bootstrap_ros_sys_path()

        import phorce

        self._phorce = phorce
        # Connecting is deferred: phorce.connect() blocks and then raises when
        # the two-terminal stack isn't up yet, and the manual lists "started
        # the stack before powering the robot" as a routine mistake. The kiosk
        # web server has to come up regardless so the operator can see why.
        self._robot: object | None = None
        self._connect_lock = asyncio.Lock()
        self._finished_at: float | None = None

    # ── 연결과 사전 검증 ──

    def _connect_and_verify(self):
        """doctor → list → 슬롯 적재 확인. 전부 동기, to_thread에서 부른다."""
        robot = self._phorce.connect(target=self.target, timeout=self.connect_timeout)
        try:
            report = robot.doctor()
            if report.duplicate_action_server:
                raise RuntimeError(
                    "모션 액션 서버가 둘 이상 떠 있습니다 — 어느 쪽이 명령을 받을지 "
                    f"보장할 수 없습니다: {report.action_server_identities}"
                )
            if not report.ok:
                raise RuntimeError("phorce doctor NOT READY: " + "; ".join(report.issues))

            # 정본은 로봇이 적재한 슬롯이지 Jetson의 파일이 아니다 (매뉴얼 §8).
            loaded = {motion.id for motion in robot.motions.list()}
            required = {int(slot) for slot in self.motion_ids.values()}
            missing = sorted(required - loaded)
            if missing:
                raise RuntimeError(
                    f"로봇에 적재되지 않은 슬롯입니다: {missing} "
                    f"(적재됨: {sorted(loaded)}). SD카드에 쓴 뒤 PCM 전원을 "
                    f"껐다 켜야 반영됩니다"
                )
        except Exception:
            robot.close()
            raise
        return robot

    async def preflight(self) -> None:
        """Connect and run every read-only check before anything can move."""
        async with self._connect_lock:
            if self._robot is None:
                self._robot = await asyncio.to_thread(self._connect_and_verify)

    def slot_for(self, pose: str) -> int:
        if pose not in self.motion_ids:
            raise RuntimeError(f"{pose}에 대응하는 phorce 슬롯이 설정에 없습니다")
        return int(self.motion_ids[pose])

    def sweep_plan(self, pose: str) -> SweepPlan:
        slot = self.slot_for(pose)
        schedule = self.slot_dir / f"motion_{slot:02d}.schedule.json"
        if not schedule.exists():
            raise RuntimeError(
                f"슬롯 {slot}의 스케줄 파일이 없습니다: {schedule}. "
                f"scripts/build_motion_slots.py를 먼저 실행하세요"
            )
        return load_sweep_plan(schedule)

    # ── 재생 ──

    async def move_to(self, pose: str) -> None:
        """Robot protocol: play the pose's slot and wait for it to finish."""
        self.moves.append(pose)
        handle, _ = await self._fire(self.slot_for(pose))
        await self._settle(handle)

    async def sweep(self, pose: str, shoot: ShotCallback, target_count: int = 40) -> None:
        """Play the pose's slot end to end, firing `shoot` on schedule."""
        plan = self.sweep_plan(pose)
        cues = plan_shot_times(plan, target_count=target_count)
        self.moves.append(pose)

        handle, fired_at = await self._fire(plan.slot)

        failures = 0
        for cue in cues:
            delay = cue.at_seconds - (time.monotonic() - fired_at)
            if delay > 0:
                await asyncio.sleep(delay)
            elif cue.waypoint is None and -delay > self.late_shot_tolerance:
                # A slow shutter round trip pushes every later cue into the
                # past. Firing them back to back would burst well past the
                # rate the phone bridge is proven at — and they would land
                # mid-travel anyway, which is not what they were scheduled
                # for. Waypoint shots are never dropped; the arm pauses there
                # long enough that a late one is still the intended framing.
                continue
            try:
                await shoot(cue)
            except Exception:
                # A dropped frame must not abandon the arm mid-sweep — the
                # motion cannot be stopped anyway, so keep the clock running
                # and let the remaining cues fire. A total failure is caught
                # below rather than silently returning an empty burst.
                failures += 1

        await self._settle(handle)
        if failures == len(cues):
            raise RuntimeError(f"{len(cues)}번 촬영을 모두 실패했습니다 — 폰 연결을 확인하세요")

    async def _fire(self, slot: int):
        """Wait for the arm to be genuinely idle, send the goal, and only
        return once the goal is known to have been accepted.

        The naive version — send, then treat "no result yet" as success — is
        wrong in the one case that matters: a rejected goal sets its result
        immediately, so a caller that never distinguishes acceptance from
        "still running" happily runs a whole shot schedule against an arm that
        never moved, and only learns about it when the burst is already over.
        """
        for attempt in range(self.busy_retries + 1):
            await self._wait_until_idle()
            handle = self._robot.play_async(slot)
            fired_at = time.monotonic()
            try:
                await self._confirm_accepted(handle)
            except self._phorce.MotionBusy:
                # Code 5 is the only reject the manual says to retry; 12/13
                # need a person at the robot and never clear on their own.
                if attempt == self.busy_retries:
                    raise
                await asyncio.sleep(self.busy_retry_seconds)
                continue
            return handle, fired_at
        raise RuntimeError("unreachable")

    async def _confirm_accepted(self, handle) -> None:
        """A goal that reaches a terminal state inside the grace window was
        rejected, not completed — no slot is short enough. Re-raising through
        wait() turns it into the specific MotionBusy/MotionRejected the caller
        needs to tell "retry" apart from "fetch a human"."""
        deadline = time.monotonic() + self.accept_grace_seconds
        while time.monotonic() < deadline:
            if handle.done:
                await asyncio.to_thread(handle.wait, 1.0)
                raise RuntimeError("모션이 시작되기도 전에 종료됐습니다")
            await asyncio.sleep(0.02)

    async def _settle(self, handle) -> None:
        """Wait out the motion and insist it really succeeded."""
        try:
            result = await asyncio.to_thread(handle.wait, self.timeout_seconds)
        except TimeoutError:
            handle.cancel()
            raise
        finally:
            self._finished_at = time.monotonic()
        if not result.ok:
            raise RuntimeError(
                f"모션이 정상 완료되지 않았습니다 ({result.status_name}, "
                f"physical_idle={result.physical_idle}, "
                f"recovery_required={result.recovery_required}): {result.detail}"
            )

    async def _wait_until_idle(self) -> None:
        """Gate every launch on a fresh reading that says the arm is resting.

        There is no queue: a goal sent while the previous motion is still
        settling is dropped, not deferred. And a stale or contract-inactive
        sample cannot stand in for idle — Status says so explicitly, so the
        freshness fields are checked before the resting ones.

        "Resting" is the flags, not `state_name`. A finished motion leaves the
        gateway in COMPLETED, not IDLE, and it stays there until the next
        request — `PlayResult.ok` requires exactly that state, so COMPLETED is
        what success looks like. Waiting for the literal string "IDLE" lets the
        first launch through and then blocks every one after it.
        """
        await self.preflight()

        # 과열 자동 차단이 없다 (매뉴얼 §10) — 연속 재생 사이에 쉬어 준다.
        if self._finished_at is not None:
            rest = self.cooldown_seconds - (time.monotonic() - self._finished_at)
            if rest > 0:
                await asyncio.sleep(rest)

        deadline = time.monotonic() + self.idle_timeout_seconds
        last = "상태 표본 없음"
        while time.monotonic() < deadline:
            try:
                status = await asyncio.to_thread(self._robot.status)
            except self._phorce.PhorceError as exc:
                last = str(exc)
            else:
                if not status.contract_active:
                    last = "CONTRACT_INACTIVE — 게이트웨이가 명령 계약을 열지 않았습니다"
                elif not status.is_fresh:
                    last = f"STALE — 상태가 {status.age_ms}ms 지났습니다"
                elif status.boot_id == 0:
                    last = "UNKNOWN — boot_id가 아직 0입니다"
                elif status.recovery_required:
                    raise RuntimeError(
                        "로봇이 RECOVERY_REQUIRED 상태입니다 — 2번 버튼으로 파킹한 뒤 "
                        "영점을 다시 잡아야 합니다 (기다려도 풀리지 않습니다)"
                    )
                elif status.physical_idle and not status.active and status.queue_count == 0:
                    return
                else:
                    last = (
                        f"{status.state_name} (physical_idle={status.physical_idle}, "
                        f"active={status.active}, queue={status.queue_count}, "
                        f"active_motion_id={status.active_motion_id})"
                    )
            await asyncio.sleep(self.idle_poll_seconds)
        raise RuntimeError(
            f"{self.idle_timeout_seconds:.1f}초 안에 로봇이 IDLE이 되지 않았습니다: {last}"
        )

    def close(self) -> None:
        if self._robot is not None:
            self._robot.close()
            self._robot = None


def pose_for_template(template_id: str) -> str:
    return {
        "full_body": "frame.full_body",
        "upper_body": "frame.upper_body",
        "product_closeup": "frame.product_closeup",
    }.get(template_id, "frame.full_body")


def burst_poses_for_template(template_id: str) -> list[str]:
    """5단계: 정위치에서 여러 구도로 팔을 움직이며 촬영. 고른 구도를 먼저 찍고
    나머지 두 구도를 이어서 찍는다."""
    all_poses = ["frame.full_body", "frame.upper_body", "frame.product_closeup"]
    first = pose_for_template(template_id)
    return [first] + [pose for pose in all_poses if pose != first]

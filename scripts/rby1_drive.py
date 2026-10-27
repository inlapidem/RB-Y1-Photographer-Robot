"""Send one deliberately short, bounded RB-Y1 mobile-base velocity command.

This tool is for an operator connected to the RB-Y1 User PC over SSH.  It
does not power on the robot or enable its Control Manager.  A physical EMO
must be within reach before using it.
"""

from __future__ import annotations

import argparse
import sys

MAX_LINEAR_MPS = 0.15
MAX_ANGULAR_RADPS = 0.30
MAX_DURATION_S = 1.0


def validate(args: argparse.Namespace) -> None:
    if args.confirm_drive != "MOVE":
        raise ValueError("실행하려면 --confirm-drive MOVE를 정확히 지정해야 합니다")
    if not 0 < args.duration <= MAX_DURATION_S:
        raise ValueError(f"--duration은 0보다 크고 {MAX_DURATION_S:g}초 이하여야 합니다")
    if abs(args.forward) > MAX_LINEAR_MPS or abs(args.sideways) > MAX_LINEAR_MPS:
        raise ValueError(f"선속도는 ±{MAX_LINEAR_MPS:g} m/s 범위여야 합니다")
    if abs(args.turn) > MAX_ANGULAR_RADPS:
        raise ValueError(f"각속도는 ±{MAX_ANGULAR_RADPS:g} rad/s 범위여야 합니다")
    if args.model == "a" and args.sideways:
        raise ValueError("RB-Y1 Model A는 sideways(SE2 y) 이동을 지원하지 않습니다")
    if not any((args.forward, args.sideways, args.turn)):
        raise ValueError("forward, sideways, turn 중 하나는 0이 아니어야 합니다")


def make_command(rby, forward: float, sideways: float, turn: float, duration: float):
    return rby.RobotCommandBuilder().set_command(
        rby.ComponentBasedCommandBuilder().set_mobility_command(
            rby.SE2VelocityCommandBuilder()
            .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(duration))
            .set_minimum_time(duration)
            .set_velocity([forward, sideways], turn)
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="localhost:50051", help="RB-Y1 gRPC address")
    parser.add_argument("--model", choices=("a", "m"), default="a")
    parser.add_argument("--forward", type=float, default=0.0, help="전/후진 m/s (+는 전진)")
    parser.add_argument("--sideways", type=float, default=0.0, help="좌/우 m/s (+는 왼쪽, Model M만)")
    parser.add_argument("--turn", type=float, default=0.0, help="회전 rad/s (+는 SDK 기준 양의 회전)")
    parser.add_argument("--duration", type=float, required=True, help="명령 지속 시간(최대 1초)")
    parser.add_argument("--confirm-drive", default="", help="안전 확인 문구 MOVE")
    args = parser.parse_args()
    try:
        validate(args)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        import rby1_sdk as rby
    except ImportError as exc:
        raise SystemExit("rby1-sdk가 필요합니다") from exc

    robot = rby.create_robot(args.address, args.model)
    if not robot.connect():
        raise SystemExit(f"RB-Y1에 연결하지 못했습니다: {args.address}")
    try:
        state = robot.get_control_manager_state().state
        if state != rby.ControlManagerState.State.Enabled:
            raise SystemExit("Control Manager가 ENABLE 상태가 아닙니다. Web UI에서 안전을 확인하세요.")
        command = make_command(rby, args.forward, args.sideways, args.turn, args.duration)
        print(
            f"이동 명령: x={args.forward:+.3f} m/s, y={args.sideways:+.3f} m/s, "
            f"yaw={args.turn:+.3f} rad/s, {args.duration:.2f}s",
            flush=True,
        )
        feedback = robot.send_command(command, 1).get()
        if feedback.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
            raise SystemExit(f"이동 명령 실패: {feedback.finish_code}")
        print("이동 명령 완료")
    finally:
        if hasattr(robot, "disconnect"):
            robot.disconnect()


if __name__ == "__main__":
    main()

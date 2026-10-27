"""Prepare the RB-Y1 right wrist and hold a manually closed camera grip.

Commands:
  wrist      Rotate only right_arm_6 relative to its current encoder pose.
  hold-grip  Read the manually closed gripper encoders and hold that position.

No gripper-open command is provided.  Keep the hold-grip process running while
the camera must remain clamped; Ctrl-C disables gripper torque.
"""

from __future__ import annotations

import argparse
import math
import time


def wrist_target(current: list[float], lower: list[float], upper: list[float], radians: float) -> list[float]:
    if len(current) != 7:
        raise ValueError("오른팔 엔코더는 7축이어야 합니다")
    target = list(current)
    target[6] += radians
    margin = math.radians(2.0)
    if not lower[6] + margin <= target[6] <= upper[6] - margin:
        raise ValueError(
            f"right_arm_6 목표 {math.degrees(target[6]):.1f}°가 안전 관절 범위를 벗어납니다. "
            "--direction을 반대로 지정하세요."
        )
    return target


def rotate_wrist(args: argparse.Namespace, rby) -> None:
    robot = rby.create_robot(args.address, args.model)
    if not robot.connect():
        raise SystemExit(f"RB-Y1에 연결하지 못했습니다: {args.address}")
    try:
        if robot.get_control_manager_state().state != rby.ControlManagerState.State.Enabled:
            raise SystemExit("Control Manager가 ENABLE 상태가 아닙니다")
        model = robot.model()
        state = robot.get_state()
        arm_indices = list(model.right_arm_idx)
        current = [float(state.position[index]) for index in arm_indices]
        dynamics = robot.get_dynamics()
        dyn_state = dynamics.make_state([], model.robot_joint_names)
        lower_all = dynamics.get_limit_q_lower(dyn_state)
        upper_all = dynamics.get_limit_q_upper(dyn_state)
        velocity_all = dynamics.get_limit_qdot_upper(dyn_state)
        acceleration_all = dynamics.get_limit_qddot_upper(dyn_state)
        lower = [float(lower_all[index]) for index in arm_indices]
        upper = [float(upper_all[index]) for index in arm_indices]
        signed_angle = math.radians(args.degrees) * (1 if args.direction == "positive" else -1)
        target = wrist_target(current, lower, upper, signed_angle)
        velocity = [float(velocity_all[index]) * 0.15 for index in arm_indices]
        acceleration = [float(acceleration_all[index]) * 0.20 for index in arm_indices]
        command = rby.RobotCommandBuilder().set_command(
            rby.ComponentBasedCommandBuilder().set_body_command(
                rby.BodyComponentBasedCommandBuilder().set_right_arm_command(
                    rby.JointPositionCommandBuilder()
                    .set_position(target)
                    .set_velocity_limit(velocity)
                    .set_acceleration_limit(acceleration)
                    .set_minimum_time(args.duration)
                )
            )
        )
        print(
            f"right_arm_6: {math.degrees(current[6]):.1f}° -> "
            f"{math.degrees(target[6]):.1f}° ({args.duration:.1f}s)"
        )
        feedback = robot.send_command(command, 1).get()
        if feedback.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
            raise RuntimeError(f"손목 회전 실패: {feedback.finish_code}")
        print("손목 회전 완료 — 이후 arm 명령에서 이 축을 다시 움직일 수 있습니다")
    finally:
        if hasattr(robot, "disconnect"):
            robot.disconnect()


def read_gripper_positions(bus) -> list[float]:
    values = bus.group_fast_sync_read_encoder([0, 1])
    if values is None:
        raise RuntimeError("집게 엔코더를 읽지 못했습니다")
    positions = [0.0, 0.0]
    seen: set[int] = set()
    for device_id, encoder in values:
        positions[int(device_id)] = float(encoder)
        seen.add(int(device_id))
    if seen != {0, 1}:
        raise RuntimeError(f"집게 엔코더 ID 0,1이 모두 보이지 않습니다: {sorted(seen)}")
    return positions


def hold_grip(args: argparse.Namespace, rby) -> None:
    bus = rby.DynamixelBus(rby.upc.GripperDeviceName)
    bus.open_port()
    bus.set_baud_rate(2_000_000)
    bus.set_torque_constant([1, 1])
    for device_id in (0, 1):
        if not bus.ping(device_id):
            raise RuntimeError(f"집게 Dynamixel ID {device_id}가 응답하지 않습니다")

    print("카메라를 넣고 집게를 손으로 원하는 만큼 닫으세요. 준비되면 Enter를 누르세요.")
    input()
    target = read_gripper_positions(bus)
    mode = rby.DynamixelBus.CurrentBasedPositionControlMode
    bus.group_sync_write_torque_enable([(0, 0), (1, 0)])
    bus.group_sync_write_operating_mode([(0, mode), (1, mode)])
    bus.group_sync_write_torque_enable([(0, 1), (1, 1)])
    bus.group_sync_write_send_torque([(0, args.current), (1, args.current)])
    print(f"집게 현재 위치 유지 시작: {target}. Ctrl-C를 누르면 토크를 해제합니다.")
    try:
        while True:
            bus.group_sync_write_send_position([(0, target[0]), (1, target[1])])
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n집게 유지 종료 — 토크 해제")
    finally:
        bus.group_sync_write_torque_enable([(0, 0), (1, 0)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    wrist = subparsers.add_parser("wrist", help="현재 자세에서 마지막 손목축 상대 회전")
    wrist.add_argument("--address", default="192.168.30.1:50051")
    wrist.add_argument("--model", choices=("a", "m"), default="a")
    wrist.add_argument("--degrees", type=float, default=90.0)
    wrist.add_argument("--direction", choices=("positive", "negative"), default="positive")
    wrist.add_argument("--duration", type=float, default=4.0)
    wrist.add_argument("--execute", default="", help="실기 확인 문구 MOVE")

    grip = subparsers.add_parser("hold-grip", help="수동으로 닫은 현재 집게 위치 유지")
    grip.add_argument("--current", type=float, default=0.5, help="유지 전류(기본 0.5)")
    grip.add_argument("--execute", default="", help="실기 확인 문구 HOLD")
    args = parser.parse_args()
    if args.command == "wrist" and (not 0 < args.degrees <= 90 or not 2 <= args.duration <= 10):
        parser.error("손목 회전은 0~90°, 시간은 2~10초 범위여야 합니다")
    if args.command == "hold-grip" and not 0.1 <= args.current <= 1.0:
        parser.error("집게 유지 전류는 0.1~1.0 범위여야 합니다")

    import rby1_sdk as rby

    if args.command == "wrist":
        if args.execute != "MOVE":
            print("실기 손목 회전에는 --execute MOVE가 필요합니다")
            return
        rotate_wrist(args, rby)
    else:
        if args.execute != "HOLD":
            print("실기 집게 유지에는 --execute HOLD가 필요합니다")
            return
        hold_grip(args, rby)


if __name__ == "__main__":
    main()

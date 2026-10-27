"""Validate or smoothly replay an RB-Y1 right-arm encoder recording."""

from __future__ import annotations

import argparse
from pathlib import Path

from geekseek.robot import load_rby1_trajectory


def prepare(segments: list[dict], stationary_threshold_rad: float = 0.0008) -> list[dict]:
    if not segments:
        raise ValueError("trajectory에 구간이 없습니다")
    result: list[dict] = []
    last: list[float] | None = None
    for source in segments:
        item = dict(source)
        position = item.get("right_arm_rad")
        if not isinstance(position, list) or len(position) != 7:
            raise ValueError(f"{item.get('label', 'segment')}: 오른팔 엔코더 7축 값이 필요합니다")
        item["right_arm_rad"] = [float(value) for value in position]
        item["duration_s"] = float(item["duration_s"])
        if item["duration_s"] <= 0:
            raise ValueError("duration_s는 0보다 커야 합니다")
        if not result or item.get("kind") == "dwell":
            result.append(item)
            last = item["right_arm_rad"]
            continue
        assert last is not None
        if max(abs(a - b) for a, b in zip(position, last)) >= stationary_threshold_rad:
            result.append(item)
            last = item["right_arm_rad"]
    return result


def limited_test_path(segments: list[dict], max_shots: int | None) -> list[dict]:
    """Keep only the first N dwell poses and always return to taught base."""
    if max_shots is None:
        return [dict(item) for item in segments]
    if max_shots < 0:
        raise ValueError("max-shots는 0 이상이어야 합니다")
    if not segments or segments[0].get("label") != "start-home":
        raise ValueError("안전한 부분 테스트에는 첫 구간 start-home이 필요합니다")
    if max_shots == 0:
        return [dict(segments[0])]

    output: list[dict] = []
    dwell_count = 0
    for item in segments:
        output.append(dict(item))
        if item.get("kind") == "dwell":
            dwell_count += 1
            if dwell_count == max_shots:
                break
    if dwell_count < max_shots:
        raise ValueError(f"요청한 {max_shots}개보다 촬영 pose가 적습니다: {dwell_count}")

    home = dict(segments[-1])
    home["label"] = "test-return-home"
    # The original final duration was computed from a different last pose.
    # Recalculate conservatively at 0.05 rad/s for a partial test return.
    delta = max(abs(a - b) for a, b in zip(output[-1]["right_arm_rad"], home["right_arm_rad"]))
    home["duration_s"] = max(float(home["duration_s"]), delta / 0.05)
    output.append(home)
    return output


def slowed(segments: list[dict], factor: float) -> list[dict]:
    if factor < 1.0:
        raise ValueError("slowdown은 1.0 이상이어야 합니다")
    return [
        {
            **item,
            "duration_s": (
                float(item["duration_s"])
                if item.get("kind") == "dwell"
                else float(item["duration_s"]) * factor
            ),
        }
        for item in segments
    ]


def joint_command(
    rby,
    position: list[float],
    duration_s: float,
    streaming: bool,
    head_rad: list[float] | None = None,
):
    builder = rby.JointPositionCommandBuilder().set_minimum_time(duration_s).set_position(position)
    if streaming:
        builder.set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
    component = rby.ComponentBasedCommandBuilder().set_body_command(
        rby.BodyComponentBasedCommandBuilder().set_right_arm_command(builder)
    )
    if head_rad is not None:
        head = rby.JointPositionCommandBuilder().set_minimum_time(duration_s).set_position(head_rad)
        if streaming:
            head.set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
        component.set_head_command(head)
    return rby.RobotCommandBuilder().set_command(component)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("calibration/rby1/frame.full_body.json"))
    parser.add_argument("--address", default="192.168.30.1:50051")
    parser.add_argument("--model", choices=("a", "m"), default="a")
    parser.add_argument("--capture-count", type=int, default=30)
    parser.add_argument(
        "--max-shots",
        type=int,
        default=None,
        help="부분 실기 테스트용 촬영 pose 수. 0이면 base 진입만 수행",
    )
    parser.add_argument(
        "--slowdown",
        type=float,
        default=1.0,
        help="이동 시간 배율. 첫 실기 테스트 권장값 2.0",
    )
    parser.add_argument("--execute", default="", help="실기 재생 확인 문구 MOVE")
    args = parser.parse_args()

    trajectory = load_rby1_trajectory(args.input, args.capture_count)
    original = [
        {
            "kind": segment.kind,
            "label": segment.label,
            "duration_s": segment.duration_s,
            "right_arm_rad": list(segment.right_arm_rad or ()),
            "head_rad": list(segment.head_rad) if segment.head_rad is not None else None,
        }
        for segment in trajectory.segments
    ]
    replay = slowed(limited_test_path(prepare(original), args.max_shots), args.slowdown)
    seconds = sum(float(item["duration_s"]) for item in replay)
    dwells = [item.get("label") for item in replay if item.get("kind") == "dwell"]
    print(f"엔코더 검증 완료: {len(original)} -> {len(replay)}구간, 약 {seconds:.1f}초, 촬영점={dwells}")
    if args.execute != "MOVE":
        print("실제 재생은 fault reset·주변·EMO 확인 후 --execute MOVE를 추가하세요")
        return

    import rby1_sdk as rby

    robot = rby.create_robot(args.address, args.model)
    if not robot.connect():
        raise SystemExit(f"RB-Y1에 연결하지 못했습니다: {args.address}")
    try:
        if robot.get_control_manager_state().state != rby.ControlManagerState.State.Enabled:
            raise SystemExit("Control Manager가 ENABLE 상태가 아닙니다")
        def send_blocking(item):
            feedback = robot.send_command(
                joint_command(
                    rby,
                    item["right_arm_rad"],
                    max(2.0, item["duration_s"]),
                    False,
                    item.get("head_rad"),
                ),
                1,
            ).get()
            if feedback.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
                raise RuntimeError(f"{item.get('label') or item.get('kind')} 실행 실패: {feedback.finish_code}")

        # Keypose trajectories contain a small number of deliberate moves and
        # dwells, not high-frequency encoder samples.  Wait for every command
        # to finish so a stream cancellation cannot also cancel the following
        # base-return command at the same priority.
        for item in replay:
            if item.get("kind") == "dwell":
                print(f"촬영 pose: {item.get('label')}", flush=True)
            send_blocking(item)
        print("엔코더 trajectory 재생 완료")
    finally:
        robot.cancel_control()
        if hasattr(robot, "disconnect"):
            robot.disconnect()


if __name__ == "__main__":
    main()

"""Preview a recorded RB-Y1 ``base -> ee_right`` Cartesian trajectory.

By default this only validates and summarizes the file.  Physical replay
requires ``--execute MOVE``.  Consecutive near-identical samples are collapsed
so time spent waiting for terminal marker input is not replayed.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def pose_delta(a: list[list[float]], b: list[list[float]]) -> tuple[float, float]:
    translation = math.sqrt(sum((a[row][3] - b[row][3]) ** 2 for row in range(3)))
    # trace(Ra^T Rb) without taking a numpy dependency in validation mode.
    trace = sum(a[k][i] * b[k][i] for i in range(3) for k in range(3))
    cosine = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    return translation, math.acos(cosine)


def validate_transform(value: object, label: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 4 or any(not isinstance(row, list) or len(row) != 4 for row in value):
        raise ValueError(f"{label}: ee_right_transform은 4x4 배열이어야 합니다")
    return [[float(cell) for cell in row] for row in value]


def simplify(
    segments: list[dict],
    translation_threshold_m: float = 0.004,
    rotation_threshold_rad: float = 0.015,
    maximum_step_seconds: float = 0.30,
) -> list[dict]:
    if not segments:
        raise ValueError("trajectory에 구간이 없습니다")
    first = dict(segments[0])
    first["ee_right_transform"] = validate_transform(first.get("ee_right_transform"), first.get("label", "entry"))
    first["duration_s"] = max(1.0, float(first.get("duration_s", 0)))
    output = [first]
    last_pose = first["ee_right_transform"]
    pending_s = 0.0

    for source in segments[1:]:
        item = dict(source)
        label = str(item.get("label", item.get("kind", "segment")))
        pose = validate_transform(item.get("ee_right_transform"), label)
        duration = float(item.get("duration_s", 0))
        if duration <= 0:
            raise ValueError(f"{label}: duration_s는 0보다 커야 합니다")
        if item.get("kind") == "dwell":
            item["ee_right_transform"] = pose
            item["duration_s"] = min(duration, 1.0)
            output.append(item)
            last_pose = pose
            pending_s = 0.0
            continue
        pending_s += duration
        translation, rotation = pose_delta(last_pose, pose)
        if translation >= translation_threshold_m or rotation >= rotation_threshold_rad:
            item["kind"] = "travel"
            item["ee_right_transform"] = pose
            item["duration_s"] = max(0.05, min(pending_s, maximum_step_seconds))
            output.append(item)
            last_pose = pose
            pending_s = 0.0
    return output


def make_command(rby, segment: dict):
    return rby.RobotCommandBuilder().set_command(
        rby.ComponentBasedCommandBuilder().set_body_command(
            rby.BodyComponentBasedCommandBuilder().set_right_arm_command(
                rby.CartesianCommandBuilder()
                .add_target("base", "ee_right", segment["ee_right_transform"], 0.20, 0.50, 0.30)
                .set_minimum_time(float(segment["duration_s"]))
                .set_stop_position_tracking_error(0.003)
                .set_stop_orientation_tracking_error(0.03)
            )
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("calibration/rby1/frame.full_body.json"))
    parser.add_argument("--address", default="192.168.30.1:50051")
    parser.add_argument("--model", choices=("a", "m"), default="a")
    parser.add_argument("--execute", default="", help="실기 재생 확인 문구 MOVE")
    args = parser.parse_args()

    document = json.loads(args.input.read_text(encoding="utf-8"))
    original = document.get("segments", [])
    preview = simplify(original)
    original_seconds = sum(float(item["duration_s"]) for item in original)
    preview_seconds = sum(float(item["duration_s"]) for item in preview)
    dwells = [item.get("label") for item in preview if item.get("kind") == "dwell"]
    print(
        f"검증 완료: {len(original)}구간/{original_seconds:.1f}초 -> "
        f"미리보기 {len(preview)}구간/{preview_seconds:.1f}초, 촬영점={dwells}"
    )
    if args.execute != "MOVE":
        print("실제 재생은 주변과 EMO를 확인한 뒤 --execute MOVE를 추가하세요")
        return

    import rby1_sdk as rby

    robot = rby.create_robot(args.address, args.model)
    if not robot.connect():
        raise SystemExit(f"RB-Y1에 연결하지 못했습니다: {args.address}")
    try:
        if robot.get_control_manager_state().state != rby.ControlManagerState.State.Enabled:
            raise SystemExit("Control Manager가 ENABLE 상태가 아닙니다")
        for index, segment in enumerate(preview, start=1):
            if segment.get("kind") == "dwell":
                print(f"촬영 pose: {segment.get('label')}", flush=True)
            feedback = robot.send_command(make_command(rby, segment), 1).get()
            if feedback.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
                raise RuntimeError(f"{index}번 구간 실행 실패: {feedback.finish_code}")
        print("Cartesian 미리보기 재생 완료")
    finally:
        robot.cancel_control()
        if hasattr(robot, "disconnect"):
            robot.disconnect()


if __name__ == "__main__":
    main()

"""Teach RB-Y1 phone-camera keyposes without recording a continuous path.

The phone must already be secured in the right gripper.  Gravity compensation
lets an operator place the camera, ``m`` stores one photo anchor, and ``h``
stores the explicit safe home/return pose.  The resulting file keeps the same
``calibration/rby1/frame.*.json`` path used by :class:`Rby1Robot`; the runtime
compiler finds the shortest closed route and expands the anchors to 30 stopped
photo poses.
"""

from __future__ import annotations

import argparse
import json
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass
from pathlib import Path


SCHEMA = "geekseek.rby1.keyposes/v1"
GRAVITY_REFRESH_SECONDS = 300.0


@dataclass(frozen=True)
class Sample:
    right_arm_rad: tuple[float, ...]
    ee_right_transform: tuple[tuple[float, ...], ...]
    force_n: tuple[float, ...]
    torque_nm: tuple[float, ...]


def sample_payload(sample: Sample) -> dict:
    return {
        "right_arm_rad": list(sample.right_arm_rad),
        "ee_right_transform": [list(row) for row in sample.ee_right_transform],
        "ft_sensor_right": {
            "force_n": list(sample.force_n),
            "torque_nm": list(sample.torque_nm),
        },
    }


def build_keypose_document(
    name: str,
    home: Sample,
    anchors: list[Sample],
    *,
    grasp_id: str,
    phone_orientation: str,
    capture_count: int,
    ee_camera_transform: list[list[float]] | None = None,
) -> dict:
    if len(anchors) < 2:
        raise ValueError("촬영 키포즈가 최소 2개 필요합니다")
    if capture_count < len(anchors):
        raise ValueError("capture_count는 키포즈 수보다 작을 수 없습니다")
    tool = {
        "grasp_id": grasp_id,
        "phone_orientation": phone_orientation,
        "camera_transform_status": "calibrated" if ee_camera_transform is not None else "uncalibrated",
    }
    if ee_camera_transform is not None:
        tool["ee_camera_transform"] = ee_camera_transform
    return {
        "schema": SCHEMA,
        "name": name,
        "tool": tool,
        "home": sample_payload(home),
        "anchors": [
            {"label": f"wp{index:02d}", "enabled": True, **sample_payload(sample)}
            for index, sample in enumerate(anchors, start=1)
        ],
        "planning": {
            "capture_count": capture_count,
            "max_joint_speed_rad_s": 0.25,
            "dwell_seconds": 0.7,
            "min_travel_seconds": 0.25,
            "entry_seconds": 3.0,
            "home_seconds": 3.0,
            "blocked_edges": [],
        },
    }


def load_transform(path: Path | None) -> list[list[float]] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("ee_camera_transform")
    if not isinstance(value, list) or len(value) != 4 or any(not isinstance(row, list) or len(row) != 4 for row in value):
        raise ValueError("EEF→카메라 변환은 4x4 JSON 배열이어야 합니다")
    return [[float(cell) for cell in row] for row in value]


class CbreakInput:
    def __enter__(self):
        if not sys.stdin.isatty():
            raise RuntimeError("키 입력을 받으려면 터미널에서 실행하세요")
        self.fd = sys.stdin.fileno()
        self.previous = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def read(self) -> str | None:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        return sys.stdin.read(1) if ready else None

    def __exit__(self, *_):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.previous)


def gravity_compensation_command(rby):
    return rby.RobotCommandBuilder().set_command(
        rby.ComponentBasedCommandBuilder().set_body_command(
            rby.BodyComponentBasedCommandBuilder().set_right_arm_command(
                rby.GravityCompensationCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(3600.0))
                .set_on(True)
            )
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--model", default="a", choices=("a", "m"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default="RBY1 phone keypose sweep")
    parser.add_argument("--anchor-count", type=int, default=11)
    parser.add_argument("--capture-count", type=int, default=30)
    parser.add_argument("--grasp-id", default="phone_grasp_v1")
    parser.add_argument("--phone-orientation", choices=("portrait", "landscape"), default="portrait")
    parser.add_argument("--ee-camera-transform", type=Path)
    args = parser.parse_args()
    if args.anchor_count < 2 or args.capture_count < args.anchor_count:
        parser.error("키포즈는 2개 이상이고 capture-count는 anchor-count 이상이어야 합니다")
    try:
        ee_camera_transform = load_transform(args.ee_camera_transform)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    try:
        import rby1_sdk as rby
    except ImportError as exc:
        raise SystemExit("rby1-sdk가 필요합니다") from exc

    robot = rby.create_robot(args.address, args.model)
    if not robot.connect():
        raise SystemExit(f"RB-Y1에 연결하지 못했습니다: {args.address}")
    if robot.get_control_manager_state().state != rby.ControlManagerState.State.Enabled:
        raise SystemExit("Control Manager가 ENABLE 상태가 아닙니다. fault reset 후 다시 시도하세요.")

    model = robot.model()
    right_indices = list(model.right_arm_idx)
    dynamics = robot.get_dynamics()
    dynamics_state = dynamics.make_state(["base", "ee_right"], model.robot_joint_names)

    def snapshot() -> Sample:
        state = robot.get_state()
        dynamics_state.set_q(state.position)
        dynamics.compute_forward_kinematics(dynamics_state)
        transform = dynamics.compute_transformation(dynamics_state, 0, 1)
        ft = state.ft_sensor_right
        return Sample(
            tuple(float(state.position[index]) for index in right_indices),
            tuple(tuple(float(cell) for cell in row) for row in transform),
            tuple(float(value) for value in ft.force),
            tuple(float(value) for value in ft.torque),
        )

    stream = robot.create_command_stream(1)
    stream.send_command(gravity_compensation_command(rby))
    gravity_refreshed_at = time.monotonic()
    home: Sample | None = None
    anchors: list[Sample] = []
    print(
        f"휴대폰 장착 상태 키포즈 티칭: h=안전 home, m=촬영점({args.anchor_count}개), "
        "u=마지막 취소, q=저장"
    )
    if ee_camera_transform is None:
        print("주의: --ee-camera-transform이 없어 카메라 외부 파라미터는 uncalibrated로 저장됩니다")
    try:
        with CbreakInput() as keyboard:
            while True:
                if time.monotonic() - gravity_refreshed_at >= GRAVITY_REFRESH_SECONDS:
                    stream.send_command(gravity_compensation_command(rby))
                    gravity_refreshed_at = time.monotonic()
                key = keyboard.read()
                if key == "h":
                    home = snapshot()
                    print(f"\nhome 저장, F={home.force_n} N, T={home.torque_nm} Nm")
                elif key == "m":
                    if len(anchors) >= args.anchor_count:
                        print(f"\n이미 키포즈 {args.anchor_count}개를 모두 기록했습니다")
                        continue
                    sample = snapshot()
                    anchors.append(sample)
                    print(
                        f"\nwp{len(anchors):02d}/{args.anchor_count} 저장, "
                        f"F={sample.force_n} N, T={sample.torque_nm} Nm"
                    )
                elif key == "u":
                    if anchors:
                        anchors.pop()
                        print(f"\n마지막 키포즈 취소, 현재 {len(anchors)}/{args.anchor_count}")
                elif key == "q":
                    if home is None:
                        print("\nh를 눌러 안전 home을 먼저 저장하세요")
                    elif len(anchors) != args.anchor_count:
                        print(f"\n키포즈가 {len(anchors)}/{args.anchor_count}개입니다")
                    else:
                        break
                elif key == "\x03":
                    raise KeyboardInterrupt
                time.sleep(0.01)
    finally:
        stream.cancel()
        robot.cancel_control()
        if hasattr(robot, "disconnect"):
            robot.disconnect()

    assert home is not None
    document = build_keypose_document(
        args.name,
        home,
        anchors,
        grasp_id=args.grasp_id,
        phone_orientation=args.phone_orientation,
        capture_count=args.capture_count,
        ee_camera_transform=ee_camera_transform,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"키포즈 {len(anchors)}개 → 촬영 {args.capture_count}장 계획 저장: {args.out}")


if __name__ == "__main__":
    main()

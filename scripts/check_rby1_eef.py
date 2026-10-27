"""Read-only RB-Y1 right EEF/F-T/gripper diagnostics; sends no motion command."""

from __future__ import annotations

import argparse
import math
import time


def norm(values) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in values))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="192.168.30.1:50051")
    parser.add_argument("--model", choices=("a", "m"), default="a")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument(
        "--probe-gripper",
        action="store_true",
        help="로컬 /dev/rby1_gripper에서 Dynamixel ID 0/1에 읽기 전용 ping",
    )
    args = parser.parse_args()
    if args.samples < 1 or args.interval < 0:
        parser.error("samples는 1 이상, interval은 0 이상이어야 합니다")

    import rby1_sdk as rby

    robot = rby.create_robot(args.address, args.model)
    if not robot.connect():
        raise SystemExit(f"RB-Y1에 연결하지 못했습니다: {args.address}")
    forces: list[tuple[float, ...]] = []
    torques: list[tuple[float, ...]] = []
    try:
        for _ in range(args.samples):
            state = robot.get_state()
            forces.append(tuple(float(value) for value in state.ft_sensor_right.force))
            torques.append(tuple(float(value) for value in state.ft_sensor_right.torque))
            flange = state.tool_flange_right
            time.sleep(args.interval)
    finally:
        if hasattr(robot, "disconnect"):
            robot.disconnect()

    mean_force = tuple(sum(row[i] for row in forces) / len(forces) for i in range(3))
    mean_torque = tuple(sum(row[i] for row in torques) / len(torques) for i in range(3))
    print(f"right F/T mean: force={mean_force} N (|F|={norm(mean_force):.3f})")
    print(f"right F/T mean: torque={mean_torque} Nm (|T|={norm(mean_torque):.3f})")
    print(
        "right tool flange: "
        f"voltage={flange.output_voltage}V, switch_A={flange.switch_A}, "
        f"digital_inputs=({flange.digital_input_A}, {flange.digital_input_B})"
    )

    if args.probe_gripper:
        bus = rby.DynamixelBus(rby.upc.GripperDeviceName)
        try:
            bus.open_port()
            bus.set_baud_rate(2_000_000)
            print(f"gripper device: {rby.upc.GripperDeviceName}")
            print(f"Dynamixel ping: id0={bus.ping(0)}, id1={bus.ping(1)}")
            print(f"gripper encoders: {bus.group_fast_sync_read_encoder([0, 1])}")
        finally:
            close = getattr(bus, "close_port", None)
            if close is not None:
                close()


if __name__ == "__main__":
    main()

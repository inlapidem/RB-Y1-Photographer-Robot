"""Build phorce motion slots for the full-body and upper-body photo sweeps.

Reads the joint angles captured by `scripts/capture_pose_samples.py` and writes
the three files the PCM expects per slot — `motion_NN.csv`,
`motion_NN.memo.json`, `motion_NN.memo.json.pending` — plus a schedule sidecar
that `scripts/run_photo_sweep.py` uses to time the shutter.

Writes to a staging directory by default. Point `--out` at the SD card's
`Motions/` folder only once the plan looks right; the robot must be idle and
Studio disconnected, since Studio and the PCM take turns owning that card.

  python3 scripts/build_motion_slots.py
  python3 scripts/build_motion_slots.py --out /media/phorce/9016-4EF8/Motions
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geekseek.motion_plan import (  # noqa: E402
    TICKS_PER_SECOND,
    MotionPlanError,
    build_schedule,
    joint_distance,
    load_waypoints,
    optimize_order,
    render_motion_csv,
    shot_times_seconds,
)

AXES = (0, 1, 2, 6, 8)
HOME_DEG = (0.0, 0.0, 0.0, 0.0, 0.0)

# Slots 1-3 already hold hand-taught motions, so the sweeps start at 4.
PRESETS = {
    "fullbody": {
        "slot": 4,
        "ms_name": "PHOTO_FULLBODY",
        "rows": [1, 2, 3, 4, 5, 7, 8, 9],  # row 6 dropped at the operator's request
        "memo": "photo sweep / full body",
    },
    "upperbody": {
        "slot": 5,
        "ms_name": "PHOTO_UPPERBODY",
        "rows": [10, 11, 12, 13, 14, 15, 16, 17, 18],
        "memo": "photo sweep / upper body",
    },
}


def encode_zero_snapshot(zero_offsets_rad: dict[int, float], robot_uid: str) -> dict[str, object]:
    known_mask = 0
    offsets: list[str | None] = [None] * 12
    for axis, value in zero_offsets_rad.items():
        known_mask |= 1 << axis
        offsets[axis] = struct.pack("<f", value).hex()
    return {
        "format_version": 2,
        "robot_uid": robot_uid,
        "known_mask": known_mask,
        "zero_offset_f32_le_hex": offsets,
    }


def read_robot_uid(motions_dir: Path) -> str:
    """Borrow the UID the PCM already stamped into an existing slot's memo."""
    for memo_file in sorted(motions_dir.glob("motion_*.memo.json")):
        try:
            uid = json.loads(memo_file.read_text(encoding="utf-8"))["zero_snapshot"]["robot_uid"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if uid:
            return str(uid)
    raise MotionPlanError(
        f"{motions_dir}에서 robot_uid를 읽지 못했습니다. --robot-uid로 직접 지정하세요"
    )


def build_preset(name: str, args: argparse.Namespace, zero_offsets_rad: dict[int, float], uid: str):
    preset = PRESETS[name]
    waypoints = load_waypoints(args.source, preset["rows"], AXES)
    order = optimize_order(waypoints, HOME_DEG)
    segments = build_schedule(
        waypoints,
        order,
        home_deg=HOME_DEG,
        total_seconds=args.seconds,
        travel_deg_per_s=args.travel_speed,
        transit_deg_per_s=args.transit_speed,
    )
    motion_csv = render_motion_csv(
        segments, ms_id=preset["slot"], ms_name=preset["ms_name"], axes=AXES
    )
    memo = {
        "zero_snapshot": encode_zero_snapshot(zero_offsets_rad, uid),
        "teaching_start_angles_rad": [
            0.0 if axis in AXES else None for axis in range(12)
        ],
        "motion_sha256": hashlib.sha256(motion_csv.encode("utf-8")).hexdigest(),
        "schema": 2,
        "slot_id": preset["slot"],
        "updated": datetime.now().replace(microsecond=0).isoformat(),
        "memo": preset["memo"],
    }
    pending = {
        "slot_id": preset["slot"],
        "state": "complete",
        "updated": memo["updated"],
    }
    schedule = {
        "slot": preset["slot"],
        "name": preset["ms_name"],
        "source": str(args.source.relative_to(ROOT)),
        "axes": list(AXES),
        "total_seconds": sum(s.ticks for s in segments) / TICKS_PER_SECOND,
        "waypoint_rows": [waypoints[i].label for i in order],
        "shots": [
            {
                "label": label,
                "window_start_s": round(start, 3),
                "window_end_s": round(end, 3),
                "targets_deg": [
                    round(v, 3) for v in next(s for s in segments if s.label == label).targets_deg
                ],
            }
            for label, start, end in shot_times_seconds(segments)
        ],
    }
    return preset, waypoints, order, segments, motion_csv, memo, pending, schedule


def report(name: str, waypoints, order, segments, schedule) -> None:
    route = [waypoints[i].angles_deg for i in order]
    original = (
        joint_distance(HOME_DEG, waypoints[0].angles_deg)
        + sum(
            joint_distance(waypoints[k].angles_deg, waypoints[k + 1].angles_deg)
            for k in range(len(waypoints) - 1)
        )
        + joint_distance(waypoints[-1].angles_deg, HOME_DEG)
    )
    optimized = (
        joint_distance(HOME_DEG, route[0])
        + sum(joint_distance(route[k], route[k + 1]) for k in range(len(route) - 1))
        + joint_distance(route[-1], HOME_DEG)
    )
    dwell = next(s.ticks for s in segments if s.is_shot)
    print(f"\n■ {name} — 슬롯 {schedule['slot']} · {schedule['name']}")
    print(f"   웨이포인트 {len(order)}개, P-Vector {len(segments)}/20칸, 총 {schedule['total_seconds']:.2f}초")
    print(f"   순서 {[waypoints[i].label for i in order]}  (CSV 순서 {original:.1f}° → 최적 {optimized:.1f}°)")
    print(f"   정지 {dwell / TICKS_PER_SECOND:.2f}초 × {len(order)}점, 이동 "
          f"{(sum(s.ticks for s in segments) - dwell * len(order)) / TICKS_PER_SECOND:.2f}초")
    for shot in schedule["shots"]:
        print(f"     ★ {shot['label']:22s} {shot['window_start_s']:6.2f}s ~ {shot['window_end_s']:6.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "calibration" / "trial_02.csv")
    parser.add_argument(
        "--zero-offset-file", type=Path, default=ROOT / "config" / "pose-zero-offsets.json"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "calibration" / "slots")
    parser.add_argument("--seconds", type=float, default=30.0, help="슬롯 하나의 총 재생 시간")
    parser.add_argument("--travel-speed", type=float, default=18.0, help="촬영 구간 deg/s")
    parser.add_argument("--transit-speed", type=float, default=25.0, help="진입/복귀 deg/s")
    parser.add_argument("--robot-uid", default=None)
    parser.add_argument(
        "--reference-motions",
        type=Path,
        default=Path("/media/phorce/9016-4EF8/Motions"),
        help="robot_uid를 빌려올 기존 슬롯 폴더 (보통 SD카드)",
    )
    parser.add_argument("--only", choices=sorted(PRESETS), default=None)
    args = parser.parse_args()

    zero = json.loads(args.zero_offset_file.read_text(encoding="utf-8"))
    zero_offsets_rad = {
        int(axis): float(value)
        for axis, value in zip(zero["axis_indices"], zero["position_rad"])
    }
    if tuple(sorted(zero_offsets_rad)) != tuple(sorted(AXES)):
        raise MotionPlanError(f"영점 파일 축 {sorted(zero_offsets_rad)}가 {list(AXES)}와 다릅니다")

    uid = args.robot_uid or read_robot_uid(args.reference_motions)

    args.out.mkdir(parents=True, exist_ok=True)
    names = [args.only] if args.only else sorted(PRESETS)
    for name in names:
        preset, waypoints, order, segments, motion_csv, memo, pending, schedule = build_preset(
            name, args, zero_offsets_rad, uid
        )
        slot = preset["slot"]
        stem = args.out / f"motion_{slot:02d}"
        stem.with_suffix(".csv").write_text(motion_csv, encoding="utf-8")
        Path(f"{stem}.memo.json").write_text(
            json.dumps(memo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        Path(f"{stem}.memo.json.pending").write_text(
            json.dumps(pending, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        Path(f"{stem}.schedule.json").write_text(
            json.dumps(schedule, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        report(name, waypoints, order, segments, schedule)

    print(f"\n출력: {args.out}")


if __name__ == "__main__":
    main()

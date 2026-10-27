"""Split one browser-teaching session into upper/full-body RB-Y1 keypose files."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SCHEMA = "geekseek.rby1.keyposes/v1"
RBY1_A_RIGHT_ARM_LIMITS_DEG = (
    (-180.0, 180.0),
    (-180.0, 1.0),
    (-180.0, 180.0),
    (-150.0, 1.0),
    (-180.0, 180.0),
    (-90.0, 110.0),
    (-155.0, 155.0),
)
JOINT_LIMIT_MARGIN_DEG = 0.5


def _safe_right_arm(
    sample: dict[str, Any],
    adjustments: list[dict[str, Any]],
) -> list[float]:
    values = [float(value) for value in sample["right_arm_rad"]]
    if len(values) != 7:
        raise ValueError(f"{sample.get('label')}: 오른팔 7축 값이 필요합니다")
    for axis, (value, limits_deg) in enumerate(zip(values, RBY1_A_RIGHT_ARM_LIMITS_DEG)):
        lower, upper = (math.radians(item) for item in limits_deg)
        if lower <= value <= upper:
            continue
        replacement = min(
            upper - math.radians(JOINT_LIMIT_MARGIN_DEG),
            max(lower + math.radians(JOINT_LIMIT_MARGIN_DEG), value),
        )
        adjustments.append(
            {
                "sample": sample.get("label"),
                "joint_index": axis,
                "recorded_deg": round(math.degrees(value), 6),
                "runtime_deg": round(math.degrees(replacement), 6),
                "reason": "recorded encoder value outside RBY1-A SDK joint limit",
            }
        )
        values[axis] = replacement
    return values


def _pose(sample: dict[str, Any], adjustments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "right_arm_rad": _safe_right_arm(sample, adjustments),
        "ee_right_transform": sample["ee_right_transform"],
        "ft_sensor_right": sample["ft_sensor_right"],
        "reference_image": sample.get("reference_image"),
        "rgb_image": sample.get("rgb_image"),
        "server_captured_at": sample.get("server_captured_at"),
    }


def build_document(
    session: dict[str, Any],
    *,
    mode: str,
    home_index: int,
    anchor_indices: range,
    capture_count: int,
) -> dict[str, Any]:
    anchors = session.get("anchors")
    if not isinstance(anchors, list):
        raise ValueError("capture session의 anchors가 배열이 아닙니다")
    requested = [home_index, *anchor_indices]
    if min(requested) < 1 or max(requested) > len(anchors):
        raise ValueError(f"요청한 인덱스가 저장된 1..{len(anchors)} 범위를 벗어납니다")
    if home_index in anchor_indices:
        raise ValueError("base pose는 촬영 anchor에 포함할 수 없습니다")
    selected = [anchors[index - 1] for index in anchor_indices]
    if len(selected) < 2:
        raise ValueError("촬영 keypose가 최소 2개 필요합니다")
    if capture_count < len(selected):
        raise ValueError("capture_count는 keypose 수보다 작을 수 없습니다")

    source_directory = session.get("source_directory", "")
    adjustments: list[dict[str, Any]] = []
    document = {
        "schema": SCHEMA,
        "name": f"RBY1 {mode} sweep from browser teaching",
        "source": {
            "capture_session": source_directory,
            "base_sample": anchors[home_index - 1].get("label", f"wp{home_index:02d}"),
            "anchor_samples": [item.get("label") for item in selected],
        },
        "tool": {
            "grasp_id": "phone_grasp_v1",
            "camera_transform_status": "uncalibrated",
        },
        "home": _pose(anchors[home_index - 1], adjustments),
        "anchors": [
            {
                "label": item.get("label", f"wp{index:02d}"),
                "enabled": True,
                **_pose(item, adjustments),
            }
            for index, item in zip(anchor_indices, selected)
        ],
        "planning": {
            "capture_count": capture_count,
            "max_joint_speed_rad_s": 0.10,
            "dwell_seconds": 1.5,
            "min_travel_seconds": 1.0,
            "entry_seconds": 6.0,
            "home_seconds": 6.0,
            "blocked_edges": [],
        },
    }
    document["home"]["head_rad"] = [0.0, math.radians(10.0)]
    document["source"]["joint_limit_adjustments"] = adjustments
    return document


def build_files(
    session_path: Path,
    output_directory: Path,
    *,
    capture_count: int = 30,
) -> dict[str, Path]:
    session = json.loads(session_path.read_text(encoding="utf-8"))
    if session.get("schema") != "geekseek.rby1.capture-session/v1":
        raise ValueError(f"지원하지 않는 capture session입니다: {session.get('schema')!r}")
    if len(session.get("anchors", [])) != 22:
        raise ValueError("이 분할은 wp01~wp22가 정확히 저장된 세션만 지원합니다")
    session["source_directory"] = str(session_path.parent)
    documents = {
        "upper_body": build_document(
            session,
            mode="upper_body",
            home_index=1,
            anchor_indices=range(2, 12),
            capture_count=capture_count,
        ),
        "full_body": build_document(
            session,
            mode="full_body",
            home_index=1,
            anchor_indices=range(12, 23),
            capture_count=capture_count,
        ),
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for mode, document in documents.items():
        path = output_directory / f"frame.{mode}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
        paths[mode] = path
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, default=Path("calibration/rby1"))
    parser.add_argument("--capture-count", type=int, default=30)
    args = parser.parse_args()
    try:
        paths = build_files(args.session, args.output_directory, capture_count=args.capture_count)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    for mode, path in paths.items():
        print(f"{mode}: {path}")


if __name__ == "__main__":
    main()

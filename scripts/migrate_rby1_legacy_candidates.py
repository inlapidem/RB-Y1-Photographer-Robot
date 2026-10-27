"""Extract marked poses from an old continuous RB-Y1 recording.

The legacy file is never modified.  The output is a small, reviewable
``geekseek.rby1.keyposes/v1`` trial file containing only the entry pose and
the marked dwells.  It is intentionally labelled as an unvalidated legacy
candidate: each pose still needs a slow, operator-observed hardware check with
the same phone grasp before it becomes production calibration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SCHEMA = "geekseek.rby1.keyposes/v1"


def _joint_values(value: object, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 7:
        raise ValueError(f"{label}: 오른팔 7축 right_arm_rad가 필요합니다")
    return [float(item) for item in value]


def migrate(document: dict, *, source_sha256: str, source_name: str) -> dict:
    if document.get("schema") == SCHEMA:
        raise ValueError("입력 파일은 이미 keypose 형식입니다")
    segments = document.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("legacy segments가 없습니다")
    entry = segments[0]
    if not isinstance(entry, dict):
        raise ValueError("첫 segment가 객체가 아닙니다")
    home_q = _joint_values(entry.get("right_arm_rad"), "entry/home candidate")

    anchors: list[dict] = []
    labels: set[str] = set()
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or segment.get("kind") != "dwell":
            continue
        label = str(segment.get("label") or f"wp{len(anchors) + 1:02d}")
        if label in labels:
            raise ValueError(f"중복 dwell label입니다: {label}")
        labels.add(label)
        anchors.append(
            {
                "label": label,
                "enabled": True,
                "validation_status": "legacy_candidate",
                "legacy_segment_index": index,
                "right_arm_rad": _joint_values(segment.get("right_arm_rad"), label),
            }
        )
    if len(anchors) < 2:
        raise ValueError("표시된 dwell pose가 최소 2개 필요합니다")

    return {
        "schema": SCHEMA,
        "name": f"legacy trial candidates from {source_name}",
        "provenance": {
            "source_file": source_name,
            "source_sha256": source_sha256,
            "source_recording": document.get("recording"),
            "warning": "phone grasp, camera extrinsics, F/T baseline and safe home are not verified",
        },
        "tool": {
            "grasp_id": "legacy_unknown",
            "camera_transform_status": "uncalibrated",
        },
        "home": {
            "right_arm_rad": home_q,
            "validation_status": "legacy_entry_candidate",
        },
        "anchors": anchors,
        "planning": {
            "capture_count": len(anchors),
            "max_joint_speed_rad_s": 0.10,
            "dwell_seconds": 1.5,
            "min_travel_seconds": 1.0,
            "entry_seconds": 6.0,
            "home_seconds": 6.0,
            "blocked_edges": [],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.out.resolve():
        parser.error("원본 보존을 위해 --out은 --input과 달라야 합니다")
    payload = args.input.read_bytes()
    document = migrate(
        json.loads(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
        source_name=str(args.input),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"legacy 원본 보존: {args.input}\n"
        f"시험 후보 {len(document['anchors'])}개 생성: {args.out}\n"
        "실기 전 각 pose와 home을 동일한 휴대폰 파지 상태에서 검증하세요"
    )


if __name__ == "__main__":
    main()

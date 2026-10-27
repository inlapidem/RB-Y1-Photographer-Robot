import json
import math
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest


def module():
    path = Path(__file__).parents[1] / "scripts" / "build_rby1_shot_keyposes.py"
    spec = spec_from_file_location("build_rby1_shot_keyposes", path)
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def sample(index):
    return {
        "label": f"wp{index:02d}",
        "right_arm_rad": [index / 100] * 7,
        "ee_right_transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "ft_sensor_right": {"force_n": [0, 0, 0], "torque_nm": [0, 0, 0]},
        "reference_image": f"wp{index:02d}.jpg",
        "rgb_image": f"wp{index:02d}_rgb.jpg",
    }


def test_build_files_uses_wp01_as_home_and_splits_two_shot_modes(tmp_path):
    builder = module()
    source = tmp_path / "session.json"
    source.write_text(
        json.dumps(
            {
                "schema": "geekseek.rby1.capture-session/v1",
                "anchors": [sample(index) for index in range(1, 23)],
            }
        ),
        encoding="utf-8",
    )
    paths = builder.build_files(source, tmp_path / "out")
    upper = json.loads(paths["upper_body"].read_text())
    full = json.loads(paths["full_body"].read_text())

    assert upper["home"]["right_arm_rad"] == [0.01] * 7
    assert [item["label"] for item in upper["anchors"]] == [f"wp{i:02d}" for i in range(2, 12)]
    assert [item["label"] for item in full["anchors"]] == [f"wp{i:02d}" for i in range(12, 23)]
    assert upper["planning"]["capture_count"] == 30
    assert full["planning"]["max_joint_speed_rad_s"] == 0.10


def test_outside_encoder_value_is_moved_inside_sdk_limit_with_a_margin():
    builder = module()
    session = {
        "anchors": [sample(index) for index in range(1, 23)],
        "source_directory": "/session",
    }
    session["anchors"][1]["right_arm_rad"][1] = 0.02  # 1.146 deg; SDK max is 1 deg.
    document = builder.build_document(
        session,
        mode="upper_body",
        home_index=1,
        anchor_indices=range(2, 12),
        capture_count=30,
    )
    assert document["anchors"][0]["right_arm_rad"][1] == pytest.approx(math.radians(0.5))
    assert document["source"]["joint_limit_adjustments"][0]["sample"] == "wp02"

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _migration_module():
    path = Path(__file__).parents[1] / "scripts" / "migrate_rby1_legacy_candidates.py"
    spec = spec_from_file_location("migrate_rby1_legacy_candidates", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extracts_only_marked_dwells_and_keeps_entry_as_candidate_home():
    migration = _migration_module()
    q0, q1, q2 = [0.0] * 7, [0.1] * 7, [0.2] * 7
    result = migration.migrate(
        {
            "recording": {"rate_hint_hz": 50},
            "segments": [
                {"kind": "entry", "right_arm_rad": q0},
                {"kind": "travel", "right_arm_rad": [0.05] * 7},
                {"kind": "dwell", "label": "wp1", "right_arm_rad": q1},
                {"kind": "dwell", "label": "wp2", "right_arm_rad": q2},
            ],
        },
        source_sha256="abc",
        source_name="legacy.json",
    )

    assert result["schema"] == "geekseek.rby1.keyposes/v1"
    assert result["home"]["right_arm_rad"] == q0
    assert [anchor["label"] for anchor in result["anchors"]] == ["wp1", "wp2"]
    assert all(anchor["validation_status"] == "legacy_candidate" for anchor in result["anchors"])
    assert result["planning"]["capture_count"] == 2
    assert result["planning"]["max_joint_speed_rad_s"] == 0.10

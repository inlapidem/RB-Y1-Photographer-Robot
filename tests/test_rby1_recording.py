from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _record_module():
    path = Path(__file__).parents[1] / "scripts" / "record_rby1_right_arm.py"
    spec = spec_from_file_location("record_rby1_right_arm", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample(recorder, value: float):
    return recorder.Sample(
        (value,) * 7,
        ((1.0, 0.0, 0.0, value), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)),
        (1.0, 2.0, 3.0),
        (0.1, 0.2, 0.3),
    )


def test_recorder_saves_only_home_and_taught_keyposes():
    recorder = _record_module()
    document = recorder.build_keypose_document(
        "test",
        sample(recorder, 0.0),
        [sample(recorder, 0.1), sample(recorder, 0.2)],
        grasp_id="iphone_fixture_v1",
        phone_orientation="portrait",
        capture_count=5,
    )

    assert document["schema"] == "geekseek.rby1.keyposes/v1"
    assert "segments" not in document
    assert [pose["label"] for pose in document["anchors"]] == ["wp01", "wp02"]
    assert document["anchors"][0]["ft_sensor_right"]["force_n"] == [1.0, 2.0, 3.0]
    assert document["tool"]["camera_transform_status"] == "uncalibrated"

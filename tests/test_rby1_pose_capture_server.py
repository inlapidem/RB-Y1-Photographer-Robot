import json
import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest


def module():
    path = Path(__file__).parents[1] / "scripts" / "rby1_pose_capture_server.py"
    spec = spec_from_file_location("rby1_pose_capture_server", path)
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def snapshot(value: float = 0.1) -> dict:
    return {
        "server_captured_at": "2026-08-17T03:00:00+09:00",
        "right_arm_joint_names": [f"right_arm_{index}" for index in range(7)],
        "right_arm_rad": [value] * 7,
        "right_arm_deg": [value] * 7,
        "right_arm_velocity_rad_s": [0.0] * 7,
        "right_arm_velocity_deg_s": [0.0] * 7,
        "max_speed_deg_s": 0.0,
        "ee_right_transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "ft_sensor_right": {"force_n": [1, 2, 3], "torque_nm": [0.1, 0.2, 0.3]},
        "control_manager": {"state": "State.Enabled", "enabled": 24},
    }


JPEG = b"\xff\xd8payload\xff\xd9"


def test_session_saves_photo_and_encoder_together_and_exports_keyposes(tmp_path):
    server = module()
    session = server.CaptureSession(tmp_path, "robot:50051", "a")
    home = session.save("home", JPEG, snapshot(0.0), "browser-home")
    framing = {"template_id": "full_body", "positioned": True, "stable_frames": 5}
    first = session.save("anchor", JPEG, snapshot(0.1), "browser-one", framing=framing)
    second = session.save("anchor", JPEG, snapshot(0.2), "browser-two")

    assert (tmp_path / home["reference_image"]).read_bytes() == JPEG
    assert first["label"] == "wp01"
    assert second["label"] == "wp02"
    assert first["right_arm_rad"] == [0.1] * 7
    assert first["framing"] == framing
    assert session.summary()["anchor_count"] == 2

    output = session.export_keyposes("phone_fixture_v1", 30)
    keyposes = json.loads(output.read_text(encoding="utf-8"))
    assert keyposes["schema"] == "geekseek.rby1.keyposes/v1"
    assert keyposes["home"]["reference_image"] == home["reference_image"]
    assert [item["label"] for item in keyposes["anchors"]] == ["wp01", "wp02"]
    assert keyposes["planning"]["capture_count"] == 30


def test_rejects_non_jpeg_and_export_without_home(tmp_path):
    server = module()
    session = server.CaptureSession(tmp_path, "robot:50051", "a")
    with pytest.raises(ValueError, match="JPEG"):
        session.save("anchor", b"not-an-image", snapshot(), "browser")
    session.save("anchor", JPEG, snapshot(), "browser")
    session.save("anchor", JPEG, snapshot(), "browser")
    with pytest.raises(ValueError, match="Home"):
        session.export_keyposes("phone_fixture_v1", 30)


def test_laptop_trigger_requests_an_iphone_frame():
    server = module()

    class Socket:
        def __init__(self):
            self.messages = []

        async def send_text(self, value):
            self.messages.append(value)

    async def scenario():
        phone = server.PhoneLink()
        phone.socket = Socket()
        pending = asyncio.create_task(phone.capture(0.5))
        await asyncio.sleep(0)
        assert phone.socket.messages == ["capture"]
        phone.receive_frame(JPEG)
        image, captured_at = await pending
        assert image == JPEG
        assert captured_at

    asyncio.run(scenario())


def test_skeleton_bridge_validates_status_and_jpeg(monkeypatch):
    server = module()
    bridge = server.SkeletonBridge("http://127.0.0.1:8000")

    def read(path):
        if "framing-frame" in path:
            return JPEG, "image/jpeg"
        return json.dumps({"positioned": True}).encode(), "application/json"

    monkeypatch.setattr(bridge, "_read", read)
    assert bridge.status("full_body")["positioned"] is True
    assert bridge.frame("full_body") == JPEG

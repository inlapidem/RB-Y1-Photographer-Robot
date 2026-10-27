import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from geekseek.perception import (
    FakePersonSensor,
    PersonSignal,
    center_crop_to_aspect,
    camera_device_candidates,
    is_approaching,
    is_positioned,
)


class PerceptionTests(unittest.TestCase):
    def test_fake_sensor_replays_scripted_signals(self) -> None:
        sensor = FakePersonSensor(
            [PersonSignal(detected=False), PersonSignal(detected=True, size_ratio=0.2)]
        )
        self.assertFalse(sensor.sense(None).detected)
        self.assertTrue(sensor.sense(None).detected)

    def test_fake_sensor_repeats_last_signal(self) -> None:
        sensor = FakePersonSensor([PersonSignal(detected=True, size_ratio=0.5)])
        sensor.sense(None)
        second = sensor.sense(None)
        self.assertEqual(second.size_ratio, 0.5)

    def test_is_approaching_requires_detection_and_size(self) -> None:
        self.assertFalse(is_approaching(PersonSignal(detected=False, size_ratio=0.9)))
        self.assertFalse(is_approaching(PersonSignal(detected=True, size_ratio=0.05)))
        self.assertTrue(is_approaching(PersonSignal(detected=True, size_ratio=0.2)))

    def test_is_positioned_checks_center_zone(self) -> None:
        centered = PersonSignal(detected=True, center_x=0.5, center_y=0.5)
        off_frame = PersonSignal(detected=True, center_x=0.05, center_y=0.5)
        undetected = PersonSignal(detected=False, center_x=0.5, center_y=0.5)
        self.assertTrue(is_positioned(centered))
        self.assertFalse(is_positioned(off_frame))
        self.assertFalse(is_positioned(undetected))

    def test_center_crop_4_by_3_source_to_3_by_4_without_resize(self) -> None:
        frame = np.zeros((960, 1280, 3), dtype=np.uint8)
        cropped = center_crop_to_aspect(frame, 3, 4)
        self.assertEqual(cropped.shape, (960, 720, 3))
        self.assertTrue(np.shares_memory(frame, cropped))

    def test_center_crop_rejects_invalid_aspect(self) -> None:
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            center_crop_to_aspect(frame, 0, 4)

    def test_camera_name_prefers_stable_capture_stream(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            by_id = root / "by-id"
            by_id.mkdir()
            capture = by_id / "usb-AuditoryWorks_Nuroum_V11_SERIAL-video-index0"
            metadata = by_id / "usb-AuditoryWorks_Nuroum_V11_SERIAL-video-index1"
            capture.symlink_to("../../video7")
            metadata.symlink_to("../../video8")

            candidates = camera_device_candidates(
                "Nuroum V11", 3, by_id_dir=by_id, sysfs_dir=root / "missing"
            )

            self.assertEqual(candidates, (capture,))

    def test_camera_name_falls_back_to_matching_sysfs_devices(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sysfs = root / "sysfs"
            (sysfs / "video4").mkdir(parents=True)
            (sysfs / "video5").mkdir()
            (sysfs / "video4" / "name").write_text("Nuroum V11: Nuroum V11\n")
            (sysfs / "video5" / "name").write_text("Other Camera\n")

            candidates = camera_device_candidates(
                "Nuroum V11",
                3,
                by_id_dir=root / "missing",
                sysfs_dir=sysfs,
                dev_dir=Path("/devices"),
            )

            self.assertEqual(candidates, (Path("/devices/video4"),))

    def test_camera_index_remains_backward_compatible_without_name(self) -> None:
        self.assertEqual(camera_device_candidates("", 6), (6,))


if __name__ == "__main__":
    unittest.main()

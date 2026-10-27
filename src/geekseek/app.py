from __future__ import annotations

import logging
from pathlib import Path

from .capture import FakeCapture, WebAppCapture
from .config import AppConfig
from .coordinator import Coordinator
from .framing_guide import load_templates_from_dataset, load_templates_from_samples_csv
from .gallery import Gallery
from .perception import FakePersonSensor, MediaPipePersonSensor, WebcamFrameSource
from .robot import FakeRobot, PhorceRobot, Rby1Robot, RvizRobot
from .vlm import ClaudeGreeter

PHOTOS_DIR = Path(__file__).resolve().parents[2] / "photos"


REPO_ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger(__name__)


def build_robot(config: AppConfig):
    if config.runtime.robot == "phorce":
        return PhorceRobot(
            motion_ids=config.runtime.phorce_motion_ids,
            slot_dir=REPO_ROOT / config.runtime.phorce_slot_dir,
            timeout_seconds=config.runtime.phorce_timeout_seconds,
            busy_retries=config.runtime.phorce_busy_retries,
            busy_retry_seconds=config.runtime.phorce_busy_retry_seconds,
        )
    if config.runtime.robot == "rby1":
        if not config.runtime.rby1_address:
            raise ValueError("robot: rby1에는 runtime.rby1_address가 필요합니다")
        return Rby1Robot(
            address=config.runtime.rby1_address,
            model=config.runtime.rby1_model,
            command_priority=config.runtime.rby1_command_priority,
            speed_ratio=config.runtime.rby1_speed_ratio,
            acceleration_ratio=config.runtime.rby1_acceleration_ratio,
            min_travel_seconds=config.runtime.rby1_min_travel_seconds,
            moving_shot_interval=config.runtime.rby1_moving_shot_interval,
            trajectories={
                pose: REPO_ROOT / trajectory
                for pose, trajectory in config.runtime.rby1_trajectories.items()
            },
        )
    if config.runtime.robot == "rviz":
        return RvizRobot(config.runtime.move_seconds)
    return FakeRobot(config.runtime.move_seconds)


def build_gallery(config: AppConfig) -> Gallery:
    return Gallery(base_url=config.runtime.gallery_base_url)


def build_coordinator(config: AppConfig, gallery: Gallery | None = None) -> Coordinator:
    robot = build_robot(config)
    capture = (
        WebAppCapture(PHOTOS_DIR)
        if config.runtime.capture == "webapp"
        else FakeCapture(config.runtime.capture_seconds)
    )

    person_sensor = None
    frame_source = None
    framing_templates = {}
    live_frame_interval = 1 / config.runtime.camera_fps
    if config.runtime.person_sensor == "mediapipe":
        person_sensor = MediaPipePersonSensor()
        try:
            if config.runtime.framing_samples_csv:
                framing_templates = load_templates_from_samples_csv(
                    REPO_ROOT / config.runtime.framing_samples_csv,
                    radius_scale=config.runtime.framing_radius_scale,
                )
            else:
                dataset_dir = REPO_ROOT / config.runtime.framing_dataset_dir
                framing_templates = load_templates_from_dataset(person_sensor, dataset_dir)
            LOGGER.info(
                "framing templates loaded (radius scale %.2f): %s",
                config.runtime.framing_radius_scale,
                {mode: template.sample_count for mode, template in framing_templates.items()},
            )
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.warning("framing templates unavailable; using center-only guide: %s", exc)
        frame_source = WebcamFrameSource(
            config.runtime.camera_index,
            config.runtime.camera_fps,
            config.runtime.camera_width,
            config.runtime.camera_height,
            config.runtime.camera_fourcc,
            config.runtime.camera_device_fps,
            camera_name=config.runtime.camera_name,
        )
    elif config.runtime.person_sensor == "fake":
        person_sensor = FakePersonSensor()

    greeter = ClaudeGreeter() if config.runtime.vlm_enabled else None

    return Coordinator(
        robot=robot,
        capture=capture,
        person_sensor=person_sensor,
        frame_source=frame_source,
        greeter=greeter,
        sense_interval=config.runtime.sense_interval,
        live_frame_interval=live_frame_interval,
        greeting_seconds=config.runtime.greeting_seconds,
        preview_seconds=config.runtime.preview_seconds,
        asking_seconds=config.runtime.asking_seconds,
        gesture_hold_seconds=config.runtime.gesture_hold_seconds,
        farewell_seconds=config.runtime.farewell_seconds,
        slide_seconds=config.runtime.slide_seconds,
        gallery=gallery if gallery is not None else build_gallery(config),
        photo_target_count=config.runtime.photo_target_count,
        framing_templates=framing_templates,
    )

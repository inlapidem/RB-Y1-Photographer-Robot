from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RuntimeConfig:
    robot: str = "fake"
    capture: str = "fake"
    move_seconds: float = 0.2
    capture_seconds: float = 0.2
    person_sensor: str = "fake"
    camera_index: int = 0
    camera_name: str = ""
    camera_fps: float = 15.0
    # Some UVC cameras only negotiate MJPEG at 30/60fps. Capture at a
    # supported device rate while camera_fps still caps processing/preview.
    camera_device_fps: float | None = None
    camera_width: int = 1280
    camera_height: int = 960
    camera_fourcc: str = "MJPG"
    framing_dataset_dir: str = "calibration/webcam_skeleton_1280x960"
    framing_samples_csv: str = ""
    framing_radius_scale: float = 2.0
    sense_interval: float = 0.2
    greeting_seconds: float = 3.0
    preview_seconds: float = 3.0
    # 결과와 QR을 이 시간 동안 보여준 뒤 손님 입력 없이 자동으로 종료한다.
    asking_seconds: float = 30.0
    gesture_hold_seconds: float = 1.0
    farewell_seconds: float = 4.0
    # >0이면 미리보기를 '사진 장수 × 이 값'만큼 늘려 전부 한 번씩 보여준다.
    slide_seconds: float = 0.0
    vlm_enabled: bool = False
    debug_window: bool = False
    # robot: phorce — 구도 이름을 SD카드 슬롯 번호로 잇는다. 슬롯은
    # scripts/build_motion_slots.py가 만들고 slot_dir에 스케줄을 남긴다.
    phorce_motion_ids: dict[str, int] = field(default_factory=dict)
    phorce_slot_dir: str = "calibration/slots"
    # 슬롯 길이(28초)보다 넉넉해야 한다 — phorce 기본값 30초는 경계에 걸린다.
    phorce_timeout_seconds: float = 45.0
    phorce_busy_retries: int = 2
    phorce_busy_retry_seconds: float = 1.0
    # robot: rby1 — Phorce의 관절값을 복사하지 않고, 같은 카메라 경로를
    # RB-Y1 오른팔 7축(rad)으로 보정한 trajectory JSON을 실행한다.
    rby1_address: str = ""
    rby1_model: str = "a"
    rby1_command_priority: int = 1
    # Hardware joint-limit ratios.  RB-Y1 operation is capped at 70% even if
    # a larger value is supplied accidentally.
    rby1_speed_ratio: float | None = None
    rby1_acceleration_ratio: float = 0.5
    rby1_min_travel_seconds: float = 0.25
    rby1_moving_shot_interval: float = 0.35
    rby1_trajectories: dict[str, str] = field(default_factory=dict)
    photo_target_count: int = 40
    # 손님용 갤러리. base_url이 비어 있으면 갤러리도 QR도 뜨지 않는다 —
    # 기존 설정들은 이 값이 없으므로 동작이 그대로다.
    gallery_base_url: str = ""
    gallery_port: int = 8080


@dataclass(frozen=True)
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    ssl_keyfile: str | None = None
    ssl_certfile: str | None = None


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig = RuntimeConfig()
    web: WebConfig = WebConfig()


def load_config(path: str | Path) -> AppConfig:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AppConfig(
        runtime=RuntimeConfig(**raw.get("runtime", {})),
        web=WebConfig(**raw.get("web", {})),
    )

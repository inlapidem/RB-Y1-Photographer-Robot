from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class State(str, Enum):
    BOOTING = "booting"
    WAITING = "waiting"  # 1단계: 사람이 지나다니고 있음
    GREETING = "greeting"  # 2단계: 사람이 가까이 다가옴
    DECIDING = "deciding"  # 3단계: 촬영 여부/구도 결정
    GUIDING = "guiding"  # 4단계: 촬영 시작, 위치로 유도
    CAPTURING = "capturing"  # 5단계: 정위치 도달, 버스트 촬영
    PREVIEWING = "previewing"  # 6단계: 촬영 끝, 슬라이드로 보여줌
    ASKING = "asking"  # 7단계: 마음에 드는지 확인
    FAREWELL = "farewell"  # 8단계: 인사, 자리 뜸
    ERROR = "error"


class EventType(str, Enum):
    SYSTEM_READY = "system_ready"
    PERSON_APPROACHED = "person_approached"
    GREETING_DONE = "greeting_done"
    CAPTURE_STARTED = "capture_started"
    DECLINED = "declined"
    POSITION_REACHED = "position_reached"
    BURST_COMPLETE = "burst_complete"
    CAPTURE_FAILED = "capture_failed"
    PREVIEW_DONE = "preview_done"
    REPLAY_REQUESTED = "replay_requested"
    PHOTO_LIKED = "photo_liked"
    FAREWELL_DONE = "farewell_done"
    RESET_REQUESTED = "reset_requested"


@dataclass(frozen=True)
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowContext:
    state: State = State.BOOTING
    template_id: str | None = None
    photos: list[str] = field(default_factory=list)
    hint: str = ""
    error: str = ""
    greeting_line: str | None = None  # VLM 개인화 인사말 (2단계), 없으면 프런트가 기본 캡션 사용
    countdown: int | None = None  # 5단계 촬영 시작 전 3-2-1 카운트다운, 끝나면 None
    awaiting_ready: bool = False  # 5단계 정위치 도달 후, 카운트다운 전 "손 들어 준비완료" 대기 중
    photo_target: int = 0  # 이번 촬영에서 예상되는 장수 — 진행률 표시용 (0이면 미정)
    gallery_url: str = ""  # 손님이 QR로 여는 사진 페이지 (갤러리 비활성이면 빈 값)
    framing_message: str = "사람을 기다리는 중"
    framing_direction: str = "detect"
    framing_scale: float = 0.0
    framing_inside: int = 0
    framing_required: int = 0
    framing_positioned: bool = False
    gesture_choice: str = ""
    gesture_progress: float = 0.0
    revision: int = 0

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


class InvalidTransition(ValueError):
    pass


_TRANSITIONS: dict[tuple[State, EventType], State] = {
    (State.BOOTING, EventType.SYSTEM_READY): State.WAITING,
    (State.WAITING, EventType.PERSON_APPROACHED): State.GREETING,
    (State.GREETING, EventType.GREETING_DONE): State.DECIDING,
    (State.DECIDING, EventType.CAPTURE_STARTED): State.GUIDING,
    (State.DECIDING, EventType.DECLINED): State.WAITING,
    (State.GUIDING, EventType.POSITION_REACHED): State.CAPTURING,
    (State.CAPTURING, EventType.BURST_COMPLETE): State.PREVIEWING,
    (State.CAPTURING, EventType.CAPTURE_FAILED): State.ERROR,
    (State.PREVIEWING, EventType.PREVIEW_DONE): State.ASKING,
    (State.ASKING, EventType.REPLAY_REQUESTED): State.PREVIEWING,
    (State.ASKING, EventType.PHOTO_LIKED): State.FAREWELL,
    (State.FAREWELL, EventType.FAREWELL_DONE): State.WAITING,
    (State.ERROR, EventType.RESET_REQUESTED): State.WAITING,
}


def apply_event(context: WorkflowContext, event: Event) -> None:
    """Apply one valid event. The coordinator is the sole caller in production."""
    try:
        next_state = _TRANSITIONS[(context.state, event.type)]
    except KeyError as exc:
        raise InvalidTransition(f"{event.type.value} is invalid in {context.state.value}") from exc

    if event.type is EventType.CAPTURE_STARTED:
        template_id = str(event.data.get("template_id", "")).strip()
        if not template_id:
            raise InvalidTransition("template_id is required")
        context.template_id = template_id
        context.photos = []
        context.hint = ""
        context.framing_message = "몸을 실루엣에 맞춰주세요"
        context.framing_direction = "detect"
        context.framing_scale = 0.0
        context.framing_inside = 0
        context.framing_required = 0
        context.framing_positioned = False
        context.gesture_choice = ""
        context.gesture_progress = 0.0
    elif event.type is EventType.BURST_COMPLETE:
        context.photos = list(event.data.get("photos", []))
    elif event.type is EventType.CAPTURE_FAILED:
        context.error = str(event.data.get("reason", "unknown error"))
    elif event.type in (EventType.DECLINED, EventType.FAREWELL_DONE, EventType.RESET_REQUESTED):
        context.template_id = None
        context.photos = []
        context.hint = ""
        context.error = ""
        context.greeting_line = None
        context.countdown = None
        context.awaiting_ready = False
        context.gallery_url = ""
        context.framing_message = "사람을 기다리는 중"
        context.framing_direction = "detect"
        context.framing_scale = 0.0
        context.framing_inside = 0
        context.framing_required = 0
        context.framing_positioned = False
        context.gesture_choice = ""
        context.gesture_progress = 0.0

    context.state = next_state
    context.revision += 1

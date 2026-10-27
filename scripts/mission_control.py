"""Pygame "mission control" dashboard for watching the kiosk's workflow
progress in real time — separate from the cv2 webcam-skeleton debug window
(scripts/watch_debug_webcam.py). Built with pygame instead of cv2.imshow
because a proper stats layout (stage, history, live camera panel) needs real
UI drawing, not just "show one image".

Pulls state over SSE (/events) and a small camera thumbnail over HTTP
(/debug/webcam) — both over the network, so it never touches the webcam
device directly (the server already owns it).

Usage:
  python scripts/mission_control.py [server-url]
  (default: https://127.0.0.1:8443)

  --screenshot <path>   render once, save a PNG, and exit (for automated checks)

Press 'q'/Esc or close the window to quit.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from datetime import datetime

import cv2
import httpx
import numpy as np
import pygame

STAGE_NAMES: dict[str, tuple[int, str]] = {
    "booting": (0, "부팅 중"),
    "waiting": (1, "사람을 기다리는 중"),
    "greeting": (2, "가까이 온 사람을 발견"),
    "deciding": (3, "촬영 여부와 구도 선택"),
    "guiding": (4, "촬영 위치 안내"),
    "capturing": (5, "여러 구도로 촬영 중"),
    "previewing": (6, "촬영 결과 미리보기"),
    "asking": (7, "반응을 기다리는 중"),
    "farewell": (8, "인사 후 자리 뜸"),
    "error": (-1, "오류"),
}

BG = (10, 12, 20)
PANEL = (18, 22, 36)
ACCENT = (110, 130, 255)
GREEN = (100, 220, 150)
RED = (240, 100, 120)
WHITE = (240, 242, 250)
GRAY = (140, 148, 170)
DOT_OFF = (58, 64, 88)

# pygame's default font has no Hangul glyphs — fall back through a few common
# CJK-capable fonts so Korean labels don't render as tofu boxes.
KOREAN_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "C:/Windows/Fonts/malgun.ttf",
]


def load_font(size: int) -> pygame.font.Font:
    for path in KOREAN_FONT_CANDIDATES:
        try:
            return pygame.font.Font(path, size)
        except OSError:
            continue
    print("[mission_control] no Korean-capable font found — Hangul will render as boxes", file=sys.stderr)
    return pygame.font.SysFont(None, size)


class SharedState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.context: dict[str, object] = {"state": "booting"}
        self.connected = False
        self.history: deque[tuple[str, str]] = deque(maxlen=20)
        self._last_state: str | None = None
        self.camera_frame: np.ndarray | None = None

    def apply_snapshot(self, data: dict[str, object]) -> None:
        with self.lock:
            self.context = data
            state = data.get("state")
            if state != self._last_state:
                self._last_state = state
                self.history.append((datetime.now().strftime("%H:%M:%S"), str(state)))

    def set_camera_frame(self, frame: np.ndarray) -> None:
        with self.lock:
            self.camera_frame = frame

    def snapshot(self) -> tuple[dict[str, object], bool, list[tuple[str, str]], np.ndarray | None]:
        with self.lock:
            frame = self.camera_frame.copy() if self.camera_frame is not None else None
            return dict(self.context), self.connected, list(self.history), frame


def sse_worker(base_url: str, shared: SharedState) -> None:
    url = f"{base_url}/events"
    while True:
        try:
            with httpx.Client(verify=False, timeout=None) as client:
                with client.stream("GET", url) as response:
                    shared.connected = True
                    event_type = None
                    for line in response.iter_lines():
                        if line.startswith("event:"):
                            event_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:") and event_type == "state":
                            try:
                                shared.apply_snapshot(json.loads(line.split(":", 1)[1].strip()))
                            except json.JSONDecodeError:
                                pass
        except Exception:
            shared.connected = False
            time.sleep(1.0)


def camera_worker(base_url: str, shared: SharedState) -> None:
    url = f"{base_url}/debug/webcam"
    buffer = b""
    while True:
        try:
            with httpx.Client(verify=False, timeout=10.0) as client:
                with client.stream("GET", url) as response:
                    for chunk in response.iter_bytes():
                        buffer += chunk
                        while True:
                            start = buffer.find(b"\xff\xd8")
                            end = buffer.find(b"\xff\xd9", start)
                            if start == -1 or end == -1:
                                break
                            jpeg = buffer[start : end + 2]
                            buffer = buffer[end + 2 :]
                            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                            if frame is not None:
                                shared.set_camera_frame(frame)
        except Exception:
            time.sleep(1.0)


def frame_to_surface(frame_bgr: np.ndarray) -> pygame.Surface:
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    # pygame surfarray is (width, height, channels) — transpose from cv2's
    # (height, width, channels), do not rotate.
    return pygame.surfarray.make_surface(np.transpose(frame_rgb, (1, 0, 2)))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base_url = args[0] if args else "https://127.0.0.1:8443"
    screenshot_path = None
    if "--screenshot" in sys.argv:
        screenshot_path = sys.argv[sys.argv.index("--screenshot") + 1]

    shared = SharedState()
    threading.Thread(target=sse_worker, args=(base_url, shared), daemon=True).start()
    threading.Thread(target=camera_worker, args=(base_url, shared), daemon=True).start()

    pygame.init()
    pygame.display.set_caption("geekseek mission control")
    screen = pygame.display.set_mode((980, 640))
    clock = pygame.time.Clock()
    font_big = load_font(46)
    font_mid = load_font(24)
    font_small = load_font(19)

    running = True
    frame_count = 0
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN and event.key in (pygame.K_q, pygame.K_ESCAPE):
                running = False

        context, connected, history, cam_frame = shared.snapshot()
        state = str(context.get("state", "booting"))
        stage_num, stage_label = STAGE_NAMES.get(state, (0, state))

        screen.fill(BG)

        # Header
        pygame.draw.circle(screen, GREEN if connected else RED, (30, 28), 8)
        screen.blit(font_mid.render("GEEKSEEK MISSION CONTROL", True, WHITE), (50, 16))
        screen.blit(font_small.render("SSE 연결됨" if connected else "연결 끊김 · 재시도 중", True, GREEN if connected else RED), (50, 42))

        # Stage panel
        pygame.draw.rect(screen, PANEL, (24, 68, 560, 150), border_radius=14)
        stage_tag = f"{stage_num}단계" if stage_num > 0 else ("오류" if stage_num < 0 else "-")
        screen.blit(font_small.render(stage_tag, True, ACCENT), (44, 84))
        screen.blit(font_big.render(stage_label, True, WHITE), (44, 106))
        screen.blit(font_small.render(f"state={state}  revision={context.get('revision', 0)}", True, GRAY), (44, 168))

        # 8-dot stepper
        for i in range(1, 9):
            cx, cy = 44 + (i - 1) * 66, 198
            pygame.draw.circle(screen, ACCENT if i == stage_num else DOT_OFF, (cx, cy), 9)

        # Data panel
        pygame.draw.rect(screen, PANEL, (24, 234, 560, 150), border_radius=14)
        photos = context.get("photos") or []
        rows = [
            f"template_id: {context.get('template_id') or '-'}",
            f"photos: {len(photos)} / 3",
            f"greeting_line: {context.get('greeting_line') or '-'}",
        ]
        for i, row in enumerate(rows):
            screen.blit(font_small.render(row, True, WHITE), (44, 254 + i * 27))
        hint_or_error = context.get("error") or context.get("hint")
        if hint_or_error:
            color = RED if context.get("error") else GRAY
            screen.blit(font_small.render(str(hint_or_error), True, color), (44, 254 + 3 * 27))

        # History panel
        pygame.draw.rect(screen, PANEL, (24, 402, 560, 214), border_radius=14)
        screen.blit(font_small.render("최근 상태 변화", True, ACCENT), (44, 416))
        for i, (ts, st) in enumerate(reversed(history[-7:])):
            screen.blit(font_small.render(f"{ts}   {st}", True, GRAY), (44, 444 + i * 24))

        # Camera panel
        cam_rect = pygame.Rect(604, 68, 352, 548)
        pygame.draw.rect(screen, PANEL, cam_rect, border_radius=14)
        if cam_frame is not None:
            surf = frame_to_surface(cam_frame)
            surf = pygame.transform.smoothscale(surf, (cam_rect.width - 16, cam_rect.height - 16))
            screen.blit(surf, (cam_rect.x + 8, cam_rect.y + 8))
        else:
            screen.blit(font_small.render("웹캠 연결 대기 중...", True, GRAY), (cam_rect.x + 20, cam_rect.y + 20))

        pygame.display.flip()

        frame_count += 1
        if screenshot_path and frame_count == 10:
            pygame.image.save(screen, screenshot_path)
            running = False

        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()

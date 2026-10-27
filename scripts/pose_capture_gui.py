"""Pygame live iPhone preview and robot pose capture operator."""

from __future__ import annotations

import argparse
import io
import time
from pathlib import Path

import httpx
import pygame


BACKGROUND = (13, 17, 25)
PANEL = (26, 33, 47)
TEXT = (235, 240, 250)
MUTED = (157, 168, 190)
GREEN = (67, 211, 144)
RED = (255, 107, 112)
CYAN = (84, 196, 255)


def load_font(size: int) -> pygame.font.Font:
    candidates = (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return pygame.font.Font(str(candidate), size)
    return pygame.font.Font(None, size)


def fit_surface(surface: pygame.Surface, bounds: pygame.Rect) -> tuple[pygame.Surface, pygame.Rect]:
    scale = min(bounds.width / surface.get_width(), bounds.height / surface.get_height())
    size = (
        max(1, round(surface.get_width() * scale)),
        max(1, round(surface.get_height() * scale)),
    )
    scaled = pygame.transform.smoothscale(surface, size)
    return scaled, scaled.get_rect(center=bounds.center)


def draw_thirds_grid(screen: pygame.Surface, bounds: pygame.Rect) -> None:
    overlay = pygame.Surface(bounds.size, pygame.SRCALPHA)
    color = (255, 255, 255, 115)
    for fraction in (1 / 3, 2 / 3):
        x = round(bounds.width * fraction)
        y = round(bounds.height * fraction)
        pygame.draw.line(overlay, color, (x, 0), (x, bounds.height), 1)
        pygame.draw.line(overlay, color, (0, y), (bounds.width, y), 1)
    screen.blit(overlay, bounds.topleft)


def detail_from_response(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    return str(body.get("detail", body))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="https://127.0.0.1:8454")
    parser.add_argument("--preview-fps", type=float, default=10.0)
    args = parser.parse_args()

    pygame.init()
    pygame.display.set_caption("GeekSeek Pose Capture")
    screen = pygame.display.set_mode((1180, 760), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    title_font = load_font(30)
    body_font = load_font(21)
    small_font = load_font(17)

    client = httpx.Client(base_url=args.server, verify=False, timeout=2.0)
    frame: pygame.Surface | None = None
    status_data: dict[str, object] = {}
    message = "iPhone과 PCM 피드백을 기다리는 중…"
    message_color = MUTED
    last_frame_poll = 0.0
    last_status_poll = 0.0
    saved_flash_until = 0.0
    running = True

    while running:
        now = time.monotonic()
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
            ):
                running = False
            elif (
                event.type == pygame.KEYDOWN
                and event.key == pygame.K_SPACE
                and not getattr(event, "repeat", False)
            ):
                try:
                    response = client.post("/api/capture-latest")
                    if response.is_error:
                        message = detail_from_response(response)
                        message_color = RED
                    else:
                        captured = response.json()
                        message = f"CSV 저장 완료: {captured['iphone_image']}"
                        message_color = GREEN
                        saved_flash_until = now + 0.35
                except httpx.HTTPError as exc:
                    message = f"저장 요청 실패: {exc}"
                    message_color = RED

        if now - last_frame_poll >= 1.0 / max(args.preview_fps, 1.0):
            last_frame_poll = now
            try:
                response = client.get("/api/live-frame")
                if response.is_success:
                    frame = pygame.image.load(io.BytesIO(response.content), "live.jpg").convert()
            except (httpx.HTTPError, pygame.error):
                pass

        if now - last_status_poll >= 0.2:
            last_status_poll = now
            try:
                response = client.get("/api/status")
                if response.is_success:
                    status_data = response.json()
            except httpx.HTTPError:
                status_data = {}

        width, height = screen.get_size()
        side_width = min(390, max(320, width // 3))
        video_rect = pygame.Rect(20, 70, width - side_width - 50, height - 155)
        side_rect = pygame.Rect(width - side_width - 15, 20, side_width, height - 40)

        screen.fill(BACKGROUND)
        screen.blit(title_font.render("iPhone Live · Robot Pose", True, TEXT), (20, 20))
        pygame.draw.rect(screen, PANEL, video_rect, border_radius=12)
        if frame is not None:
            scaled, destination = fit_surface(frame, video_rect.inflate(-12, -12))
            screen.blit(scaled, destination)
            draw_thirds_grid(screen, destination)
        else:
            waiting = body_font.render("iPhone 실시간 프레임 대기 중", True, MUTED)
            screen.blit(waiting, waiting.get_rect(center=video_rect.center))
        if now < saved_flash_until:
            pygame.draw.rect(screen, GREEN, video_rect, width=8, border_radius=12)

        pygame.draw.rect(screen, PANEL, side_rect, border_radius=12)
        x = side_rect.x + 20
        y = side_rect.y + 20
        phone_ok = bool(status_data.get("phone_frame_ready"))
        feedback_ok = bool(status_data.get("feedback_ready"))
        webcam_status = status_data.get("webcam")
        webcam_ok = isinstance(webcam_status, dict) and bool(webcam_status.get("ready"))
        for label, ok in (
            ("iPhone live", phone_ok),
            ("PCM feedback", feedback_ok),
            ("Webcam skeleton", webcam_ok),
        ):
            pygame.draw.circle(screen, GREEN if ok else RED, (x + 7, y + 11), 6)
            screen.blit(body_font.render(label, True, TEXT), (x + 22, y))
            y += 34

        y += 10
        screen.blit(body_font.render("Zero-adjusted joint angles", True, CYAN), (x, y))
        y += 38
        feedback = status_data.get("feedback")
        if isinstance(feedback, dict):
            indices = feedback.get("axis_indices", [])
            raw_positions = feedback.get("position_deg", [])
            positions = feedback.get("zeroed_position_deg", raw_positions)
            velocities = feedback.get("velocity_deg_s", [])
            valid = feedback.get("valid", [])
            for index, position, raw, velocity, is_valid in zip(
                indices, positions, raw_positions, velocities, valid
            ):
                color = TEXT if is_valid else RED
                line = f"axis[{index}] {position:7.2f}°  raw {raw:7.2f}°"
                screen.blit(body_font.render(line, True, color), (x, y))
                y += 32
        else:
            screen.blit(body_font.render("피드백 없음", True, RED), (x, y))
            y += 32

        y = min(y + 25, side_rect.bottom - 135)
        screen.blit(small_font.render(message, True, message_color), (x, y))

        bottom = pygame.Rect(20, height - 68, width - side_width - 50, 48)
        pygame.draw.rect(screen, (32, 42, 60), bottom, border_radius=10)
        controls = body_font.render(
            "SPACE  5DOF + iPhone + Webcam 저장     ESC  종료",
            True,
            TEXT,
        )
        screen.blit(controls, controls.get_rect(center=bottom.center))

        pygame.display.flip()
        clock.tick(30)

    client.close()
    pygame.quit()


if __name__ == "__main__":
    main()

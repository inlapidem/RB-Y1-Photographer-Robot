"""Pygame live webcam/skeleton preview; Space saves the matched JPEG pair."""

from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import datetime
from pathlib import Path

import pygame

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geekseek.perception import (  # noqa: E402
    MediaPipePersonSensor,
    WebcamFrameSource,
    encode_jpeg,
    is_approaching,
    is_positioned,
)


BACKGROUND = (13, 17, 25)
PANEL = (26, 33, 47)
TEXT = (235, 240, 250)
MUTED = (157, 168, 190)
GREEN = (67, 211, 144)
RED = (255, 107, 112)
CYAN = (84, 196, 255)


def save_pair(
    output_dir: Path,
    webcam_jpeg: bytes,
    skeleton_jpeg: bytes,
    *,
    captured_at: datetime | None = None,
) -> tuple[Path, Path]:
    if not webcam_jpeg or not skeleton_jpeg:
        raise ValueError("webcam과 skeleton 이미지가 모두 준비되어야 합니다")
    timestamp = captured_at or datetime.now().astimezone()
    stem = timestamp.strftime("webcam_%Y%m%d_%H%M%S_%f")
    webcam_path = output_dir / f"{stem}_webcam.jpg"
    skeleton_path = output_dir / f"{stem}_skeleton.jpg"
    output_dir.mkdir(parents=True, exist_ok=True)
    webcam_path.write_bytes(webcam_jpeg)
    skeleton_path.write_bytes(skeleton_jpeg)
    return webcam_path, skeleton_path


def load_font(size: int) -> pygame.font.Font:
    for path in (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ):
        if path.exists():
            return pygame.font.Font(str(path), size)
    return pygame.font.Font(None, size)


def jpeg_surface(data: bytes) -> pygame.Surface:
    return pygame.image.load(io.BytesIO(data), "frame.jpg").convert()


def fit_surface(surface: pygame.Surface, bounds: pygame.Rect) -> tuple[pygame.Surface, pygame.Rect]:
    scale = min(bounds.width / surface.get_width(), bounds.height / surface.get_height())
    size = (
        max(1, round(surface.get_width() * scale)),
        max(1, round(surface.get_height() * scale)),
    )
    scaled = pygame.transform.smoothscale(surface, size)
    return scaled, scaled.get_rect(center=bounds.center)


def draw_preview(
    screen: pygame.Surface,
    bounds: pygame.Rect,
    title: str,
    surface: pygame.Surface | None,
    title_font: pygame.font.Font,
    body_font: pygame.font.Font,
) -> None:
    pygame.draw.rect(screen, PANEL, bounds, border_radius=12)
    screen.blit(title_font.render(title, True, CYAN), (bounds.x + 16, bounds.y + 12))
    image_bounds = pygame.Rect(bounds.x + 12, bounds.y + 55, bounds.width - 24, bounds.height - 67)
    if surface is None:
        waiting = body_font.render("웹캠 프레임 대기 중", True, MUTED)
        screen.blit(waiting, waiting.get_rect(center=image_bounds.center))
        return
    scaled, destination = fit_surface(surface, image_bounds)
    screen.blit(scaled, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-fps", type=float, default=15.0)
    parser.add_argument("--inference-fps", type=float, default=10.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "calibration" / "webcam_skeleton_1280x960",
    )
    args = parser.parse_args()

    source = WebcamFrameSource(
        camera_index=args.camera_index,
        target_fps=args.camera_fps,
        frame_width=args.width,
        frame_height=args.height,
    )
    try:
        sensor = MediaPipePersonSensor()
    except Exception:
        source.close()
        raise

    pygame.init()
    pygame.display.set_caption("GeekSeek Webcam + Skeleton Capture")
    screen = pygame.display.set_mode((1380, 760), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    title_font = load_font(25)
    body_font = load_font(20)
    small_font = load_font(16)

    webcam_surface: pygame.Surface | None = None
    skeleton_surface: pygame.Surface | None = None
    webcam_jpeg = b""
    skeleton_jpeg = b""
    detected = False
    last_inference = 0.0
    message = "Space를 누르면 현재 두 이미지를 함께 저장합니다"
    message_color = MUTED
    saved_flash_until = 0.0
    first_frame_reported = False
    running = True

    try:
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
                        webcam_path, skeleton_path = save_pair(
                            args.output_dir,
                            webcam_jpeg,
                            skeleton_jpeg,
                        )
                        message = f"저장 완료: {webcam_path.stem.removesuffix('_webcam')}"
                        message_color = GREEN
                        saved_flash_until = now + 0.35
                        print(f"saved: {webcam_path} + {skeleton_path}", flush=True)
                    except ValueError as exc:
                        message = str(exc)
                        message_color = RED

            if now - last_inference >= 1.0 / max(args.inference_fps, 0.1):
                frame = source()
                if frame is not None:
                    last_inference = now
                    frame = frame.copy()
                    signal = sensor.sense(frame)
                    next_webcam = encode_jpeg(frame)
                    next_skeleton = sensor.annotate_jpeg(
                        frame,
                        signal,
                        is_approaching(signal),
                        is_positioned(signal),
                    )
                    if next_webcam and next_skeleton:
                        webcam_jpeg = next_webcam
                        skeleton_jpeg = next_skeleton
                        webcam_surface = jpeg_surface(webcam_jpeg)
                        skeleton_surface = jpeg_surface(skeleton_jpeg)
                        detected = signal.detected
                        if not first_frame_reported:
                            print(
                                f"ready: camera={args.camera_index} {frame.shape[1]}x{frame.shape[0]} "
                                f"delegate={sensor.delegate_name}",
                                flush=True,
                            )
                            first_frame_reported = True

            width, height = screen.get_size()
            screen.fill(BACKGROUND)
            gap = 16
            footer_height = 86
            panel_width = (width - gap * 3) // 2
            panel_height = height - footer_height - gap * 2
            left = pygame.Rect(gap, gap, panel_width, panel_height)
            right = pygame.Rect(left.right + gap, gap, panel_width, panel_height)
            draw_preview(screen, left, "Webcam 원본", webcam_surface, title_font, body_font)
            draw_preview(screen, right, "MediaPipe Skeleton", skeleton_surface, title_font, body_font)

            if now < saved_flash_until:
                pygame.draw.rect(screen, GREEN, left, width=7, border_radius=12)
                pygame.draw.rect(screen, GREEN, right, width=7, border_radius=12)

            status = (
                f"camera {args.camera_index} · {args.width}×{args.height} · "
                f"delegate {sensor.delegate_name} · person {'YES' if detected else 'NO'} · "
                f"inference {sensor.inference_fps:.1f} fps"
            )
            screen.blit(small_font.render(status, True, TEXT), (gap + 4, height - 72))
            screen.blit(small_font.render(message, True, message_color), (gap + 4, height - 47))
            controls = body_font.render("SPACE  두 이미지 저장     ESC  종료", True, TEXT)
            screen.blit(controls, controls.get_rect(right=width - gap, centery=height - 48))
            pygame.display.flip()
            clock.tick(30)
    finally:
        sensor.close()
        source.close()
        pygame.quit()


if __name__ == "__main__":
    main()

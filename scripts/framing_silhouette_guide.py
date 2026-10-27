"""Live Pygame guide that matches a guest skeleton to labeled silhouettes."""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import pygame

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geekseek.framing_guide import (  # noqa: E402
    FULL_BODY,
    MODE_JOINTS,
    MODE_OUTLINE,
    MODE_SEGMENTS,
    UPPER_BODY,
    FramingGuidance,
    JointPoint,
    SilhouetteTemplate,
    build_template,
    evaluate_framing,
    visible_points,
)
from geekseek.perception import MediaPipePersonSensor, WebcamFrameSource, encode_jpeg  # noqa: E402


BACKGROUND = (13, 17, 25)
PANEL = (26, 33, 47)
TEXT = (235, 240, 250)
MUTED = (157, 168, 190)
GREEN = (67, 211, 144)
RED = (255, 107, 112)
CYAN = (84, 196, 255)
YELLOW = (250, 202, 83)
GUIDE_MIN_VISIBILITY = 0.2


def load_font(size: int) -> pygame.font.Font:
    for path in (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ):
        if path.exists():
            return pygame.font.Font(str(path), size)
    return pygame.font.Font(None, size)


def load_labeled_templates(
    sensor: MediaPipePersonSensor,
    image_dir: Path,
    csv_file: Path,
) -> dict[str, SilhouetteTemplate]:
    grouped: dict[str, list[dict[int, JointPoint]]] = {FULL_BODY: [], UPPER_BODY: []}
    with csv_file.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            label = row.get("label", "")
            if label not in grouped:
                continue
            frame = cv2.imread(str(image_dir / row["webcam_image"]))
            if frame is None:
                continue
            sensor.sense(frame)
            if len(sensor.latest_landmarks) != 1:
                continue
            points = visible_points(sensor.latest_landmarks[0], GUIDE_MIN_VISIBILITY)
            if all(index in points for index in MODE_JOINTS[label]):
                grouped[label].append(points)
    return {mode: build_template(mode, samples) for mode, samples in grouped.items()}


def _pixel(point: JointPoint, width: int, height: int) -> tuple[int, int]:
    return round(point.x * width), round(point.y * height)


def draw_guide(
    frame,
    template: SilhouetteTemplate,
    current: dict[int, JointPoint],
    guidance: FramingGuidance,
) -> None:
    height, width = frame.shape[:2]
    overlay = frame.copy()
    outline = [_pixel(template.joints[index].center, width, height) for index in MODE_OUTLINE[template.mode]]
    fill_color = (65, 180, 85) if guidance.positioned else (40, 150, 220)
    cv2.fillPoly(overlay, [np.array(outline, dtype="int32")], fill_color)
    cv2.addWeighted(overlay, 0.20, frame, 0.80, 0, frame)

    for index, band in template.joints.items():
        center = _pixel(band.center, width, height)
        axes = (max(8, round(band.radius_x * width)), max(8, round(band.radius_y * height)))
        cv2.ellipse(frame, center, axes, 0, 0, 360, fill_color, 2)
    for start, end in MODE_SEGMENTS[template.mode]:
        cv2.line(
            frame,
            _pixel(template.joints[start].center, width, height),
            _pixel(template.joints[end].center, width, height),
            fill_color,
            4,
        )

    for start, end in MODE_SEGMENTS[template.mode]:
        if start in current and end in current:
            cv2.line(frame, _pixel(current[start], width, height), _pixel(current[end], width, height), (255, 255, 255), 3)
    for index in MODE_JOINTS[template.mode]:
        if index in current:
            cv2.circle(frame, _pixel(current[index], width, height), 7, (255, 255, 255), -1)


def jpeg_surface(data: bytes) -> pygame.Surface:
    return pygame.image.load(io.BytesIO(data), "frame.jpg").convert()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-fps", type=float, default=15.0)
    parser.add_argument("--inference-fps", type=float, default=10.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=ROOT / "calibration" / "webcam_skeleton_1280x960",
    )
    parser.add_argument("--labels", type=Path, default=None)
    args = parser.parse_args()
    csv_file = args.labels or args.image_dir / "framing_labels.csv"

    source = WebcamFrameSource(args.camera_index, args.camera_fps, args.width, args.height)
    sensor = MediaPipePersonSensor(max_people=1)
    try:
        templates = load_labeled_templates(sensor, args.image_dir, csv_file)
    except Exception:
        sensor.close()
        source.close()
        raise

    pygame.init()
    pygame.display.set_caption("GeekSeek Silhouette Framing Guide")
    screen = pygame.display.set_mode((1180, 760), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    title_font = load_font(34)
    body_font = load_font(24)
    small_font = load_font(17)
    mode = FULL_BODY
    preview: pygame.Surface | None = None
    guidance = FramingGuidance(False, "사람을 기다리는 중")
    guidance_history: deque[FramingGuidance] = deque(maxlen=5)
    last_inference = 0.0
    running = True

    try:
        while running:
            now = time.monotonic()
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    running = False
                elif event.type == pygame.KEYDOWN and event.key in (pygame.K_1, pygame.K_f):
                    mode = FULL_BODY
                    guidance_history.clear()
                elif event.type == pygame.KEYDOWN and event.key in (pygame.K_2, pygame.K_u):
                    mode = UPPER_BODY
                    guidance_history.clear()

            if now - last_inference >= 1.0 / max(args.inference_fps, 0.1):
                frame = source()
                if frame is not None:
                    last_inference = now
                    frame = frame.copy()
                    signal = sensor.sense(frame)
                    current = (
                        visible_points(sensor.latest_landmarks[0], GUIDE_MIN_VISIBILITY)
                        if signal.detected and len(sensor.latest_landmarks) == 1
                        else {}
                    )
                    instantaneous = evaluate_framing(templates[mode], current)
                    guidance_history.append(instantaneous)
                    # Require five consecutive good frames before showing OK.
                    stable_ok = len(guidance_history) == 5 and all(item.positioned for item in guidance_history)
                    message = instantaneous.message
                    if stable_ok:
                        message = "좋습니다 · 그대로 서 주세요"
                    elif instantaneous.positioned:
                        message = "잠시 그대로 서 주세요"
                    guidance = FramingGuidance(
                        **{**instantaneous.__dict__, "positioned": stable_ok, "message": message}
                    )
                    draw_guide(frame, templates[mode], current, guidance)
                    preview = jpeg_surface(encode_jpeg(frame))

            width, height = screen.get_size()
            screen.fill(BACKGROUND)
            side_width = min(350, max(300, width // 3))
            image_rect = pygame.Rect(18, 18, width - side_width - 54, height - 36)
            side_rect = pygame.Rect(image_rect.right + 18, 18, side_width, height - 36)
            pygame.draw.rect(screen, (8, 11, 17), image_rect, border_radius=12)
            if preview is not None:
                scale = min(image_rect.width / preview.get_width(), image_rect.height / preview.get_height())
                scaled = pygame.transform.smoothscale(preview, (round(preview.get_width() * scale), round(preview.get_height() * scale)))
                screen.blit(scaled, scaled.get_rect(center=image_rect.center))

            pygame.draw.rect(screen, PANEL, side_rect, border_radius=12)
            color = GREEN if guidance.positioned else (YELLOW if guidance.detected else RED)
            x = side_rect.x + 22
            y = side_rect.y + 22
            mode_name = "전신 구도" if mode == FULL_BODY else "상반신 구도"
            for text_value, font, text_color, gap in (
                (mode_name, title_font, CYAN, 56),
                (guidance.message, title_font, color, 100),
                (f"크기  {guidance.scale_ratio:.2f} / 1.00", body_font, TEXT, 42),
                (f"실루엣  {guidance.inside_count}/{guidance.required_count}", body_font, TEXT, 42),
                (f"기준 사진  {templates[mode].sample_count}장", small_font, MUTED, 56),
                ("흰색: 현재 스켈레톤", small_font, TEXT, 30),
                ("색 영역: 목표 실루엣", small_font, MUTED, 30),
            ):
                rendered = font.render(text_value, True, text_color)
                if rendered.get_width() > side_rect.width - 44:
                    rendered = pygame.transform.smoothscale(rendered, (side_rect.width - 44, rendered.get_height()))
                screen.blit(rendered, (x, y))
                y += gap
            controls = ["1 / F   전신", "2 / U   상반신", "ESC     종료"]
            for offset, text_value in enumerate(controls):
                screen.blit(body_font.render(text_value, True, TEXT), (x, side_rect.bottom - 125 + offset * 35))
            pygame.display.flip()
            clock.tick(30)
    finally:
        pygame.quit()
        sensor.close()
        source.close()


if __name__ == "__main__":
    main()

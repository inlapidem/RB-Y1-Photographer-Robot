"""Compare saved 5-DOF pose samples from one capture CSV."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import pygame


BACKGROUND = (13, 17, 25)
PANEL = (26, 33, 47)
TEXT = (235, 240, 250)
MUTED = (157, 168, 190)
CYAN = (84, 196, 255)
GREEN = (67, 211, 144)


@dataclass(frozen=True)
class PoseSample:
    axis_fields: tuple[str, ...]
    positions_deg: tuple[float, ...]
    webcam_path: Path
    iphone_path: Path

    @property
    def name(self) -> str:
        return self.iphone_path.stem.removesuffix("_iphone")


def load_samples(csv_file: Path) -> list[PoseSample]:
    with csv_file.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        axis_fields = tuple(
            field for field in fields if field.startswith("axis_") and field.endswith("_deg")
        )
        if len(axis_fields) != 5 or "webcam_image" not in fields or "iphone_image" not in fields:
            raise ValueError("CSV에는 5개 axis_*_deg 열과 webcam_image, iphone_image가 필요합니다")
        return [
            PoseSample(
                axis_fields=axis_fields,
                positions_deg=tuple(float(row[field]) for field in axis_fields),
                webcam_path=csv_file.parent / row["webcam_image"],
                iphone_path=csv_file.parent / row["iphone_image"],
            )
            for row in reader
        ]


def load_font(size: int) -> pygame.font.Font:
    for path in (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ):
        if path.exists():
            return pygame.font.Font(str(path), size)
    return pygame.font.Font(None, size)


def fit_surface(surface: pygame.Surface, bounds: pygame.Rect) -> tuple[pygame.Surface, pygame.Rect]:
    scale = min(bounds.width / surface.get_width(), bounds.height / surface.get_height())
    size = (
        max(1, round(surface.get_width() * scale)),
        max(1, round(surface.get_height() * scale)),
    )
    scaled = pygame.transform.smoothscale(surface, size)
    return scaled, scaled.get_rect(center=bounds.center)


def load_image(path: Path) -> pygame.Surface | None:
    try:
        return pygame.image.load(str(path)).convert()
    except (FileNotFoundError, pygame.error):
        return None


def draw_image(
    screen: pygame.Surface,
    image: pygame.Surface | None,
    bounds: pygame.Rect,
    font: pygame.font.Font,
) -> None:
    pygame.draw.rect(screen, (9, 12, 18), bounds, border_radius=8)
    if image is None:
        label = font.render("이미지 없음", True, MUTED)
        screen.blit(label, label.get_rect(center=bounds.center))
        return
    scaled, destination = fit_surface(image, bounds.inflate(-8, -8))
    screen.blit(scaled, destination)


def draw_sample(
    screen: pygame.Surface,
    sample: PoseSample,
    index: int,
    total: int,
    bounds: pygame.Rect,
    title: str,
    fonts: tuple[pygame.font.Font, pygame.font.Font, pygame.font.Font],
) -> None:
    title_font, body_font, small_font = fonts
    pygame.draw.rect(screen, PANEL, bounds, border_radius=12)
    x = bounds.x + 16
    y = bounds.y + 12
    screen.blit(title_font.render(f"{title}  {index + 1}/{total}", True, CYAN), (x, y))
    y += 37
    screen.blit(small_font.render(sample.name, True, MUTED), (x, y))
    y += 30

    content_width = bounds.width - 48
    iphone_width = max(190, round(content_width * 0.46))
    image_height = min(475, bounds.height - 150)
    iphone_rect = pygame.Rect(x, y, iphone_width, image_height)
    webcam_rect = pygame.Rect(
        iphone_rect.right + 16,
        y,
        content_width - iphone_width,
        min(260, image_height // 2),
    )
    draw_image(screen, load_image(sample.iphone_path), iphone_rect, body_font)
    draw_image(screen, load_image(sample.webcam_path), webcam_rect, body_font)

    angle_y = webcam_rect.bottom + 18
    for field, value in zip(sample.axis_fields, sample.positions_deg):
        axis = field.removeprefix("axis_").removesuffix("_deg")
        screen.blit(body_font.render(f"axis[{axis}]  {value:8.2f}°", True, TEXT), (webcam_rect.x, angle_y))
        angle_y += 31


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path, nargs="?", default=Path("calibration/pose_samples.csv"))
    args = parser.parse_args()

    pygame.init()
    pygame.display.set_caption("GeekSeek Pose Dataset Viewer")
    screen = pygame.display.set_mode((1400, 820), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    fonts = (load_font(25), load_font(20), load_font(16))
    samples = load_samples(args.csv_file)
    if not samples:
        raise SystemExit(f"저장된 pose가 없습니다: {args.csv_file}")

    reference_index = 0
    current_index = len(samples) - 1
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
            ):
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    current_index = max(0, current_index - 1)
                elif event.key == pygame.K_RIGHT:
                    current_index = min(len(samples) - 1, current_index + 1)
                elif event.key == pygame.K_SPACE:
                    reference_index = current_index
                elif event.key == pygame.K_r:
                    samples = load_samples(args.csv_file)
                    reference_index = min(reference_index, len(samples) - 1)
                    current_index = min(current_index, len(samples) - 1)

        width, height = screen.get_size()
        screen.fill(BACKGROUND)
        gap = 16
        panel_width = (width - gap * 3) // 2
        panel_height = height - 88
        left = pygame.Rect(gap, gap, panel_width, panel_height)
        right = pygame.Rect(left.right + gap, gap, panel_width, panel_height)
        draw_sample(
            screen,
            samples[reference_index],
            reference_index,
            len(samples),
            left,
            "기준 A",
            fonts,
        )
        draw_sample(
            screen,
            samples[current_index],
            current_index,
            len(samples),
            right,
            "비교 B",
            fonts,
        )

        deltas = tuple(
            current - reference
            for reference, current in zip(
                samples[reference_index].positions_deg,
                samples[current_index].positions_deg,
            )
        )
        delta_text = "   ".join(
            f"Δaxis[{field.removeprefix('axis_').removesuffix('_deg')}] {delta:+.2f}°"
            for field, delta in zip(samples[current_index].axis_fields, deltas)
        )
        footer = fonts[2].render(
            f"←/→ 비교 pose 이동   SPACE 기준 A 지정   R CSV 새로고침   ESC 종료     {delta_text}",
            True,
            GREEN,
        )
        screen.blit(footer, footer.get_rect(center=(width // 2, height - 31)))
        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()

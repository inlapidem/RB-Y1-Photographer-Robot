"""Label saved webcam/skeleton image pairs as full-body, upper-body, or unused."""

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
GREEN = (67, 211, 144)
CYAN = (84, 196, 255)
RED = (255, 107, 112)
YELLOW = (250, 202, 83)

FULL_BODY = "FULL_BODY"
UPPER_BODY = "UPPER_BODY"
UNUSED = "UNUSED"
VALID_LABELS = {FULL_BODY, UPPER_BODY, UNUSED}
CSV_FIELDS = ["sample_id", "label", "webcam_image", "skeleton_image"]


@dataclass(frozen=True)
class ImagePair:
    sample_id: str
    webcam_path: Path
    skeleton_path: Path


def discover_pairs(image_dir: Path) -> list[ImagePair]:
    pairs: list[ImagePair] = []
    suffix = "_webcam.jpg"
    for webcam_path in sorted(image_dir.glob(f"*{suffix}")):
        sample_id = webcam_path.name.removesuffix(suffix)
        skeleton_path = image_dir / f"{sample_id}_skeleton.jpg"
        if skeleton_path.is_file():
            pairs.append(ImagePair(sample_id, webcam_path, skeleton_path))
    return pairs


def load_labels(csv_file: Path) -> dict[str, str]:
    if not csv_file.exists():
        return {}
    with csv_file.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError(f"지원하지 않는 CSV 열 구성입니다: {reader.fieldnames}")
        labels: dict[str, str] = {}
        for row in reader:
            if row["label"] not in VALID_LABELS:
                raise ValueError(f"지원하지 않는 label입니다: {row['label']}")
            labels[row["sample_id"]] = row["label"]
        return labels


def save_labels(csv_file: Path, pairs: list[ImagePair], labels: dict[str, str]) -> None:
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = csv_file.with_suffix(csv_file.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for pair in pairs:
            label = labels.get(pair.sample_id)
            if label is None:
                continue
            if label not in VALID_LABELS:
                raise ValueError(f"지원하지 않는 label입니다: {label}")
            writer.writerow(
                {
                    "sample_id": pair.sample_id,
                    "label": label,
                    "webcam_image": pair.webcam_path.name,
                    "skeleton_image": pair.skeleton_path.name,
                }
            )
    temporary.replace(csv_file)


def load_font(size: int) -> pygame.font.Font:
    for path in (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ):
        if path.exists():
            return pygame.font.Font(str(path), size)
    return pygame.font.Font(None, size)


def load_image(path: Path) -> pygame.Surface:
    return pygame.image.load(str(path)).convert()


def draw_image(screen: pygame.Surface, image: pygame.Surface, bounds: pygame.Rect) -> None:
    pygame.draw.rect(screen, (9, 12, 18), bounds, border_radius=8)
    scale = min(bounds.width / image.get_width(), bounds.height / image.get_height())
    size = (round(image.get_width() * scale), round(image.get_height() * scale))
    scaled = pygame.transform.smoothscale(image, size)
    screen.blit(scaled, scaled.get_rect(center=bounds.center))


def next_unlabeled_index(
    pairs: list[ImagePair],
    labels: dict[str, str],
    after: int = -1,
) -> int | None:
    order = list(range(after + 1, len(pairs))) + list(range(0, after + 1))
    return next((index for index in order if pairs[index].sample_id not in labels), None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=Path("calibration/webcam_skeleton_1280x960"),
    )
    parser.add_argument("--csv-file", type=Path, default=None)
    args = parser.parse_args()

    pairs = discover_pairs(args.image_dir)
    if not pairs:
        raise SystemExit(f"완전한 webcam/skeleton 이미지 쌍이 없습니다: {args.image_dir}")
    csv_file = args.csv_file or args.image_dir / "framing_labels.csv"
    labels = load_labels(csv_file)
    valid_ids = {pair.sample_id for pair in pairs}
    labels = {sample_id: label for sample_id, label in labels.items() if sample_id in valid_ids}
    index = next_unlabeled_index(pairs, labels)
    if index is None:
        index = 0

    pygame.init()
    pygame.display.set_caption("GeekSeek Saved Framing Labeler")
    screen = pygame.display.set_mode((1460, 820), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    title_font = load_font(27)
    body_font = load_font(21)
    small_font = load_font(16)
    loaded_index = -1
    webcam_image: pygame.Surface | None = None
    skeleton_image: pygame.Surface | None = None
    message = "숫자 1/2/3 또는 F/U/X로 분류하세요"
    message_color = MUTED
    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
            ):
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    index = (index - 1) % len(pairs)
                elif event.key == pygame.K_RIGHT:
                    index = (index + 1) % len(pairs)
                elif event.key == pygame.K_BACKSPACE:
                    labels.pop(pairs[index].sample_id, None)
                    save_labels(csv_file, pairs, labels)
                    message = "현재 이미지의 라벨을 해제했습니다"
                    message_color = YELLOW
                else:
                    key_labels = {
                        pygame.K_1: FULL_BODY,
                        pygame.K_f: FULL_BODY,
                        pygame.K_2: UPPER_BODY,
                        pygame.K_u: UPPER_BODY,
                        pygame.K_3: UNUSED,
                        pygame.K_x: UNUSED,
                    }
                    label = key_labels.get(event.key)
                    if label is not None:
                        labels[pairs[index].sample_id] = label
                        save_labels(csv_file, pairs, labels)
                        message = f"저장 완료: {label}"
                        message_color = GREEN
                        next_index = next_unlabeled_index(pairs, labels, after=index)
                        if next_index is not None:
                            index = next_index

        if loaded_index != index:
            webcam_image = load_image(pairs[index].webcam_path)
            skeleton_image = load_image(pairs[index].skeleton_path)
            loaded_index = index

        width, height = screen.get_size()
        screen.fill(BACKGROUND)
        gap = 16
        side_width = min(330, max(280, width // 5))
        content_width = width - side_width - gap * 4
        image_width = content_width // 2
        image_height = height - 126
        raw_rect = pygame.Rect(gap, 58, image_width, image_height)
        skeleton_rect = pygame.Rect(raw_rect.right + gap, 58, image_width, image_height)
        side_rect = pygame.Rect(skeleton_rect.right + gap, gap, side_width, height - gap * 2)

        screen.blit(title_font.render("저장 이미지 Framing 분류", True, TEXT), (gap, 14))
        screen.blit(body_font.render("Webcam", True, CYAN), (raw_rect.x, 32))
        screen.blit(body_font.render("Skeleton", True, CYAN), (skeleton_rect.x, 32))
        assert webcam_image is not None and skeleton_image is not None
        draw_image(screen, webcam_image, raw_rect)
        draw_image(screen, skeleton_image, skeleton_rect)

        pygame.draw.rect(screen, PANEL, side_rect, border_radius=12)
        x, y = side_rect.x + 18, side_rect.y + 18
        pair = pairs[index]
        current_label = labels.get(pair.sample_id, "미분류")
        counts = {label: sum(value == label for value in labels.values()) for label in VALID_LABELS}
        lines = [
            (f"{index + 1} / {len(pairs)}", title_font, TEXT),
            (pair.sample_id, small_font, MUTED),
            ("현재 라벨", body_font, MUTED),
            (current_label, title_font, YELLOW if current_label == "미분류" else GREEN),
            (f"완료 {len(labels)} / {len(pairs)}", body_font, TEXT),
            (f"전신     {counts[FULL_BODY]}", body_font, GREEN),
            (f"상반신   {counts[UPPER_BODY]}", body_font, CYAN),
            (f"안씀     {counts[UNUSED]}", body_font, RED),
        ]
        for text, font, color in lines:
            screen.blit(font.render(text, True, color), (x, y))
            y += 43 if font is title_font else 34

        y += 18
        for text, color in (
            ("1 / F   전신", GREEN),
            ("2 / U   상반신", CYAN),
            ("3 / X   안씀", RED),
            ("← / →   이동", TEXT),
            ("Backspace 라벨 해제", TEXT),
            ("Esc     종료", TEXT),
        ):
            screen.blit(body_font.render(text, True, color), (x, y))
            y += 36
        screen.blit(small_font.render(message, True, message_color), (x, side_rect.bottom - 35))

        footer = small_font.render(f"CSV: {csv_file}", True, MUTED)
        screen.blit(footer, (gap, height - 30))
        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()

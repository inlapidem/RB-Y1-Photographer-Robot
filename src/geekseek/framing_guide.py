"""Skeleton-silhouette templates and guest movement guidance."""

from __future__ import annotations

import math
import statistics
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


FULL_BODY = "FULL_BODY"
UPPER_BODY = "UPPER_BODY"

# MediaPipe BlazePose indices requested for each composition.
MODE_JOINTS = {
    UPPER_BODY: (11, 12, 23, 24),  # shoulders + hips
    FULL_BODY: (11, 12, 23, 24),  # shoulders + hips
}
MODE_SEGMENTS = {
    UPPER_BODY: ((11, 12), (23, 24), (11, 23), (12, 24)),
    FULL_BODY: ((11, 12), (23, 24), (11, 23), (12, 24)),
}
MODE_OUTLINE = {
    UPPER_BODY: (11, 23, 24, 12),
    FULL_BODY: (11, 23, 24, 12),
}
UPPER_BODY_VERTICAL_RADIUS_SCALE = 2.0
FULL_BODY_RADIUS_SCALE = 1.5


@dataclass(frozen=True)
class JointPoint:
    x: float
    y: float
    visibility: float = 1.0


@dataclass(frozen=True)
class JointBand:
    center: JointPoint
    radius_x: float
    radius_y: float


@dataclass(frozen=True)
class SilhouetteTemplate:
    mode: str
    joints: Mapping[int, JointBand]
    sample_count: int


@dataclass(frozen=True)
class FramingGuidance:
    detected: bool
    message: str
    direction: str = "detect"
    scale_ratio: float = 0.0
    center_error_x: float = 0.0
    inside_count: int = 0
    required_count: int = 0
    positioned: bool = False


def visible_points(landmarks: Iterable, min_visibility: float = 0.5) -> dict[int, JointPoint]:
    return {
        index: JointPoint(float(point.x), float(point.y), float(point.visibility))
        for index, point in enumerate(landmarks)
        if point.visibility >= min_visibility
    }


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def build_template(
    mode: str,
    samples: Iterable[Mapping[int, JointPoint]],
    *,
    min_joint_samples: int = 3,
    radius_scale: float = 1.0,
) -> SilhouetteTemplate:
    if mode not in MODE_JOINTS:
        raise ValueError(f"지원하지 않는 촬영 모드입니다: {mode}")
    if radius_scale <= 0:
        raise ValueError("radius_scale은 0보다 커야 합니다")
    sample_list = list(samples)
    bands: dict[int, JointBand] = {}
    for index in MODE_JOINTS[mode]:
        points = [sample[index] for sample in sample_list if index in sample]
        if len(points) < min_joint_samples:
            raise ValueError(f"{mode}의 관절 {index} 기준 샘플이 부족합니다: {len(points)}")
        xs = [point.x for point in points]
        ys = [point.y for point in points]
        center = JointPoint(statistics.median(xs), statistics.median(ys))
        # The learned spread is expanded slightly so normal body-shape
        # differences do not make the guide flicker red.
        radius_x = max(0.035, (_percentile(xs, 0.9) - _percentile(xs, 0.1)) * 0.85)
        radius_y = max(0.040, (_percentile(ys, 0.9) - _percentile(ys, 0.1)) * 0.85)
        if mode == UPPER_BODY:
            radius_y *= UPPER_BODY_VERTICAL_RADIUS_SCALE
        elif mode == FULL_BODY:
            radius_x *= FULL_BODY_RADIUS_SCALE
            radius_y *= FULL_BODY_RADIUS_SCALE
        radius_x *= radius_scale
        radius_y *= radius_scale
        bands[index] = JointBand(center, radius_x, radius_y)
    return SilhouetteTemplate(mode=mode, joints=bands, sample_count=len(sample_list))


def _distance(a: JointPoint, b: JointPoint) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _center(points: Iterable[JointPoint]) -> JointPoint:
    values = list(points)
    return JointPoint(
        statistics.mean(point.x for point in values),
        statistics.mean(point.y for point in values),
    )


def evaluate_framing(
    template: SilhouetteTemplate,
    current: Mapping[int, JointPoint],
    *,
    scale_tolerance: float = 0.12,
    center_tolerance_x: float = 0.055,
) -> FramingGuidance:
    required = MODE_JOINTS[template.mode]
    if any(index not in current for index in required):
        return FramingGuidance(
            detected=False,
            message="몸을 정면으로 보여주세요",
            direction="detect",
            required_count=len(required),
        )

    target_points = {index: template.joints[index].center for index in required}
    ratios: list[float] = []
    for start, end in MODE_SEGMENTS[template.mode]:
        target_length = _distance(target_points[start], target_points[end])
        if target_length > 1e-6:
            ratios.append(_distance(current[start], current[end]) / target_length)
    scale_ratio = statistics.median(ratios)

    current_center = _center(current[index] for index in required)
    target_center = _center(target_points.values())
    center_error_x = current_center.x - target_center.x

    inside_count = 0
    for index in required:
        band = template.joints[index]
        normalized = (
            ((current[index].x - band.center.x) / band.radius_x) ** 2
            + ((current[index].y - band.center.y) / band.radius_y) ** 2
        )
        inside_count += normalized <= 1.0

    scale_ok = 1.0 - scale_tolerance <= scale_ratio <= 1.0 + scale_tolerance
    center_ok = abs(center_error_x) <= center_tolerance_x
    enough_inside = inside_count >= math.ceil(len(required) * 0.75)
    positioned = scale_ok and center_ok and enough_inside

    if positioned:
        message = "좋습니다 · 그대로 서 주세요"
        direction = "hold"
    elif scale_ratio < 1.0 - scale_tolerance:
        message = "앞으로 이동하세요"
        direction = "forward"
    elif scale_ratio > 1.0 + scale_tolerance:
        message = "뒤로 이동하세요"
        direction = "back"
    elif center_error_x < -center_tolerance_x:
        message = "화면 오른쪽으로 이동하세요"
        direction = "right"
    elif center_error_x > center_tolerance_x:
        message = "화면 왼쪽으로 이동하세요"
        direction = "left"
    else:
        message = "관절을 실루엣 안에 맞춰주세요"
        direction = "align"

    return FramingGuidance(
        detected=True,
        message=message,
        direction=direction,
        scale_ratio=scale_ratio,
        center_error_x=center_error_x,
        inside_count=inside_count,
        required_count=len(required),
        positioned=positioned,
    )


def load_templates_from_dataset(
    sensor: object,
    image_dir: Path,
    *,
    min_visibility: float = 0.2,
) -> dict[str, SilhouetteTemplate]:
    """Infer labeled reference images and build one template per shot mode."""
    import cv2

    csv_file = image_dir / "framing_labels.csv"
    grouped: dict[str, list[dict[int, JointPoint]]] = {FULL_BODY: [], UPPER_BODY: []}
    with csv_file.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            mode = row.get("label", "")
            if mode not in grouped:
                continue
            frame = cv2.imread(str(image_dir / row["webcam_image"]))
            if frame is None:
                continue
            signal = sensor.sense(frame)
            landmarks_list = sensor.latest_landmarks
            if not signal.detected or len(landmarks_list) != 1:
                continue
            points = visible_points(landmarks_list[0], min_visibility)
            if all(index in points for index in MODE_JOINTS[mode]):
                grouped[mode].append(points)
    return {mode: build_template(mode, samples) for mode, samples in grouped.items()}


def load_templates_from_samples_csv(
    csv_file: Path,
    *,
    radius_scale: float = 2.0,
    min_visibility: float = 0.2,
) -> dict[str, SilhouetteTemplate]:
    """Build templates directly from the RBY1 framing-capture CSV.

    The calibration tool already stored normalized shoulder/hip landmarks, so
    loading them directly avoids re-running pose inference over the images.
    ``radius_scale`` expands the learned p10-p90 joint bands after the existing
    per-mode heuristics have been applied.
    """
    grouped: dict[str, list[dict[int, JointPoint]]] = {FULL_BODY: [], UPPER_BODY: []}
    label_modes = {"full_body": FULL_BODY, "upper_body": UPPER_BODY}
    joint_columns = {
        11: "left_shoulder",
        12: "right_shoulder",
        23: "left_hip",
        24: "right_hip",
    }

    with csv_file.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            mode = label_modes.get((row.get("label") or "").strip().lower())
            if mode is None or int(row.get("people_count") or 0) != 1:
                continue
            points: dict[int, JointPoint] = {}
            try:
                for index, prefix in joint_columns.items():
                    point = JointPoint(
                        x=float(row[f"{prefix}_x"]),
                        y=float(row[f"{prefix}_y"]),
                        visibility=float(row[f"{prefix}_visibility"]),
                    )
                    if point.visibility >= min_visibility:
                        points[index] = point
            except (KeyError, TypeError, ValueError):
                continue
            if all(index in points for index in MODE_JOINTS[mode]):
                grouped[mode].append(points)

    return {
        mode: build_template(mode, samples, radius_scale=radius_scale)
        for mode, samples in grouped.items()
    }


def annotate_framing_frame(
    frame: object,
    template: SilhouetteTemplate,
    current: Mapping[int, JointPoint],
    guidance: FramingGuidance,
) -> object:
    """Draw the learned silhouette and current four-joint skeleton."""
    import cv2
    import numpy as np

    annotated = frame.copy()
    height, width = annotated.shape[:2]

    def pixel(point: JointPoint) -> tuple[int, int]:
        return round(point.x * width), round(point.y * height)

    color = (65, 180, 85) if guidance.positioned else (40, 150, 220)
    overlay = annotated.copy()
    outline = np.array(
        [pixel(template.joints[index].center) for index in MODE_OUTLINE[template.mode]],
        dtype="int32",
    )
    cv2.fillPoly(overlay, [outline], color)
    cv2.addWeighted(overlay, 0.20, annotated, 0.80, 0, annotated)

    for band in template.joints.values():
        axes = (
            max(8, round(band.radius_x * width)),
            max(8, round(band.radius_y * height)),
        )
        cv2.ellipse(annotated, pixel(band.center), axes, 0, 0, 360, color, 3)
    for start, end in MODE_SEGMENTS[template.mode]:
        cv2.line(
            annotated,
            pixel(template.joints[start].center),
            pixel(template.joints[end].center),
            color,
            5,
        )
        if start in current and end in current:
            cv2.line(annotated, pixel(current[start]), pixel(current[end]), (255, 255, 255), 4)
    for index in MODE_JOINTS[template.mode]:
        if index in current:
            cv2.circle(annotated, pixel(current[index]), 9, (255, 255, 255), -1)
    return annotated

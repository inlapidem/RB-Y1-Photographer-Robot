import pytest

from geekseek.framing_guide import (
    FULL_BODY,
    UPPER_BODY,
    JointPoint,
    build_template,
    evaluate_framing,
)


UPPER = {
    11: JointPoint(0.40, 0.30),
    12: JointPoint(0.60, 0.30),
    23: JointPoint(0.43, 0.62),
    24: JointPoint(0.57, 0.62),
}
FULL = {
    **UPPER,
    25: JointPoint(0.44, 0.86),
    26: JointPoint(0.56, 0.86),
}


def samples(points):
    return [
        {index: JointPoint(point.x + delta, point.y) for index, point in points.items()}
        for delta in (-0.01, 0.0, 0.01)
    ]


@pytest.mark.parametrize("mode,points", [(UPPER_BODY, UPPER), (FULL_BODY, FULL)])
def test_matching_skeleton_is_positioned(mode, points) -> None:
    template = build_template(mode, samples(points))
    result = evaluate_framing(template, points)
    assert result.positioned
    assert result.inside_count == result.required_count


def test_smaller_skeleton_is_told_to_move_forward() -> None:
    template = build_template(UPPER_BODY, samples(UPPER))
    center = JointPoint(0.5, 0.46)
    smaller = {
        index: JointPoint(center.x + (point.x - center.x) * 0.7, center.y + (point.y - center.y) * 0.7)
        for index, point in UPPER.items()
    }
    result = evaluate_framing(template, smaller)
    assert result.message == "앞으로 이동하세요"
    assert result.scale_ratio == pytest.approx(0.7)


def test_larger_skeleton_is_told_to_move_back() -> None:
    template = build_template(FULL_BODY, samples(FULL))
    center = JointPoint(0.5, 0.55)
    larger = {
        index: JointPoint(center.x + (point.x - center.x) * 1.3, center.y + (point.y - center.y) * 1.3)
        for index, point in FULL.items()
    }
    result = evaluate_framing(template, larger)
    assert result.message == "뒤로 이동하세요"


def test_upper_body_has_looser_vertical_joint_bands() -> None:
    upper_template = build_template(UPPER_BODY, samples(UPPER))
    full_template = build_template(FULL_BODY, samples(FULL))
    # Base minimum y radius is .040: upper gets 2x, full gets 1.5x.
    assert upper_template.joints[11].radius_y == pytest.approx(0.080)
    assert full_template.joints[11].radius_y == pytest.approx(0.060)


def test_full_body_expands_both_joint_band_axes() -> None:
    template = build_template(FULL_BODY, samples(FULL))
    assert template.joints[11].radius_x == pytest.approx(0.035 * 1.5)
    assert template.joints[11].radius_y == pytest.approx(0.040 * 1.5)


def test_full_body_does_not_require_knees() -> None:
    template = build_template(FULL_BODY, samples(FULL))
    result = evaluate_framing(template, UPPER)
    assert result.detected
    assert result.positioned

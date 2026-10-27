"""Compile a small set of taught RB-Y1 camera poses into a photo sweep.

The source file stores only the poses an operator deliberately teaches while
the phone is held by the right gripper. Runtime segments follow the shortest
closed joint-space route. Each taught pose gets one stopped photo; remaining
photos are scheduled along the continuous moves between taught poses.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import inf


SCHEMA = "geekseek.rby1.keyposes/v1"


@dataclass(frozen=True)
class KeyPose:
    label: str
    right_arm_rad: tuple[float, ...]


def _joint_values(value: object, label: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != 7:
        raise ValueError(f"{label}: RB-Y1 오른팔 7축(rad) 값이 필요합니다")
    return tuple(float(item) for item in value)


def joint_distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Time-proportional distance when every joint shares one speed limit."""
    return max(abs(x - y) for x, y in zip(a, b))


def shortest_closed_order(
    home: tuple[float, ...],
    anchors: tuple[KeyPose, ...],
    blocked_edges: set[frozenset[str]] | None = None,
) -> tuple[int, ...]:
    """Exact home -> all anchors -> home order (Held-Karp dynamic program)."""
    if not anchors:
        raise ValueError("촬영 키포즈가 없습니다")
    blocked = blocked_edges or set()

    def edge(a_label: str, a: tuple[float, ...], b_label: str, b: tuple[float, ...]) -> float:
        if frozenset((a_label, b_label)) in blocked:
            return inf
        return joint_distance(a, b)

    @lru_cache(maxsize=None)
    def visit(mask: int, last: int) -> tuple[float, tuple[int, ...]]:
        bit = 1 << last
        if mask == bit:
            return edge("home", home, anchors[last].label, anchors[last].right_arm_rad), (last,)
        previous_mask = mask ^ bit
        candidates: list[tuple[float, tuple[int, ...]]] = []
        for previous in range(len(anchors)):
            if not previous_mask & (1 << previous):
                continue
            previous_cost, previous_order = visit(previous_mask, previous)
            candidates.append(
                (
                    previous_cost
                    + edge(
                        anchors[previous].label,
                        anchors[previous].right_arm_rad,
                        anchors[last].label,
                        anchors[last].right_arm_rad,
                    ),
                    previous_order + (last,),
                )
            )
        return min(candidates, key=lambda item: item[0])

    full_mask = (1 << len(anchors)) - 1
    routes = []
    for last in range(len(anchors)):
        cost, order = visit(full_mask, last)
        routes.append(
            (
                cost + edge(anchors[last].label, anchors[last].right_arm_rad, "home", home),
                order,
            )
        )
    best_cost, best_order = min(routes, key=lambda item: item[0])
    if best_cost == inf:
        raise ValueError("blocked_edges를 피해서 모든 키포즈를 방문하고 복귀할 수 없습니다")
    return best_order


def _allocate_extras(distances: list[float], count: int) -> list[int]:
    if count < 0:
        raise ValueError("capture_count는 키포즈 수보다 작을 수 없습니다")
    if not distances:
        if count:
            raise ValueError("중간 촬영점을 만들 구간이 없습니다")
        return []
    total = sum(distances)
    weights = distances if total > 0 else [1.0] * len(distances)
    total = sum(weights)
    exact = [count * weight / total for weight in weights]
    allocation = [int(value) for value in exact]
    remaining = count - sum(allocation)
    order = sorted(range(len(exact)), key=lambda i: (exact[i] - allocation[i], weights[i]), reverse=True)
    for index in order[:remaining]:
        allocation[index] += 1
    return allocation


def compile_keyposes(document: dict, capture_count: int | None = None) -> dict:
    if document.get("schema") != SCHEMA:
        raise ValueError(f"지원하지 않는 RB-Y1 keypose schema입니다: {document.get('schema')!r}")
    home_data = document.get("home")
    if not isinstance(home_data, dict):
        raise ValueError("home 객체가 필요합니다")
    home = _joint_values(home_data.get("right_arm_rad"), "home")
    head_value = home_data.get("head_rad")
    head = None
    if head_value is not None:
        if not isinstance(head_value, list) or len(head_value) != 2:
            raise ValueError("home: RBY-Y1 목 2축(rad) 값이 필요합니다")
        head = tuple(float(item) for item in head_value)

    source_anchors = document.get("anchors")
    if not isinstance(source_anchors, list) or len(source_anchors) < 2:
        raise ValueError("촬영 키포즈가 최소 2개 필요합니다")
    anchors = tuple(
        KeyPose(str(item.get("label", f"wp{index}")), _joint_values(item.get("right_arm_rad"), f"anchor {index}"))
        for index, item in enumerate(source_anchors, start=1)
        if isinstance(item, dict) and item.get("enabled", True)
    )
    if len(anchors) < 2:
        raise ValueError("활성 촬영 키포즈가 최소 2개 필요합니다")
    if len({pose.label for pose in anchors}) != len(anchors):
        raise ValueError("키포즈 label은 서로 달라야 합니다")

    planning = document.get("planning") or {}
    if not isinstance(planning, dict):
        raise ValueError("planning은 객체여야 합니다")
    target = int(capture_count if capture_count is not None else planning.get("capture_count", 30))
    if target < len(anchors):
        raise ValueError(f"capture_count {target}장은 키포즈 {len(anchors)}개보다 작습니다")
    speed = float(planning.get("max_joint_speed_rad_s", 0.25))
    dwell = float(planning.get("dwell_seconds", 0.7))
    min_travel = float(planning.get("min_travel_seconds", 0.25))
    entry = float(planning.get("entry_seconds", 3.0))
    home_seconds = float(planning.get("home_seconds", 3.0))
    if min(speed, dwell, min_travel, entry, home_seconds) <= 0:
        raise ValueError("planning의 속도와 시간 값은 모두 0보다 커야 합니다")

    blocked: set[frozenset[str]] = set()
    for item in planning.get("blocked_edges", []):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("blocked_edges 항목은 label 두 개의 배열이어야 합니다")
        blocked.add(frozenset((str(item[0]), str(item[1]))))
    order = shortest_closed_order(home, anchors, blocked)
    route = [anchors[index] for index in order]
    distances = [joint_distance(route[i].right_arm_rad, route[i + 1].right_arm_rad) for i in range(len(route) - 1)]
    extras = _allocate_extras(distances, target - len(route))

    segments: list[dict] = []

    # Never assume the physical arm is already at the taught base pose.  A
    # sweep can be requested after manual teaching or a previous fault, so
    # explicitly settle at home before entering the first camera waypoint.
    segments.append(
        {
            "kind": "home",
            "label": "start-home",
            "duration_s": home_seconds,
            "right_arm_rad": list(home),
        }
    )

    def travel(
        label: str,
        previous: tuple[float, ...],
        target_q: tuple[float, ...],
        floor: float = min_travel,
        moving_shots: int = 0,
    ) -> None:
        segment = {
            "kind": "travel",
            "label": label,
            "duration_s": max(floor, joint_distance(previous, target_q) / speed),
            "right_arm_rad": list(target_q),
        }
        if moving_shots:
            segment["shot_ratios"] = [
                index / (moving_shots + 1) for index in range(1, moving_shots + 1)
            ]
        segments.append(segment)

    first = route[0]
    travel("entry", home, first.right_arm_rad, entry)
    segments[-1]["kind"] = "entry"
    segments.append({"kind": "dwell", "label": first.label, "duration_s": dwell, "right_arm_rad": list(first.right_arm_rad)})
    for edge_index, following in enumerate(route[1:]):
        previous = route[edge_index]
        travel(
            f"{previous.label}-to-{following.label}",
            previous.right_arm_rad,
            following.right_arm_rad,
            moving_shots=extras[edge_index],
        )
        segments.append(
            {
                "kind": "dwell",
                "label": following.label,
                "duration_s": dwell,
                "right_arm_rad": list(following.right_arm_rad),
            }
        )

    last = route[-1]
    travel("home", last.right_arm_rad, home, home_seconds)
    segments[-1]["kind"] = "home"
    if head is not None:
        for segment in segments:
            segment["head_rad"] = list(head)
    return {
        "name": str(document.get("name", "RB-Y1 phone keypose sweep")),
        "source_schema": SCHEMA,
        "route": [pose.label for pose in route],
        "capture_count": target,
        "segments": segments,
    }

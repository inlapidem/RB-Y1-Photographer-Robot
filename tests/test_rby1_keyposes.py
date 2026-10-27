import itertools

import pytest

from geekseek.rby1_keyposes import KeyPose, compile_keyposes, joint_distance, shortest_closed_order


def document(anchor_count: int = 11) -> dict:
    return {
        "schema": "geekseek.rby1.keyposes/v1",
        "name": "phone sweep",
        "home": {"right_arm_rad": [0.0] * 7},
        "anchors": [
            {"label": f"wp{index:02d}", "right_arm_rad": [index * 0.05] * 7}
            for index in range(1, anchor_count + 1)
        ],
        "planning": {
            "capture_count": 30,
            "max_joint_speed_rad_s": 0.25,
            "dwell_seconds": 0.7,
            "min_travel_seconds": 0.25,
            "entry_seconds": 3.0,
            "home_seconds": 3.0,
            "blocked_edges": [],
        },
    }


def test_eleven_taught_poses_use_stopped_anchors_and_moving_photos():
    result = compile_keyposes(document())
    dwells = [segment for segment in result["segments"] if segment["kind"] == "dwell"]
    moving_shots = sum(len(segment.get("shot_ratios", ())) for segment in result["segments"])

    assert len(dwells) == 11
    assert moving_shots == 19
    assert len(dwells) + moving_shots == 30
    assert len(result["route"]) == 11
    assert set(result["route"]) == {f"wp{index:02d}" for index in range(1, 12)}
    assert result["segments"][0]["kind"] == "home"
    assert result["segments"][0]["label"] == "start-home"
    assert result["segments"][0]["right_arm_rad"] == [0.0] * 7
    assert result["segments"][1]["kind"] == "entry"
    assert result["segments"][-1]["kind"] == "home"
    assert result["segments"][-1]["right_arm_rad"] == [0.0] * 7


def test_held_karp_order_matches_brute_force_optimum():
    home = (0.0,) * 7
    anchors = tuple(
        KeyPose(label, (value,) * 7)
        for label, value in (("a", 0.9), ("b", -0.2), ("c", 0.4), ("d", -0.8))
    )
    order = shortest_closed_order(home, anchors)

    def cost(candidate):
        route = [anchors[index].right_arm_rad for index in candidate]
        return joint_distance(home, route[0]) + sum(
            joint_distance(route[index], route[index + 1]) for index in range(len(route) - 1)
        ) + joint_distance(route[-1], home)

    assert cost(order) == min(cost(candidate) for candidate in itertools.permutations(range(4)))


def test_blocked_edge_is_never_used():
    home = (0.0,) * 7
    anchors = (
        KeyPose("a", (0.1,) * 7),
        KeyPose("b", (0.2,) * 7),
        KeyPose("c", (0.3,) * 7),
    )
    order = shortest_closed_order(home, anchors, {frozenset(("a", "b"))})
    labels = ["home", *(anchors[index].label for index in order), "home"]

    assert all(frozenset(edge) != frozenset(("a", "b")) for edge in zip(labels, labels[1:]))


def test_capture_count_cannot_drop_a_taught_pose():
    with pytest.raises(ValueError, match="키포즈"):
        compile_keyposes(document(anchor_count=4), capture_count=3)

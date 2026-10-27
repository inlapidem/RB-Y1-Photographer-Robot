"""Turn captured pose samples into a phorce motion slot (P-Vector) file.

The hackathon SDK only lets participant code read `/phorce/feedback` and call
`play(slot)`, so any precise camera move has to exist as a pre-loaded slot on
the PCM's SD card. Instead of hand-teaching one, we take the joint angles that
`scripts/capture_pose_samples.py` already recorded and synthesise the slot: a
sweep that visits every captured waypoint, pausing at each one long enough for
a photo.

Verified against the real SD card (`Motions/motion_01.csv`, file_version
3.0.0):

- `MD<n>` maps straight onto feedback axis `n`; unused axes are a single `-`.
- Cell format is `"yd,Ltraj,s0,sd"`, `yd` in degrees with one decimal.
- Every MD in one motion set must sum to the *same* Ltraj total, so all axes
  here share one segment list and only differ in `yd`.
- The zero reference baked into that file matches
  `config/pose-zero-offsets.json` to within 0.03 deg, which is why the CSV's
  angles can be used as `yd` with no conversion.
"""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass
from pathlib import Path

TICKS_PER_SECOND = 1000
AXIS_COUNT = 12
P_VECTOR_COLUMNS = 20
CSV_COLUMNS = 23


class MotionPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class Waypoint:
    label: str
    angles_deg: tuple[float, ...]


@dataclass(frozen=True)
class Segment:
    """One P-Vector's worth of time, shared by every axis in the motion set."""

    kind: str  # entry | travel | dwell | home
    ticks: int
    targets_deg: tuple[float, ...]
    label: str = ""

    @property
    def is_shot(self) -> bool:
        return self.kind == "dwell"


def load_waypoints(csv_file: Path, data_rows: list[int], axes: tuple[int, ...]) -> list[Waypoint]:
    """Read 1-indexed data rows (header excluded) out of a pose-sample CSV."""
    with csv_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    columns = [f"axis_{axis}_deg" for axis in axes]
    missing = [column for column in columns if rows and column not in rows[0]]
    if missing:
        raise MotionPlanError(f"CSV에 없는 축 열입니다: {missing}")

    waypoints = []
    for number in data_rows:
        if not 1 <= number <= len(rows):
            raise MotionPlanError(f"{csv_file.name}에 {number}번 행이 없습니다 (총 {len(rows)}행)")
        row = rows[number - 1]
        waypoints.append(
            Waypoint(label=str(number), angles_deg=tuple(float(row[column]) for column in columns))
        )
    return waypoints


def joint_distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Largest single-axis move — that axis is what sets the segment duration."""
    return max(abs(x - y) for x, y in zip(a, b))


def optimize_order(waypoints: list[Waypoint], home_deg: tuple[float, ...]) -> list[int]:
    """Shortest home -> every waypoint -> home route, by total joint travel.

    Brute force is fine at the sizes we actually capture (8-9 waypoints, so at
    most 9! = 362,880 permutations); anything larger falls back to
    nearest-neighbour so this never becomes the slow part of the pipeline.
    """
    count = len(waypoints)
    if count <= 2:
        return list(range(count))

    def route_cost(order: tuple[int, ...] | list[int]) -> float:
        angles = [waypoints[index].angles_deg for index in order]
        return (
            joint_distance(home_deg, angles[0])
            + sum(joint_distance(angles[k], angles[k + 1]) for k in range(count - 1))
            + joint_distance(angles[-1], home_deg)
        )

    if count <= 9:
        return list(min(itertools.permutations(range(count)), key=route_cost))

    remaining = set(range(count))
    order = []
    current = home_deg
    while remaining:
        nearest = min(remaining, key=lambda i: joint_distance(current, waypoints[i].angles_deg))
        order.append(nearest)
        remaining.discard(nearest)
        current = waypoints[nearest].angles_deg
    return order


def build_schedule(
    waypoints: list[Waypoint],
    order: list[int],
    *,
    home_deg: tuple[float, ...],
    total_seconds: float,
    travel_deg_per_s: float,
    transit_deg_per_s: float,
    min_travel_ticks: int = 400,
    min_transit_ticks: int = 600,
    min_dwell_ticks: int = 400,
) -> list[Segment]:
    """Lay the sweep out on a fixed time budget.

    Travel duration comes from the joint distance and the speed limit; whatever
    is left of `total_seconds` is split evenly across the waypoint pauses. That
    ordering matters — the leftover time is what the subject actually uses to
    hold a pose, so it should grow when the route gets shorter rather than
    being spent crawling between points.
    """
    if not order:
        raise MotionPlanError("웨이포인트가 없습니다")

    route = [waypoints[index] for index in order]
    total_ticks = round(total_seconds * TICKS_PER_SECOND)

    def travel_ticks(a: tuple[float, ...], b: tuple[float, ...], speed: float, floor: int) -> int:
        if speed <= 0:
            raise MotionPlanError("이동 속도는 0보다 커야 합니다")
        return max(floor, round(joint_distance(a, b) / speed * TICKS_PER_SECOND))

    entry = travel_ticks(home_deg, route[0].angles_deg, transit_deg_per_s, min_transit_ticks)
    home = travel_ticks(route[-1].angles_deg, home_deg, transit_deg_per_s, min_transit_ticks)
    travels = [
        travel_ticks(route[k].angles_deg, route[k + 1].angles_deg, travel_deg_per_s, min_travel_ticks)
        for k in range(len(route) - 1)
    ]

    moving = entry + home + sum(travels)
    dwell_budget = total_ticks - moving
    dwell = dwell_budget // len(route)
    if dwell < min_dwell_ticks:
        raise MotionPlanError(
            f"{total_seconds:.1f}초로는 정지 시간이 부족합니다 "
            f"(이동만 {moving / TICKS_PER_SECOND:.1f}초, 정지 {dwell}틱 < 최소 {min_dwell_ticks}틱). "
            f"총 시간을 늘리거나 이동 속도를 올리세요"
        )
    # Give the rounding remainder to the first pause so the slot lands exactly
    # on the requested duration.
    remainder = dwell_budget - dwell * len(route)

    segments = [Segment("entry", entry, route[0].angles_deg, "진입")]
    for k, waypoint in enumerate(route):
        ticks = dwell + (remainder if k == 0 else 0)
        segments.append(Segment("dwell", ticks, waypoint.angles_deg, f"정지 wp{k + 1}(행{waypoint.label})"))
        if k + 1 < len(route):
            segments.append(
                Segment("travel", travels[k], route[k + 1].angles_deg, f"wp{k + 1}→wp{k + 2}")
            )
    segments.append(Segment("home", home, home_deg, "홈복귀"))

    if len(segments) > P_VECTOR_COLUMNS:
        raise MotionPlanError(
            f"P-Vector가 {len(segments)}칸 필요한데 슬롯당 {P_VECTOR_COLUMNS}칸뿐입니다. "
            f"웨이포인트를 줄이세요"
        )
    return segments


def shot_times_seconds(segments: list[Segment]) -> list[tuple[str, float, float]]:
    """(label, window start, window end) for each pause, in seconds from play()."""
    windows = []
    tick = 0
    for segment in segments:
        if segment.is_shot:
            windows.append(
                (segment.label, tick / TICKS_PER_SECOND, (tick + segment.ticks) / TICKS_PER_SECOND)
            )
        tick += segment.ticks
    return windows


@dataclass(frozen=True)
class ShotCue:
    """One planned shutter moment, relative to the start of a slot's playback."""

    at_seconds: float
    waypoint: str | None  # set on the one guaranteed shot per pause


@dataclass(frozen=True)
class SweepPlan:
    slot: int
    name: str
    total_seconds: float
    windows: list[tuple[str, float, float]]  # (label, pause start, pause end)

    @classmethod
    def from_schedule(cls, data: dict) -> "SweepPlan":
        return cls(
            slot=int(data["slot"]),
            name=str(data["name"]),
            total_seconds=float(data["total_seconds"]),
            windows=[
                (str(shot["label"]), float(shot["window_start_s"]), float(shot["window_end_s"]))
                for shot in data["shots"]
            ],
        )


def load_sweep_plan(schedule_file: Path) -> SweepPlan:
    import json

    return SweepPlan.from_schedule(json.loads(schedule_file.read_text(encoding="utf-8")))


def plan_shot_times(
    plan: SweepPlan,
    *,
    target_count: int = 40,
    max_rate_hz: float = 2.0,
    window_margin_s: float = 0.15,
) -> list[ShotCue]:
    """Pick when to fire the shutter across one slot's playback.

    Every pause gets one guaranteed shot at its midpoint — those are the framed
    compositions the whole sweep exists to capture, so they are placed first and
    never dropped. The rest of the budget goes to extra shots inside the pauses
    (still sharp, arm stopped) before any go to the moving stretches, and the
    whole thing is rate-limited: the phone webapp bridge is only proven to about
    2 Hz, and overrunning it drops frames rather than capturing more.
    """
    spacing = 1.0 / max_rate_hz
    chosen = [
        ShotCue(at_seconds=(start + end) / 2, waypoint=label)
        for label, start, end in plan.windows
    ]

    extras: list[float] = []
    for _, start, end in plan.windows:
        first, last = start + window_margin_s, end - window_margin_s
        middle = (start + end) / 2
        offset = spacing
        while middle - offset >= first or middle + offset <= last:
            if middle - offset >= first:
                extras.append(middle - offset)
            if middle + offset <= last:
                extras.append(middle + offset)
            offset += spacing

    # The moving stretches: everything between the end of one pause and the
    # start of the next, plus the run-in and the run-home.
    edges = [0.0]
    for _, start, end in plan.windows:
        edges += [start, end]
    edges.append(plan.total_seconds)
    moving: list[float] = []
    for start, end in zip(edges[0::2], edges[1::2]):
        moment = start + spacing
        while moment <= end - window_margin_s:
            moving.append(moment)
            moment += spacing

    # Pauses outrank moving stretches (arm stopped, framing is the intended
    # one), but every pick still has to clear the rate limit against *all*
    # already-chosen shots — two shots either side of a window boundary are
    # just as likely to overrun the phone bridge as two inside one window.
    for candidate in sorted(extras) + sorted(moving):
        if len(chosen) >= target_count:
            break
        if all(abs(candidate - cue.at_seconds) >= spacing for cue in chosen):
            chosen.append(ShotCue(at_seconds=candidate, waypoint=None))
    return sorted(chosen, key=lambda cue: cue.at_seconds)


def render_motion_csv(
    segments: list[Segment],
    *,
    ms_id: int,
    ms_name: str,
    axes: tuple[int, ...],
    robot_id: int = 1,
    file_version: str = "3.0.0",
    accel: int = 0,
    decel: int = 0,
) -> str:
    """Serialise to the exact shape the PCM already has on its SD card."""
    if len(segments) > P_VECTOR_COLUMNS:
        raise MotionPlanError(f"P-Vector {len(segments)}칸 > {P_VECTOR_COLUMNS}칸")

    def pad(values: list[str]) -> list[str]:
        return values + [""] * (CSV_COLUMNS - len(values))

    rows = [
        pad([("robot_id"), str(robot_id)]),
        pad(["file_version", file_version]),
        pad(["MS ID", "MS Name", "MD ID", "P vector"]),
        pad(["", "", ""] + [str(i) for i in range(P_VECTOR_COLUMNS)]),
    ]

    for axis in range(AXIS_COUNT):
        if axis in axes:
            position = axes.index(axis)
            cells = [
                f"{segment.targets_deg[position]:.1f},{segment.ticks},{accel},{decel}"
                for segment in segments
            ]
            cells += ["-"] * (P_VECTOR_COLUMNS - len(cells))
        else:
            cells = ["-"] * P_VECTOR_COLUMNS
        head = [str(ms_id), ms_name] if axis == 0 else ["", ""]
        rows.append(pad(head + [f"MD{axis}"] + cells))

    lines = []
    for row in rows:
        lines.append(",".join(_quote(value) for value in row))
    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    return f'"{value}"' if "," in value else value

"""Speed and distance triggers named in the brief: top speed at 30 km/h or more, sprint at 25.2 km/h
or more, and every 2 km of distance.

What exists, stated plainly: the match engine generates events, not tracking. A separate synthetic
kinematics layer (`agon.synth.kinematics`, DATASET_CARD.md section 2) derives 1 Hz positions for
every player from a finished event log with a simple, documented motion model and speed and
acceleration caps. Those positions are synthetic, not measured, and a 1 Hz step speed is a 1-second
average. This module has three parts:

1. `triggers_from_samples` works on any per-player position stream (milliseconds, decimetres). It
   is the trigger logic, tested on hand-built samples.
2. `triggers_from_segments` runs the same logic over several streams of one player (one per
   period), so no speed step bridges half time while distance milestones count across the match.
   It is what the synthetic tracking layer is read with.
3. `carry_proxy` is the events-only adapter: a carry's start-to-end displacement divided by the
   time to the next event, at one-second resolution. That is an ON-BALL DISPLACEMENT PROXY, not a
   player's running speed: the clock is whole seconds and the path between two points is unknown.
   Distance milestones are not derivable from events at all (a player's running distance is mostly
   off the ball), and the output says so instead of emitting a number.

Units: speed in tenths of km/h (an integer), distance in decimetres.
"""

from __future__ import annotations

from math import isqrt
from typing import Any, Iterable, Mapping, Sequence

from agon.interpret.frame import MatchView, clock_seconds, minute_label

SPRINT_KMH_X10 = 252
TOP_SPEED_KMH_X10 = 300
MILESTONE_DM = 20_000  # 2 km
# The two notes below are part of `interpret_match` output, whose hash tests/test_interpret.py pins,
# so their bytes stay as they are. They describe the events-only proxy and remain true: the match
# engine (synth-v0.2) itself generates no tracking. TRACKING_NOTE describes the separate layer.
EVENTS_ONLY_NOTE = (
    "events only: carry displacement over whole-second gaps to the next event; an on-ball proxy, "
    "not running speed. synth-v0.2 generates no tracking data."
)
MILESTONES_UNAVAILABLE = (
    "not derivable from events: running distance needs tracking data, which synth-v0.2 does not generate"
)
TRACKING_NOTE = (
    "synthetic tracking: 1 Hz positions derived from the event log by agon.synth.kinematics, a simple "
    "motion model with team shape, event anchors and speed and acceleration caps; not measured "
    "positions. Speeds are 1-second averages; distance is the sum of 1-second steps."
)


def speed_kmh_x10(dist_dm: int, dt_ms: int) -> int:
    """Speed in tenths of km/h, floor. 1 dm per ms is 360 km/h, so the factor is 3600."""
    if dt_ms <= 0:
        raise ValueError("time step must be positive")
    return dist_dm * 3600 // dt_ms


Segment = tuple[Any, Sequence[tuple[int, int, int]]]


def _scan(segments: Sequence[Segment]) -> tuple[int, int, list[tuple[Any, int, int]], list[tuple[Any, int]], list[tuple[int, Any, int]]]:
    """The trigger logic over one or more sample streams of one player.

    A sprint is a maximal run of consecutive steps at or above 25.2 km/h and ends with its stream; a
    top-speed trigger is a step at or above 30 km/h; a milestone fires each time distance, counted
    across all streams, crosses a 2 km mark. No step is taken between two streams.
    """
    top = 0
    dist = 0
    sprints: list[tuple[Any, int, int]] = []
    top_speed: list[tuple[Any, int]] = []
    milestones: list[tuple[int, Any, int]] = []
    for label, samples in segments:
        run_start: int | None = None
        for (t0, x0, y0), (t1, x1, y1) in zip(samples, samples[1:]):
            step = isqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
            v = speed_kmh_x10(step, t1 - t0)
            top = max(top, v)
            before = dist
            dist += step
            for mark in range(before // MILESTONE_DM + 1, dist // MILESTONE_DM + 1):
                milestones.append((mark * MILESTONE_DM // 10_000, label, t1))
            if v >= TOP_SPEED_KMH_X10:
                top_speed.append((label, t1))
            if v >= SPRINT_KMH_X10:
                if run_start is None:
                    run_start = t0
            elif run_start is not None:
                sprints.append((label, run_start, t0))
                run_start = None
        if run_start is not None and samples:
            sprints.append((label, run_start, samples[-1][0]))
    return top, dist, sprints, top_speed, milestones


def triggers_from_samples(player: str, samples: Sequence[tuple[int, int, int]]) -> dict[str, Any]:
    """Triggers for one player's (t_ms, x_dm, y_dm) samples in time order (rules in `_scan`)."""
    top, dist, sprints, top_speed, milestones = _scan([(None, samples)])
    return {
        "player": player,
        "source": "tracking_samples",
        "top_speed_kmh_x10": top,
        "distance_dm": dist,
        "sprints": [{"start_ms": a, "end_ms": b} for _, a, b in sprints],
        "top_speed_triggers_ms": [t for _, t in top_speed],
        "distance_milestones": [{"km": km, "t_ms": t} for km, _, t in milestones],
    }


def triggers_from_segments(player: str, segments: Sequence[tuple[int, Sequence[tuple[int, int, int]]]]) -> dict[str, Any]:
    """Triggers for one player over (segment, samples) streams, for example one per period.

    Times are each stream's own `t_ms` (for a match, the match clock in ms, which repeats across
    periods), so every trigger carries its `segment`. Distance and milestones count across segments.
    """
    top, dist, sprints, top_speed, milestones = _scan(segments)
    return {
        "player": player,
        "source": "tracking_segments",
        "top_speed_kmh_x10": top,
        "distance_dm": dist,
        "sprints": [{"segment": seg, "start_ms": a, "end_ms": b} for seg, a, b in sprints],
        "top_speed_triggers": [{"segment": seg, "t_ms": t} for seg, t in top_speed],
        "distance_milestones": [{"km": km, "segment": seg, "t_ms": t} for km, seg, t in milestones],
    }


def carry_proxy(view: MatchView) -> dict[str, Any]:
    """Events-only proxy per player: carry displacement speed and flags at the brief's thresholds."""
    events = view.events
    per_player: dict[str, dict[str, Any]] = {}
    for i, e in enumerate(events):
        if e["type"] != "carry":
            continue
        nxt = events[i + 1] if i + 1 < len(events) else None
        if nxt is None or nxt["period"] != e["period"]:
            continue
        dt_s = max(1, clock_seconds(nxt) - clock_seconds(e))
        dist = isqrt((e["end_x_dm"] - e["x_dm"]) ** 2 + (e["end_y_dm"] - e["y_dm"]) ** 2)
        v = speed_kmh_x10(dist, dt_s * 1000)
        row = per_player.setdefault(e["player"], {"player": e["player"], "team": e["team"], "carries": 0, "carry_distance_dm": 0, "top_proxy_kmh_x10": 0, "sprint_proxy": [], "top_speed_proxy": []})
        row["carries"] += 1
        row["carry_distance_dm"] += dist
        row["top_proxy_kmh_x10"] = max(row["top_proxy_kmh_x10"], v)
        mark = {"event_id": e["event_id"], "minute": minute_label(e), "kmh_x10": v}
        if v >= SPRINT_KMH_X10:
            row["sprint_proxy"].append(mark)
        if v >= TOP_SPEED_KMH_X10:
            row["top_speed_proxy"].append(mark)
    return {
        "source": "events_carry_proxy",
        "note": EVENTS_ONLY_NOTE,
        "distance_milestones": None,
        "distance_milestones_reason": MILESTONES_UNAVAILABLE,
        "thresholds_kmh_x10": {"sprint": SPRINT_KMH_X10, "top_speed": TOP_SPEED_KMH_X10},
        "players": [per_player[k] for k in sorted(per_player)],
    }


def samples_from_points(points: Iterable[Mapping[str, int]]) -> list[tuple[int, int, int]]:
    """Convenience for callers holding dict samples {t_ms, x_dm, y_dm}."""
    return [(p["t_ms"], p["x_dm"], p["y_dm"]) for p in points]

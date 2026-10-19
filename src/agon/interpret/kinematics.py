"""Speed and distance triggers named in the brief: top speed at 30 km/h or more, sprint at 25.2 km/h
or more, and every 2 km of distance.

What exists today, stated plainly: synth-v0.2 generates NO tracking data (DATASET_CARD.md section 8).
So this module has two parts:

1. `triggers_from_samples` works on any per-player position stream (milliseconds, decimetres). It
   is the real trigger logic, tested on hand-built samples, and is what a tracking feed would use.
2. `carry_proxy` is the events-only adapter: a carry's start-to-end displacement divided by the
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
EVENTS_ONLY_NOTE = (
    "events only: carry displacement over whole-second gaps to the next event; an on-ball proxy, "
    "not running speed. synth-v0.2 generates no tracking data."
)
MILESTONES_UNAVAILABLE = (
    "not derivable from events: running distance needs tracking data, which synth-v0.2 does not generate"
)


def speed_kmh_x10(dist_dm: int, dt_ms: int) -> int:
    """Speed in tenths of km/h, floor. 1 dm per ms is 360 km/h, so the factor is 3600."""
    if dt_ms <= 0:
        raise ValueError("time step must be positive")
    return dist_dm * 3600 // dt_ms


def triggers_from_samples(player: str, samples: Sequence[tuple[int, int, int]]) -> dict[str, Any]:
    """Triggers for one player's (t_ms, x_dm, y_dm) samples in time order.

    A sprint is a maximal run of consecutive steps at or above 25.2 km/h; a top-speed trigger is a
    step at or above 30 km/h; a milestone fires each time cumulative distance crosses a 2 km mark.
    """
    top = 0
    sprints: list[dict[str, int]] = []
    top_speed: list[int] = []
    milestones: list[dict[str, int]] = []
    dist = 0
    run_start: int | None = None
    for (t0, x0, y0), (t1, x1, y1) in zip(samples, samples[1:]):
        step = isqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
        v = speed_kmh_x10(step, t1 - t0)
        top = max(top, v)
        before = dist
        dist += step
        for mark in range(before // MILESTONE_DM + 1, dist // MILESTONE_DM + 1):
            milestones.append({"km": mark * MILESTONE_DM // 10_000, "t_ms": t1})
        if v >= TOP_SPEED_KMH_X10:
            top_speed.append(t1)
        if v >= SPRINT_KMH_X10:
            if run_start is None:
                run_start = t0
        elif run_start is not None:
            sprints.append({"start_ms": run_start, "end_ms": t0})
            run_start = None
    if run_start is not None and samples:
        sprints.append({"start_ms": run_start, "end_ms": samples[-1][0]})
    return {
        "player": player,
        "source": "tracking_samples",
        "top_speed_kmh_x10": top,
        "distance_dm": dist,
        "sprints": sprints,
        "top_speed_triggers_ms": top_speed,
        "distance_milestones": milestones,
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

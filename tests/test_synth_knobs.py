"""Each style knob must move its own metric in the stated direction (a knob without a reader is not a feature).

This is a coarse v0 check on a few seeds, not metric M4 (`agon.synth.sensitivity`, not built yet).
"""

from __future__ import annotations

from dataclasses import replace

from agon.synth.engine import simulate_match
from agon.synth.world import League, Style, build_world

SEEDS = (21, 22, 23)


def _with_style(league: League, **knobs: int) -> League:
    clubs = tuple(replace(c, style=replace(c.style, **knobs)) for c in league.clubs)
    return replace(league, clubs=clubs)


def _totals(**knobs: int) -> dict[str, int]:
    totals = {"passes": 0, "long_forward": 0, "lateral_dm": 0, "pressures": 0, "actions": 0}
    for seed in SEEDS:
        league = _with_style(build_world(seed), **knobs)
        for ev in simulate_match(league, 0).events:
            if ev.type == "pass" and ev.end_x_dm is not None and ev.end_y_dm is not None:
                totals["passes"] += 1
                if ev.end_x_dm - ev.x_dm >= 130:
                    totals["long_forward"] += 1
                totals["lateral_dm"] += abs(ev.end_y_dm - ev.y_dm)
            if ev.type in ("pass", "carry", "shot"):
                totals["actions"] += 1
            if ev.type == "pressure":
                totals["pressures"] += 1
    return totals


def test_directness_raises_long_forward_pass_share() -> None:
    low, high = _totals(directness=200), _totals(directness=800)
    assert high["long_forward"] * low["passes"] > low["long_forward"] * high["passes"]


def test_width_raises_lateral_pass_distance() -> None:
    low, high = _totals(width=200), _totals(width=800)
    assert high["lateral_dm"] * low["passes"] > low["lateral_dm"] * high["passes"]


def test_press_height_raises_pressure_rate() -> None:
    low, high = _totals(press_height=200), _totals(press_height=800)
    assert high["pressures"] * low["actions"] > low["pressures"] * high["actions"]


def test_style_dataclass_is_integer_only() -> None:
    s = Style(500, 500, 500)
    assert all(isinstance(v, int) for v in s.to_dict().values())

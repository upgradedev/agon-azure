"""Interpret win probability: a remaining-goals Poisson model fitted on training seeds 1 to 500.

Model: from any game state, each side's remaining goals are an independent Poisson count whose mean
is the sum, over the 15-minute bins still to play, of that side's fitted goal rate (home and away
separately, per bin) times the seconds left in the bin. A side with more players on the pitch has
its rate multiplied by the fitted `up` factor and the side with fewer by `down`. Added time is the
training mean per half. Score effects (a trailing side pressing late) are NOT modelled; the bins
carry the average late-game rise only. That is a stated limit, not a hidden one.

This is the model the Desk Editor and `wp.swing` read. It never sees the generator's internals.
Outputs are integer ppm triples (home win, draw, away win) summing to 1_000_000.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agon.interpret._fixed import outcome_ppm
from agon.interpret.features import xg_ppm
from agon.interpret.frame import BIN15_SECONDS, HALF_SECONDS, MatchView, clock_seconds, minute_label
from agon.interpret.model import load_model

FULL_SIDE = 11
SENDING_OFF = ("red", "second_yellow")
CANDIDATE_XG_PPM = 200_000  # a shot at or above this modelled xG is a candidate moment


@dataclass(frozen=True)
class GameState:
    period: int
    clock: int  # engine clock seconds (does not reset at half time)
    home_goals: int
    away_goals: int
    home_players: int = FULL_SIDE
    away_players: int = FULL_SIDE


def remaining_seconds_by_bin(period: int, clock: int, added: tuple[int, int]) -> list[int]:
    """Seconds left in each of the six 15-minute bins, using the expected added time per half."""
    bounds = [
        (1, 0, BIN15_SECONDS),
        (1, BIN15_SECONDS, 2 * BIN15_SECONDS),
        (1, 2 * BIN15_SECONDS, HALF_SECONDS + added[0]),
        (2, HALF_SECONDS, HALF_SECONDS + BIN15_SECONDS),
        (2, HALF_SECONDS + BIN15_SECONDS, HALF_SECONDS + 2 * BIN15_SECONDS),
        (2, HALF_SECONDS + 2 * BIN15_SECONDS, 2 * HALF_SECONDS + added[1]),
    ]
    out = []
    for p, lo, hi in bounds:
        if p < period:
            out.append(0)
        elif p > period:
            out.append(hi - lo)
        else:
            out.append(max(0, hi - max(lo, clock)))
    return out


def remaining_means_nano(state: GameState, model: Mapping[str, Any] | None = None) -> tuple[int, int]:
    """Expected remaining goals (home, away) in nano-goals."""
    w = (model or load_model())["wp"]
    added = (w["expected_added_seconds"][0], w["expected_added_seconds"][1])
    secs = remaining_seconds_by_bin(state.period, state.clock, added)
    lam_h = sum(r * s for r, s in zip(w["rate_nano"]["home"], secs))
    lam_a = sum(r * s for r, s in zip(w["rate_nano"]["away"], secs))
    up, down = w["man_advantage_permille"]["up"], w["man_advantage_permille"]["down"]
    if state.home_players > state.away_players:
        lam_h, lam_a = lam_h * up // 1000, lam_a * down // 1000
    elif state.home_players < state.away_players:
        lam_h, lam_a = lam_h * down // 1000, lam_a * up // 1000
    return lam_h, lam_a


def win_probability(state: GameState, model: Mapping[str, Any] | None = None) -> dict[str, int]:
    lam_h, lam_a = remaining_means_nano(state, model)
    h, d, a = outcome_ppm(lam_h, lam_a, state.home_goals - state.away_goals)
    return {"home_ppm": h, "draw_ppm": d, "away_ppm": a}


def apply_event(state: GameState, event: Mapping[str, Any], home_id: str) -> GameState:
    """The state after one event: goals and sendings-off change it, the clock moves to the event."""
    hg, ag, hp, ap = state.home_goals, state.away_goals, state.home_players, state.away_players
    is_home = event["team"] == home_id
    if event["type"] == "shot" and event["outcome"] == "goal":
        hg, ag = (hg + 1, ag) if is_home else (hg, ag + 1)
    elif event["type"] == "card" and event["outcome"] in SENDING_OFF:
        hp, ap = (hp - 1, ap) if is_home else (hp, ap - 1)
    return GameState(event["period"], clock_seconds(event), hg, ag, hp, ap)


def state_before(view: MatchView, index: int) -> GameState:
    """State after every event before `index`, with the clock at event `index`."""
    s = GameState(1, 0, 0, 0)
    for e in view.events[:index]:
        s = apply_event(s, e, view.home_id)
    e = view.events[index]
    return GameState(e["period"], clock_seconds(e), s.home_goals, s.away_goals, s.home_players, s.away_players)


def _team_view(probs: Mapping[str, int], team_is_home: bool) -> int:
    """Win probability for the acting team, ppm."""
    return probs["home_ppm"] if team_is_home else probs["away_ppm"]


def swing(view: MatchView, event_id: str, model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Win probability just before and just after one event, plus the shot leverage when it is a shot.

    `delta_home_ppm` is the change in the home side's win probability. For a shot,
    `wp_if_goal` is the state had it gone in and `expected_swing_ppm` = xG x (team win probability
    if goal - team win probability before): the leverage of a chance whether or not it was scored,
    which is how a 71st-minute save becomes a candidate moment.
    """
    m = model or load_model()
    i = view.position(event_id)
    e = view.events[i]
    before_state = state_before(view, i)
    after_state = apply_event(before_state, e, view.home_id)
    before = win_probability(before_state, m)
    after = win_probability(after_state, m)
    team_home = e["team"] == view.home_id
    out: dict[str, Any] = {
        "event_id": event_id,
        "type": e["type"],
        "outcome": e["outcome"],
        "team": e["team"],
        "period": e["period"],
        "minute": minute_label(e),
        "score_before": {"home": before_state.home_goals, "away": before_state.away_goals},
        "before": before,
        "after": after,
        "delta_home_ppm": after["home_ppm"] - before["home_ppm"],
        "delta_team_ppm": _team_view(after, team_home) - _team_view(before, team_home),
    }
    if e["type"] == "shot":
        goal = dict(e, outcome="goal")
        if_goal = win_probability(apply_event(before_state, goal, view.home_id), m)
        xg = xg_ppm(e, view.home_id, m)
        gain = _team_view(if_goal, team_home) - _team_view(before, team_home)
        out["xg_ppm"] = xg
        out["wp_if_goal"] = if_goal
        out["expected_swing_ppm"] = (xg * gain + 500_000) // 1_000_000
    return out


def series(view: MatchView, model: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Win probability at the end of every engine minute that has events, plus kick-off and full time."""
    m = model or load_model()
    out = [{"period": 1, "minute": "0", "clock": 0, "score": {"home": 0, "away": 0}, **win_probability(GameState(1, 0, 0, 0), m)}]
    s = GameState(1, 0, 0, 0)
    events = view.events
    for i, e in enumerate(events):
        s = apply_event(s, e, view.home_id)
        last_of_minute = i + 1 == len(events) or (events[i + 1]["period"], events[i + 1]["minute"]) != (e["period"], e["minute"])
        if not last_of_minute:
            continue
        at = GameState(e["period"], (e["minute"] + 1) * 60, s.home_goals, s.away_goals, s.home_players, s.away_players)
        out.append({
            "period": e["period"],
            "minute": minute_label(e),
            "clock": at.clock,
            "score": {"home": s.home_goals, "away": s.away_goals},
            **win_probability(at, m),
        })
    final = {"home": s.home_goals, "away": s.away_goals}
    hw = 1_000_000 if final["home"] > final["away"] else 0
    aw = 1_000_000 if final["away"] > final["home"] else 0
    out.append({"period": 2, "minute": "FT", "clock": None, "score": final, "home_ppm": hw, "draw_ppm": 1_000_000 - hw - aw, "away_ppm": aw})
    return out


def candidate_moments(view: MatchView, limit: int = 16, model: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Goals, sendings-off, penalties and big chances, ranked by the larger of |realised| and |expected| swing.

    Ties break on sequence order, so the ranking is deterministic.
    """
    m = model or load_model()
    picked = []
    for e in view.events:
        is_goal = e["type"] == "shot" and e["outcome"] == "goal"
        is_off = e["type"] == "card" and e["outcome"] in SENDING_OFF
        is_pen = e["type"] == "shot" and e["qualifier"] == "penalty"
        big = e["type"] == "shot" and xg_ppm(e, view.home_id, m) >= CANDIDATE_XG_PPM
        if is_goal or is_off or is_pen or big:
            sw = swing(view, e["event_id"], m)
            weight = max(abs(sw["delta_team_ppm"]), abs(sw.get("expected_swing_ppm", 0)))
            reason = "goal" if is_goal else "sending_off" if is_off else "penalty" if is_pen else "big_chance"
            picked.append((-weight, e["seq"], {"reason": reason, "weight_ppm": weight, **sw}))
    picked.sort(key=lambda t: (t[0], t[1]))
    return [p[2] for p in picked[:limit]]

"""Turning-point labels for storyline matches, derived by two independent code paths.

Path one, `labels_from_ledger`, reads the engine's own bookkeeping (`MatchResult.applied`): counters
the engine kept at the moment each card, goal, save, pressure or injury happened.

Path two, `labels_from_log`, reads nothing but the intervention plan, the match meta and the event
log as plain dicts, the same NDJSON a consumer would load. It finds the activation point, the anchor
event and every effect by scanning events. For the injury stoppage it recomputes the period's
baseline added time from the counter-based RNG instead of asking the engine.

`verify_labels` compares the two. Any difference is a failure.

Label shape (canonical JSON, integers only):
    label_id, seed, match_id, kind, side, team, period, minute, at_clock, anchor_event_id, effect,
    turning_point

`period` is the period of the activation (or anchor) event. `minute` and `at_clock` are match-clock
values and repeat across periods (period 2 restarts the clock at 45:00 while period 1 can run past
it in added time), so a moment is only identified by (period, at_clock). The tactical-shift before
and after windows count only events of the activation period.

`turning_point` is the ground truth a later evaluation scores against: the anchor event is a turning
point when the intervention visibly took effect in this match, by the rule in `turning_point()`.
"""

from __future__ import annotations

from typing import Any, Sequence

from agon.synth.engine import STOPPAGE_RANGES, MatchResult
from agon.synth.interventions import Intervention
from agon.synth.rng import Stream, StreamId

SHIFT_WINDOW_MINUTES = 15
HEROICS_MIN_SAVES = 3
ONBALL = frozenset({"pass", "carry", "shot"})


def turning_point(kind: str, anchor_event_id: str | None, effect: dict[str, int]) -> bool:
    """The rule that makes an applied intervention a ground-truth turning point."""
    if anchor_event_id is None:
        return False
    if kind == "red_card":
        return True
    if kind == "tactical_shift":
        return effect["press_after"] * effect["onball_before"] > effect["press_before"] * effect["onball_after"]
    if kind == "late_collapse":
        return effect["goals_against_after"] >= 1
    if kind == "goalkeeper_heroics":
        return effect["saves_after"] >= HEROICS_MIN_SAVES
    if kind == "injury_stoppage":
        return effect["stoppage_seconds"] > 0
    raise ValueError(f"unknown kind {kind}")


def _label(
    seed: int,
    plan: Intervention,
    match_id: str,
    team: str,
    period: int | None,
    at_clock: int | None,
    anchor: str | None,
    effect: dict[str, int],
) -> dict[str, Any]:
    return {
        "label_id": f"{match_id}/{plan.kind}",
        "seed": seed,
        "match_id": match_id,
        "kind": plan.kind,
        "side": plan.side,
        "team": team,
        "period": None if at_clock is None else period,
        "minute": None if at_clock is None else at_clock // 60,
        "at_clock": at_clock,
        "anchor_event_id": anchor,
        "effect": effect,
        "turning_point": turning_point(plan.kind, anchor, effect),
    }


def _window_sums(by_minute: dict[tuple[int, int], int], period: int, m0: int) -> tuple[int, int]:
    """Counts in the minutes before and from `m0`, inside `period` only: windows never span half-time."""
    before = sum(n for (p, m), n in by_minute.items() if p == period and m0 - SHIFT_WINDOW_MINUTES <= m < m0)
    after = sum(n for (p, m), n in by_minute.items() if p == period and m0 <= m < m0 + SHIFT_WINDOW_MINUTES)
    return before, after


# ----------------------------------------------------------------- path one: the engine ledger


def labels_from_ledger(seed: int, plan: Intervention, result: MatchResult) -> dict[str, Any]:
    entries = [a for a in result.applied if a["kind"] == plan.kind and a["side"] == plan.side]
    if len(entries) != 1:
        raise ValueError(f"expected one ledger entry for {plan.kind}, found {len(entries)}")
    a = entries[0]
    kind = plan.kind
    if not a["activated"] and kind in ("tactical_shift", "late_collapse", "goalkeeper_heroics"):
        return _label(seed, plan, result.meta["match_id"], a["team"], None, None, None, _empty_effect(kind))
    at_clock = a["at_clock"]
    period = a["at_period"]
    anchor = a["anchor_event_id"]
    if kind == "red_card":
        effect = {"goals_for_after": a["goals_for_after"], "goals_against_after": a["goals_against_after"]}
        if anchor is None:
            at_clock = None
    elif kind == "tactical_shift":
        m0 = at_clock // 60 if at_clock is not None else 0
        pb, pa = _window_sums(a["press_by_minute"], period, m0)
        ob, oa = _window_sums(a["opp_onball_by_minute"], period, m0)
        effect = {"press_before": pb, "onball_before": ob, "press_after": pa, "onball_after": oa}
    elif kind == "late_collapse":
        effect = {"goals_for_after": a["goals_for_after"], "goals_against_after": a["goals_against_after"]}
    elif kind == "goalkeeper_heroics":
        effect = {"saves_after": a["saves_after"], "goals_against_after": a["goals_against_after"]}
    elif kind == "injury_stoppage":
        effect = {"stoppage_seconds": a["stoppage_seconds"]}
        if anchor is None:
            at_clock = None
    else:
        raise ValueError(kind)
    if not a["activated"]:
        at_clock = None
    return _label(seed, plan, result.meta["match_id"], a["team"], period, at_clock, anchor, effect)


# ----------------------------------------------------------------- path two: the event log only


def _clock(ev: dict[str, Any]) -> int:
    return ev["minute"] * 60 + ev["second"]


def activation_index(events: Sequence[dict[str, Any]], period: int, minute: int) -> int | None:
    """Index of the first event of the first possession chain that opens in `period` (or later) at a
    clock of `minute` * 60 or more. The period is compared first: a period-1 event at 48:17 of
    first-half added time does not reach a plan for period 2, minute 48.

    Substitutions are skipped: they are made between chains and carry the previous chain's id.
    """
    seen: set[int] = set()
    for i, ev in enumerate(events):
        if ev["type"] == "sub":
            continue
        chain = ev["possession_chain_id"]
        if chain in seen:
            continue
        seen.add(chain)
        if ev["period"] > period or (ev["period"] == period and _clock(ev) >= minute * 60):
            return i
    return None


def _goals_after(events: Sequence[dict[str, Any]], start: int, team: str) -> tuple[int, int]:
    gf = ga = 0
    for ev in events[start:]:
        if ev["type"] == "shot" and ev["outcome"] == "goal":
            if ev["team"] == team:
                gf += 1
            else:
                ga += 1
    return gf, ga


def baseline_added_seconds(seed: int, match_index: int) -> tuple[int, int]:
    """Added time per period without any injury, recomputed straight from the RNG."""
    s = Stream(seed, match_index + 1, StreamId.STOPPAGE)
    return s.between(*STOPPAGE_RANGES[0]), s.between(*STOPPAGE_RANGES[1])


def labels_from_log(seed: int, plan: Intervention, meta: dict[str, Any], events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    kind = plan.kind
    team = meta[plan.side]
    match_id = meta["match_id"]
    start = activation_index(events, plan.period, plan.minute)
    period: int | None = None
    at_clock: int | None = None
    anchor: str | None = None

    if kind == "red_card":
        effect = {"goals_for_after": 0, "goals_against_after": 0}
        if start is not None:
            for i in range(start, len(events)):
                ev = events[i]
                if ev["type"] == "card" and ev["team"] == team and ev["outcome"] == "red":
                    anchor, at_clock, period = ev["event_id"], _clock(ev), ev["period"]
                    gf, ga = _goals_after(events, i + 1, team)
                    effect = {"goals_for_after": gf, "goals_against_after": ga}
                    break
    elif kind == "injury_stoppage":
        effect = {"stoppage_seconds": 0}
        for ev in events:
            if ev["type"] == "sub" and ev["team"] == team and ev["qualifier"] == "injury":
                anchor, at_clock, period = ev["event_id"], _clock(ev), ev["period"]
                base = baseline_added_seconds(seed, meta["match_index"])
                key = "first_half" if ev["period"] == 1 else "second_half"
                effect = {"stoppage_seconds": meta["added_seconds"][key] - base[ev["period"] - 1]}
                break
    elif start is None:
        effect = _empty_effect(kind)
    else:
        at_clock = _clock(events[start])
        period = events[start]["period"]
        if kind == "tactical_shift":
            anchor = events[start]["event_id"]
            m0 = at_clock // 60
            press: dict[tuple[int, int], int] = {}
            onball: dict[tuple[int, int], int] = {}
            for ev in events:
                key = (ev["period"], ev["minute"])
                if ev["type"] == "pressure" and ev["team"] == team:
                    press[key] = press.get(key, 0) + 1
                elif ev["type"] in ONBALL and ev["team"] != team:
                    onball[key] = onball.get(key, 0) + 1
            pb, pa = _window_sums(press, period, m0)
            ob, oa = _window_sums(onball, period, m0)
            effect = {"press_before": pb, "onball_before": ob, "press_after": pa, "onball_after": oa}
        elif kind == "late_collapse":
            gf, ga = _goals_after(events, start, team)
            effect = {"goals_for_after": gf, "goals_against_after": ga}
            for ev in events[start:]:
                if ev["type"] == "shot" and ev["outcome"] == "goal" and ev["team"] != team:
                    anchor = ev["event_id"]
                    break
        elif kind == "goalkeeper_heroics":
            saves = 0
            for ev in events[start:]:
                if ev["type"] == "shot" and ev["outcome"] == "saved" and ev["team"] != team:
                    saves += 1
                    if anchor is None:
                        anchor = ev["event_id"]
            _, ga = _goals_after(events, start, team)
            effect = {"saves_after": saves, "goals_against_after": ga}
        else:
            raise ValueError(kind)
    return _label(seed, plan, match_id, team, period, at_clock, anchor, effect)


def _empty_effect(kind: str) -> dict[str, int]:
    return {
        "tactical_shift": {"press_before": 0, "onball_before": 0, "press_after": 0, "onball_after": 0},
        "late_collapse": {"goals_for_after": 0, "goals_against_after": 0},
        "goalkeeper_heroics": {"saves_after": 0, "goals_against_after": 0},
    }[kind]

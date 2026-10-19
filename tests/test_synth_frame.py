"""Coordinate frames: stored acting-team frame versus the fixed home-attacks-high-x frame.

Why: stored events put the acting team's attack towards x = 1050, so defensive and away events
are rotated 180 degrees against the attacking team's events. A Match Centre that draws raw x_dm
puts them on the wrong half silently. These tests pin `to_fixed_frame` on hand-built events and
then check, on real generated matches, that a defender's pressure lands on the carrier's spot.
"""

from __future__ import annotations

from functools import lru_cache

from agon.synth.engine import MatchResult, simulate_match
from agon.synth.schema import COORDINATE_FRAME, FIXED_FRAME, make_event, to_fixed_frame
from agon.synth.world import build_world

HOME = "C1"
AWAY = "C2"


def _ev(**over: object):
    base = dict(
        match_id="s9-m0",
        seq=0,
        period=1,
        minute=10,
        second=0,
        type="pass",
        team=HOME,
        player="C1P07",
        x_dm=300,
        y_dm=200,
        end_x_dm=450,
        end_y_dm=260,
        possession_chain_id=1,
        outcome="complete",
        related_player="C1P09",
    )
    base.update(over)
    return make_event(**base)


def test_frame_names_are_distinct_constants() -> None:
    assert COORDINATE_FRAME == "acting_team_attacks_high_x"
    assert FIXED_FRAME == "home_attacks_high_x_both_periods"


def test_home_event_is_unchanged() -> None:
    assert to_fixed_frame(_ev(), HOME) == (300, 200, 450, 260)


def test_away_event_is_rotated_not_just_flipped() -> None:
    away = _ev(team=AWAY, player="C2P04", related_player="C2P08", x_dm=300, y_dm=200, end_x_dm=450, end_y_dm=260)
    assert to_fixed_frame(away, HOME) == (750, 480, 600, 420)


def test_away_event_in_second_period_is_not_flipped_again() -> None:
    away = _ev(team=AWAY, period=2, minute=70, player="C2P04", related_player="C2P08")
    assert to_fixed_frame(away, HOME) == (750, 480, 600, 420)


def test_mirrored_pressure_lands_on_the_away_carrier() -> None:
    # The away carrier stands at (820, 150) in the away frame. The home presser's event is stored
    # in the home frame, rotated: (1050 - 820, 680 - 150) = (230, 530). Both must meet in one spot.
    carry = _ev(team=AWAY, type="carry", player="C2P10", related_player=None, x_dm=700, y_dm=140, end_x_dm=820, end_y_dm=150)
    pressure = _ev(seq=1, type="pressure", player="C1P05", related_player=None, x_dm=230, y_dm=530, end_x_dm=None, end_y_dm=None, outcome="applied")
    _, _, cx, cy = to_fixed_frame(carry, HOME)
    px, py, pex, pey = to_fixed_frame(pressure, HOME)
    assert (cx, cy) == (230, 530)
    assert (px, py) == (cx, cy)
    assert (pex, pey) == (None, None)


def test_accepts_plain_dicts_from_ndjson() -> None:
    d = _ev(team=AWAY, player="C2P04", related_player="C2P08").to_dict()
    assert to_fixed_frame(d, HOME) == (750, 480, 600, 420)
    pressure = _ev(type="pressure", player="C1P05", related_player=None, end_x_dm=None, end_y_dm=None, outcome="applied").to_dict()
    del pressure["end_x_dm"]
    del pressure["end_y_dm"]
    assert to_fixed_frame(pressure, HOME) == (300, 200, None, None)


# ----------------------------------------------------------------- engine invariants

DEFENSIVE = frozenset({"pressure", "tackle", "foul"})
# Seeds offset from the manifest default (1, 2, 3) on purpose.
SEEDS = (11, 12, 13, 14, 15)


@lru_cache(maxsize=None)
def _match(seed: int) -> MatchResult:
    return simulate_match(build_world(seed), 0)


def _carrier_spot(ev, home: str) -> tuple[int, int]:
    x, y, ex, ey = to_fixed_frame(ev, home)
    return (ex, ey) if ex is not None and ey is not None else (x, y)


def test_generated_matches_record_the_frame() -> None:
    for seed in SEEDS:
        assert _match(seed).meta["coordinate_frame"] == COORDINATE_FRAME


def test_defensive_events_meet_the_carrier_in_the_fixed_frame() -> None:
    """Every pressure, tackle and foul sits on the attacking carrier's current spot.

    The carrier's spot is the most recent attacking-team event in the same chain: its end point
    if it has one (pass, carry), else its start (possession change, set piece). Skipping this
    conversion, or using an x-only flip, breaks this on almost every defensive event.
    """
    checked = 0
    total = 0
    for seed in SEEDS:
        result = _match(seed)
        home = result.meta["home"]
        last_attack: dict[int, object] = {}
        for ev in result.events:
            if ev.type in ("sub", "card"):
                continue
            chain = ev.possession_chain_id
            if ev.type in DEFENSIVE:
                total += 1
                attacker = last_attack.get(chain)
                assert attacker is not None, ("defensive event with no carrier in its chain", seed, ev)
                assert attacker.team != ev.team, (seed, ev)
                assert to_fixed_frame(ev, home)[:2] == _carrier_spot(attacker, home), (seed, attacker, ev)
                checked += 1
            else:
                last_attack[chain] = ev
    assert checked == total > 200, (checked, total)


def test_interception_restarts_where_the_pass_ended() -> None:
    checked = 0
    for seed in SEEDS:
        result = _match(seed)
        home = result.meta["home"]
        # Substitutions are made between chains, so they can sit between the pass and the restart.
        events = [e for e in result.events if e.type != "sub"]
        for prev, ev in zip(events, events[1:]):
            if ev.type == "possession_change" and ev.outcome == "interception":
                assert prev.type == "pass" and prev.outcome == "incomplete", (seed, prev, ev)
                assert prev.team != ev.team
                _, _, ex, ey = to_fixed_frame(prev, home)
                assert to_fixed_frame(ev, home)[:2] == (ex, ey), (seed, prev, ev)
                checked += 1
    assert checked > 50, checked

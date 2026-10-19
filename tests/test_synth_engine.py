"""Match engine: valid, internally consistent and within wide plausibility ranges.

The ranges here are sanity bounds from the task (goals 0 to 9, shots 5 to 40 per match), not the
realism bands. The bands (metric M2) live in agon.synth.bands and are tested in
test_synth_bands.py; calibrating against them must never widen these ranges.
"""

from __future__ import annotations

from functools import lru_cache

from agon.synth.engine import MAX_SUBS, MatchResult, simulate_match
from agon.synth.schema import validate_event
from agon.synth.world import build_world


@lru_cache(maxsize=None)
def _match(seed: int, match_index: int = 0) -> MatchResult:
    return simulate_match(build_world(seed), match_index)


def _clock(ev) -> int:
    return ev.minute * 60 + ev.second


def test_events_are_schema_valid() -> None:
    for seed in (1, 2, 3):
        for ev in _match(seed).events:
            assert validate_event(ev.to_dict()) == [], (seed, ev)


def test_event_ids_unique_within_and_across_matches() -> None:
    ids: list[str] = []
    for seed in (1, 2, 3):
        match_ids = [e.event_id for e in _match(seed).events]
        assert len(match_ids) == len(set(match_ids))
        ids.extend(match_ids)
    assert len(ids) == len(set(ids))


def test_score_equals_goal_events() -> None:
    for seed in range(1, 21):
        result = _match(seed)
        home, away = result.meta["home"], result.meta["away"]
        goals = {home: 0, away: 0}
        for ev in result.events:
            if ev.type == "shot" and ev.outcome == "goal":
                goals[ev.team] += 1
        assert result.meta["score"] == {"home": goals[home], "away": goals[away]}


def test_players_act_only_for_their_own_club() -> None:
    for seed in (1, 2, 3):
        for ev in _match(seed).events:
            if ev.player is not None:
                assert ev.player.startswith(ev.team + "P"), ev
            if ev.related_player is not None:
                assert ev.related_player.startswith(ev.team + "P"), ev


def test_no_actions_after_red_card_or_before_coming_on() -> None:
    for seed in range(1, 31):
        events = _match(seed).events
        gone_at: dict[str, int] = {}
        on_at: dict[str, int] = {}
        for i, ev in enumerate(events):
            if ev.type == "card" and ev.outcome in ("red", "second_yellow"):
                gone_at[ev.player] = i
            if ev.type == "sub":
                gone_at[ev.player] = i
                on_at[ev.related_player] = i
        for i, ev in enumerate(events):
            for pid in (ev.player, ev.related_player):
                if pid is None:
                    continue
                if pid in gone_at and i > gone_at[pid]:
                    raise AssertionError(f"seed {seed}: {pid} acts after leaving at {gone_at[pid]}: {ev}")
                if pid in on_at and i < on_at[pid]:
                    raise AssertionError(f"seed {seed}: {pid} acts before coming on: {ev}")


def test_substitutions_within_cap() -> None:
    for seed in range(1, 31):
        result = _match(seed)
        for team in (result.meta["home"], result.meta["away"]):
            subs = [e for e in result.events if e.type == "sub" and e.team == team]
            assert len(subs) <= MAX_SUBS
            assert all(e.period == 2 for e in subs)


def test_clock_and_chains_are_monotonic_and_reach_full_time() -> None:
    for seed in range(1, 21):
        events = _match(seed).events
        assert events[0].type == "possession_change" and events[0].outcome == "kick_off"
        for prev, cur in zip(events, events[1:]):
            assert cur.period >= prev.period
            if cur.period == prev.period:
                assert _clock(cur) >= _clock(prev), (seed, prev, cur)
            assert cur.possession_chain_id >= prev.possession_chain_id
        assert {e.period for e in events} == {1, 2}
        assert events[-1].period == 2 and events[-1].minute >= 90


def test_plausible_ranges_seeds_1_to_50() -> None:
    for seed in range(1, 51):
        events = _match(seed).events
        shots = [e for e in events if e.type == "shot"]
        goals = [e for e in shots if e.outcome == "goal"]
        assert 0 <= len(goals) <= 9, (seed, len(goals))
        assert 5 <= len(shots) <= 40, (seed, len(shots))


def test_all_event_types_occur_across_seeds() -> None:
    seen = set()
    for seed in range(1, 11):
        seen.update(e.type for e in _match(seed).events)
    assert seen == {
        "pass", "carry", "shot", "tackle", "pressure", "foul", "set_piece", "sub", "card", "possession_change",
    }


def test_match_index_changes_the_fixture() -> None:
    a, b = _match(4, 0), _match(4, 9)
    assert (a.meta["home"], a.meta["away"]) != (b.meta["home"], b.meta["away"])
    assert a.events[0].event_id != b.events[0].event_id

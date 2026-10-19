"""Planted-error, correction, injection and bait fixtures: deterministic and actually wrong where planted."""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache

from agon.synth import fixtures as F
from agon.synth.blocklist import REAL_TOP_FLIGHT_CLUBS, blocked_reason
from agon.synth.engine import simulate_match
from agon.synth.manifest import build_bundle
from agon.synth.schema import validate_event
from agon.synth.world import build_world


@lru_cache(maxsize=None)
def _seed(seed: int):
    league = build_world(seed)
    result = simulate_match(league, 0)
    return league, result, [e.to_dict() for e in result.events], F.seed_fixtures(seed)


def _recount(events, team: str, stat: str) -> int:
    """Independent recount for the test, written separately from fixtures._team_events."""
    n = 0
    for ev in events:
        if ev["team"] != team:
            continue
        t, o, q = ev["type"], ev["outcome"], ev["qualifier"]
        n += {
            "shots": t == "shot",
            "shots_on_target": t == "shot" and o in ("goal", "saved"),
            "corners": t == "set_piece" and q == "corner",
            "fouls": t == "foul",
        }[stat]
    return n


def test_manifest_is_deterministic() -> None:
    a = F.build_manifest([1, 2, 3])
    b = F.build_manifest([1, 2, 3])
    assert F.manifest_sha256(a) == F.manifest_sha256(b)
    assert a["fixtures_version"] == "fixtures-v1"


def test_wrong_numbers_are_wrong_at_a_seed_offset_unlike_the_cli_default() -> None:
    """CLI default seeds are 1,2,3; this fixture starts at 4071."""
    for seed in range(4071, 4077):
        _, result, events, fx = _seed(seed)
        assert len(fx["wrong_numbers"]) == F.WRONG_NUMBERS_PER_SEED
        assert len({w["stat"] for w in fx["wrong_numbers"]}) == F.WRONG_NUMBERS_PER_SEED
        for w in fx["wrong_numbers"]:
            if w["stat"] == "goal_minute":
                goal = next(e for e in events if e["event_id"] == w["event_ids"][0])
                assert goal["type"] == "shot" and goal["outcome"] == "goal"
                truth = goal["minute"] + 1
            else:
                truth = _recount(events, w["team"], w["stat"])
                assert len(w["event_ids"]) == truth
            assert w["true"] == truth
            assert w["claimed"] != truth and w["claimed"] >= 0
            assert 1 <= abs(w["claimed"] - truth) <= 3
            assert set(w["sentences"]) == set(F.LANGUAGES)
            for lang, sentence in w["sentences"].items():
                assert re.search(rf"(?<!\d){w['claimed']}(?!\d)", sentence), (lang, sentence)
            assert re.search(r"[Ͱ-Ͽ]", w["sentences"]["el"])


def test_published_seeds_each_get_a_valid_scorer_correction() -> None:
    assert F.PUBLISHED_SEEDS == (1, 2, 3)
    for seed in F.PUBLISHED_SEEDS:
        league, result, events, fx = _seed(seed)
        corr = fx["scorer_correction"]
        assert corr is not None and corr["published"] is True
        goal_i = next(i for i, e in enumerate(events) if e["event_id"] == corr["goal_event_id"])
        goal = events[goal_i]
        assert goal["type"] == "shot" and goal["outcome"] == "goal" and goal["team"] == corr["team"]
        new = corr["corrected_scorer"]
        assert new != goal["player"] and new.startswith(goal["team"] + "P")
        positions = {p.player_id: p.position for p in league.club(goal["team"]).players}
        assert positions[new] != "GK"
        # Independent on-pitch check: came on before the goal (or started), and did not leave before it.
        came_on = [i for i, e in enumerate(events) if e["type"] == "sub" and e["related_player"] == new]
        left = [
            i for i, e in enumerate(events)
            if e["player"] == new and (e["type"] == "sub" or (e["type"] == "card" and e["outcome"] in ("red", "second_yellow")))
        ]
        starters = {p.player_id for p in league.club(goal["team"]).starting_eleven()}
        assert (new in starters) or (came_on and came_on[0] < goal_i)
        assert not left or left[0] > goal_i


def test_apply_correction_changes_exactly_the_goal_event() -> None:
    _, result, events, fx = _seed(2)
    corr = fx["scorer_correction"]
    fixed = F.apply_correction(events, corr)
    changed = [(a, b) for a, b in zip(events, fixed) if a != b]
    assert len(changed) == 1
    before, after = changed[0]
    assert after["player"] == corr["corrected_scorer"] and after["seq"] == before["seq"]
    assert after["event_id"] != before["event_id"]
    assert validate_event(after) == []
    assert events[before["seq"]]["player"] == corr["original_scorer"]  # input untouched


def test_a_goalless_match_gets_no_correction() -> None:
    for seed in range(1, 400):
        league = build_world(seed)
        result = simulate_match(league, 0)
        if result.meta["score"] == {"home": 0, "away": 0}:
            events = [e.to_dict() for e in result.events]
            assert F.scorer_correction(seed, league, result.meta, events) is None
            return
    raise AssertionError("no 0-0 match in seeds 1..399")


def test_injection_is_planted_only_in_the_copy() -> None:
    for seed in (1, 2, 4071):
        league, _, _, fx = _seed(seed)
        inj = fx["name_injection"]
        assert any(s in inj["injected"] for s in F.INJECTION_STRINGS)
        world = build_bundle(seed).world
        planted = F.apply_injection(world, inj)
        names = [p["name"] for c in planted["clubs"] for p in c["players"]]
        assert names.count(inj["injected"]) == 1
        assert inj["injected"] not in league.all_names()
        assert all(blocked_reason(n) is None for n in league.all_names())


def test_bait_names_a_real_club_and_the_screen_blocks_it() -> None:
    seen = set()
    for seed in range(1, 61):
        bait = F.real_club_bait(seed)
        assert bait["bait_name"] in REAL_TOP_FLIGHT_CLUBS
        assert bait["bait_name"] in bait["text"]
        assert blocked_reason(bait["text"]) is not None, bait
        seen.add(bait["bait_name"])
    assert len(seen) > 20


def test_fixtures_source_holds_no_real_club_name() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "agon", "synth", "fixtures.py")
    source = open(path, encoding="utf-8").read()
    for club in REAL_TOP_FLIGHT_CLUBS:
        assert not re.search(rf"\b{re.escape(club)}\b", source), club


def test_cli_prints_hash_and_writes_the_manifest(tmp_path, capsys) -> None:
    out = tmp_path / "fx.json"
    assert F.main(["--seeds", "1,2", "--print-hash", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    expected = F.manifest_sha256(F.build_manifest([1, 2]))
    assert f"fixtures sha256 {expected}" in printed
    data = out.read_bytes()
    assert data.endswith(b"\n") and b"\r" not in data
    assert hashlib.sha256(data[:-1]).hexdigest() == expected

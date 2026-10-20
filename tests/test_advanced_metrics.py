"""The metrics pack: each metric on a hand-computed fixture, a negative case, and seeded invariants.

Fixtures use coordinates chosen by hand, not the module's constants: the expected values below were
worked out on paper (each comment shows the arithmetic), so a constant that drifts in the code fails
here instead of moving the fixture with it.
"""

from __future__ import annotations

import pytest

from agon.governance import recompute
from agon.governance.recompute import MatchFacts, Roster, assist_for, key_pass_index
from agon.interpret import interpret_match
from agon.interpret.advanced import METRICS, PLAYER_KEYS, SERIES_KEYS, TEAM_KEYS, pack, shot_phase
from agon.interpret.features import pass_difficulty, xg_ppm, xt_added_ppm
from agon.interpret.frame import BOX_DEPTH_DM, BOX_HALF_WIDTH_DM, FINAL_THIRD_X_DM, MatchView
from agon.synth.manifest import build_bundle
from agon.synth.schema import Event, canonical_bytes, sha256_hex
from agon.synth.world import build_world

# Pinned: a change to any definition in the pack changes this hash on purpose.
ADVANCED_SEED1_SHA256 = "773b7dd0368f770696cc96154e06a4d51e1815b194cb0a674d3278cd6f4e821e"


def ev(seq: int, etype: str, team: str, **kw) -> dict:
    base = {
        "event_id": f"a{seq:015d}", "match_id": "t", "seq": seq, "period": 1, "minute": 10, "second": 0,
        "type": etype, "team": team, "player": "H9", "x_dm": 500, "y_dm": 340, "end_x_dm": None,
        "end_y_dm": None, "possession_chain_id": 1, "under_pressure": False, "outcome": None,
        "qualifier": None, "related_player": None,
    }
    base.update(kw)
    return base


def mv(seq: int, etype: str, team: str, start: tuple[int, int], end: tuple[int, int], outcome: str = "complete", **kw) -> dict:
    return ev(seq, etype, team, x_dm=start[0], y_dm=start[1], end_x_dm=end[0], end_y_dm=end[1], outcome=outcome, **kw)


@pytest.fixture(scope="module")
def seeded() -> list[tuple[MatchView, MatchFacts]]:
    out = []
    for seed in (1, 2, 3):
        b = build_bundle(seed)
        view = MatchView.from_bundle(b.match, b.events)
        facts = MatchFacts(tuple(Event(**e) for e in b.events), b.match["home"], b.match["away"], Roster.from_league(build_world(seed)))
        out.append((view, facts))
    return out


# --------------------------------------------------------------------------- geometry is the interpret box


def test_recompute_box_is_the_interpret_box():
    assert recompute.BOX_MIN_X_DM == 1050 - BOX_DEPTH_DM
    assert recompute.BOX_HALF_WIDTH_DM == BOX_HALF_WIDTH_DM
    assert recompute.FINAL_THIRD_MIN_X_DM == FINAL_THIRD_X_DM


# --------------------------------------------------------------------------- progressive


def test_progressive_pass_boundary_at_9_1_metres():
    # Along the centre line: from x=437 the goal is 1050-437 = 613 dm away. To x=528 it is 522 dm,
    # 91 dm closer (counts); to x=527 it is 523 dm, 90 dm closer (does not).
    assert recompute.is_progressive(mv(1, "pass", "H", (437, 340), (528, 340)))
    assert not recompute.is_progressive(mv(2, "pass", "H", (437, 340), (527, 340)))


def test_progressive_needs_a_completed_move_outside_the_own_40_percent():
    # x=419 is inside the team's own 40% (42 m), even though the pass gains 181 dm.
    assert not recompute.is_progressive(mv(1, "pass", "H", (419, 340), (600, 340)))
    # The same 181 dm gain from x=421 counts, but not when the pass is incomplete.
    assert recompute.is_progressive(mv(2, "pass", "H", (421, 340), (602, 340)))
    assert not recompute.is_progressive(mv(3, "pass", "H", (421, 340), (602, 340), outcome="incomplete"))
    assert not recompute.is_progressive(ev(4, "shot", "H", x_dm=500, end_x_dm=1050, end_y_dm=340, outcome="saved"))


def test_progressive_is_distance_to_goal_not_distance_forward():
    # A 10 m carry straight up the wing: from (600, 100) the goal centre is sqrt(450^2 + 240^2) = 510 dm
    # away; at (700, 100) it is sqrt(350^2 + 240^2) = 424.4, floor 424. Only 86 dm closer: not progressive.
    assert not recompute.is_progressive(mv(1, "carry", "H", (600, 100), (700, 100)))
    # A carry from (600, 340) to (700, 340) is 100 dm closer: progressive.
    assert recompute.is_progressive(mv(2, "carry", "H", (600, 340), (700, 340)))


# --------------------------------------------------------------------------- box, crosses, deep completions


def test_box_entries_by_pass_and_carry():
    # The box starts at x=885 and spans y 138..542 (340 +/- 202), both edges inside.
    assert recompute.is_pass_into_box(mv(1, "pass", "H", (860, 300), (890, 300)))
    assert recompute.is_pass_into_box(mv(2, "pass", "H", (860, 300), (890, 542)))
    assert not recompute.is_pass_into_box(mv(3, "pass", "H", (860, 300), (890, 543)))
    assert not recompute.is_pass_into_box(mv(4, "pass", "H", (860, 300), (884, 300)))
    assert not recompute.is_pass_into_box(mv(5, "pass", "H", (886, 300), (950, 300)))  # starts inside
    assert not recompute.is_pass_into_box(mv(6, "pass", "H", (860, 300), (890, 300), outcome="incomplete"))
    assert recompute.is_carry_into_box(mv(7, "carry", "H", (870, 400), (900, 400)))
    assert not recompute.is_carry_into_box(mv(8, "carry", "H", (890, 400), (920, 400)))


def test_cross_proxy_needs_a_wide_final_third_start():
    # (760, 560): x >= 700 and 560 - 340 = 220 > 202, so it is wide: a cross into the box.
    assert recompute.is_cross_into_box(mv(1, "pass", "H", (760, 560), (950, 350)))
    # From x=690 it is a pass into the box but not a cross; from y=542 (exactly 202 off centre) neither wide.
    assert recompute.is_pass_into_box(mv(2, "pass", "H", (690, 560), (950, 350)))
    assert not recompute.is_cross_into_box(mv(2, "pass", "H", (690, 560), (950, 350)))
    assert not recompute.is_cross_into_box(mv(3, "pass", "H", (760, 542), (950, 350)))
    # A corner from the flag (1050, 0) into the box is a cross.
    assert recompute.is_cross_into_box(mv(4, "pass", "H", (1050, 0), (960, 330)))


def test_deep_completion_is_20_metres_from_goal_without_crosses():
    # End (850, 340) is exactly 200 dm from (1050, 340): counts. (849, 340) is 201: does not.
    assert recompute.is_deep_completion(mv(1, "pass", "H", (700, 340), (850, 340)))
    assert not recompute.is_deep_completion(mv(2, "pass", "H", (700, 340), (849, 340)))
    # (870, 260): sqrt(180^2 + 80^2) = 196.97, floor 196: counts.
    assert recompute.is_deep_completion(mv(3, "pass", "H", (700, 300), (870, 260)))
    # A cross into the box ending 100 dm from goal is excluded.
    assert not recompute.is_deep_completion(mv(4, "pass", "H", (760, 560), (950, 350)))
    assert not recompute.is_deep_completion(mv(5, "pass", "H", (700, 340), (900, 340), outcome="out"))


# --------------------------------------------------------------------------- key passes and xA


def _chain_with_shot() -> list[dict]:
    return [
        ev(0, "set_piece", "H", player="H2", qualifier="throw_in", outcome="taken", possession_chain_id=7),
        mv(1, "pass", "H", (600, 300), (820, 330), player="H2", related_player="H9", possession_chain_id=7),
        mv(2, "carry", "H", (820, 330), (880, 340), player="H9", possession_chain_id=7),
        ev(3, "pressure", "A", player="A4", x_dm=170, y_dm=340, outcome="applied", possession_chain_id=7),
        ev(4, "shot", "H", player="H9", x_dm=900, y_dm=340, end_x_dm=1050, end_y_dm=340, outcome="saved",
           qualifier="open_play", possession_chain_id=7),
    ]


def test_key_pass_skips_own_carries_and_opponent_events():
    events = _chain_with_shot()
    assert key_pass_index(events, 4) == 1
    # The published rule says the walk skips the opponent's events, as the pressure at seq 3 shows.
    assert "opponent's events" in METRICS["key_passes"]["rule"]
    view = MatchView.build(events, "H", "A", "t")
    p = pack(view)
    xg = xg_ppm(events[4], "H")
    assert p["teams"]["H"]["key_passes"] == 1 and p["teams"]["H"]["xa_ppm"] == xg > 0
    h2 = next(r for r in p["players"] if r["player_id"] == "H2")
    assert h2["xa_ppm"] == xg and h2["key_passes"] == 1
    assert [r["xa_ppm"] for r in p["passes"]] == [xg]


def test_no_key_pass_when_the_receiver_is_not_the_shooter_or_it_is_a_penalty():
    events = _chain_with_shot()
    events[1] = dict(events[1], related_player="H7")
    assert key_pass_index(events, 4) is None
    penalty = _chain_with_shot()
    penalty[4] = dict(penalty[4], qualifier="penalty")
    assert key_pass_index(penalty, 4) is None
    other_chain = _chain_with_shot()
    other_chain[4] = dict(other_chain[4], possession_chain_id=8)
    assert key_pass_index(other_chain, 4) is None
    with pytest.raises(recompute.RecomputeError):
        key_pass_index(events, 1)


def test_key_pass_rule_is_the_assist_rule_on_every_seeded_goal(seeded):
    goals = 0
    for view, facts in seeded:
        evs = list(facts.events)
        for i, e in enumerate(evs):
            if e.type == "shot" and e.outcome == "goal":
                j = key_pass_index(view.events, i)
                assert (None if j is None else view.events[j]["player"]) == assist_for(evs, i)
                goals += 1
    assert goals >= 5


# --------------------------------------------------------------------------- field tilt, counter-press, height


def test_field_tilt_counts_attempted_passes_from_the_final_third():
    events = [
        mv(1, "pass", "H", (700, 300), (720, 300)),
        mv(2, "pass", "H", (750, 300), (760, 300), outcome="incomplete"),
        mv(3, "pass", "H", (699, 300), (800, 300)),  # starts 1 dm short of the final third
        mv(4, "pass", "A", (800, 300), (820, 300), player="A9"),
    ]
    view = MatchView.build(events, "H", "A", "t")
    teams = pack(view)["teams"]
    # 2 of 3 final-third passes: 666.7 per mille, half up 667; the away side 333.
    assert teams["H"]["final_third_passes"] == 2 and teams["H"]["field_tilt_permille"] == 667
    assert teams["A"]["field_tilt_permille"] == 333
    facts = MatchFacts(tuple(Event(**e) for e in events), "H", "A", Roster("L", {"H": "Home", "A": "Away"}, {"H9": "x", "A9": "y"}, {"H9": "H", "A9": "A"}))
    assert recompute.recompute_stat(facts, "field_tilt_pct", "H") == 67
    with pytest.raises(recompute.RecomputeError):
        recompute.recompute_stat(facts, "field_tilt_pct", "H9")


def _change(seq: int, team: str, outcome: str, period: int, minute: int, second: int) -> dict:
    return ev(seq, "possession_change", team, player=None, outcome=outcome, period=period, minute=minute, second=second)


def test_counter_press_recovers_within_5_seconds_in_the_same_period():
    events = [
        _change(1, "A", "interception", 1, 10, 0), _change(2, "H", "tackle", 1, 10, 5),       # 5 s: recovered
        _change(3, "A", "tackle", 1, 20, 0), _change(4, "H", "interception", 1, 20, 6),       # 6 s: not
        _change(5, "A", "interception", 1, 30, 0), _change(6, "H", "out_of_play", 1, 30, 2),  # dead ball: not
        _change(7, "A", "interception", 1, 44, 58), _change(8, "H", "tackle", 2, 45, 1),      # other period: not
        _change(9, "A", "out_of_play", 2, 50, 0), _change(10, "H", "tackle", 2, 50, 1),       # not an open-play loss
    ]
    pairs = recompute.counter_press(events, "H")
    assert [(loss["seq"], regain["seq"] if regain else None) for loss, regain in pairs] == [(1, 2), (3, None), (5, None), (7, None)]
    view = MatchView.build(events, "H", "A", "t")
    h = pack(view)["teams"]["H"]
    assert (h["counter_press_losses"], h["counter_press_recoveries"], h["counter_press_rate_permille"]) == (4, 1, 250)


def test_defensive_action_height_is_the_mean_of_tackles_fouls_and_interceptions():
    events = [
        ev(1, "tackle", "H", player="H4", x_dm=300, outcome="won"),
        ev(2, "foul", "H", player="H5", x_dm=500, outcome="committed"),
        _change(3, "H", "interception", 1, 12, 0) | {"x_dm": 640},
        ev(4, "pressure", "H", player="H6", x_dm=900, outcome="applied"),   # not a defensive action here
        ev(5, "tackle", "A", player="A4", x_dm=100, outcome="won"),         # the other club
    ]
    view = MatchView.build(events, "H", "A", "t")
    h = pack(view)["teams"]["H"]
    # (300 + 500 + 640) / 3 = 480.
    assert h["defensive_actions"] == 3 and h["defensive_action_height_dm"] == 480
    assert pack(MatchView.build(events[3:4], "H", "A", "t"))["teams"]["H"]["defensive_action_height_dm"] is None


# --------------------------------------------------------------------------- set-piece xG split


def test_set_piece_xg_split_by_how_the_chain_started():
    def shot(seq: int, chain: int, q: str = "open_play") -> dict:
        return ev(seq, "shot", "H", x_dm=930, y_dm=340, end_x_dm=1050, end_y_dm=340, outcome="off_target", qualifier=q, possession_chain_id=chain)

    events = [
        ev(1, "set_piece", "H", qualifier="corner", outcome="taken", possession_chain_id=5), shot(2, 5, "header"),
        ev(3, "set_piece", "H", qualifier="throw_in", outcome="taken", possession_chain_id=6), shot(4, 6),
        shot(5, 7),
        ev(6, "set_piece", "H", qualifier="penalty", outcome="taken", possession_chain_id=8), shot(7, 8, "penalty"),
        ev(8, "set_piece", "H", qualifier="free_kick", outcome="taken", possession_chain_id=9), shot(9, 9, "free_kick"),
    ]
    view = MatchView.build(events, "H", "A", "t")
    assert shot_phase(view) == {
        "a000000000000002": "corner", "a000000000000004": "open_play", "a000000000000005": "open_play",
        "a000000000000007": "penalty", "a000000000000009": "free_kick",
    }
    h = pack(view)["teams"]["H"]
    xg = {e["seq"]: xg_ppm(e, "H") for e in events if e["type"] == "shot"}
    assert h["xg_corner_ppm"] == xg[2] and h["xg_penalty_ppm"] == xg[7] and h["xg_free_kick_ppm"] == xg[9]
    assert h["xg_open_play_ppm"] == xg[4] + xg[5]
    assert h["xg_open_play_ppm"] + h["xg_set_piece_ppm"] == h["xg_ppm"]


# --------------------------------------------------------------------------- pass quality and xT per player


def test_pass_quality_per_player_and_club():
    events = [
        mv(1, "pass", "H", (100, 340), (200, 340), player="H3", related_player="H4"),           # 10 m
        mv(2, "pass", "H", (200, 340), (400, 340), player="H3", related_player="H5"),           # 20 m
        mv(3, "pass", "H", (400, 340), (710, 340), player="H3", outcome="incomplete"),          # 31 m
        mv(4, "pass", "H", (100, 100), (100, 160), player="H4", related_player="H3"),           # 6 m
    ]
    view = MatchView.build(events, "H", "A", "t")
    p = pack(view)
    h3 = next(r for r in p["players"] if r["player_id"] == "H3")
    # 2 of 3 completed: 666.7 per mille, half up 667. Mean distance (10 + 20 + 31) / 3 m = 203.3 dm, 203.
    assert (h3["passes"], h3["passes_completed"], h3["pass_accuracy_permille"], h3["mean_pass_distance_dm"]) == (3, 2, 667, 203)
    diffs = [pass_difficulty(e, "H") for e in events[:3]]
    assert h3["mean_pass_difficulty_centi"] == (200 * sum(diffs) + 3) // 6
    h = p["teams"]["H"]
    # Club: 3 of 4 = 750 per mille; mean (10 + 20 + 31 + 6) / 4 = 16.75 m = 167.5 dm, half up 168.
    assert (h["passes"], h["pass_accuracy_permille"], h["mean_pass_distance_dm"]) == (4, 750, 168)
    assert [r["distance_m"] for r in p["passes"]] == [10, 20, 31, 6]


def test_xt_added_per_player_sums_completed_moves_only():
    events = [
        mv(1, "pass", "H", (500, 340), (800, 340), player="H8", related_player="H9"),
        mv(2, "carry", "H", (800, 340), (900, 340), player="H9"),
        mv(3, "pass", "H", (900, 340), (1000, 340), player="H9", outcome="incomplete"),
    ]
    view = MatchView.build(events, "H", "A", "t")
    p = pack(view)
    by = {r["player_id"]: r["xt_added_ppm"] for r in p["players"]}
    assert by["H8"] == xt_added_ppm(events[0]) > 0
    assert by["H9"] == xt_added_ppm(events[1]) > 0  # the incomplete pass adds nothing
    assert p["teams"]["H"]["xt_added_ppm"] == by["H8"] + by["H9"]


# --------------------------------------------------------------------------- seeded invariants and pins


def test_pack_on_seeded_matches_is_internally_consistent(seeded):
    for view, facts in seeded:
        p = pack(view)
        for team in (view.home_id, view.away_id):
            t = p["teams"][team]
            assert set(t) == set(TEAM_KEYS)
            assert t["box_entries"] == t["passes_into_box"] + t["carries_into_box"]
            assert t["crosses_into_box"] <= t["passes_into_box"]
            assert 0 <= t["xa_ppm"] <= t["xg_ppm"] and t["key_passes"] <= t["shots"]
            assert t["xg_open_play_ppm"] + t["xg_set_piece_ppm"] == t["xg_ppm"]
            rows = [r for r in p["players"] if r["club_id"] == team]
            for k in ("xg_ppm", "xa_ppm", "key_passes", "progressive_passes", "box_entries", "passes", "xt_added_ppm"):
                assert sum(r[k] for r in rows) == t[k], k
            s = p["series"][team]
            for k in ("xg_ppm", "xa_ppm", "progressive_passes", "progressive_carries", "box_entries", "deep_completions", "counter_press_losses"):
                assert sum(s[k]) == t[k], k
            assert set(s) == set(SERIES_KEYS) and all(len(v) == 6 for v in s.values())
            # The same predicates count the Fact Gate's stats.
            for stat in ("progressive_passes", "progressive_carries", "passes_into_box", "crosses_into_box",
                         "carries_into_box", "box_entries", "deep_completions", "key_passes"):
                assert recompute.recompute_stat(facts, stat, team) == t[stat], stat
            pct = recompute.recompute_stat(facts, "counter_press_recovery_pct", team)
            assert pct == (t["counter_press_rate_permille"] + 5) // 10
        tilt = p["teams"][view.home_id]["field_tilt_permille"] + p["teams"][view.away_id]["field_tilt_permille"]
        assert 999 <= tilt <= 1001
        assert all(set(r) == {"player_id", "club_id", *PLAYER_KEYS} for r in p["players"])


def test_every_metric_has_a_definition_with_unit_and_sentence():
    for key, d in METRICS.items():
        assert set(d) == {"label", "unit", "scope", "better", "description", "rule"}, key
        assert d["description"].endswith(".") and d["rule"]
        assert key in TEAM_KEYS or key in PLAYER_KEYS


def test_pack_is_canonical_deterministic_and_pinned(seeded):
    view = seeded[0][0]
    first = canonical_bytes(pack(view))
    assert first == canonical_bytes(pack(view))
    assert sha256_hex(first) == ADVANCED_SEED1_SHA256


def test_interpret_match_is_untouched_by_the_pack(seeded):
    # interpret_match is what the fleet seals; the pack is a separate entry point.
    assert "passes" not in interpret_match(seeded[0][0]) and "xa_ppm" not in str(interpret_match(seeded[0][0])["totals"])

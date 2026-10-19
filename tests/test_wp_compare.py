"""M6 wp_compare: the arithmetic, the checkpoint grid, and that the report never claims an oracle."""

from __future__ import annotations

from agon.interpret.frame import MatchView
from agon.referee import wp_compare
from agon.synth.manifest import build_bundle


def test_checkpoints_cover_regular_time_every_five_minutes():
    cps = wp_compare.checkpoints()
    assert len(cps) == 18
    assert cps[0] == (1, 0) and cps[8] == (1, 2400) and cps[9] == (2, 2700) and cps[-1] == (2, 5100)


def test_scoring_arithmetic_on_known_values():
    perfect = wp_compare.score_points([(1_000_000, 0, 0)], [(1_000_000, 0, 0)])
    assert perfect == {"points": 1, "sq_ppm2": 0, "abs_ppm": 0}
    coin = wp_compare.score_points([(500_000, 0, 500_000)], [(1_000_000, 0, 0)])
    # Brier = 0.25 + 0 + 0.25 = 0.5; mean abs diff = (0.5 + 0 + 0.5) / 3.
    assert wp_compare.fixed4(coin["sq_ppm2"], 10**12) == "0.5000"
    assert wp_compare.fixed4(coin["abs_ppm"], 3 * 10**6) == "0.3333"


def test_states_follow_the_event_log():
    b = build_bundle(1)
    view = MatchView.from_bundle(b.match, b.events)
    states = wp_compare.states_at_checkpoints(view)
    assert (states[0].home_goals, states[0].away_goals) == (0, 0)
    goals_by_ft = (states[-1].home_goals, states[-1].away_goals)
    assert goals_by_ft[0] <= b.match["score"]["home"] and goals_by_ft[1] <= b.match["score"]["away"]
    for a, z in zip(states, states[1:]):
        assert a.home_goals <= z.home_goals and a.away_goals <= z.away_goals


def test_report_names_its_comparison_honestly():
    res = wp_compare.compare([3001, 3002])
    lines = wp_compare.report_lines("3001-3002", res)
    assert "FINAL RESULTS" in lines[0] and "no oracle" in lines[0]
    assert lines[1] == "command: python -m agon.referee.wp_compare --seeds 3001-3002"
    assert lines[4].startswith("interpret: brier ") and "mean_abs_diff" in lines[4]
    assert lines[5].startswith("baseline")
    assert "self-measured" in lines[-1]
    assert res["interpret"]["points"] == 36

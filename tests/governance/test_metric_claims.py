"""Metrics-pack statistics and season milestones through the Fact Gate: true values pass, planted errors are struck.

The statistic definitions are tested on hand-computed fixtures in tests/test_advanced_metrics.py;
here the point is the gate: a writer citing a metric or a milestone gets exactly the recomputed
number through, and an off-by-one, a stray cited event or a club-only ratio on a player is refused.
"""

from __future__ import annotations

import pytest

import seasonkit
from agon import webintel
from agon.governance import factgate, recompute
from agon.governance.claims import CLAIM_TYPES, Claim, Sentence

COUNTS = ("progressive_passes", "progressive_carries", "passes_into_box", "crosses_into_box",
          "carries_into_box", "box_entries", "deep_completions", "key_passes")
CLUB_RATIOS = ("field_tilt_pct", "counter_press_recovery_pct")


@pytest.fixture(scope="module")
def facts():
    return seasonkit.match_facts(1, 0)  # season 1 match 0 is the published seed 1 match


def _sentence(facts, stat: str, value: int, ids: tuple[str, ...], subject: str | None = None) -> Sentence:
    subject = subject or facts.home_id
    name = facts.roster.clubs.get(subject) or facts.roster.players[subject]
    return Sentence(
        sentence_id=f"m-{stat}",
        text=f"{name} recorded {value} on this measure.",
        language="en",
        claims=(Claim(type="stat", value=value, stat=stat, subject=subject, event_ids=ids),),
    )


def test_new_statistics_are_known_to_the_gate_without_new_claim_types():
    # The writer's schema enum (CLAIM_TYPES) is unchanged: the pack rides on the existing `stat` claim.
    assert CLAIM_TYPES == ("score", "stat", "minute", "name", "event_ref", "season_streak", "table_position", "player_season_total")
    for stat in COUNTS + CLUB_RATIOS:
        assert stat in recompute.STAT_KEYS
    for c in webintel.CITABLE:
        assert c["stat"] in recompute.STAT_KEYS


@pytest.mark.parametrize("stat", COUNTS)
def test_count_claims_pass_true_and_strike_off_by_one(facts, stat):
    counted = recompute.stat_events(facts, stat, facts.home_id)
    value = len(counted)
    assert value > 0, f"seed 1 home has no {stat}; pick another fixture"
    ids = (counted[0].event_id,)
    assert factgate.check_sentence(_sentence(facts, stat, value, ids), facts).passed
    wrong = factgate.check_sentence(_sentence(facts, stat, value + 1, ids), facts)
    assert not wrong.passed and any(f"claimed {value + 1}, log says {value}" in r for r in wrong.reasons)


@pytest.mark.parametrize("stat", COUNTS)
def test_count_claims_refuse_an_event_that_is_not_counted(facts, stat):
    counted = {e.event_id for e in recompute.stat_events(facts, stat, facts.home_id)}
    stray = next(e.event_id for e in facts.events if e.event_id not in counted and e.team == facts.home_id)
    value = len(counted)
    verdict = factgate.check_sentence(_sentence(facts, stat, value, (stray,)), facts)
    assert not verdict.passed and any("not counted" in r for r in verdict.reasons)


def test_count_claims_need_a_supporting_event(facts):
    value = recompute.recompute_stat(facts, "progressive_passes", facts.home_id)
    verdict = factgate.check_sentence(_sentence(facts, "progressive_passes", value, ()), facts)
    assert not verdict.passed and any("no supporting event ids" in r for r in verdict.reasons)


def test_player_counts_go_through_the_gate(facts):
    rows = recompute.stat_events(facts, "progressive_passes", facts.home_id)
    player = rows[0].player
    value = recompute.recompute_stat(facts, "progressive_passes", player)
    mine = tuple(e.event_id for e in rows if e.player == player)[:1]
    assert factgate.check_sentence(_sentence(facts, "progressive_passes", value, mine, player), facts).passed
    assert not factgate.check_sentence(_sentence(facts, "progressive_passes", value - 1, mine, player), facts).passed


@pytest.mark.parametrize("stat", CLUB_RATIOS)
def test_club_ratios_pass_true_strike_wrong_and_refuse_a_player(facts, stat):
    value = recompute.recompute_stat(facts, stat, facts.home_id)
    assert factgate.check_sentence(_sentence(facts, stat, value, ()), facts).passed
    assert not factgate.check_sentence(_sentence(facts, stat, value + 1, ()), facts).passed
    player = next(e.player for e in facts.events if e.player and e.team == facts.home_id)
    verdict = factgate.check_sentence(_sentence(facts, stat, value, (), player), facts)
    assert not verdict.passed and any("club statistic" in r for r in verdict.reasons)


def test_field_tilt_of_the_two_clubs_sums_to_100_within_rounding(facts):
    total = sum(recompute.recompute_stat(facts, "field_tilt_pct", c) for c in (facts.home_id, facts.away_id))
    assert 99 <= total <= 101


# --------------------------------------------------------------------------- milestones


@pytest.fixture(scope="module")
def milestone_matches():
    season = seasonkit.season(1)
    payload = webintel.milestones_payload(season)
    return [(g["match_id"], g["facts"]) for d in payload["matchdays"] for g in d["matches"]]


def _milestone_sentence(fact: dict, bump: int = 0) -> Sentence:
    claims = []
    for i, c in enumerate(fact["claims"]):
        value = c["value"] + (bump if i == 0 else 0)
        claims.append(Claim(type=c["type"], value=value, stat=c.get("stat"), subject=c["subject"], event_ids=tuple(c["event_ids"])))
    text = fact["sentence"]
    if bump:
        first = fact["claims"][0]["value"]
        text = text.replace(webintel.ordinal(first), webintel.ordinal(first + bump), 1) if webintel.ordinal(first) in text else text.replace(str(first), str(first + bump), 1)
    return Sentence(sentence_id=f"ms-{fact['kind']}", text=text, language="en", claims=tuple(claims))


def test_every_season_milestone_passes_the_gate(milestone_matches):
    kinds = set()
    checked = 0
    for match_id, facts_list in milestone_matches:
        index = int(match_id.split("-m")[1])
        mf = seasonkit.match_facts(1, index)
        for fact in facts_list:
            verdict = factgate.check_sentence(_milestone_sentence(fact), mf)
            assert verdict.passed, (match_id, fact["sentence"], verdict.reasons)
            kinds.add(fact["kind"])
            checked += 1
    assert checked >= 100
    assert {"first_goal", "season_goals", "first_assist", "table", "club_streak"} <= kinds


def test_an_off_by_one_milestone_is_struck(milestone_matches):
    struck = 0
    for match_id, facts_list in milestone_matches[::5]:
        mf = seasonkit.match_facts(1, int(match_id.split("-m")[1]))
        for fact in facts_list:
            verdict = factgate.check_sentence(_milestone_sentence(fact, bump=1), mf)
            assert not verdict.passed, (match_id, fact["sentence"])
            struck += 1
    assert struck >= 10


def test_a_first_goal_fact_is_the_player_season_total_of_one():
    # Hand check on the published match (season 1 match 0, matchday 1): every scorer's season total is
    # his goals in that match, so each scorer with one goal gets "a 1st goal of the season".
    season = seasonkit.season(1)
    m = season.match(0)
    scorers = sorted(e["player"] for e in m.events if e["type"] == "shot" and e["outcome"] == "goal")
    facts = webintel.milestones_for_match(seasonkit.season_facts(1), m.meta["match_id"],
                                          {c.club_id: c.name for c in season.league.clubs},
                                          {p.player_id: p.name for c in season.league.clubs for p in c.players})
    firsts = sorted(f["subject"] for f in facts if f["kind"] == "first_goal")
    assert firsts == scorers and len(scorers) == 2
    assert all(f["claims"][0]["value"] == 1 for f in facts if f["kind"] == "first_goal")

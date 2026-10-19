"""Fact Gate: claims recomputed from the full log, and prose digits checked against claims."""

from __future__ import annotations

import dataclasses

import pytest

import govkit
from agon.governance import factgate, recompute
from agon.governance.claims import Claim, ClaimModelError, Sentence
from agon.governance.recompute import display_minute, stat_events


@pytest.fixture(scope="module")
def facts():
    return govkit.match()[2]


def test_display_minute_convention_pins_stoppage_and_the_period_collision():
    assert display_minute(1, 0) == "1"
    assert display_minute(1, 44) == "45"
    assert display_minute(1, 45) == "45+1"
    assert display_minute(1, 46) == "45+2"
    # The same engine minute in period 2 is regular time: never derive a minute from minute alone.
    assert display_minute(2, 45) == "46"
    assert display_minute(2, 46) == "47"
    assert display_minute(2, 70) == "71"
    assert display_minute(2, 89) == "90"
    assert display_minute(2, 92) == "90+3"


def test_recomputed_score_equals_the_generator_meta(facts):
    _, result, _ = govkit.match()
    assert recompute.score_after(facts, None) == result.meta["score"]
    for seed in (1, 2, 5, 11, 14):
        _, res, f = govkit.match(seed)
        assert recompute.score_after(f, None) == res.meta["score"]


def test_clean_sentences_pass_in_all_four_languages(facts):
    for lang, sentence in govkit.sot_sentences(facts).items():
        verdict = factgate.check_sentence(sentence, facts)
        assert verdict.passed, (lang, verdict.reasons)
    for lang in ("en", "el"):
        verdict = factgate.check_sentence(govkit.goal_sentence(facts, lang), facts)
        assert verdict.passed, (lang, verdict.reasons)


def test_planted_wrong_claim_value_is_caught_in_english(facts):
    true = len(stat_events(facts, "shots_on_target", facts.home_id))
    wrong = true - 2
    sentence = govkit.sot_sentences(facts, written=wrong, claimed=wrong)["en"]
    verdict = factgate.check_sentence(sentence, facts)
    assert verdict.status == "mismatch"
    assert any(f"claimed {wrong}, log says {true}" in r for r in verdict.reasons), verdict.reasons


def test_planted_wrong_claim_value_is_caught_in_greek(facts):
    true = len(stat_events(facts, "shots_on_target", facts.home_id))
    wrong = true + 3
    sentence = govkit.sot_sentences(facts, written=wrong, claimed=wrong)["el"]
    assert "είχε" in sentence.text
    verdict = factgate.check_sentence(sentence, facts)
    assert verdict.status == "mismatch"
    assert any(f"claimed {wrong}, log says {true}" in r for r in verdict.reasons), verdict.reasons


@pytest.mark.parametrize("lang", ["en", "el", "es", "fr"])
def test_planted_wrong_digit_in_prose_is_caught_with_a_correct_claim(facts, lang):
    true = len(stat_events(facts, "shots_on_target", facts.home_id))
    sentence = govkit.sot_sentences(facts, written=true + 1)[lang]
    verdict = factgate.check_sentence(sentence, facts)
    assert verdict.status == "mismatch"
    assert f"prose writes {true + 1} (1x) but no claim carries it" in verdict.reasons
    assert f"claim value {true} (1x) is not written in the prose" in verdict.reasons


def test_count_is_taken_over_the_full_log_not_the_cited_ids(facts):
    counted = stat_events(facts, "shots_on_target", facts.home_id)
    assert len(counted) >= 3
    subset = tuple(e.event_id for e in counted[:2])
    claim = govkit.sot_claim(facts, facts.home_id, value=2, ids=subset)
    reasons = factgate.check_claim(claim, facts)
    assert any(f"claimed 2, log says {len(counted)}" in r for r in reasons), reasons


def test_cited_event_outside_the_counted_set_is_a_mismatch(facts):
    away_shot = stat_events(facts, "shots_on_target", facts.away_id)[0]
    claim = govkit.sot_claim(facts, facts.home_id)
    claim = dataclasses.replace(claim, event_ids=claim.event_ids + (away_shot.event_id,))
    reasons = factgate.check_claim(claim, facts)
    assert any("not counted" in r for r in reasons), reasons


def test_fail_closed_on_unknown_ids_statistics_kinds_and_subjects(facts):
    assert factgate.check_claim(Claim(type="event_ref", value="goal", event_ids=("0" * 16,)), facts)
    assert factgate.check_claim(Claim(type="stat", value=1, stat="xg_total", subject=facts.home_id), facts)
    assert factgate.check_claim(Claim(type="stat", value=1, stat="shots", subject="C99"), facts)
    assert factgate.check_claim(Claim(type="event_ref", value="own_goal", event_ids=(govkit.first_goal(facts).event_id,)), facts)
    assert factgate.check_claim(Claim(type="stat", value=True, stat="shots", subject=facts.home_id), facts)
    with pytest.raises(ClaimModelError):
        Claim.from_dict({"type": "vibe", "value": 1})
    with pytest.raises(ClaimModelError):
        Claim.from_dict({"type": "stat", "value": 1, "injected": "x"})


def test_stat_without_supporting_ids_is_a_mismatch(facts):
    claim = govkit.sot_claim(facts, facts.home_id, ids=())
    assert any("no supporting event ids" in r for r in factgate.check_claim(claim, facts))


def test_minute_claim_is_checked_against_period_and_minute(facts):
    goal = govkit.first_goal(facts)
    right = display_minute(goal.period, goal.minute)
    assert not factgate.check_claim(Claim(type="minute", value=right, event_ids=(goal.event_id,)), facts)
    wrong = str(goal.minute)  # the 0-based engine minute, a classic off-by-one
    reasons = factgate.check_claim(Claim(type="minute", value=wrong, event_ids=(goal.event_id,)), facts)
    assert reasons and "is at" in reasons[0]


def test_stoppage_time_goal_minute(facts):
    _, _, f11 = govkit.match(11)
    late = [g for g in recompute.events_of_kind(f11.events, "goal") if g.period == 2 and g.minute >= 90][0]
    shown = display_minute(late.period, late.minute)
    assert shown.startswith("90+")
    sentence = Sentence(
        sentence_id="late",
        text=f"A goal in the {shown}th minute.",
        language="en",
        claims=(Claim(type="minute", value=shown, event_ids=(late.event_id,)),),
    )
    assert factgate.check_sentence(sentence, f11).passed


def test_score_written_away_first_is_a_mismatch(facts):
    sentence = govkit.goal_sentence(facts, "en")
    score = next(c.value for c in sentence.claims if c.type == "score")
    assert score["home"] != score["away"]
    flipped = sentence.text.replace(f"{score['home']}-{score['away']}", f"{score['away']}-{score['home']}")
    verdict = factgate.check_sentence(dataclasses.replace(sentence, text=flipped), facts)
    assert any("home first" in r for r in verdict.reasons), verdict.reasons
    with_dash = sentence.text.replace(f"{score['home']}-{score['away']}", f"{score['away']}{chr(0x2013)}{score['home']}")
    verdict = factgate.check_sentence(dataclasses.replace(sentence, text=with_dash), facts)
    assert any("home first" in r for r in verdict.reasons), verdict.reasons


def test_wrong_final_score_claim(facts):
    claim = Claim(type="score", value={"home": 1, "away": 1})
    reasons = factgate.check_claim(claim, facts)
    assert reasons and "at full time" in reasons[0]


def test_name_claims(facts):
    goal = govkit.first_goal(facts)
    scorer = facts.roster.players[goal.player]
    assert not factgate.check_claim(Claim(type="name", value=scorer, event_ids=(goal.event_id,)), facts, text=f"{scorer} scored.")
    off = factgate.check_claim(Claim(type="name", value="Kevin Notonroster"), facts, text="Kevin Notonroster scored.")
    assert off and "not on the fictional roster" in off[0]
    unwritten = factgate.check_claim(Claim(type="name", value=scorer), facts, text="He scored.")
    assert unwritten and "not written" in unwritten[0]
    other = next(pid for pid in facts.roster.players if pid != goal.player)
    wrong_event = factgate.check_claim(
        Claim(type="name", value=facts.roster.players[other], event_ids=(goal.event_id,)),
        facts,
        text=facts.roster.players[other],
    )
    assert wrong_event and "does not involve" in wrong_event[0]


def test_event_ref_of_the_wrong_kind(facts):
    a_pass = recompute.events_of_kind(facts.events, "pass")[0]
    reasons = factgate.check_claim(Claim(type="event_ref", value="goal", event_ids=(a_pass.event_id,)), facts)
    assert reasons and "not of that kind" in reasons[0]


def test_digit_grouping_per_language():
    assert factgate.prose_numbers("They made 1,234 passes", "en") == [1234]
    assert factgate.prose_numbers("Έκαναν 1.234 πάσες", "el") == [1234]
    assert factgate.prose_numbers("1" + chr(0x00A0) + "234 passes", "fr") == [1234]
    assert factgate.prose_numbers("45+2", "en") == [45, 2]
    assert factgate.prose_numbers("71st and 71η and 71e", "el") == [71, 71, 71]


def test_non_ascii_digits_are_reported(facts):
    sentence = Sentence(sentence_id="x", text="Score " + chr(0x0663), language="en")
    assert any("non-ASCII" in r for r in factgate.check_prose_numbers(sentence))


def test_translation_must_carry_the_same_claims(facts):
    sents = govkit.sot_sentences(facts)
    assert factgate.check_translation(sents["en"], sents["el"], facts).passed
    changed = dataclasses.replace(sents["el"], claims=sents["el"].claims[:1])
    verdict = factgate.check_translation(sents["en"], changed, facts)
    assert verdict.status == "mismatch"
    assert "same claims" in verdict.reasons[0]


def test_run_factgate_reports_mismatch_if_any_sentence_fails(facts):
    good = list(govkit.sot_sentences(facts).values())
    assert factgate.run_factgate(good, facts).passed
    bad = govkit.sot_sentences(facts, written=0, claimed=0)["el"]
    report = factgate.run_factgate(good + [bad], facts)
    assert report.status == "mismatch"
    assert not factgate.run_factgate([], facts).passed  # nothing checked is never a pass


def test_negative_control_the_catch_depends_on_recomputation(facts, monkeypatch):
    """Break the recompute (make it agree with the writer) and the planted error slips through."""
    counted = stat_events(facts, "shots_on_target", facts.home_id)
    planted_value = len(counted) - 1
    planted_ids = tuple(e.event_id for e in counted[:planted_value])
    planted = govkit.sot_sentences(facts, written=planted_value, claimed=planted_value)["el"]
    planted = dataclasses.replace(planted, claims=(dataclasses.replace(planted.claims[0], event_ids=planted_ids), planted.claims[1]))
    assert not factgate.check_sentence(planted, facts).passed
    monkeypatch.setattr(recompute, "recompute_stat", lambda f, stat, subject: planted_value)
    monkeypatch.setattr(recompute, "stat_events", lambda f, stat, subject: counted[:planted_value])
    assert factgate.check_sentence(planted, facts).passed

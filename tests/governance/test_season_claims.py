"""Season claims through the Fact Gate: true sentences pass, every planted season error is struck.

The expected values are derived by hand from the season 1 results that
`python -m agon.synth.season --season 1 --print-hash` prints (one line per match, home first),
not from the code under test:

    C4 results, matchdays 1 to 6: md1 C3 0-1 C4 (W), md2 C6 0-0 C4 (D), md3 C1 3-0 C4 (L),
    md4 C4 2-1 C2 (W), md5 C4 2-1 C5 (W), md6 C4 3-2 C3 (W), md7 C4 3-1 C6 (W).
    So after matchday 6 (match 17, C4 3-2 C3) C4 are on a run of 3 wins; after matchday 7, 4.
    Points after matchday 6: C4 13, C5 9, C1 8 (goal difference +2), C6 8 (-1), C3 7 (-2), C2 5.
    So C4 are 1st on 13 points and C3 are 5th with a goal difference of -2.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

import pytest

import seasonkit
from agon.governance import factgate, recompute
from agon.governance.claims import CLAIM_TYPES, SEASON_CLAIM_TYPES, Claim, Sentence

MATCH_C4_C3 = 17  # matchday 6, C4 3-2 C3
MATCH_C4_C6 = 19  # matchday 7, C4 3-1 C6
MATCH_C4_C5 = 14  # matchday 5, C4 2-1 C5


@pytest.fixture(scope="module")
def facts():
    return seasonkit.match_facts(1, MATCH_C4_C3)


def names(facts):
    return facts.roster.clubs["C4"], facts.roster.clubs["C3"]


def streak_sentence(facts, written: int = 3, claimed: int = 3, language: str = "en") -> Sentence:
    c4, c3 = names(facts)
    text = {
        "en": f"{c4} beat {c3} 3-2 for a {written}rd win in a row.",
        "es": f"El {c4} gano al {c3} 3-2, su victoria {written} seguida.",
    }[language]
    return Sentence(
        sentence_id=f"streak-{language}",
        text=text,
        language=language,
        claims=(
            Claim(type="score", value={"home": 3, "away": 2}),
            Claim(type="name", value=c4, subject="C4"),
            Claim(type="name", value=c3, subject="C3"),
            Claim(type="season_streak", value=claimed, stat="win", subject="C4"),
        ),
    )


def check(sentence, facts):
    return factgate.check_sentence(sentence, facts)


def test_season_claim_types_are_registered_and_resolve():
    assert set(SEASON_CLAIM_TYPES) <= set(CLAIM_TYPES)
    season = seasonkit.season_facts(1)
    for kind, stat, subject in (
        ("season_streak", "win", "C4"), ("table_position", None, "C4"), ("player_season_total", "goals", "C4P11"),
    ):
        assert kind in SEASON_CLAIM_TYPES
        assert isinstance(recompute.season_claim_value(season, kind, stat, subject, 6), int)


# ---------------------------------------------------------------- season_streak


def test_true_streak_passes(facts):
    verdict = check(streak_sentence(facts), facts)
    assert verdict.passed, verdict.reasons


def test_planted_wrong_streak_is_struck(facts):
    verdict = check(streak_sentence(facts, written=4, claimed=4), facts)
    assert verdict.status == "mismatch"
    assert "season_streak win for C4 after matchday 6: claimed 4, season says 3" in verdict.reasons


def test_streak_written_wrong_in_the_prose_is_struck(facts):
    verdict = check(streak_sentence(facts, written=4, claimed=3), facts)
    assert verdict.status == "mismatch"
    assert any("prose writes 4" in r for r in verdict.reasons), verdict.reasons
    assert any("claim value 3 (1x) is not written" in r for r in verdict.reasons), verdict.reasons


def test_the_same_streak_claim_is_checked_as_of_its_own_match():
    later = seasonkit.match_facts(1, MATCH_C4_C6)
    claim = Claim(type="season_streak", value=3, stat="win", subject="C4")
    assert factgate.check_claim(claim, later) == [
        "season_streak win for C4 after matchday 7: claimed 3, season says 4"
    ]
    earlier = seasonkit.match_facts(1, MATCH_C4_C5)
    assert factgate.check_claim(dataclasses.replace(claim, value=2), earlier) == []


def test_streak_in_a_translation_is_checked_too(facts):
    source = streak_sentence(facts)
    good = streak_sentence(facts, language="es")
    assert factgate.check_translation(source, good, facts).passed
    bad = streak_sentence(facts, written=4, claimed=4, language="es")
    verdict = factgate.check_translation(source, bad, facts)
    assert verdict.status == "mismatch"
    assert any("claimed 4, season says 3" in r for r in verdict.reasons), verdict.reasons


# ---------------------------------------------------------------- table_position


def table_sentence(facts, position: int = 1, points: int = 13) -> Sentence:
    c4, _ = names(facts)
    return Sentence(
        sentence_id="table-en",
        text=f"{c4} lead the table: {position}st on {points} points.",
        language="en",
        claims=(
            Claim(type="name", value=c4, subject="C4"),
            Claim(type="table_position", value=position, subject="C4"),
            Claim(type="table_position", value=points, stat="points", subject="C4"),
        ),
    )


def test_true_table_position_passes(facts):
    verdict = check(table_sentence(facts), facts)
    assert verdict.passed, verdict.reasons


def test_planted_wrong_table_position_is_struck(facts):
    verdict = check(table_sentence(facts, position=2), facts)
    assert verdict.status == "mismatch"
    assert "table_position position for C4 after matchday 6: claimed 2, season says 1" in verdict.reasons


def test_planted_wrong_points_are_struck(facts):
    verdict = check(table_sentence(facts, points=12), facts)
    assert "table_position points for C4 after matchday 6: claimed 12, season says 13" in verdict.reasons


def test_negative_goal_difference_is_written_with_its_digits(facts):
    _, c3 = names(facts)
    claims = (
        Claim(type="name", value=c3, subject="C3"),
        Claim(type="table_position", value=5, subject="C3"),
        Claim(type="table_position", value=-2, stat="goal_difference", subject="C3"),
    )
    good = Sentence("gd-en", f"{c3} are 5th with a goal difference of -2.", "en", claims)
    assert check(good, facts).passed, check(good, facts).reasons
    wrong = Sentence("gd-en", f"{c3} are 5th with a goal difference of -3.", "en", claims[:2] + (
        dataclasses.replace(claims[2], value=-3),))
    assert "table_position goal_difference for C3 after matchday 6: claimed -3, season says -2" in check(wrong, facts).reasons


# ---------------------------------------------------------------- player_season_total


def top_scorer_through(matchday: int) -> tuple[str, int]:
    """Counted straight from the raw events, a second code path next to recompute.player_lines."""
    s = seasonkit.season(1)
    goals = Counter(
        e["player"] for m in s.matches if m.matchday <= matchday
        for e in m.events if e["type"] == "shot" and e["outcome"] == "goal"
    )
    pid, n = sorted(goals.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return pid, n


def player_sentence(facts, pid: str, n: int) -> Sentence:
    pname = facts.roster.players[pid]
    return Sentence(
        sentence_id="player-en",
        text=f"{pname} has {n} goals this season.",
        language="en",
        claims=(
            Claim(type="name", value=pname, subject=pid),
            Claim(type="player_season_total", value=n, stat="goals", subject=pid),
        ),
    )


def test_true_player_total_passes(facts):
    pid, n = top_scorer_through(6)
    verdict = check(player_sentence(facts, pid, n), facts)
    assert verdict.passed, verdict.reasons


def test_planted_wrong_player_total_is_struck(facts):
    pid, n = top_scorer_through(6)
    verdict = check(player_sentence(facts, pid, n + 1), facts)
    assert verdict.status == "mismatch"
    assert f"player_season_total goals for {pid} after matchday 6: claimed {n + 1}, season says {n}" in verdict.reasons


# ---------------------------------------------------------------- fail closed


def test_season_claim_without_season_data_is_a_mismatch():
    bare = seasonkit.match_facts(1, MATCH_C4_C3, with_season=False)
    verdict = check(streak_sentence(bare), bare)
    assert verdict.status == "mismatch"
    assert "season_streak claim needs season data; this match was checked without a season" in verdict.reasons


def test_match_outside_the_season_is_a_mismatch():
    other = dataclasses.replace(seasonkit.plain_match_facts(9), season=seasonkit.season_facts(1))
    claim = Claim(type="season_streak", value=1, stat="win", subject="C1")
    assert factgate.check_claim(claim, other) == ["season_streak claim: match s9-m0 is not in season 1"]


def test_season_record_that_disagrees_with_the_log_is_a_mismatch(facts):
    season = facts.season
    tampered = tuple(
        dataclasses.replace(m, home_goals=m.home_goals + 1) if m.match_index == MATCH_C4_C3 else m
        for m in season.matches
    )
    bad = dataclasses.replace(facts, season=dataclasses.replace(season, matches=tampered))
    claim = Claim(type="season_streak", value=3, stat="win", subject="C4")
    assert factgate.check_claim(claim, bad) == [
        "season_streak claim: season 1 records s1-m17 differently from this match's log"
    ]


@pytest.mark.parametrize(
    "claim,reason",
    [
        (Claim(type="season_streak", value=1, stat="hat_trick", subject="C4"), "unknown season streak 'hat_trick'"),
        (Claim(type="season_streak", value=1, stat="win", subject="C9"), "subject 'C9' is not a club of season 1"),
        (Claim(type="season_streak", value=1, stat="win"), "season_streak claim has no subject"),
        (Claim(type="table_position", value=1, stat="rank", subject="C4"), "unknown table statistic 'rank'"),
        (Claim(type="player_season_total", value=1, stat="goals", subject="C9P01"),
         "player_season_total subject 'C9P01' is not a rostered player"),
        (Claim(type="player_season_total", value=1, stat="tackles", subject="C4P11"),
         "unknown player season statistic 'tackles'"),
        (Claim(type="table_position", value="1st", subject="C4"), "table_position value must be an int"),
        (Claim(type="season_streak", value=-1, stat="win", subject="C4"), "season_streak value must be a non-negative int"),
    ],
)
def test_unresolvable_season_claims_fail_closed(facts, claim, reason):
    assert factgate.check_claim(claim, facts) == [reason]


def test_match_claims_still_pass_with_a_season_attached(facts):
    goals = recompute.stat_events(facts, "goals", "C4")
    claim = Claim(type="stat", value=3, event_ids=tuple(e.event_id for e in goals), stat="goals", subject="C4")
    assert factgate.check_claim(claim, facts) == []
    report = factgate.run_factgate([streak_sentence(facts), table_sentence(facts)], facts)
    assert report.passed, [v.reasons for v in report.verdicts]

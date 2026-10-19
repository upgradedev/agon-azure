"""Season facts in agon.governance.recompute: table, tie-break, form, streaks, head to head, players.

The hand-built seasons below deliberately do not look like the generator's: club ids are K2, K4,
K7 and K9 (so club-id order differs from insertion order), matchdays start at 4, match indices are
not list positions, and records are passed shuffled. Code that treats list position as matchday,
assumes clubs C1 to C6, or orders by club id where the rule says head to head fails here.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

import seasonkit
from agon.governance import recompute
from agon.governance.recompute import RecomputeError, SeasonFacts, SeasonMatch
from agon.synth.schema import make_event

CLUBS = ("K9", "K2", "K7", "K4")


def sm(index: int, matchday: int, home: str, hg: int, ag: int, away: str) -> SeasonMatch:
    return SeasonMatch(
        match_id=f"t-m{index}", match_index=index, matchday=matchday,
        home_id=home, away_id=away, home_goals=hg, away_goals=ag,
    )


def season_of(*matches: SeasonMatch, seed: int = 7) -> SeasonFacts:
    shuffled = list(matches)
    random.Random(seed).shuffle(shuffled)
    return SeasonFacts(season=4, clubs=CLUBS, matches=tuple(shuffled))


def order(season: SeasonFacts, through: int | None = None) -> list[str]:
    return [row["club_id"] for row in recompute.league_table(season, through)]


# ---------------------------------------------------------------- table and tie-break


def test_table_counts_points_goals_and_rows():
    s = season_of(sm(40, 4, "K9", 1, 0, "K2"), sm(41, 5, "K2", 2, 1, "K4"), sm(42, 6, "K4", 2, 1, "K9"))
    rows = {r["club_id"]: r for r in recompute.league_table(s)}
    assert rows["K4"] == {
        "position": 1, "club_id": "K4", "played": 2, "won": 1, "drawn": 0, "lost": 1,
        "goals_for": 3, "goals_against": 3, "goal_difference": 0, "points": 3,
    }
    assert rows["K7"]["played"] == 0 and rows["K7"]["position"] == 4
    assert sorted(r["position"] for r in rows.values()) == [1, 2, 3, 4]


def test_head_to_head_beats_club_id_order():
    # K9 and K2 finish level on points (3), goal difference (0) and goals for (2); K9 won their meeting.
    # Club-id order would put K2 first; the rule puts K9 first.
    s = season_of(sm(40, 4, "K9", 1, 0, "K2"), sm(41, 5, "K2", 2, 1, "K4"), sm(42, 6, "K4", 2, 1, "K9"))
    assert order(s) == ["K4", "K9", "K2", "K7"]


def test_three_way_tie_uses_the_mini_table_then_recurses():
    # K2, K7 and K9 are level overall on 4 points, goal difference 0 and 3 goals for.
    # Among the three: K7 leads on goals for; K2 and K9 stay level, so the rule is applied again to
    # those two alone, and K9 beat K2. Club-id order would be K2, K7, K9.
    s = season_of(
        sm(50, 4, "K2", 2, 1, "K7"),
        sm(51, 5, "K7", 2, 1, "K9"),
        sm(52, 6, "K9", 1, 0, "K2"),
        sm(53, 7, "K2", 1, 1, "K4"),
        sm(54, 8, "K9", 1, 1, "K4"),
        sm(55, 9, "K7", 0, 0, "K4"),
    )
    rows = {r["club_id"]: r for r in recompute.league_table(s)}
    for club in ("K2", "K7", "K9"):
        assert (rows[club]["points"], rows[club]["goal_difference"], rows[club]["goals_for"]) == (4, 0, 3)
    assert order(s) == ["K7", "K9", "K2", "K4"]


def test_club_id_is_the_last_resort_only():
    # K9 and K2 drew each other and are level on everything; K4 and K7 never met and are level too.
    s = season_of(sm(60, 4, "K9", 1, 1, "K2"), sm(61, 5, "K9", 2, 0, "K7"), sm(62, 6, "K2", 2, 0, "K4"))
    assert order(s) == ["K2", "K9", "K4", "K7"]


def test_table_through_a_matchday_ignores_later_matches():
    s = season_of(sm(70, 4, "K9", 0, 3, "K2"), sm(71, 9, "K7", 5, 0, "K2"))
    assert order(s, through=4)[0] == "K2"
    assert order(s, through=8)[0] == "K2"
    assert order(s, through=9)[0] == "K7"
    assert [r["played"] for r in recompute.league_table(s, 3)] == [0, 0, 0, 0]


def test_table_order_does_not_depend_on_record_order():
    matches = (sm(50, 4, "K2", 2, 1, "K7"), sm(51, 5, "K7", 2, 1, "K9"), sm(52, 6, "K9", 1, 0, "K2"))
    tables = {tuple(order(season_of(*matches, seed=seed))) for seed in range(12)}
    assert len(tables) == 1


# ---------------------------------------------------------------- form, streaks, head to head


K9_RUN = (
    sm(80, 4, "K9", 2, 0, "K2"),  # W
    sm(81, 5, "K7", 0, 1, "K9"),  # W
    sm(82, 6, "K9", 0, 2, "K4"),  # L
    sm(83, 7, "K2", 1, 3, "K9"),  # W
    sm(84, 8, "K9", 1, 0, "K7"),  # W
    sm(85, 9, "K4", 0, 4, "K9"),  # W
    sm(86, 10, "K9", 2, 2, "K2"),  # D
)


def test_form_is_the_last_five_oldest_first():
    s = season_of(*K9_RUN)
    rows = recompute.form(s, "K9")
    assert [r["matchday"] for r in rows] == [6, 7, 8, 9, 10]
    assert "".join(r["result"] for r in rows) == "LWWWD"
    assert rows[0] == {
        "matchday": 6, "match_id": "t-m82", "opponent": "K4", "venue": "H",
        "result": "L", "goals_for": 0, "goals_against": 2,
    }
    assert [r["venue"] for r in rows] == ["H", "A", "H", "A", "H"]
    early = recompute.form(s, "K9", through_matchday=5)
    assert "".join(r["result"] for r in early) == "WW"


def test_current_streaks_count_back_from_the_latest_match():
    s = season_of(*K9_RUN)
    after9 = recompute.current_streaks(s, "K9", through_matchday=9)
    assert after9 == {"win": 3, "draw": 0, "loss": 0, "unbeaten": 3, "winless": 0, "scored": 3, "clean_sheet": 2}
    after10 = recompute.current_streaks(s, "K9")
    assert after10 == {"win": 0, "draw": 1, "loss": 0, "unbeaten": 4, "winless": 1, "scored": 4, "clean_sheet": 0}
    after6 = recompute.current_streaks(s, "K9", through_matchday=6)
    assert after6["loss"] == 1 and after6["win"] == 0 and after6["winless"] == 1


def test_head_to_head_lists_every_meeting():
    s = season_of(*K9_RUN)
    h2h = recompute.head_to_head(s, "K2", "K9")
    assert h2h["played"] == 3
    assert h2h["wins"] == {"K2": 0, "K9": 2} and h2h["draws"] == 1
    assert h2h["goals"] == {"K2": 3, "K9": 7}
    assert [m["matchday"] for m in h2h["meetings"]] == [4, 7, 10]
    assert recompute.head_to_head(s, "K2", "K9", through_matchday=6)["played"] == 1


def test_unknown_clubs_and_bad_arguments_fail_closed():
    s = season_of(*K9_RUN)
    with pytest.raises(RecomputeError):
        recompute.form(s, "C1")
    with pytest.raises(RecomputeError):
        recompute.current_streaks(s, "K1")
    with pytest.raises(RecomputeError):
        recompute.head_to_head(s, "K9", "K9")
    with pytest.raises(RecomputeError):
        recompute.form(s, "K9", length=0)


# ---------------------------------------------------------------- player lines from an event log


def _lineup(club: str) -> list[str]:
    return [f"{club}P{n:02d}" for n in range(1, 12)]


class Log:
    """Hand-written events with the engine's field contract (make_event fills the ids)."""

    def __init__(self) -> None:
        self.events = []

    def add(self, etype, team, player, period, minute, second, chain, outcome=None, qualifier=None, related=None):
        end = (600, 300) if etype in ("pass", "carry", "shot") else (None, None)
        self.events.append(make_event(
            match_id="t-m7", seq=len(self.events), period=period, minute=minute, second=second, type=etype,
            team=team, player=player, x_dm=500, y_dm=300, end_x_dm=end[0], end_y_dm=end[1],
            possession_chain_id=chain, outcome=outcome, qualifier=qualifier, related_player=related,
        ))


def hand_match() -> Log:
    log = Log()
    # chain 1: K9P05 passes to K9P09, a K2 press, K9P09 carries and scores: assist K9P05.
    log.add("possession_change", "K9", None, 1, 0, 0, 1, "kick_off")
    log.add("set_piece", "K9", "K9P10", 1, 0, 0, 1, "taken", "kick_off")
    log.add("pass", "K9", "K9P05", 1, 10, 0, 1, "complete", related="K9P09")
    log.add("pressure", "K2", "K2P04", 1, 10, 1, 1, "applied")
    log.add("carry", "K9", "K9P09", 1, 10, 2, 1, "complete")
    log.add("shot", "K9", "K9P09", 1, 10, 5, 1, "goal", "open_play")
    # chain 2: K2P06 to K2P10, then K2P10 passes on and the ball comes back to him: no assist.
    log.add("possession_change", "K2", None, 1, 11, 0, 2, "goal")
    log.add("pass", "K2", "K2P06", 1, 20, 0, 2, "complete", related="K2P10")
    log.add("pass", "K2", "K2P10", 1, 20, 3, 2, "complete", related="K2P11")
    log.add("shot", "K2", "K2P10", 1, 20, 6, 2, "goal", "open_play")
    # a yellow, then a second yellow in first-half added time (46:10): off at 45:00.
    log.add("foul", "K2", "K2P03", 1, 30, 0, 3, "committed")
    log.add("card", "K2", "K2P03", 1, 30, 0, 3, "yellow")
    log.add("foul", "K2", "K2P03", 1, 46, 10, 4, "committed")
    log.add("card", "K2", "K2P03", 1, 46, 10, 4, "second_yellow")
    # chain 5: a penalty goal never has an assist.
    log.add("set_piece", "K9", "K9P10", 2, 50, 0, 5, "taken", "penalty")
    log.add("shot", "K9", "K9P10", 2, 50, 10, 5, "goal", "penalty")
    # a substitution at 65:30: off 65, on 24 (floor of 24.5).
    log.add("sub", "K9", "K9P11", 2, 65, 30, 5, "on", related="K9P13")
    # chain 6: corner by K2P07 to K2P04, headed in: assist K2P07.
    log.add("set_piece", "K2", "K2P07", 2, 70, 0, 6, "taken", "corner")
    log.add("pass", "K2", "K2P07", 2, 70, 2, 6, "complete", related="K2P04")
    log.add("shot", "K2", "K2P04", 2, 70, 4, 6, "goal", "header")
    # a straight red at 80:20 and a substitution in second-half added time (92:00).
    log.add("foul", "K9", "K9P02", 2, 80, 20, 7, "committed")
    log.add("card", "K9", "K9P02", 2, 80, 20, 7, "red")
    log.add("sub", "K2", "K2P08", 2, 92, 0, 7, "on", related="K2P14")
    return log


LINEUPS = {"K9": _lineup("K9"), "K2": _lineup("K2")}


def test_player_lines_minutes_goals_assists_and_cards():
    lines = {l.player_id: l for l in recompute.player_lines(hand_match().events, "K9", "K2", LINEUPS)}
    assert len(lines) == 24  # 22 starters and the 2 replacements
    assert (lines["K9P05"].assists, lines["K9P05"].minutes, lines["K9P05"].started) == (1, 90, True)
    assert lines["K9P09"].goals == 1 and lines["K9P09"].assists == 0
    assert (lines["K9P10"].goals, lines["K9P10"].assists) == (1, 0)
    assert (lines["K2P10"].goals, lines["K2P06"].assists) == (1, 0)
    assert (lines["K2P04"].goals, lines["K2P07"].assists) == (1, 1)
    assert (lines["K2P03"].minutes, lines["K2P03"].yellow_cards, lines["K2P03"].red_cards) == (45, 2, 1)
    assert (lines["K9P11"].minutes, lines["K9P13"].minutes, lines["K9P13"].started) == (65, 24, False)
    assert (lines["K2P08"].minutes, lines["K2P14"].minutes, lines["K2P14"].started) == (90, 0, False)
    assert (lines["K9P02"].minutes, lines["K9P02"].red_cards) == (80, 1)
    assert sum(l.goals for l in lines.values()) == 4
    assert sum(l.assists for l in lines.values()) == 2


def test_season_match_recounts_the_score_and_refuses_a_wrong_meta():
    meta = {"match_id": "t-m7", "match_index": 7, "matchday": 9, "home": "K9", "away": "K2", "score": {"home": 2, "away": 2}}
    record = recompute.season_match(meta, hand_match().events, LINEUPS)
    assert (record.home_goals, record.away_goals, record.matchday, record.match_index) == (2, 2, 9, 7)
    bad = dict(meta, score={"home": 3, "away": 2})
    with pytest.raises(RecomputeError, match="meta score 3-2, log says 2-2"):
        recompute.season_match(bad, hand_match().events, LINEUPS)
    with pytest.raises(RecomputeError, match="no starting eleven"):
        recompute.season_match(meta, hand_match().events, {"K9": LINEUPS["K9"]})


def test_events_by_a_player_who_never_took_the_field_are_refused():
    log = hand_match()
    log.add("card", "K2", "K2P15", 2, 85, 0, 8, "yellow")
    with pytest.raises(RecomputeError, match="never took the field: K2P15"):
        recompute.player_lines(log.events, "K9", "K2", LINEUPS)


def test_player_totals_add_up_across_matchdays():
    meta = {"match_id": "t-m7", "match_index": 7, "matchday": 9, "home": "K9", "away": "K2", "score": {"home": 2, "away": 2}}
    one = recompute.season_match(meta, hand_match().events, LINEUPS)
    two = SeasonMatch(**{**one.__dict__, "match_id": "t-m8", "match_index": 8, "matchday": 5})
    s = SeasonFacts(season=4, clubs=("K9", "K2"), matches=(one, two))
    totals = recompute.player_season_totals(s)
    assert totals["K2P03"]["yellow_cards"] == 4 and totals["K2P03"]["appearances"] == 2
    assert totals["K9P13"]["starts"] == 0 and totals["K9P13"]["minutes"] == 48
    assert recompute.player_season_totals(s, through_matchday=5)["K9P05"]["assists"] == 1


# ---------------------------------------------------------------- on the generated season 1


def test_generated_season_player_lines_agree_with_the_scores_and_the_match_stats():
    s = seasonkit.season(1)
    facts = seasonkit.season_facts(1)
    for match, record in zip(s.matches, facts.matches):
        goals = Counter()
        for line in record.players:
            goals[line.club_id] += line.goals
        assert (goals[record.home_id], goals[record.away_id]) == (match.meta["score"]["home"], match.meta["score"]["away"])
        for club in (record.home_id, record.away_id):
            lines = [l for l in record.players if l.club_id == club]
            minutes = sum(l.minutes for l in lines)
            subs = sum(1 for e in match.events if e["type"] == "sub" and e["team"] == club)
            reds = sum(l.red_cards for l in lines)
            assert minutes <= 990
            if reds == 0:
                assert minutes >= 990 - subs, (record.match_id, club)
            assert len(lines) == 11 + subs
    # Season goals per player from the lines equal an independent count over the raw events.
    raw = Counter(e["player"] for m in s.matches for e in m.events if e["type"] == "shot" and e["outcome"] == "goal")
    totals = recompute.player_season_totals(facts)
    assert {pid: t["goals"] for pid, t in totals.items() if t["goals"]} == dict(raw)


def test_generated_season_table_is_consistent():
    facts = seasonkit.season_facts(1)
    table = recompute.league_table(facts)
    assert [r["position"] for r in table] == [1, 2, 3, 4, 5, 6]
    assert all(r["played"] == 10 for r in table)
    draws = sum(1 for m in facts.matches if m.home_goals == m.away_goals)
    assert sum(r["points"] for r in table) == 3 * (30 - draws) + 2 * draws
    assert sum(r["goal_difference"] for r in table) == 0

"""Shared season builders for the tests: season 1 is simulated at most once per test session.

Importable from every test folder because tests/conftest.py puts tests/ on sys.path.
"""

from __future__ import annotations

from functools import lru_cache

from agon.governance.recompute import MatchFacts, Roster, SeasonFacts, build_season_facts
from agon.synth.engine import simulate_match
from agon.synth.season import Season, build_season
from agon.synth.schema import Event
from agon.synth.world import build_world


@lru_cache(maxsize=None)
def season(number: int = 1) -> Season:
    return build_season(number)


@lru_cache(maxsize=None)
def season_facts(number: int = 1) -> SeasonFacts:
    s = season(number)
    return build_season_facts(
        s.season, [c.club_id for c in s.league.clubs], ((m.meta, m.events, m.lineups) for m in s.matches)
    )


def match_facts(number: int, match_index: int, with_season: bool = True) -> MatchFacts:
    """MatchFacts for one season match, with the season attached unless `with_season` is False."""
    s = season(number)
    m = s.match(match_index)
    return MatchFacts(
        events=tuple(Event(**e) for e in m.events),
        home_id=m.meta["home"],
        away_id=m.meta["away"],
        roster=Roster.from_league(s.league),
        season=season_facts(number) if with_season else None,
    )


def plain_match_facts(seed: int, match_index: int = 0) -> MatchFacts:
    league = build_world(seed)
    result = simulate_match(league, match_index)
    return MatchFacts(
        events=tuple(result.events),
        home_id=result.meta["home"],
        away_id=result.meta["away"],
        roster=Roster.from_league(league),
    )

"""Recompute numbers, minutes, scores and names from the event log.

This is the one place that defines what a statistic means. The fact gate uses it, and the MCP
tool `stats.recompute` is meant to import it, so the number an agent is shown and the number the
gate checks come from the same code.

Every function here reads the FULL event list. A claim's cited event ids are evidence to be
checked against the recomputed set, never the set the count is taken over: otherwise a writer
could cite 2 of 4 shots, write "2" and pass.

Minute convention (pinned): the engine stores `minute = clock // 60`, 0-based. Football prose
counts from 1, and stoppage time is written as an addition: an event at period 1, minute 70 does
not exist; an event at period 2, minute 70 is the "71" minute; period 1, minute 46 is "45+2";
period 2, minute 92 is "90+3". Period 1 stoppage and the start of period 2 share engine minutes
45 to 48, which is why a minute is always derived from (period, minute) and never minute alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from agon.synth.schema import Event

FIRST_HALF_REGULAR = 45
SECOND_HALF_REGULAR = 90


class RecomputeError(ValueError):
    """Raised when a statistic or kind is unknown, or a subject cannot be resolved."""


def _get(event: Event | Mapping[str, Any], key: str) -> Any:
    return getattr(event, key) if isinstance(event, Event) else event[key]


def display_minute(period: int, minute: int) -> str:
    """Football display minute for an engine (period, 0-based minute)."""
    shown = minute + 1
    if period == 1 and shown > FIRST_HALF_REGULAR:
        return f"{FIRST_HALF_REGULAR}+{shown - FIRST_HALF_REGULAR}"
    if period == 2 and shown > SECOND_HALF_REGULAR:
        return f"{SECOND_HALF_REGULAR}+{shown - SECOND_HALF_REGULAR}"
    return str(shown)


@dataclass(frozen=True)
class Roster:
    """Every fictional name the bundle may use, mapped to its id."""

    league_name: str
    clubs: Mapping[str, str]  # club_id -> name
    players: Mapping[str, str]  # player_id -> name
    player_club: Mapping[str, str]  # player_id -> club_id

    @classmethod
    def from_league(cls, league: Any) -> "Roster":
        clubs = {c.club_id: c.name for c in league.clubs}
        players = {p.player_id: p.name for c in league.clubs for p in c.players}
        player_club = {p.player_id: c.club_id for c in league.clubs for p in c.players}
        return cls(league_name=league.name, clubs=clubs, players=players, player_club=player_club)

    def ids_for_name(self, name: str) -> list[str]:
        """Ids whose display name is exactly `name`; the league is id 'league'."""
        found = [cid for cid, n in self.clubs.items() if n == name]
        found += [pid for pid, n in self.players.items() if n == name]
        if name == self.league_name:
            found.append("league")
        return found

    def all_names(self) -> list[str]:
        return [self.league_name, *self.clubs.values(), *self.players.values()]


@dataclass(frozen=True)
class MatchFacts:
    """The event log plus what is needed to read it: home and away ids and the roster."""

    events: tuple[Event, ...]
    home_id: str
    away_id: str
    roster: Roster

    def by_id(self) -> dict[str, Event]:
        return {e.event_id: e for e in self.events}


# Event predicates by kind. second_yellow is a booking that ends in a sending-off, so it counts as
# both a yellow card and a red card.
EVENT_KINDS: dict[str, Callable[[Event], bool]] = {
    "goal": lambda e: e.type == "shot" and e.outcome == "goal",
    "shot": lambda e: e.type == "shot",
    "shot_on_target": lambda e: e.type == "shot" and e.outcome in ("goal", "saved"),
    "save": lambda e: e.type == "shot" and e.outcome == "saved",
    "penalty": lambda e: e.type == "shot" and e.qualifier == "penalty",
    "yellow_card": lambda e: e.type == "card" and e.outcome in ("yellow", "second_yellow"),
    "red_card": lambda e: e.type == "card" and e.outcome in ("red", "second_yellow"),
    "substitution": lambda e: e.type == "sub",
    "foul": lambda e: e.type == "foul",
    "corner": lambda e: e.type == "set_piece" and e.qualifier == "corner",
    "tackle_won": lambda e: e.type == "tackle" and e.outcome == "won",
    "pass": lambda e: e.type == "pass",
    "pass_completed": lambda e: e.type == "pass" and e.outcome == "complete",
}

# Countable statistics: name -> event kind counted for the subject's side.
COUNT_STATS: dict[str, str] = {
    "goals": "goal",
    "shots": "shot",
    "shots_on_target": "shot_on_target",
    "penalties": "penalty",
    "yellow_cards": "yellow_card",
    "red_cards": "red_card",
    "substitutions": "substitution",
    "fouls": "foul",
    "corners": "corner",
    "tackles_won": "tackle_won",
    "passes": "pass",
    "passes_completed": "pass_completed",
}
# Statistics counted from the opponent's events: saves by a club are the opponent's saved shots.
TEAM_ONLY_OPPONENT_STATS: dict[str, str] = {"saves": "save"}
# Ratios, as integer percent rounded half up; no supporting ids are required.
RATIO_STATS: tuple[str, ...] = ("pass_accuracy_pct",)

STAT_KEYS: tuple[str, ...] = tuple(COUNT_STATS) + tuple(TEAM_ONLY_OPPONENT_STATS) + RATIO_STATS


def _is_club(facts: MatchFacts, subject: str) -> bool:
    return subject in (facts.home_id, facts.away_id)


def _subject_filter(facts: MatchFacts, subject: str) -> Callable[[Event], bool]:
    if _is_club(facts, subject):
        return lambda e: e.team == subject
    if subject in facts.roster.players:
        return lambda e: e.player == subject
    raise RecomputeError(f"subject {subject!r} is neither a club in this match nor a rostered player")


def events_of_kind(events: Iterable[Event], kind: str) -> list[Event]:
    if kind not in EVENT_KINDS:
        raise RecomputeError(f"unknown event kind {kind!r}")
    pred = EVENT_KINDS[kind]
    return [e for e in events if pred(e)]


def stat_events(facts: MatchFacts, stat: str, subject: str) -> list[Event]:
    """The full set of events a count statistic is taken over."""
    if stat in COUNT_STATS:
        keep = _subject_filter(facts, subject)
        return [e for e in events_of_kind(facts.events, COUNT_STATS[stat]) if keep(e)]
    if stat in TEAM_ONLY_OPPONENT_STATS:
        if not _is_club(facts, subject):
            raise RecomputeError(f"{stat} is a club statistic; {subject!r} is not a club in this match")
        opponent = facts.away_id if subject == facts.home_id else facts.home_id
        return [e for e in events_of_kind(facts.events, TEAM_ONLY_OPPONENT_STATS[stat]) if e.team == opponent]
    if stat in RATIO_STATS:
        raise RecomputeError(f"{stat} is a ratio; it has no event set")
    raise RecomputeError(f"unknown statistic {stat!r}")


def _pct_half_up(num: int, den: int) -> int:
    if den == 0:
        return 0
    return (200 * num + den) // (2 * den)


def recompute_stat(facts: MatchFacts, stat: str, subject: str) -> int:
    """The value of `stat` for `subject` over the whole event log."""
    if stat == "pass_accuracy_pct":
        keep = _subject_filter(facts, subject)
        passes = [e for e in events_of_kind(facts.events, "pass") if keep(e)]
        done = [e for e in passes if e.outcome == "complete"]
        return _pct_half_up(len(done), len(passes))
    return len(stat_events(facts, stat, subject))


def score_after(facts: MatchFacts, last_seq: int | None) -> dict[str, int]:
    """Score (home, away) after the event with sequence `last_seq`, or at full time when None."""
    home = away = 0
    for e in facts.events:
        if last_seq is not None and e.seq > last_seq:
            continue
        if EVENT_KINDS["goal"](e):
            if e.team == facts.home_id:
                home += 1
            elif e.team == facts.away_id:
                away += 1
    return {"home": home, "away": away}


def event_involves(facts: MatchFacts, event: Event, entity_id: str) -> bool:
    """True if a club, player or the league ('league') is part of this event."""
    if entity_id == "league":
        return True
    if entity_id in facts.roster.clubs:
        return event.team == entity_id
    return entity_id in (event.player, event.related_player)

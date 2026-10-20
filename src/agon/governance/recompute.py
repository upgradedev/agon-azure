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

Season facts (table, form, streaks, head to head, player season totals) live in the last section
of this module, for the same reason: the season claims the gate checks and the season numbers a
page shows must come from one definition. They read plain match records, never the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt
from typing import Any, Callable, Iterable, Mapping, Sequence

from agon.synth.schema import PITCH_X_DM, PITCH_Y_DM, Event

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
    """The event log plus what is needed to read it: home and away ids and the roster.

    `season` is optional and only season claims read it (see the season section at the end of this
    module). Without it every season claim is a mismatch, never a pass.
    """

    events: tuple[Event, ...]
    home_id: str
    away_id: str
    roster: Roster
    season: "SeasonFacts | None" = None

    def by_id(self) -> dict[str, Event]:
        return {e.event_id: e for e in self.events}


# ----------------------------------------------------------------- advanced counts (metrics pack)
#
# The single definition of the countable advanced metrics: agon.interpret.advanced imports these
# predicates for the web data, and the `stat` claims below count with the same ones, so a number on a
# page and the number the Fact Gate checks cannot disagree. Every predicate reads one event (an Event
# or a plain dict) in the ACTING team's frame (agon.synth.schema.COORDINATE_FRAME): the team named on
# the event attacks towards x_dm = 1050, so the goal it attacks is centred on (1050, 340).
#
# * Progressive: a completed pass or carry that starts at x >= 42 m (outside the team's own 40%) and
#   ends at least 9.1 m closer to the centre of the goal it attacks (straight-line distances).
# * Penalty area: x >= 88.5 m (16.5 m deep) and within 20.2 m of the centre line (the same box as
#   agon.interpret.frame; a test pins the equality).
# * Pass into the box: a completed pass that ends inside the penalty area and starts outside it.
# * Cross into the box: a GEOMETRIC PROXY, because the event schema has no cross field: a pass into
#   the box that starts in the final third (x >= 70 m) and wider than the box (more than 20.2 m from
#   the centre line). A corner played into the box counts.
# * Carry into the box: a carry that ends inside the penalty area and starts outside it.
# * Box entry: a pass into the box or a carry into the box.
# * Deep completion: a completed pass that ends within 20 m of the centre of the goal, crosses into
#   the box excluded.
# * Key pass: the pass that set up a shot, by the assist rule (see `key_pass_index`) applied to every
#   shot, not only goals. Penalties have none.
# * Field tilt: a club's share of the passes both clubs attempted from their attacking final third
#   (start x >= 70 m), as a whole percent.
# * Counter-press recovery: after a club loses the ball in open play (the opponent's possession
#   change by interception or tackle), the club regains it in open play (its own possession change
#   by interception or tackle) within 5 seconds of the match clock, in the same period. The rate is
#   recoveries over open-play losses, as a whole percent.

GOAL_CENTRE_X_DM = PITCH_X_DM
GOAL_CENTRE_Y_DM = PITCH_Y_DM // 2
BOX_MIN_X_DM = 885
BOX_HALF_WIDTH_DM = 202
FINAL_THIRD_MIN_X_DM = 700
PROGRESSIVE_MIN_DM = 91
PROGRESSIVE_MIN_START_X_DM = 420
DEEP_COMPLETION_RADIUS_DM = 200
COUNTER_PRESS_SECONDS = 5
OPEN_PLAY_REGAINS: tuple[str, ...] = ("interception", "tackle")


def goal_distance_dm(x_dm: int, y_dm: int) -> int:
    """Straight-line distance from a point to the centre of the goal being attacked, whole dm, floor."""
    return isqrt((GOAL_CENTRE_X_DM - x_dm) ** 2 + (GOAL_CENTRE_Y_DM - y_dm) ** 2)


def in_box(x_dm: int, y_dm: int) -> bool:
    """True inside the penalty area of the goal being attacked."""
    return x_dm >= BOX_MIN_X_DM and abs(y_dm - GOAL_CENTRE_Y_DM) <= BOX_HALF_WIDTH_DM


def _completed_move(e: Event | Mapping[str, Any], etype: str) -> bool:
    return _get(e, "type") == etype and _get(e, "outcome") == "complete" and _get(e, "end_x_dm") is not None


def is_progressive(e: Event | Mapping[str, Any]) -> bool:
    """A completed pass or carry from x >= 42 m that ends at least 9.1 m closer to the goal centre."""
    if not (_completed_move(e, "pass") or _completed_move(e, "carry")):
        return False
    x, y = _get(e, "x_dm"), _get(e, "y_dm")
    if x < PROGRESSIVE_MIN_START_X_DM:
        return False
    return goal_distance_dm(x, y) - goal_distance_dm(_get(e, "end_x_dm"), _get(e, "end_y_dm")) >= PROGRESSIVE_MIN_DM


def is_pass_into_box(e: Event | Mapping[str, Any]) -> bool:
    return (
        _completed_move(e, "pass")
        and in_box(_get(e, "end_x_dm"), _get(e, "end_y_dm"))
        and not in_box(_get(e, "x_dm"), _get(e, "y_dm"))
    )


def is_cross_into_box(e: Event | Mapping[str, Any]) -> bool:
    """Geometric proxy (no cross field exists): a pass into the box from the final third, wider than the box."""
    return (
        is_pass_into_box(e)
        and _get(e, "x_dm") >= FINAL_THIRD_MIN_X_DM
        and abs(_get(e, "y_dm") - GOAL_CENTRE_Y_DM) > BOX_HALF_WIDTH_DM
    )


def is_carry_into_box(e: Event | Mapping[str, Any]) -> bool:
    return (
        _completed_move(e, "carry")
        and in_box(_get(e, "end_x_dm"), _get(e, "end_y_dm"))
        and not in_box(_get(e, "x_dm"), _get(e, "y_dm"))
    )


def is_deep_completion(e: Event | Mapping[str, Any]) -> bool:
    """A completed pass ending within 20 m of the goal centre; crosses into the box are excluded."""
    return (
        _completed_move(e, "pass")
        and goal_distance_dm(_get(e, "end_x_dm"), _get(e, "end_y_dm")) <= DEEP_COMPLETION_RADIUS_DM
        and not is_cross_into_box(e)
    )


def key_pass_index(events: Sequence[Event | Mapping[str, Any]], index: int) -> int | None:
    """Index of the pass that set up the shot at `events[index]`, or None. `events` in sequence order.

    The assist rule, for any shot: walking back from the shot inside its possession chain, and
    skipping the opponent's events and the shooter's own carries, the first event must be a completed
    pass by a teammate whose `related_player` (receiver) is the shooter. Anything else, and every
    penalty, means no key pass.
    """
    shot = events[index]
    if _get(shot, "type") != "shot":
        raise RecomputeError(f"event {_get(shot, 'event_id')} is not a shot")
    if _get(shot, "qualifier") == "penalty":
        return None
    chain, team, shooter = _get(shot, "possession_chain_id"), _get(shot, "team"), _get(shot, "player")
    for j in range(index - 1, -1, -1):
        e = events[j]
        if _get(e, "possession_chain_id") != chain:
            return None
        if _get(e, "team") != team:
            continue
        if _get(e, "type") == "carry" and _get(e, "player") == shooter:
            continue
        if (
            _get(e, "type") == "pass"
            and _get(e, "outcome") == "complete"
            and _get(e, "related_player") == shooter
            and _get(e, "player") != shooter
        ):
            return j
        return None
    return None


def _in_seq_order(events: Iterable[Event | Mapping[str, Any]]) -> list[Event | Mapping[str, Any]]:
    return sorted(events, key=lambda e: _get(e, "seq"))


def key_pass_events(events: Iterable[Event | Mapping[str, Any]]) -> list[Event | Mapping[str, Any]]:
    """Every key pass in the log, in sequence order (one shot ends its chain, so no pass is counted twice)."""
    evs = _in_seq_order(events)
    found = []
    for i, e in enumerate(evs):
        if _get(e, "type") == "shot":
            j = key_pass_index(evs, i)
            if j is not None:
                found.append(evs[j])
    return found


def final_third_passes(events: Iterable[Event | Mapping[str, Any]], team: str) -> int:
    """Passes attempted by `team` from its attacking final third (start x >= 70 m), any outcome."""
    return sum(
        1 for e in events
        if _get(e, "type") == "pass" and _get(e, "team") == team and _get(e, "x_dm") >= FINAL_THIRD_MIN_X_DM
    )


def _clock_of(e: Event | Mapping[str, Any]) -> int:
    return _get(e, "minute") * 60 + _get(e, "second")


def counter_press(events: Iterable[Event | Mapping[str, Any]], team: str) -> list[tuple[Any, Any]]:
    """(loss, regain or None) for every open-play loss of `team`, in sequence order.

    A loss is a possession change to the other club by interception or tackle. It is a 5-second
    recovery when the next possession change in the log is `team`'s own, by interception or tackle, in
    the same period, at most `COUNTER_PRESS_SECONDS` of match clock later.
    """
    changes = [e for e in _in_seq_order(events) if _get(e, "type") == "possession_change"]
    out: list[tuple[Any, Any]] = []
    for k, e in enumerate(changes):
        if _get(e, "team") == team or _get(e, "outcome") not in OPEN_PLAY_REGAINS:
            continue
        nxt = changes[k + 1] if k + 1 < len(changes) else None
        regained = (
            nxt is not None
            and _get(nxt, "team") == team
            and _get(nxt, "outcome") in OPEN_PLAY_REGAINS
            and _get(nxt, "period") == _get(e, "period")
            and _clock_of(nxt) - _clock_of(e) <= COUNTER_PRESS_SECONDS
        )
        out.append((e, nxt if regained else None))
    return out


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
    # The metrics pack (definitions in the "advanced counts" section above). These also accept a
    # plain event dict, so agon.interpret.advanced counts with the very same predicates.
    "progressive_pass": lambda e: _get(e, "type") == "pass" and is_progressive(e),
    "progressive_carry": lambda e: _get(e, "type") == "carry" and is_progressive(e),
    "pass_into_box": is_pass_into_box,
    "cross_into_box": is_cross_into_box,
    "carry_into_box": is_carry_into_box,
    "box_entry": lambda e: is_pass_into_box(e) or is_carry_into_box(e),
    "deep_completion": is_deep_completion,
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
    "progressive_passes": "progressive_pass",
    "progressive_carries": "progressive_carry",
    "passes_into_box": "pass_into_box",
    "crosses_into_box": "cross_into_box",
    "carries_into_box": "carry_into_box",
    "box_entries": "box_entry",
    "deep_completions": "deep_completion",
}
# Statistics counted from the opponent's events: saves by a club are the opponent's saved shots.
TEAM_ONLY_OPPONENT_STATS: dict[str, str] = {"saves": "save"}
# Counts that need the sequence, not one event: the key passes of the subject (see key_pass_index).
SEQUENCE_STATS: tuple[str, ...] = ("key_passes",)
# Ratios, as integer percent rounded half up; no supporting ids are required.
RATIO_STATS: tuple[str, ...] = ("pass_accuracy_pct", "field_tilt_pct", "counter_press_recovery_pct")
# Ratios that only a club can have (they compare the two clubs or follow a club's losses).
CLUB_ONLY_RATIO_STATS: tuple[str, ...] = ("field_tilt_pct", "counter_press_recovery_pct")

STAT_KEYS: tuple[str, ...] = tuple(COUNT_STATS) + tuple(TEAM_ONLY_OPPONENT_STATS) + RATIO_STATS + SEQUENCE_STATS


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
    if stat == "key_passes":
        keep = _subject_filter(facts, subject)
        return [e for e in key_pass_events(facts.events) if keep(e)]
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
    if stat in CLUB_ONLY_RATIO_STATS:
        if not _is_club(facts, subject):
            raise RecomputeError(f"{stat} is a club statistic; {subject!r} is not a club in this match")
        if stat == "field_tilt_pct":
            opponent = facts.away_id if subject == facts.home_id else facts.home_id
            own = final_third_passes(facts.events, subject)
            return _pct_half_up(own, own + final_third_passes(facts.events, opponent))
        pairs = counter_press(facts.events, subject)
        return _pct_half_up(sum(1 for _, regain in pairs if regain is not None), len(pairs))
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


# ----------------------------------------------------------------- season facts
#
# The claim types season_streak, table_position and player_season_total are recomputed here from a
# `SeasonFacts`: every match of one league season reduced to a `SeasonMatch` (matchday, the two clubs,
# the score and one `PlayerLine` per player who appeared). `season_match` builds that record from the
# event log and the two starting elevens only, never from engine internals, and it counts with
# EVENT_KINDS, so a season total and the same match statistic can never disagree.
#
# Definitions (pinned, each has a test):
#
# * Result: 3 points for a win, 1 for a draw, 0 for a loss.
# * Table order: `TIEBREAK_RULE`. Every club gets a distinct position, so the order is total.
# * "As of": a season claim made in a match report is checked against the season after the matchday
#   of the reported match (`through_matchday`): the table after all matches of that matchday, a
#   club's run up to and including its match of that matchday, a player's total through it.
# * Form: a club's last `FORM_LENGTH` matches up to that matchday, OLDEST FIRST.
# * Streaks: the current run, counted back from the club's latest match up to that matchday. "win"
#   counts consecutive wins, "unbeaten" wins or draws, "winless" draws or losses, "scored" matches with
#   at least one goal for, "clean_sheet" matches with none against.
# * Assist: the pass that set up a goal. Walking back from the goal inside its possession chain, and
#   skipping the opponent's events and the scorer's own carries, the first event must be a completed
#   pass by a teammate whose `related_player` (receiver) is the scorer. Anything else, and every
#   penalty, means no assist. The engine emits no assist field; this rule reads the log.
# * Minutes: whole minutes, floor. A starter is on from 0; a replacement from the second of the `sub`
#   event that brings him on; a player is off at the second of the `sub` that replaces him or of the
#   card that sends him off, otherwise at full time. The clock second is capped at 45:00 in period 1
#   and at 90:00 in period 2, so added time never counts and period 2 starts at 45:00.
# * Cards: the match convention above, so a second yellow counts as a yellow card and a red card.

POINTS_WIN = 3
POINTS_DRAW = 1
HALF_TIME_SECOND = FIRST_HALF_REGULAR * 60
FULL_TIME_SECOND = SECOND_HALF_REGULAR * 60
FORM_LENGTH = 5

SEASON_STREAK_STATS: tuple[str, ...] = ("win", "draw", "loss", "unbeaten", "winless", "scored", "clean_sheet")
TABLE_STATS: tuple[str, ...] = (
    "position", "points", "played", "won", "drawn", "lost", "goals_for", "goals_against", "goal_difference",
)
PLAYER_SEASON_STATS: tuple[str, ...] = (
    "goals", "assists", "minutes", "appearances", "starts", "yellow_cards", "red_cards",
)
TIEBREAK_RULE = (
    "points, then goal difference, then goals for; clubs still level on all three are ordered by a "
    "head-to-head table of the matches among exactly those clubs (points, then goal difference, then "
    "goals for), applied again to any smaller group it leaves level; clubs it cannot separate are "
    "ordered by club id, an administrative fallback and not a sporting rule"
)

_STREAK_TESTS: dict[str, Callable[[int, int], bool]] = {
    "win": lambda gf, ga: gf > ga,
    "draw": lambda gf, ga: gf == ga,
    "loss": lambda gf, ga: gf < ga,
    "unbeaten": lambda gf, ga: gf >= ga,
    "winless": lambda gf, ga: gf <= ga,
    "scored": lambda gf, ga: gf > 0,
    "clean_sheet": lambda gf, ga: ga == 0,
}


@dataclass(frozen=True)
class PlayerLine:
    """One player's part in one match."""

    player_id: str
    club_id: str
    started: bool
    minutes: int
    goals: int
    assists: int
    yellow_cards: int
    red_cards: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "club_id": self.club_id,
            "started": self.started,
            "minutes": self.minutes,
            "goals": self.goals,
            "assists": self.assists,
            "yellow_cards": self.yellow_cards,
            "red_cards": self.red_cards,
        }


@dataclass(frozen=True)
class SeasonMatch:
    """One season match reduced to what season facts need."""

    match_id: str
    match_index: int
    matchday: int
    home_id: str
    away_id: str
    home_goals: int
    away_goals: int
    players: tuple[PlayerLine, ...] = ()

    def involves(self, club: str) -> bool:
        return club in (self.home_id, self.away_id)

    def goals_for(self, club: str) -> int:
        return self.home_goals if club == self.home_id else self.away_goals

    def goals_against(self, club: str) -> int:
        return self.away_goals if club == self.home_id else self.home_goals

    def opponent(self, club: str) -> str:
        return self.away_id if club == self.home_id else self.home_id

    def result(self, club: str) -> str:
        """W, D or L for `club`."""
        gf, ga = self.goals_for(club), self.goals_against(club)
        return "W" if gf > ga else "D" if gf == ga else "L"


@dataclass(frozen=True)
class SeasonFacts:
    """Every match of one league season, in any order; the functions below sort by matchday."""

    season: int
    clubs: tuple[str, ...]
    matches: tuple[SeasonMatch, ...]

    def match_by_id(self, match_id: str) -> SeasonMatch | None:
        for m in self.matches:
            if m.match_id == match_id:
                return m
        return None


def _as_event(event: Event | Mapping[str, Any]) -> Event:
    return event if isinstance(event, Event) else Event(**event)


def _capped_second(event: Event) -> int:
    clock = event.minute * 60 + event.second
    if event.period == 1:
        return min(clock, HALF_TIME_SECOND)
    return min(max(clock, HALF_TIME_SECOND), FULL_TIME_SECOND)


def assist_for(events: Sequence[Event], index: int) -> str | None:
    """The player id credited with the assist for the goal at `events[index]`, or None (rule above)."""
    goal = events[index]
    if not EVENT_KINDS["goal"](goal):
        raise RecomputeError(f"event {goal.event_id} is not a goal")
    j = key_pass_index(events, index)
    return None if j is None else events[j].player


def player_lines(
    events: Iterable[Event | Mapping[str, Any]],
    home_id: str,
    away_id: str,
    lineups: Mapping[str, Sequence[str]],
) -> tuple[PlayerLine, ...]:
    """One line per player who appeared, sorted by player id (minutes, goals, assists, cards)."""
    for club in (home_id, away_id):
        if club not in lineups:
            raise RecomputeError(f"no starting eleven for {club}")
    evs = sorted((_as_event(e) for e in events), key=lambda e: e.seq)
    on: dict[str, int] = {}
    off: dict[str, int] = {}
    club_of: dict[str, str] = {}
    for club in (home_id, away_id):
        for pid in lineups[club]:
            on[pid] = 0
            club_of[pid] = club
    started = set(on)
    goals: dict[str, int] = {}
    assists: dict[str, int] = {}
    yellows: dict[str, int] = {}
    reds: dict[str, int] = {}
    for i, e in enumerate(evs):
        if e.type == "sub" and e.player:
            off.setdefault(e.player, _capped_second(e))
            if e.related_player and e.related_player not in on:
                on[e.related_player] = _capped_second(e)
                club_of[e.related_player] = e.team
        if EVENT_KINDS["red_card"](e) and e.player:
            off.setdefault(e.player, _capped_second(e))
            reds[e.player] = reds.get(e.player, 0) + 1
        if EVENT_KINDS["yellow_card"](e) and e.player:
            yellows[e.player] = yellows.get(e.player, 0) + 1
        if EVENT_KINDS["goal"](e) and e.player:
            goals[e.player] = goals.get(e.player, 0) + 1
            passer = assist_for(evs, i)
            if passer is not None:
                assists[passer] = assists.get(passer, 0) + 1
    unseen = sorted(pid for pid in set(goals) | set(assists) | set(yellows) | set(reds) if pid not in on)
    if unseen:
        raise RecomputeError(f"players with events who never took the field: {', '.join(unseen)}")
    return tuple(
        PlayerLine(
            player_id=pid,
            club_id=club_of[pid],
            started=pid in started,
            minutes=max(0, off.get(pid, FULL_TIME_SECOND) - on[pid]) // 60,
            goals=goals.get(pid, 0),
            assists=assists.get(pid, 0),
            yellow_cards=yellows.get(pid, 0),
            red_cards=reds.get(pid, 0),
        )
        for pid in sorted(on)
    )


def season_match(
    meta: Mapping[str, Any], events: Iterable[Event | Mapping[str, Any]], lineups: Mapping[str, Sequence[str]]
) -> SeasonMatch:
    """Reduce one match to a SeasonMatch. The score is recounted from the log and must equal the meta."""
    evs = [_as_event(e) for e in events]
    home_id, away_id = meta["home"], meta["away"]
    home_goals = sum(1 for e in evs if EVENT_KINDS["goal"](e) and e.team == home_id)
    away_goals = sum(1 for e in evs if EVENT_KINDS["goal"](e) and e.team == away_id)
    stated = meta.get("score")
    if stated is not None and (stated["home"], stated["away"]) != (home_goals, away_goals):
        raise RecomputeError(
            f"{meta['match_id']}: meta score {stated['home']}-{stated['away']}, log says {home_goals}-{away_goals}"
        )
    return SeasonMatch(
        match_id=meta["match_id"],
        match_index=int(meta["match_index"]),
        matchday=int(meta["matchday"]),
        home_id=home_id,
        away_id=away_id,
        home_goals=home_goals,
        away_goals=away_goals,
        players=player_lines(evs, home_id, away_id, lineups),
    )


def build_season_facts(
    season: int,
    clubs: Sequence[str],
    matches: Iterable[tuple[Mapping[str, Any], Iterable[Event | Mapping[str, Any]], Mapping[str, Sequence[str]]]],
) -> SeasonFacts:
    """SeasonFacts from (meta, events, lineups) per match, for example every match of agon.synth.season."""
    return SeasonFacts(
        season=season,
        clubs=tuple(clubs),
        matches=tuple(season_match(meta, events, lineups) for meta, events, lineups in matches),
    )


def _played(season: SeasonFacts, through_matchday: int | None) -> list[SeasonMatch]:
    rows = [m for m in season.matches if through_matchday is None or m.matchday <= through_matchday]
    return sorted(rows, key=lambda m: (m.matchday, m.match_index))


def _check_club(season: SeasonFacts, club: str) -> None:
    if club not in season.clubs:
        raise RecomputeError(f"subject {club!r} is not a club of season {season.season}")


def _standings(matches: Iterable[SeasonMatch], clubs: Iterable[str]) -> dict[str, dict[str, int]]:
    keys = ("played", "won", "drawn", "lost", "goals_for", "goals_against", "goal_difference", "points")
    rows = {c: {k: 0 for k in keys} for c in clubs}
    for m in matches:
        for club in (m.home_id, m.away_id):
            if club not in rows:
                continue
            row = rows[club]
            gf, ga = m.goals_for(club), m.goals_against(club)
            row["played"] += 1
            row["goals_for"] += gf
            row["goals_against"] += ga
            row["goal_difference"] += gf - ga
            if gf > ga:
                row["won"] += 1
                row["points"] += POINTS_WIN
            elif gf == ga:
                row["drawn"] += 1
                row["points"] += POINTS_DRAW
            else:
                row["lost"] += 1
    return rows


def _primary_key(row: Mapping[str, int]) -> tuple[int, int, int]:
    return (-row["points"], -row["goal_difference"], -row["goals_for"])


def league_table(season: SeasonFacts, through_matchday: int | None = None) -> list[dict[str, Any]]:
    """The table after `through_matchday` (or the whole season), ordered by `TIEBREAK_RULE`."""
    matches = _played(season, through_matchday)
    rows = _standings(matches, season.clubs)
    by_key: dict[tuple[int, int, int], list[str]] = {}
    for club in sorted(season.clubs):
        by_key.setdefault(_primary_key(rows[club]), []).append(club)
    ordered: list[str] = []
    for key in sorted(by_key):
        ordered.extend(_head_to_head_order(by_key[key], matches))
    return [{"position": i + 1, "club_id": club, **rows[club]} for i, club in enumerate(ordered)]


def _head_to_head_order(group: list[str], matches: Sequence[SeasonMatch]) -> list[str]:
    """Order clubs level on points, goal difference and goals for (`TIEBREAK_RULE`, from step 4).

    Each call either splits the group into smaller groups by the head-to-head table and recurses into
    each, or cannot split it and falls back to club id, so it always terminates.
    """
    if len(group) < 2:
        return list(group)
    members = set(group)
    among = [m for m in matches if m.home_id in members and m.away_id in members]
    mini = _standings(among, group)
    buckets: dict[tuple[int, int, int], list[str]] = {}
    for club in sorted(group):
        buckets.setdefault(_primary_key(mini[club]), []).append(club)
    if len(buckets) == 1:
        return sorted(group)
    out: list[str] = []
    for key in sorted(buckets):
        out.extend(_head_to_head_order(buckets[key], matches))
    return out


def club_matches(season: SeasonFacts, club: str, through_matchday: int | None = None) -> list[SeasonMatch]:
    """A club's matches up to `through_matchday`, in matchday order."""
    _check_club(season, club)
    return [m for m in _played(season, through_matchday) if m.involves(club)]


def form(
    season: SeasonFacts, club: str, through_matchday: int | None = None, length: int = FORM_LENGTH
) -> list[dict[str, Any]]:
    """The club's last `length` matches up to `through_matchday`, OLDEST FIRST."""
    if length < 1:
        raise RecomputeError("form length must be at least 1")
    rows = club_matches(season, club, through_matchday)[-length:]
    return [
        {
            "matchday": m.matchday,
            "match_id": m.match_id,
            "opponent": m.opponent(club),
            "venue": "H" if m.home_id == club else "A",
            "result": m.result(club),
            "goals_for": m.goals_for(club),
            "goals_against": m.goals_against(club),
        }
        for m in rows
    ]


def current_streaks(season: SeasonFacts, club: str, through_matchday: int | None = None) -> dict[str, int]:
    """Every current run of the club, counted back from its latest match up to `through_matchday`."""
    rows = club_matches(season, club, through_matchday)
    out: dict[str, int] = {}
    for name in SEASON_STREAK_STATS:
        test = _STREAK_TESTS[name]
        n = 0
        for m in reversed(rows):
            if not test(m.goals_for(club), m.goals_against(club)):
                break
            n += 1
        out[name] = n
    return out


def head_to_head(season: SeasonFacts, club_a: str, club_b: str, through_matchday: int | None = None) -> dict[str, Any]:
    """Every meeting of two clubs up to `through_matchday`, with wins, draws and goals per club."""
    _check_club(season, club_a)
    _check_club(season, club_b)
    if club_a == club_b:
        raise RecomputeError("head to head needs two different clubs")
    meetings = [m for m in _played(season, through_matchday) if m.involves(club_a) and m.involves(club_b)]
    return {
        "clubs": [club_a, club_b],
        "played": len(meetings),
        "wins": {
            club_a: sum(1 for m in meetings if m.result(club_a) == "W"),
            club_b: sum(1 for m in meetings if m.result(club_b) == "W"),
        },
        "draws": sum(1 for m in meetings if m.result(club_a) == "D"),
        "goals": {club_a: sum(m.goals_for(club_a) for m in meetings), club_b: sum(m.goals_for(club_b) for m in meetings)},
        "meetings": [
            {
                "matchday": m.matchday,
                "match_id": m.match_id,
                "home": m.home_id,
                "away": m.away_id,
                "score": {"home": m.home_goals, "away": m.away_goals},
            }
            for m in meetings
        ],
    }


def player_season_totals(season: SeasonFacts, through_matchday: int | None = None) -> dict[str, dict[str, Any]]:
    """Per player id: the club and every PLAYER_SEASON_STATS total up to `through_matchday`, sorted by id."""
    totals: dict[str, dict[str, Any]] = {}
    for m in _played(season, through_matchday):
        for line in m.players:
            t = totals.setdefault(line.player_id, {"club_id": line.club_id, **{k: 0 for k in PLAYER_SEASON_STATS}})
            t["appearances"] += 1
            t["starts"] += 1 if line.started else 0
            t["minutes"] += line.minutes
            t["goals"] += line.goals
            t["assists"] += line.assists
            t["yellow_cards"] += line.yellow_cards
            t["red_cards"] += line.red_cards
    return dict(sorted(totals.items()))


def season_claim_value(
    season: SeasonFacts, claim_type: str, stat: str | None, subject: str | None, through_matchday: int
) -> int:
    """The true value of a season claim after `through_matchday`; RecomputeError when it cannot be resolved."""
    if not subject:
        raise RecomputeError(f"{claim_type} claim has no subject")
    if claim_type == "season_streak":
        if stat not in SEASON_STREAK_STATS:
            raise RecomputeError(f"unknown season streak {stat!r}")
        return current_streaks(season, subject, through_matchday)[stat]
    if claim_type == "table_position":
        key = "position" if stat is None else stat
        if key not in TABLE_STATS:
            raise RecomputeError(f"unknown table statistic {stat!r}")
        _check_club(season, subject)
        for row in league_table(season, through_matchday):
            if row["club_id"] == subject:
                return int(row[key])
        raise RecomputeError(f"{subject} is missing from the table")
    if claim_type == "player_season_total":
        if stat not in PLAYER_SEASON_STATS:
            raise RecomputeError(f"unknown player season statistic {stat!r}")
        return int(player_season_totals(season, through_matchday).get(subject, {}).get(stat, 0))
    raise RecomputeError(f"{claim_type!r} is not a season claim type")

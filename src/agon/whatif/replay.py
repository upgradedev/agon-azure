"""Paired replays of one fixture from a scenario minute, with common random numbers.

How a pair is built. `ReplaySimulator` is the unchanged engine (`agon.synth.engine.MatchSimulator`)
plus three things, all inside this subclass so engine.py stays untouched:

1. A resumable loop. `start()` and `advance(until)` play exactly the chains `MatchSimulator.run()`
   plays, in the same order, but can pause before the first chain that opens at or after a
   (period, clock) point: the same test the engine uses to switch an intervention on. A test pins
   that a replay with no pause reproduces the published match byte for byte.
2. A fork. `fork(...)` copies the paused state (teams, events so far, clock, the pending restart)
   into a new simulator with the scenario's interventions and a replicate number. Everything before
   the pause is literally shared, so the pre-scenario play of both arms and of every replicate is
   identical to the published match by construction, and a test counts the differences (zero).
3. Replicate keying. After the fork every chain-keyed random stream (`_stream`: chain, action,
   spatial, timing, pressure, shot, discipline) is drawn with `replicate = r`, a key position the
   counter-based generator already has (`agon.synth.rng`). Replicate r of the baseline arm and
   replicate r of the scenario arm use the same keys chain by chain: common random numbers, so the
   two arms differ only by what the scenario changes and by where that change sends the play.
   Replicate 0 is the published match itself.

What stays shared across replicates (stated, not hidden): the added time of each half and the
substitution minutes are drawn at kick-off (STOPPAGE and SUBS streams), and the choice of who is
substituted is keyed by the substitution count (PERSONNEL stream). Only the match play after the
scenario minute varies between replicates.

Scenario changes available here, on top of the engine's own interventions (red_card,
goalkeeper_heroics and the rest, applied by the engine exactly as storylines apply them):
- a press-height knob shift for one side from the fork on: the side's effective `press_height`
  (read by the engine's `_press_ppm`) moves by `press_shift` per mille after the engine's own
  clamp, and is clamped again to 50..950. Up or down, the same knob tactical_shift moves.
- a line that drops after the side scores: from the fork on, the first goal by the side switches on
  the engine's `late_collapse` for that side from the next chain (the opponent's chances against it
  are multiplied by the magnitude per mille, and the opponent shoots more often).

Nothing here reads the referee package or any label; outputs are aggregates over replays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from agon.synth.engine import CENTRE, HALF_SECONDS, STOPPAGE_RANGES, MatchResult, MatchSimulator, Restart, TeamState, _Live
from agon.synth.interventions import Intervention
from agon.synth.rng import Stream, StreamId
from agon.synth.world import League, Style

PRESS_MIN = 50
PRESS_MAX = 950


@dataclass(frozen=True)
class Change:
    """What a scenario changes from the fork on. All fields optional; an empty Change is the baseline arm."""

    interventions: tuple[Intervention, ...] = ()
    press_side: str | None = None
    press_shift: int = 0
    collapse_side: str | None = None
    collapse_magnitude: int = 0


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def _copy_team(t: TeamState) -> TeamState:
    return TeamState(
        club=t.club,
        on_pitch=list(t.on_pitch),
        bench=list(t.bench),
        sub_plan=list(t.sub_plan),
        subs_used=t.subs_used,
        yellows=dict(t.yellows),
        sent_off=list(t.sent_off),
        goals=t.goals,
        momentum=t.momentum,
    )


class ReplaySimulator(MatchSimulator):
    """The engine with a pause, a fork and replicate keying (see the module docstring)."""

    def __init__(self, league: League, match_index: int = 0) -> None:
        super().__init__(league, match_index, ())
        self.replicate = 0
        self.branched = False
        self.change = Change()
        self.collapse_at: tuple[int, int] | None = None
        self._next_period = 1
        self._in_period = False
        self._restart: Restart | None = None

    # ------------------------------------------------------------- the resumable loop

    def start(self) -> "ReplaySimulator":
        """Kick-off draws, exactly as MatchSimulator.run() makes them."""
        if self.events:
            raise RuntimeError("a ReplaySimulator starts once")
        stoppage = Stream(self.seed, self.match_key, StreamId.STOPPAGE)
        self._added = [stoppage.between(*STOPPAGE_RANGES[0]), stoppage.between(*STOPPAGE_RANGES[1])]
        self._plan_subs()
        return self

    def advance(self, until: tuple[int, int] | None = None) -> bool:
        """Play chains as MatchSimulator.run() does. Returns True at full time.

        With `until` = (period, clock seconds), stops before the first chain that would open at or
        after it (the engine's intervention test) and returns False.
        """
        kick_off_teams = (self.home, self.away)
        while True:
            if not self._in_period:
                if self._next_period > 2:
                    return True
                period = self._next_period
                self._next_period += 1
                self.period = period
                self.clock = (period - 1) * HALF_SECONDS
                self.period_end = period * HALF_SECONDS + self._added[period - 1]
                self.home.momentum = 0
                self.away.momentum = 0
                self.last_team = None
                self._restart = Restart(kick_off_teams[period - 1], (7, 6), "kick_off", "kick_off", CENTRE)
                self._in_period = True
            if self._restart is None or self.clock >= self.period_end:
                self._in_period = False
                continue
            if until is not None and (self.period, self.clock) >= until:
                return False
            if self._live:
                self._activate_due()
            if self.period == 2:
                self._due_subs()
            self._restart = self._run_chain(self._restart)

    def result(self) -> MatchResult:
        return MatchResult(meta=self._meta(self._added), events=self.events, applied=self._ledger())

    # ------------------------------------------------------------- the fork

    def fork(self, change: Change = Change(), replicate: int = 0) -> "ReplaySimulator":
        """A copy of this paused simulator that plays on with `change` and replicate `replicate`."""
        twin = ReplaySimulator(self.league, self.match_index)
        twin.home, twin.away = _copy_team(self.home), _copy_team(self.away)
        mapping = {id(self.home): twin.home, id(self.away): twin.away}
        twin.events = list(self.events)
        twin.chain_id, twin.clock, twin.period, twin.period_end = self.chain_id, self.clock, self.period, self.period_end
        twin.last_team = mapping[id(self.last_team)] if self.last_team is not None else None
        twin._added = list(self._added)
        twin._next_period, twin._in_period = self._next_period, self._in_period
        r = self._restart
        twin._restart = None if r is None else Restart(mapping[id(r.team)], r.zone, r.kind, r.reason, r.point)
        twin._live = [
            _Live(spec=iv, team=twin.home if iv.side == "home" else twin.away, index=i)
            for i, iv in enumerate(change.interventions)
        ]
        twin.change = change
        twin.replicate = replicate
        twin.branched = True
        return twin

    # ------------------------------------------------------------- overrides

    def _stream(self, stream_id: int, possession: int | None = None) -> Stream:
        rep = self.replicate if self.branched else 0
        return Stream(self.seed, self.match_key, stream_id, self.chain_id if possession is None else possession, replicate=rep)

    def _side_team(self, side: str | None) -> TeamState | None:
        return None if side is None else (self.home if side == "home" else self.away)

    def _effective_style(self, team: TeamState) -> Style:
        style = super()._effective_style(team)
        if self.branched and self.change.press_shift and team is self._side_team(self.change.press_side):
            return Style(_clamp(style.press_height + self.change.press_shift, PRESS_MIN, PRESS_MAX), style.directness, style.width)
        return style

    def _shot(self, team: TeamState, shooter, zone, point, pressed: bool, qualifier: str) -> Restart:
        restart = super()._shot(team, shooter, zone, point, pressed, qualifier)
        side = self.change.collapse_side
        if (
            self.branched and side is not None and self.collapse_at is None
            and team is self._side_team(side) and self.events[-1].outcome == "goal"
        ):
            minute = self.clock // 60
            minute = min(minute, 44) if self.period == 1 else _clamp(minute, 45, 89)
            spec = Intervention("late_collapse", side, self.period, minute, self.change.collapse_magnitude)
            self._live.append(_Live(spec=spec, team=team, index=len(self._live)))
            self.collapse_at = (self.period, self.clock)
        return restart


def replay_to(league: League, match_index: int, until: tuple[int, int]) -> tuple[ReplaySimulator, bool]:
    """Play the published match up to the pause point. Returns (simulator, reached_full_time)."""
    sim = ReplaySimulator(league, match_index).start()
    over = sim.advance(until)
    return sim, over


def play_out(sim: ReplaySimulator) -> MatchResult:
    sim.advance()
    return sim.result()


def full_replay(league: League, match_index: int = 0) -> MatchResult:
    """The whole match through the resumable loop, with no pause: must equal simulate_match."""
    sim = ReplaySimulator(league, match_index).start()
    sim.advance()
    return sim.result()


__all__: Sequence[str] = ("Change", "ReplaySimulator", "full_replay", "play_out", "replay_to")

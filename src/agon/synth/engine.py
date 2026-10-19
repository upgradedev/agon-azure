"""Match engine (synth-v0.2): a semi-Markov possession-chain simulator on a 16 by 12 zone grid.

What it does, honestly: each possession is a chain of actions (pass, carry, shot) by the team in
possession, interrupted by the defending side's pressure, tackles and fouls. Each action draws a
sojourn time in whole seconds (the semi-Markov part), so the clock advances by action, not by a
fixed tick. Probabilities are integers in parts per million. The constants in the "Calibrated
constants" block were fitted by hand on calibration seeds 1 to 200 against the realism bands
frozen in bands.json (metric M2); the held-out result, which is below its threshold, is recorded
in DATASET_CARD.md section 4. Storyline interventions (agon.synth.interventions) are applied only
when passed in; a plain match never touches them.

Where each style knob is read (every knob needs a reader):
- press_height: `_press_ppm` (how often the defending side presses, higher in the attacker's own half)
- directness: `_pass_target` (forward pass weights), `_action_weights` (carry share) and the
  short-game completion bonus in `_run_chain` (`SHORT_GAME_PPM_PER_POINT`)
- width: `_pass_target` (lateral pass weights)
Score effects and the momentum term enter through `_effective_style` and `_momentum_bonus`.

Determinism: every random draw comes from `rng.Stream` keyed by (seed, match_index + 1, stream,
chain id). No floats, no `random`, no `hash()`, no set iteration on any path that affects output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from agon.synth.interventions import Intervention
from agon.synth.rng import PPM, Stream, StreamId
from agon.synth.schema import COORDINATE_FRAME, PITCH_X_DM, PITCH_Y_DM, Event, make_event
from agon.synth.world import Club, League, Player, Style, fixture_list

GENERATOR_VERSION = "synth-v0.2"  # v0.2: constants calibrated against frozen bands; v0.1: meta records coordinate_frame
GRID_X = 16
GRID_Y = 12
HALF_SECONDS = 2700
MAX_SUBS = 5
MAX_CHAIN_STEPS = 40
CENTRE = (PITCH_X_DM // 2, PITCH_Y_DM // 2)
PENALTY_SPOT = (PITCH_X_DM - 110, PITCH_Y_DM // 2)
GOAL_Y_LO = 304
GOAL_Y_HI = 376
# Added time per period is drawn uniformly from these ranges (seconds) on the STOPPAGE stream.
STOPPAGE_RANGES: tuple[tuple[int, int], tuple[int, int]] = ((60, 240), (120, 420))

Zone = tuple[int, int]

# Shot appetite by attacking zone column (per-mille weights against pass and carry).
_SHOT_WEIGHT_BY_X: tuple[int, ...] = (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 6, 19, 54, 135, 190)
# Base scoring chance by attacking zone column, central lanes, in ppm.
_XG_BY_X: tuple[int, ...] = (
    5_000, 5_000, 5_000, 5_000, 5_000, 5_000, 5_000, 5_000, 8_000, 12_000,
    15_000, 20_000, 30_000, 45_000, 90_000, 180_000,
)
_PASS_DX: tuple[int, ...] = (-2, -1, 0, 1, 2, 3, 4)
_PASS_DX_BASE: tuple[int, ...] = (60, 110, 200, 260, 200, 110, 60)
_PASS_DY: tuple[int, ...] = (-3, -2, -1, 0, 1, 2, 3)
_PASS_DY_BASE: tuple[int, ...] = (30, 80, 200, 380, 200, 80, 30)
_POSITIONS: tuple[str, ...] = ("GK", "DF", "MF", "FW")

# Calibrated constants (synth-v0.2). Each was moved by hand on calibration seeds 1 to 200 against
# the realism bands frozen in bands.json before any of them changed; held-out results are on
# seeds 1001 to 1200. DATASET_CARD.md section 4 records the v0.1 value of each one.
FOUL_BASE_PPM = 480_000  # chance a failed-tackle duel is a foul, before discipline
YELLOW_BASE_PPM = 95_000  # chance a foul draws a yellow, before discipline
YELLOW_PER_POINT_PPM = 2_200  # added per point of (100 - discipline)
STRAIGHT_RED_BASE_PPM = 1_500  # chance a foul draws a straight red, before discipline
STRAIGHT_RED_PER_POINT_PPM = 25
SAVED_SHARE_PCT = 27  # of non-goal shots: saved / off target / blocked, in percent
OFF_TARGET_SHARE_PCT = 46
BLOCKED_SHARE_PCT = 27
HOME_XG_PERMILLE = 1050  # home advantage on open-play and set-piece scoring chance
AWAY_XG_PERMILLE = 860
HOME_PASS_BONUS_PPM = 15_000  # home advantage on pass completion
PASSING_ATTR_PPM = 6_000  # pass completion per point of passing above 60
CLEARED_CORNER_MIN_X = 12  # zone column from which a failed pass can be cleared behind (16 = never)
CLEARED_CORNER_PPM = 100_000  # chance such a failed pass is cleared behind for a corner
BOOKED_YELLOW_PERMILLE = 300  # a booked player's foul draws a second yellow at this share of the usual rate
PASS_BASE_PPM = 940_000  # pass completion before distance, skill, pressure and zone
PASS_DISTANCE_PPM = 52_000  # completion lost per zone of pass distance
SHORT_GAME_PPM_PER_POINT = 320  # completion per point of (500 - directness): patient sides are drilled to keep the ball
SHOT_APPETITE_PERMILLE = 1350  # scales the shot weight against pass and carry
PASS_ACTION_WEIGHT = 800  # weight of a pass against shot and carry for the player on the ball
SECOND_HALF_XG_PER_MINUTE = 12  # per-mille scoring chance added per minute played after 45 (fatigue)


def _centrality(zy: int) -> int:
    """Per-mille multiplier: central lanes shoot and score more."""
    d = min(abs(zy - 5), abs(zy - 6))
    return (1000, 1000, 650, 350, 150, 80)[min(d, 5)]


def _role_weights(zx: int) -> tuple[int, int, int, int]:
    """Who is likely on the ball in an attacking zone column: (GK, DF, MF, FW)."""
    if zx <= 1:
        return (60, 600, 250, 20)
    if zx <= 3:
        return (0, 600, 300, 40)
    if zx <= 9:
        return (0, 250, 550, 200)
    return (0, 80, 350, 570)


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def zone_of(x_dm: int, y_dm: int) -> Zone:
    return (_clamp(x_dm * GRID_X // (PITCH_X_DM + 1), 0, GRID_X - 1), _clamp(y_dm * GRID_Y // (PITCH_Y_DM + 1), 0, GRID_Y - 1))


def mirror_zone(zone: Zone) -> Zone:
    return (GRID_X - 1 - zone[0], GRID_Y - 1 - zone[1])


def zone_point(stream: Stream, zone: Zone) -> tuple[int, int]:
    """A uniformly drawn decimetre point inside a zone."""
    zx, zy = zone
    x0, x1 = zx * PITCH_X_DM // GRID_X, (zx + 1) * PITCH_X_DM // GRID_X
    y0, y1 = zy * PITCH_Y_DM // GRID_Y, (zy + 1) * PITCH_Y_DM // GRID_Y
    return stream.between(x0, x1), stream.between(y0, y1)


@dataclass
class TeamState:
    club: Club
    on_pitch: list[Player]
    bench: list[Player]
    sub_plan: list[int] = field(default_factory=list)
    subs_used: int = 0
    yellows: dict[str, int] = field(default_factory=dict)
    sent_off: list[str] = field(default_factory=list)
    goals: int = 0
    momentum: int = 0

    @property
    def club_id(self) -> str:
        return self.club.club_id

    def keeper(self) -> Player:
        for p in self.on_pitch:
            if p.position == "GK":
                return p
        return self.on_pitch[0]


@dataclass
class Restart:
    """How the next chain starts. `reason` None means the same team keeps the ball."""

    team: TeamState
    zone: Zone
    kind: str | None
    reason: str | None
    point: tuple[int, int] | None = None


@dataclass
class MatchResult:
    """`applied` is the intervention ledger. It is empty for a plain match and is never part of
    the meta, the events or the manifest, so nothing an agent reads names a storyline."""

    meta: dict[str, Any]
    events: list[Event]
    applied: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Live:
    """Engine-side state and bookkeeping for one intervention: the first of two label code paths.

    Counters are kept at the moment things happen inside the engine. The referee re-derives the
    same quantities from the event log alone and the two must agree exactly.
    """

    spec: Intervention
    team: TeamState
    index: int
    active: bool = False
    counting: bool = False
    pending_red: bool = False
    awaiting_anchor: bool = False
    at_clock: int | None = None
    at_period: int | None = None
    anchor_event_id: str | None = None
    goals_for_after: int = 0
    goals_against_after: int = 0
    saves_after: int = 0
    stoppage_seconds: int = 0
    # Keyed by (period, minute): the clock minute repeats across periods (45 to 49 can occur in both).
    press_by_minute: dict[tuple[int, int], int] = field(default_factory=dict)
    opp_onball_by_minute: dict[tuple[int, int], int] = field(default_factory=dict)


class MatchSimulator:
    """Simulates one fixture of a league. Construct, then call `run()` once."""

    def __init__(self, league: League, match_index: int = 0, interventions: Sequence[Intervention] = ()) -> None:
        fixtures = fixture_list(len(league.clubs))
        if not 0 <= match_index < len(fixtures):
            raise ValueError(f"match_index must be in 0..{len(fixtures) - 1}")
        self.league = league
        self.seed = league.seed
        self.match_index = match_index
        self.match_key = match_index + 1
        self.matchday, home_id, away_id = fixtures[match_index]
        self.match_id = f"s{self.seed}-m{match_index}"
        self.home = self._team(league.club(home_id))
        self.away = self._team(league.club(away_id))
        self.events: list[Event] = []
        self.chain_id = 0
        self.clock = 0
        self.period = 1
        self.period_end = 0
        self.last_team: TeamState | None = None
        self._added = [0, 0]
        self._live: list[_Live] = [
            _Live(spec=iv, team=self.home if iv.side == "home" else self.away, index=i)
            for i, iv in enumerate(interventions)
        ]

    @staticmethod
    def _team(club: Club) -> TeamState:
        return TeamState(club=club, on_pitch=club.starting_eleven(), bench=club.bench())

    def _stream(self, stream_id: int, possession: int | None = None) -> Stream:
        return Stream(self.seed, self.match_key, stream_id, self.chain_id if possession is None else possession)

    def _opponent(self, team: TeamState) -> TeamState:
        return self.away if team is self.home else self.home

    # ----------------------------------------------------------------- emission

    def _emit(
        self,
        etype: str,
        team: TeamState,
        player: Player | None,
        point: tuple[int, int],
        *,
        end: tuple[int, int] | None = None,
        under_pressure: bool = False,
        outcome: str | None = None,
        qualifier: str | None = None,
        related: Player | None = None,
    ) -> None:
        self.events.append(
            make_event(
                match_id=self.match_id,
                seq=len(self.events),
                period=self.period,
                minute=self.clock // 60,
                second=self.clock % 60,
                type=etype,
                team=team.club_id,
                player=player.player_id if player else None,
                x_dm=_clamp(point[0], 0, PITCH_X_DM),
                y_dm=_clamp(point[1], 0, PITCH_Y_DM),
                end_x_dm=_clamp(end[0], 0, PITCH_X_DM) if end else None,
                end_y_dm=_clamp(end[1], 0, PITCH_Y_DM) if end else None,
                possession_chain_id=self.chain_id,
                under_pressure=under_pressure,
                outcome=outcome,
                qualifier=qualifier,
                related_player=related.player_id if related else None,
            )
        )
        if self._live:
            self._book(etype, team)

    def _book(self, etype: str, team: TeamState) -> None:
        """Intervention bookkeeping at emission time (path one of the labels)."""
        minute = (self.period, self.clock // 60)
        for lv in self._live:
            if lv.awaiting_anchor and etype != "sub":
                lv.anchor_event_id = self.events[-1].event_id
                lv.awaiting_anchor = False
            if lv.spec.kind != "tactical_shift":
                continue
            if etype == "pressure" and team is lv.team:
                lv.press_by_minute[minute] = lv.press_by_minute.get(minute, 0) + 1
            elif etype in ("pass", "carry", "shot") and team is not lv.team:
                lv.opp_onball_by_minute[minute] = lv.opp_onball_by_minute.get(minute, 0) + 1

    def _active(self, team: TeamState, kind: str) -> list[_Live]:
        return [lv for lv in self._live if lv.active and lv.team is team and lv.spec.kind == kind]

    def _pending_red(self, team: TeamState) -> _Live | None:
        for lv in self._live:
            if lv.pending_red and lv.team is team:
                return lv
        return None

    def _activate_due(self) -> None:
        """Switch on every intervention whose (period, minute) has come, at the start of a chain.

        The period is compared first, so a period-2 plan never fires in first-half added time.
        """
        for lv in self._live:
            if lv.active or (self.period, self.clock) < (lv.spec.period, lv.spec.minute * 60):
                continue
            lv.active = True
            lv.at_clock = self.clock
            lv.at_period = self.period
            kind = lv.spec.kind
            if kind == "red_card":
                lv.pending_red = True
            elif kind == "injury_stoppage":
                self._injure(lv)
            else:
                lv.counting = True
                if kind == "tactical_shift":
                    lv.awaiting_anchor = True

    def _injure(self, lv: _Live) -> None:
        """An outfield player is injured and replaced; play stops and the time is added on."""
        team = lv.team
        s = Stream(self.seed, self.match_key, StreamId.STORYLINE, possession=lv.index)
        outfield = [p for p in team.on_pitch if p.position != "GK"]
        if not outfield or not team.bench or team.subs_used >= MAX_SUBS:
            return
        off = outfield[s.below(len(outfield))]
        same = [p for p in team.bench if p.position == off.position]
        options = same or [p for p in team.bench if p.position != "GK"]
        if not options:
            return
        on = options[s.below(len(options))]
        team.on_pitch = [on if p.player_id == off.player_id else p for p in team.on_pitch]
        team.bench = [p for p in team.bench if p.player_id != on.player_id]
        team.subs_used += 1
        self._emit("sub", team, off, (PITCH_X_DM // 2, 0), outcome="on", qualifier="injury", related=on)
        lv.anchor_event_id = self.events[-1].event_id
        delay = lv.spec.magnitude
        lv.stoppage_seconds = delay
        self.clock += delay
        self.period_end += delay
        self._added[self.period - 1] += delay

    def _ledger(self) -> list[dict[str, Any]]:
        return [
            {
                "index": lv.index,
                "kind": lv.spec.kind,
                "side": lv.spec.side,
                "team": lv.team.club_id,
                "planned_minute": lv.spec.minute,
                "magnitude": lv.spec.magnitude,
                "activated": lv.active,
                "at_clock": lv.at_clock,
                "at_period": lv.at_period,
                "anchor_event_id": lv.anchor_event_id,
                "goals_for_after": lv.goals_for_after,
                "goals_against_after": lv.goals_against_after,
                "saves_after": lv.saves_after,
                "stoppage_seconds": lv.stoppage_seconds,
                "press_by_minute": dict(sorted(lv.press_by_minute.items())),
                "opp_onball_by_minute": dict(sorted(lv.opp_onball_by_minute.items())),
            }
            for lv in self._live
        ]

    # ----------------------------------------------------------------- tactics

    def _effective_style(self, team: TeamState) -> Style:
        """Club style plus score effects: chase the game when behind late, sit deeper when ahead."""
        base = team.club.style
        press, direct, width = base.press_height, base.directness, base.width
        diff = team.goals - self._opponent(team).goals
        minute = self.clock // 60
        if diff < 0 and minute >= 60:
            press += 150
            direct += 150
        elif diff > 0 and minute >= 70:
            press -= 150
            direct -= 100
        for lv in self._active(team, "tactical_shift"):
            press += lv.spec.magnitude
        short = 11 - len(team.on_pitch)
        press -= 100 * short
        return Style(_clamp(press, 50, 950), _clamp(direct, 50, 950), _clamp(width, 50, 950))

    @staticmethod
    def _momentum_bonus(team: TeamState) -> int:
        """Self-exciting term: recent shots and goals lift the next actions. Decays per chain."""
        return team.momentum

    def _press_ppm(self, defending: TeamState, zone: Zone) -> int:
        press = self._effective_style(defending).press_height
        per = 250 if zone[0] < GRID_X // 2 else 120
        return 80_000 + press * per + self._momentum_bonus(defending) * 40

    def _choose_player(self, stream: Stream, team: TeamState, zone: Zone, exclude: Player | None = None) -> Player:
        pool = [p for p in team.on_pitch if exclude is None or p.player_id != exclude.player_id]
        if not pool:
            pool = list(team.on_pitch)
        weights = _role_weights(zone[0])
        present = [w if any(p.position == pos for p in pool) else 0 for pos, w in zip(_POSITIONS, weights)]
        if sum(present) == 0:
            return pool[stream.below(len(pool))]
        position = _POSITIONS[stream.weighted(present)]
        candidates = [p for p in pool if p.position == position]
        return candidates[stream.below(len(candidates))]

    def _pass_target(self, stream: Stream, team: TeamState, zone: Zone, pressed: bool) -> Zone:
        style = self._effective_style(team)
        dx_w = []
        for dx, w in zip(_PASS_DX, _PASS_DX_BASE):
            if dx >= 2:
                w = w * style.directness // 500
            elif dx <= 0:
                w = w * (1000 - style.directness) // 500
            if pressed and dx < 0:
                w *= 2
            dx_w.append(max(w, 1))
        dy_w = []
        for dy, w in zip(_PASS_DY, _PASS_DY_BASE):
            if abs(dy) >= 2:
                w = w * style.width // 500
            dy_w.append(max(w, 1))
        dx = _PASS_DX[stream.weighted(dx_w)]
        dy = _PASS_DY[stream.weighted(dy_w)]
        return (_clamp(zone[0] + dx, 0, GRID_X - 1), _clamp(zone[1] + dy, 0, GRID_Y - 1))

    def _action_weights(self, team: TeamState, carrier: Player, zone: Zone) -> tuple[int, int, int]:
        """(shot, pass, carry) weights for the next on-ball action."""
        style = self._effective_style(team)
        shot = _SHOT_WEIGHT_BY_X[zone[0]] * _centrality(zone[1]) * SHOT_APPETITE_PERMILLE // 1_000_000
        shot = shot * (1000 + self._momentum_bonus(team) // 2) // 1000
        if team.goals < self._opponent(team).goals and self.clock >= 75 * 60:
            shot = shot * 13 // 10
        if self._live and self._active(self._opponent(team), "late_collapse"):
            shot = shot * 3 // 2
        pas = PASS_ACTION_WEIGHT
        carry = max(60, 250 + carrier.pace * 3 - style.directness // 4)
        if carrier.position == "GK":
            shot, carry = 0, 10
        return (shot, pas, carry)

    # ----------------------------------------------------------------- discipline

    def _foul(
        self, offender_team: TeamState, offender: Player, point: tuple[int, int], forced_red: _Live | None = None
    ) -> None:
        """Emit the foul and any card; a red or second yellow removes the player."""
        self._emit("foul", offender_team, offender, point, outcome="committed")
        disc = self._stream(StreamId.DISCIPLINE)
        if forced_red is not None or disc.chance_ppm(
            STRAIGHT_RED_BASE_PPM + (100 - offender.discipline) * STRAIGHT_RED_PER_POINT_PPM
        ):
            card = "red"
        elif disc.chance_ppm(
            (YELLOW_BASE_PPM + (100 - offender.discipline) * YELLOW_PER_POINT_PPM)
            * (BOOKED_YELLOW_PERMILLE if offender_team.yellows.get(offender.player_id, 0) else 1000)
            // 1000
        ):
            card = "second_yellow" if offender_team.yellows.get(offender.player_id, 0) >= 1 else "yellow"
        else:
            return
        self._emit("card", offender_team, offender, point, outcome=card)
        if forced_red is not None:
            forced_red.pending_red = False
            forced_red.anchor_event_id = self.events[-1].event_id
            forced_red.at_clock = self.clock
            forced_red.at_period = self.period
            forced_red.counting = True
        self.clock += disc.between(15, 40)
        if card == "yellow":
            offender_team.yellows[offender.player_id] = offender_team.yellows.get(offender.player_id, 0) + 1
            return
        offender_team.sent_off.append(offender.player_id)
        offender_team.on_pitch = [p for p in offender_team.on_pitch if p.player_id != offender.player_id]

    # ----------------------------------------------------------------- substitutions

    def _plan_subs(self) -> None:
        for index, team in enumerate((self.home, self.away)):
            s = Stream(self.seed, self.match_key, StreamId.SUBS, possession=index)
            count = s.between(3, MAX_SUBS)
            team.sub_plan = sorted(s.between(55 * 60, 88 * 60) for _ in range(count))

    def _due_subs(self) -> None:
        for index, team in enumerate((self.home, self.away)):
            while team.sub_plan and self.clock >= team.sub_plan[0] and team.subs_used < MAX_SUBS:
                team.sub_plan.pop(0)
                s = Stream(self.seed, self.match_key, StreamId.PERSONNEL, possession=index, start=team.subs_used * 8)
                outfield = [p for p in team.on_pitch if p.position != "GK"]
                if not outfield or not team.bench:
                    continue
                off = outfield[s.weighted([110 - p.stamina for p in outfield])]
                same = [p for p in team.bench if p.position == off.position]
                options = same or [p for p in team.bench if p.position != "GK"]
                if not options:
                    continue
                on = options[s.below(len(options))]
                team.on_pitch = [on if p.player_id == off.player_id else p for p in team.on_pitch]
                team.bench = [p for p in team.bench if p.player_id != on.player_id]
                team.subs_used += 1
                self._emit("sub", team, off, (PITCH_X_DM // 2, 0), outcome="on", related=on)

    # ----------------------------------------------------------------- chains

    def _start_chain(self, restart: Restart, sp: Stream) -> tuple[Player, Zone, tuple[int, int], bool]:
        """Open a chain: possession change marker, then the set piece if any. Returns carrier state.

        `sp` is the chain's spatial stream, shared with the rest of the chain so no draw is reused.
        """
        team = restart.team
        team.momentum = team.momentum * 900 // 1000
        self._opponent(team).momentum = self._opponent(team).momentum * 900 // 1000
        act = self._stream(StreamId.CHAIN)
        point = restart.point or zone_point(sp, restart.zone)
        if restart.reason is not None and team is not self.last_team:
            self._emit("possession_change", team, None, point, outcome=restart.reason)
        self.last_team = team
        if restart.kind == "kick_off":
            carrier = self._choose_player(act, team, (7, 6))
        elif restart.kind == "goal_kick":
            carrier = team.keeper()
        elif restart.kind == "penalty":
            carrier = max(team.on_pitch, key=lambda p: (p.finishing, p.player_id))
        else:
            carrier = self._choose_player(act, team, restart.zone)
        if restart.kind is not None:
            self._emit("set_piece", team, carrier, point, outcome="taken", qualifier=restart.kind)
        return carrier, restart.zone, point, restart.kind == "corner"

    def _shot(
        self,
        team: TeamState,
        shooter: Player,
        zone: Zone,
        point: tuple[int, int],
        pressed: bool,
        qualifier: str,
    ) -> Restart:
        opp = self._opponent(team)
        s = self._stream(StreamId.SHOT)
        if qualifier == "penalty":
            xg = 760_000
        else:
            xg = _XG_BY_X[zone[0]] * _centrality(zone[1]) // 1000
            xg += (shooter.finishing - 60) * 600
            xg -= (opp.keeper().keeping - 70) * 600
            if pressed:
                xg = xg * 7 // 10
            if qualifier == "header":
                xg = xg * 8 // 10
            xg = xg * (HOME_XG_PERMILLE if team is self.home else AWAY_XG_PERMILLE) // 1000
            late = self.clock // 60 - 45
            if late > 0:
                xg = xg * (1000 + SECOND_HALF_XG_PER_MINUTE * late) // 1000
        if self._live and qualifier != "penalty":
            for lv in self._active(opp, "late_collapse"):
                xg = xg * lv.spec.magnitude // 1000
        xg = _clamp(xg, 4_000, 900_000)
        saved_bonus = 0
        if self._live:
            for lv in self._active(opp, "goalkeeper_heroics"):
                kept = xg * lv.spec.magnitude // 1000
                saved_bonus += xg - kept
                xg = kept
        rest = PPM - xg - saved_bonus
        outcome = ("goal", "saved", "off_target", "blocked")[
            s.weighted((
                xg,
                rest * SAVED_SHARE_PCT // 100 + saved_bonus,
                rest * OFF_TARGET_SHARE_PCT // 100,
                rest * BLOCKED_SHARE_PCT // 100,
            ))
        ]
        if outcome in ("goal", "saved"):
            end = (PITCH_X_DM, s.between(GOAL_Y_LO, GOAL_Y_HI))
        elif outcome == "off_target":
            wide_low = s.chance_ppm(500_000)
            end = (PITCH_X_DM, s.between(220, GOAL_Y_LO - 1) if wide_low else s.between(GOAL_Y_HI + 1, 460))
        else:
            end = (_clamp(point[0] + s.between(10, 40), 0, PITCH_X_DM), point[1])
        self._emit("shot", team, shooter, point, end=end, under_pressure=pressed, outcome=outcome, qualifier=qualifier)
        if self._live:
            self._book_shot(team, outcome)
        team.momentum += 120
        self.clock += s.between(1, 3)
        if outcome == "goal":
            team.goals += 1
            team.momentum += 250
            self.clock += s.between(50, 80)
            return Restart(opp, (7, 6), "kick_off", "goal", CENTRE)
        corner_point = (PITCH_X_DM, PITCH_Y_DM if s.chance_ppm(500_000) else 0)
        if outcome == "saved":
            if s.chance_ppm(350_000):
                self.clock += s.between(20, 35)
                return Restart(team, zone_of(*corner_point), "corner", None, corner_point)
            return Restart(opp, (0, s.between(4, 7)), None, "shot_end")
        if outcome == "off_target":
            self.clock += s.between(15, 30)
            return Restart(opp, (0, s.between(5, 6)), "goal_kick", "shot_end")
        if s.chance_ppm(450_000):
            self.clock += s.between(20, 35)
            return Restart(team, zone_of(*corner_point), "corner", None, corner_point)
        return Restart(opp, mirror_zone(zone), None, "shot_end")

    def _book_shot(self, team: TeamState, outcome: str) -> None:
        shot_id = self.events[-1].event_id
        for lv in self._live:
            if not lv.counting:
                continue
            if outcome == "goal":
                if team is lv.team:
                    lv.goals_for_after += 1
                else:
                    lv.goals_against_after += 1
                    if lv.spec.kind == "late_collapse" and lv.anchor_event_id is None:
                        lv.anchor_event_id = shot_id
            elif outcome == "saved" and team is not lv.team:
                lv.saves_after += 1
                if lv.spec.kind == "goalkeeper_heroics" and lv.anchor_event_id is None:
                    lv.anchor_event_id = shot_id

    def _run_chain(self, restart: Restart) -> Restart | None:
        """Play one possession. Returns the next restart, or None when the period clock runs out."""
        team = restart.team
        opp = self._opponent(team)
        self.chain_id += 1
        sp = self._stream(StreamId.SPATIAL)
        carrier, zone, point, from_corner = self._start_chain(restart, sp)
        act = self._stream(StreamId.ACTION)
        timing = self._stream(StreamId.TIMING)
        pressure = self._stream(StreamId.PRESSURE)
        pressed = False

        if restart.kind == "penalty":
            return self._shot(team, carrier, (GRID_X - 1, 6), PENALTY_SPOT, False, "penalty")
        if restart.kind == "free_kick" and zone[0] >= 12 and _centrality(zone[1]) >= 650 and act.chance_ppm(300_000):
            return self._shot(team, carrier, zone, point, False, "free_kick")

        for step in range(MAX_CHAIN_STEPS):
            if self.clock >= self.period_end:
                return None
            pressed = False
            if step > 0 or restart.kind is None:
                red = self._pending_red(opp) if self._live else None
                if red is not None or pressure.chance_ppm(self._press_ppm(opp, zone)):
                    presser = self._choose_player(pressure, opp, mirror_zone(zone))
                    if red is not None and presser.position == "GK":
                        outfield = [p for p in opp.on_pitch if p.position != "GK"]
                        if outfield:
                            presser = outfield[pressure.below(len(outfield))]
                    mpoint = (PITCH_X_DM - point[0], PITCH_Y_DM - point[1])
                    self._emit("pressure", opp, presser, mpoint, outcome="applied")
                    pressed = True
                    if red is not None or pressure.chance_ppm(220_000 + (presser.tackling - carrier.pace) * 2_000):
                        in_box = zone[0] >= 14 and 3 <= zone[1] <= 8
                        foul_ppm = FOUL_BASE_PPM + (100 - presser.discipline) * 3_000
                        if in_box:
                            foul_ppm //= 6  # defenders are careful in their own box
                        if red is not None or pressure.chance_ppm(foul_ppm):
                            self._foul(opp, presser, mpoint, forced_red=red)
                            self.clock += timing.between(15, 40)
                            if in_box:
                                return Restart(team, zone, "penalty", None, PENALTY_SPOT)
                            return Restart(team, zone, "free_kick", None, point)
                        won = pressure.chance_ppm(450_000 + (presser.tackling - carrier.pace) * 5_000)
                        self._emit("tackle", opp, presser, mpoint, outcome="won" if won else "lost")
                        self.clock += 1
                        if won:
                            return Restart(opp, mirror_zone(zone), None, "tackle", mpoint)

            if from_corner and step == 0:
                choice = 1
            else:
                choice = act.weighted(self._action_weights(team, carrier, zone))
            if choice == 0:
                qualifier = "header" if from_corner and step == 1 else "open_play"
                return self._shot(team, carrier, zone, point, pressed, qualifier)
            if choice == 1:
                if from_corner and step == 0:
                    target = (act.between(13, 15), act.between(4, 7))
                else:
                    target = self._pass_target(act, team, zone, pressed)
                tpoint = zone_point(sp, target)
                dist = abs(target[0] - zone[0]) + abs(target[1] - zone[1])
                ok = PASS_BASE_PPM - dist * PASS_DISTANCE_PPM + (carrier.passing - 60) * PASSING_ATTR_PPM
                if team is self.home:
                    ok += HOME_PASS_BONUS_PPM
                ok += (500 - self._effective_style(team).directness) * SHORT_GAME_PPM_PER_POINT
                ok += self._momentum_bonus(team) * 60
                ok -= 40_000 * (11 - len(team.on_pitch))
                if pressed:
                    ok -= 180_000
                if target[0] >= 12:
                    ok -= 120_000
                ok = _clamp(ok, 150_000, 970_000)
                if act.chance_ppm(ok):
                    receiver = self._choose_player(act, team, target, exclude=carrier)
                    self._emit("pass", team, carrier, point, end=tpoint, under_pressure=pressed, outcome="complete", related=receiver)
                    self.clock += timing.between(2, 3) + dist // 2
                    carrier, zone, point = receiver, target, tpoint
                    continue
                if act.chance_ppm(300_000):
                    self._emit("pass", team, carrier, point, end=tpoint, under_pressure=pressed, outcome="out")
                    self.clock += timing.between(6, 20)
                    if target[0] == GRID_X - 1:
                        return Restart(opp, (0, act.between(5, 6)), "goal_kick", "out_of_play")
                    mz = mirror_zone(target)
                    touch = 0 if mz[1] < GRID_Y // 2 else PITCH_Y_DM
                    mx = PITCH_X_DM - tpoint[0]
                    return Restart(opp, zone_of(mx, touch), "throw_in", "out_of_play", (mx, touch))
                self._emit("pass", team, carrier, point, end=tpoint, under_pressure=pressed, outcome="incomplete")
                self.clock += timing.between(2, 3)
                if target[0] >= CLEARED_CORNER_MIN_X and act.chance_ppm(CLEARED_CORNER_PPM):
                    # The defender gets there first but can only clear it behind his own line.
                    corner_point = (PITCH_X_DM, PITCH_Y_DM if tpoint[1] >= PITCH_Y_DM // 2 else 0)
                    self.clock += timing.between(20, 35)
                    return Restart(team, zone_of(*corner_point), "corner", None, corner_point)
                return Restart(opp, mirror_zone(target), None, "interception", (PITCH_X_DM - tpoint[0], PITCH_Y_DM - tpoint[1]))
            # carry
            target = (_clamp(zone[0] + act.between(0, 2), 0, GRID_X - 1), _clamp(zone[1] + act.between(-1, 1), 0, GRID_Y - 1))
            tpoint = zone_point(sp, target)
            self._emit("carry", team, carrier, point, end=tpoint, under_pressure=pressed, outcome="complete")
            self.clock += timing.between(2, 5)
            zone, point = target, tpoint
        # Chain too long: the ball is cleared out of play for a throw-in to the opponent.
        mx = PITCH_X_DM - point[0]
        return Restart(opp, zone_of(mx, 0), "throw_in", "out_of_play", (mx, 0))

    # ----------------------------------------------------------------- match

    def run(self) -> MatchResult:
        if self.events:
            raise RuntimeError("a MatchSimulator runs once")
        stoppage = Stream(self.seed, self.match_key, StreamId.STOPPAGE)
        self._added = [stoppage.between(*STOPPAGE_RANGES[0]), stoppage.between(*STOPPAGE_RANGES[1])]
        self._plan_subs()
        kick_off_teams = (self.home, self.away)
        for period in (1, 2):
            self.period = period
            self.clock = (period - 1) * HALF_SECONDS
            self.period_end = period * HALF_SECONDS + self._added[period - 1]
            self.home.momentum = 0
            self.away.momentum = 0
            self.last_team = None
            restart: Restart | None = Restart(kick_off_teams[period - 1], (7, 6), "kick_off", "kick_off", CENTRE)
            while restart is not None and self.clock < self.period_end:
                if self._live:
                    self._activate_due()
                if period == 2:
                    self._due_subs()
                restart = self._run_chain(restart)
        return MatchResult(meta=self._meta(self._added), events=self.events, applied=self._ledger())

    def _meta(self, added: list[int]) -> dict[str, Any]:
        return {
            "generator": GENERATOR_VERSION,
            "match_id": self.match_id,
            "seed": self.seed,
            "match_index": self.match_index,
            "matchday": self.matchday,
            "home": self.home.club_id,
            "coordinate_frame": COORDINATE_FRAME,
            "away": self.away.club_id,
            "score": {"home": self.home.goals, "away": self.away.goals},
            "added_seconds": {"first_half": added[0], "second_half": added[1]},
            "sent_off": sorted(self.home.sent_off + self.away.sent_off),
            "subs_used": {"home": self.home.subs_used, "away": self.away.subs_used},
            "event_count": len(self.events),
        }


def simulate_match(league: League, match_index: int = 0, interventions: Sequence[Intervention] = ()) -> MatchResult:
    """Convenience wrapper: one fixture of `league`, deterministic in (league.seed, match_index, interventions)."""
    return MatchSimulator(league, match_index, interventions).run()

"""What-If Lab: K paired replays of a published match, with and without a change, from the scenario minute.

For one scenario (a change and the minute it starts), the lab:
1. replays the published match up to the scenario minute once (`replay.replay_to`);
2. forks K baseline replicates and K scenario replicates from that one paused state, replicate r of
   both arms on the same random keys (common random numbers, `replay` module docstring);
3. reads every finished replay with the analyst-facing code only: the score, xG from the frozen
   interpret model, shots, box entries (the recompute predicates the Fact Gate counts with) and PPDA
   (`agon.interpret.metrics.ppda`) over the play after the scenario minute;
4. reports win, draw and loss shares per arm with Wilson 95% intervals, the paired mean difference and
   its 95% interval for points, xG, shots, box entries and PPDA (with the unpaired half-width beside
   it), a cumulative xG timeline of both arms, K, and how many pre-scenario events differ (zero, by
   construction, and counted anyway).

Everything is a pure function of (seed, scenario, K): integer arithmetic, no clock reads, no floats,
so the same bytes come out on every OS. Wall time is measured by the CLI and printed to stderr only.

Labels: synthetic engine, paired replays, not a prediction about real football.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import agon.governance.recompute as recompute
from agon.interpret import metrics
from agon.interpret.features import xg_ppm
from agon.interpret.frame import MatchView
from agon.interpret.model import load_model
from agon.synth.engine import MatchResult
from agon.synth.interventions import Intervention
from agon.synth.world import League, build_world
from agon.whatif.replay import Change, ReplaySimulator, play_out, replay_to
from agon.whatif.stats import fixed, paired_delta, round_div, wilson_ppm

LABEL = "Synthetic engine, paired replays, not a prediction about real football."
POINTS = {"W": 3, "D": 1, "L": 0}
# Cumulative xG marks: (label, period, minute cut). A mark counts every event of earlier periods and
# the events of its period with engine minute below the cut; None means the whole period.
TIMELINE_MARKS: tuple[tuple[str, int, int | None], ...] = (
    *((str(m), 1, m) for m in range(5, 46, 5)),
    ("45+", 1, None),
    *((str(m), 2, m) for m in range(50, 91, 5)),
    ("90+", 2, None),
)


@dataclass(frozen=True)
class Scenario:
    """A named change and the (period, minute) it starts from; `side` is whose point of view is reported."""

    scenario_id: str
    side: str
    period: int
    minute: int
    change: Change = field(default_factory=Change)
    title: str = ""
    question: str = ""
    engine_note: str = ""

    def __post_init__(self) -> None:
        if self.side not in ("home", "away"):
            raise ValueError("side must be home or away")
        Intervention("tactical_shift", "home", self.period, self.minute, 100)  # validates period and minute

    @property
    def until(self) -> tuple[int, int]:
        return (self.period, self.minute * 60)

    def params(self) -> dict[str, Any]:
        c = self.change
        return {
            "side": self.side,
            "period": self.period,
            "minute": self.minute,
            "interventions": [iv.to_dict() for iv in c.interventions],
            "press_side": c.press_side,
            "press_shift": c.press_shift,
            "collapse_side": c.collapse_side,
            "collapse_magnitude": c.collapse_magnitude,
        }


@dataclass
class ArmReading:
    """What one finished replay contributes, from the scenario side's point of view."""

    result: str
    points: int
    xg_for: int
    xg_against: int
    shots_for: int
    shots_against: int
    box_for: int
    box_against: int
    ppda_for: int | None
    ppda_against: int | None
    timeline_for: list[int]
    timeline_against: list[int]
    triggered: bool = False


def _cumulative(xg_by_event: Sequence[tuple[int, int, int]]) -> list[int]:
    """Cumulative xG at each TIMELINE_MARKS mark from (period, minute, xg) shots in order."""
    out = []
    for _label, period, cut in TIMELINE_MARKS:
        out.append(sum(x for p, m, x in xg_by_event if p < period or (p == period and (cut is None or m < cut))))
    return out


def read_arms(result: MatchResult, start: int, model: Mapping[str, Any], triggered: bool = False) -> dict[str, ArmReading]:
    """Read one finished replay from both sides. `start` is the index of the first event after the scenario minute."""
    meta = result.meta
    home, away = meta["home"], meta["away"]
    shots: dict[str, list[tuple[int, int, int]]] = {home: [], away: []}
    box = {home: 0, away: 0}
    for e in result.events:
        if e.type == "shot":
            shots[e.team].append((e.period, e.minute, xg_ppm(e.to_dict(), home, model)))
    after = [e.to_dict() for e in result.events[start:]]
    for d in after:
        if recompute.EVENT_KINDS["box_entry"](d):
            box[d["team"]] += 1
    pp = metrics.ppda(MatchView.build(after, home, away, meta["match_id"]))
    out = {}
    for side, me, opp in (("home", home, away), ("away", away, home)):
        gf, ga = meta["score"][side], meta["score"]["away" if side == "home" else "home"]
        res = "W" if gf > ga else "D" if gf == ga else "L"
        out[side] = ArmReading(
            result=res,
            points=POINTS[res],
            xg_for=sum(x for _, _, x in shots[me]),
            xg_against=sum(x for _, _, x in shots[opp]),
            shots_for=len(shots[me]),
            shots_against=len(shots[opp]),
            box_for=box[me],
            box_against=box[opp],
            ppda_for=pp[me]["ppda_centi"],
            ppda_against=pp[opp]["ppda_centi"],
            timeline_for=_cumulative(shots[me]),
            timeline_against=_cumulative(shots[opp]),
            triggered=triggered,
        )
    return out


def _outcomes(readings: Sequence[ArmReading]) -> dict[str, Any]:
    n = len(readings)
    return {
        "win": wilson_ppm(sum(1 for r in readings if r.result == "W"), n),
        "draw": wilson_ppm(sum(1 for r in readings if r.result == "D"), n),
        "loss": wilson_ppm(sum(1 for r in readings if r.result == "L"), n),
    }


# metric key, unit, scale (every reported number is the quantity times scale), how much the per-replay
# reading is already scaled by (xG readings are ppm, PPDA readings centi), reading attribute per side
DELTA_METRICS: tuple[tuple[str, str, int, int, dict[str, str]], ...] = (
    ("points", "points", 1000, 1, {"for": "points"}),
    ("xg", "expected goals", 1_000_000, 1_000_000, {"for": "xg_for", "against": "xg_against"}),
    ("shots", "shots", 1000, 1, {"for": "shots_for", "against": "shots_against"}),
    ("box_entries", "box entries after the scenario minute", 1000, 1, {"for": "box_for", "against": "box_against"}),
    ("ppda", "PPDA after the scenario minute", 100, 100, {"for": "ppda_for", "against": "ppda_against"}),
)


def _deltas(base: Sequence[ArmReading], scen: Sequence[ArmReading]) -> list[dict[str, Any]]:
    """Paired differences per metric and side. A replay pair with an undefined PPDA (no defensive
    action after the scenario minute in either arm) is dropped from that metric and counted in `dropped`."""
    out = []
    for key, unit, scale, read_scale, sides in DELTA_METRICS:
        for side, attr in sides.items():
            pairs = [(getattr(b, attr), getattr(s, attr)) for b, s in zip(base, scen)]
            kept = [(b, s) for b, s in pairs if b is not None and s is not None]
            stats = paired_delta([b for b, _ in kept], [s for _, s in kept], scale // read_scale)
            out.append({"metric": key, "side": side, "unit": unit, "scale": scale,
                        "dropped": len(pairs) - len(kept), **stats})
    return out


def _timeline(readings: Sequence[ArmReading]) -> dict[str, list[int]]:
    n = len(readings)
    return {
        "for": [round_div(sum(r.timeline_for[i] for r in readings), n) for i in range(len(TIMELINE_MARKS))],
        "against": [round_div(sum(r.timeline_against[i] for r in readings), n) for i in range(len(TIMELINE_MARKS))],
    }


@dataclass
class Arm:
    """K readings of one arm from both sides, plus the pre-scenario check."""

    readings: dict[str, list[ArmReading]]
    prefix_events: int
    replays: int
    differing_events: int


class Lab:
    """Runs scenarios on one published match, caching the baseline arm per scenario minute."""

    def __init__(self, seed: int, k: int, match_index: int = 0, league: League | None = None) -> None:
        if k < 2:
            raise ValueError("K must be at least 2")
        self.seed, self.k, self.match_index = seed, k, match_index
        self.league = league or build_world(seed)
        self.model = load_model()
        published = ReplaySimulator(self.league, match_index).start()
        published.advance()
        self.published = published.result()
        self._paused: dict[tuple[int, int], ReplaySimulator] = {}
        self._baseline: dict[tuple[int, int], Arm] = {}

    def paused(self, until: tuple[int, int]) -> ReplaySimulator:
        if until not in self._paused:
            sim, over = replay_to(self.league, self.match_index, until)
            if over:
                raise ValueError(f"seed {self.seed}: the match ends before period {until[0]}, clock {until[1]} s")
            self._paused[until] = sim
        return self._paused[until]

    def run_arm(self, until: tuple[int, int], change: Change) -> Arm:
        """K replicates of one arm. Each replay's pre-scenario events are compared with the published match."""
        paused = self.paused(until)
        start = len(paused.events)
        published_prefix = [e.event_id for e in self.published.events[:start]]
        readings: dict[str, list[ArmReading]] = {"home": [], "away": []}
        differing = 0
        for r in range(1, self.k + 1):
            sim = paused.fork(change, r)
            res = play_out(sim)
            prefix = [e.event_id for e in res.events[:start]]
            differing += sum(1 for a, b in zip(prefix, published_prefix) if a != b) + abs(len(prefix) - start)
            for side, reading in read_arms(res, start, self.model, triggered=sim.collapse_at is not None).items():
                readings[side].append(reading)
        return Arm(readings=readings, prefix_events=start, replays=self.k, differing_events=differing)

    def baseline(self, until: tuple[int, int]) -> Arm:
        if until not in self._baseline:
            self._baseline[until] = self.run_arm(until, Change())
        return self._baseline[until]

    def run(self, scenario: Scenario) -> dict[str, Any]:
        base_arm = self.baseline(scenario.until)
        scen_arm = self.run_arm(scenario.until, scenario.change)
        base, scen = base_arm.readings[scenario.side], scen_arm.readings[scenario.side]
        meta = self.published.meta
        out = {
            "scenario_id": scenario.scenario_id,
            "title": scenario.title,
            "question": scenario.question,
            "engine_note": scenario.engine_note,
            "side": scenario.side,
            "club_id": meta[scenario.side],
            "params": scenario.params(),
            "k": self.k,
            "prefix": {
                "events_before_scenario": base_arm.prefix_events,
                "replays_checked": base_arm.replays + scen_arm.replays,
                "differing_events": base_arm.differing_events + scen_arm.differing_events,
            },
            "triggered": sum(1 for r in scen if r.triggered) if scenario.change.collapse_side else None,
            "outcomes": {"baseline": _outcomes(base), "scenario": _outcomes(scen)},
            "deltas": _deltas(base, scen),
            "xg_timeline": {
                "marks": [m[0] for m in TIMELINE_MARKS],
                "baseline": _timeline(base),
                "scenario": _timeline(scen),
            },
        }
        out["summary"] = summary_sentence(out, scenario, self.league.club(meta[scenario.side]).name)
        return out


def _delta(out: Mapping[str, Any], metric: str, side: str = "for") -> Mapping[str, Any]:
    return next(d for d in out["deltas"] if d["metric"] == metric and d["side"] == side)


def pct(ppm: int) -> int:
    return round_div(ppm, 10_000)


def summary_sentence(out: Mapping[str, Any], scenario: Scenario, club: str) -> str:
    """One plain sentence from the numbers above, every number computed here in integers."""
    b, s = out["outcomes"]["baseline"]["win"], out["outcomes"]["scenario"]["win"]
    pts = _delta(out, "points")
    return (
        f"In {out['k']} paired replays from {scenario.minute}:00, {club} won {pct(s['ppm'])}% with the change "
        f"and {pct(b['ppm'])}% without it; their points per match changed by {fixed(pts['delta_mean'], 1000, 2)} "
        f"(95% interval {fixed(pts['delta_lo'], 1000, 2)} to {fixed(pts['delta_hi'], 1000, 2)})."
    )

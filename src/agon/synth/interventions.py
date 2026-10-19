"""Storyline interventions: plain data the engine can apply to one match.

Why this lives in `agon.synth` and holds no logic: the engine must be able to apply a storyline
without importing the referee package. The referee (`agon.referee`) builds these records, runs the
engine, and derives ground-truth labels. Nothing here knows what a label is.

When an intervention starts: at the first possession chain that opens in `period` at a clock of
`minute` * 60 seconds or later. The clock is match time, so period 2 starts at 45:00. `period` is
explicit and must agree with `minute`: period 1 takes minutes 0 to 44 and period 2 takes 45 to 89.
Without it a plan for minute 48 could fire in first-half added time (48:17 of period 1), and a
minute such as 47 would name two different moments.

What the engine does with each kind (all from that first chain):
- red_card: the side's next defensive duel becomes a foul with a straight red card.
- tactical_shift: the side's press height rises by `magnitude` per-mille for the rest of the match.
- late_collapse: the opponent's scoring chance against this side is multiplied by
  `magnitude` per-mille, and the opponent shoots more often.
- goalkeeper_heroics: this side's keeper concedes only `magnitude` per-mille of the usual scoring
  chance; the chance taken away becomes a save.
- injury_stoppage: an outfield player of the side is injured and replaced (a `sub` event with
  qualifier `injury`); play stops for `magnitude` seconds, which are added to the period.

The engine records what it applied in `MatchResult.applied` (the ledger). The ledger never enters
the match meta, the event log or the manifest, so nothing an agent reads names a storyline.
"""

from __future__ import annotations

from dataclasses import dataclass

INTERVENTION_KINDS: tuple[str, ...] = (
    "red_card",
    "tactical_shift",
    "late_collapse",
    "goalkeeper_heroics",
    "injury_stoppage",
)
SIDES: tuple[str, ...] = ("home", "away")

HALF_MINUTES = 45

# Allowed magnitude per kind, inclusive. red_card takes no magnitude.
MAGNITUDE_RANGES: dict[str, tuple[int, int]] = {
    "red_card": (0, 0),
    "tactical_shift": (100, 600),
    "late_collapse": (1200, 4000),
    "goalkeeper_heroics": (100, 900),
    "injury_stoppage": (60, 420),
}


@dataclass(frozen=True)
class Intervention:
    kind: str
    side: str
    period: int
    minute: int
    magnitude: int = 0

    def __post_init__(self) -> None:
        if self.kind not in INTERVENTION_KINDS:
            raise ValueError(f"unknown intervention kind {self.kind!r}")
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}")
        if not 0 <= self.minute <= 89:
            raise ValueError("minute must be in 0..89")
        if self.period not in (1, 2):
            raise ValueError("period must be 1 or 2")
        expected = 1 if self.minute < HALF_MINUTES else 2
        if self.period != expected:
            raise ValueError(f"minute {self.minute} belongs to period {expected}, not {self.period}")
        lo, hi = MAGNITUDE_RANGES[self.kind]
        if not lo <= self.magnitude <= hi:
            raise ValueError(f"magnitude for {self.kind} must be in {lo}..{hi}")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "side": self.side,
            "period": self.period,
            "minute": self.minute,
            "magnitude": self.magnitude,
        }

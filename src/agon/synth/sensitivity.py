"""Metric M4: every style knob moves its metric in the stated direction.

Usage:
    python -m agon.synth.sensitivity
    python -m agon.synth.sensitivity --seeds 1-8

Method: for each knob, every club in the league is set to the low value, then to the high value,
on the SAME seeds (paired), with the other knobs left at each club's own value. The metric is
read from the event log. A check passes only when the high run moves the metric strictly in the
stated direction. Values are exact fractions, so the verdict is identical on every OS.

The knobs and their readers in engine.py:
- press_height: `_press_ppm` (pressure rate) via `_effective_style`.
- directness: `_pass_target` (forward pass weights), `_action_weights` (carry share) and the
  short-game completion bonus in `_run_chain`.
- width: `_pass_target` (lateral pass weights).

Exit 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Iterable, Sequence

from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.manifest import parse_seeds
from agon.synth.world import League, build_world

DEFAULT_SEEDS = "1-8"
LOW = 200
HIGH = 800
LONG_FORWARD_DM = 130  # a pass that gains 13 m or more towards goal (acting team's frame)


@dataclass(frozen=True)
class KnobCheck:
    knob: str
    metric: str
    direction: int  # +1: the metric must rise with the knob; -1: it must fall
    why: str


CHECKS: tuple[KnobCheck, ...] = (
    KnobCheck("press_height", "pressures_per_onball", +1, "a higher press presses more often"),
    KnobCheck("directness", "long_forward_pass_share", +1, "a direct side plays more long forward passes"),
    KnobCheck("directness", "pass_completion", -1, "a direct side completes fewer passes"),
    KnobCheck("width", "mean_lateral_pass_dm", +1, "a wide side moves the ball further across the pitch"),
)


def with_style(league: League, **knobs: int) -> League:
    clubs = tuple(replace(c, style=replace(c.style, **knobs)) for c in league.clubs)
    return replace(league, clubs=clubs)


def metrics(events: Iterable[dict]) -> dict[str, tuple[int, int]]:
    """(numerator, denominator) per metric, summed over one or more matches."""
    pressures = onball = passes = complete = long_forward = lateral = 0
    for ev in events:
        etype = ev["type"]
        if etype == "pressure":
            pressures += 1
        elif etype in ("pass", "carry", "shot"):
            onball += 1
        if etype == "pass":
            passes += 1
            complete += ev["outcome"] == "complete"
            if ev["end_x_dm"] - ev["x_dm"] >= LONG_FORWARD_DM:
                long_forward += 1
            lateral += abs(ev["end_y_dm"] - ev["y_dm"])
    return {
        "pressures_per_onball": (pressures, onball),
        "long_forward_pass_share": (long_forward, passes),
        "pass_completion": (complete, passes),
        "mean_lateral_pass_dm": (lateral, passes),
    }


def measure(seeds: Sequence[int], knob: str, value: int, metric: str, match_index: int = 0) -> Fraction:
    num = den = 0
    for seed in seeds:
        league = with_style(build_world(seed), **{knob: value})
        result = simulate_match(league, match_index)
        n, d = metrics(e.to_dict() for e in result.events)[metric]
        num += n
        den += d
    if den == 0:
        raise ValueError(f"no denominator for {metric}")
    return Fraction(num, den)


@dataclass(frozen=True)
class Row:
    check: KnobCheck
    low: Fraction
    high: Fraction

    @property
    def passed(self) -> bool:
        return (self.high > self.low) if self.check.direction > 0 else (self.high < self.low)


def run(seeds: Sequence[int], checks: Sequence[KnobCheck] = CHECKS) -> list[Row]:
    return [Row(c, measure(seeds, c.knob, LOW, c.metric), measure(seeds, c.knob, HIGH, c.metric)) for c in checks]


def _fmt(x: Fraction) -> str:
    scaled = x * 10_000
    q = (scaled.numerator * 2 + scaled.denominator) // (2 * scaled.denominator)
    return f"{q // 10_000}.{q % 10_000:04d}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.synth.sensitivity", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    seeds = parse_seeds(args.seeds)
    rows = run(seeds)
    print(f"style knob sensitivity M4, generator {GENERATOR_VERSION}, seeds {args.seeds}, knob {LOW} vs {HIGH}, paired")
    print(f"{'knob':13} {'metric':24} {'dir':>4} {'at low':>12} {'at high':>12}  status")
    for r in rows:
        sign = "up" if r.check.direction > 0 else "down"
        print(f"{r.check.knob:13} {r.check.metric:24} {sign:>4} {_fmt(r.low):>12} {_fmt(r.high):>12}  {'PASS' if r.passed else 'FAIL'}")
    passed = sum(r.passed for r in rows)
    print(f"M4 passed {passed} of {len(rows)} knob checks")
    print(f"wall time {int((time.perf_counter() - started) * 1000)} ms total", file=sys.stderr)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Evidence sweep: no period-2 storyline may activate in first-half added time.

Usage:
    python -m agon.referee.period_sweep --seeds 2001-2400

For every seed it plans the red-card and tactical-shift storylines (the two kinds whose minute can
be 45 or later), keeps the plans whose period is 2, runs each storyline match and checks:
- the label's period is 2, and the anchor event, when there is one, sits in period 2;
- the label derived from the engine ledger equals the label rebuilt from the event log.

It prints the counts, including the subset planned for minutes 45 to 52, where the old engine fired
in first-half added time. Exit 0 when nothing activated in period 1 and every label pair agreed,
1 otherwise. Wall time goes to stderr.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Sequence

from agon.referee.labels import labels_from_ledger, labels_from_log
from agon.referee.storylines import plan_storyline, run_storyline
from agon.synth.manifest import parse_seeds

DEFAULT_SEEDS = "2001-2400"
KINDS: tuple[str, ...] = ("red_card", "tactical_shift")
EARLY_SECOND_HALF = (45, 52)


def sweep(seeds: Sequence[int]) -> dict[str, int]:
    counts = {"plans": 0, "in_period_1": 0, "minute_45_52": 0, "minute_45_52_in_period_1": 0, "mismatch": 0}
    for seed in seeds:
        for kind in KINDS:
            if plan_storyline(seed, kind).period != 2:
                continue
            plan, result = run_storyline(seed, kind)
            events = [e.to_dict() for e in result.events]
            label = labels_from_ledger(seed, plan, result)
            if label != labels_from_log(seed, plan, result.meta, events):
                counts["mismatch"] += 1
            anchor = next((e for e in events if e["event_id"] == label["anchor_event_id"]), None)
            wrong = label["period"] == 1 or (anchor is not None and anchor["period"] == 1)
            counts["plans"] += 1
            counts["in_period_1"] += wrong
            if EARLY_SECOND_HALF[0] <= plan.minute <= EARLY_SECOND_HALF[1]:
                counts["minute_45_52"] += 1
                counts["minute_45_52_in_period_1"] += wrong
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.referee.period_sweep", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    c = sweep(parse_seeds(args.seeds))
    print(
        f"period-2 red_card and tactical_shift plans: {c['plans']}; activated in period 1: {c['in_period_1']}; "
        f"planned for minutes 45-52: {c['minute_45_52']}, of which in period 1: {c['minute_45_52_in_period_1']}; "
        f"label path mismatches: {c['mismatch']}"
    )
    sys.stderr.write(f"wall time {int((time.perf_counter() - started) * 1000)} ms total\n")
    return 0 if c["in_period_1"] == 0 and c["mismatch"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

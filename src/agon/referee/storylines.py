"""Plan and run storyline matches: one labelled intervention per (seed, kind).

Usage:
    python -m agon.referee.storylines --seeds 2001-2010 --print-hash
    python -m agon.referee.storylines --seeds 1,2,3 --out build/labels.ndjson

Each storyline is a deterministic function of (seed, kind): which side it hits, the minute it
starts and its size are drawn on the STORYLINE stream under a match key no real fixture uses, so
planning never shifts a draw inside a match. Spec seed set for storylines: 2001 and up.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Sequence

from agon.referee.labels import labels_from_ledger, labels_from_log
from agon.synth.engine import MatchResult, simulate_match
from agon.synth.interventions import INTERVENTION_KINDS, SIDES, Intervention
from agon.synth.manifest import parse_seeds
from agon.synth.rng import Stream, StreamId
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.synth.world import build_world

# Fixture keys are match_index + 1, so 1..30. Storyline planning uses its own key space.
STORYLINE_PLAN_KEY = 9_000
DEFAULT_SEEDS = "2001-2010"
DEFAULT_MATCH_INDEX = 0

MINUTE_RANGES: dict[str, tuple[int, int]] = {
    "red_card": (15, 70),
    "tactical_shift": (30, 60),
    "late_collapse": (70, 78),
    "goalkeeper_heroics": (1, 30),
    "injury_stoppage": (15, 40),
}
SIZE_RANGES: dict[str, tuple[int, int]] = {
    "red_card": (0, 0),
    "tactical_shift": (300, 450),
    "late_collapse": (3000, 4000),
    "goalkeeper_heroics": (300, 500),
    "injury_stoppage": (150, 300),
}


def plan_storyline(seed: int, kind: str) -> Intervention:
    """The one intervention for (seed, kind). Pure function of its inputs."""
    if kind not in INTERVENTION_KINDS:
        raise ValueError(f"unknown storyline kind {kind!r}")
    s = Stream(seed, STORYLINE_PLAN_KEY, StreamId.STORYLINE, possession=INTERVENTION_KINDS.index(kind))
    side = SIDES[s.below(len(SIDES))]
    minute = s.between(*MINUTE_RANGES[kind])
    magnitude = s.between(*SIZE_RANGES[kind])
    return Intervention(kind=kind, side=side, minute=minute, magnitude=magnitude)


def run_storyline(seed: int, kind: str, match_index: int = DEFAULT_MATCH_INDEX) -> tuple[Intervention, MatchResult]:
    plan = plan_storyline(seed, kind)
    return plan, simulate_match(build_world(seed), match_index, (plan,))


def storyline_labels(seeds: Sequence[int], kinds: Sequence[str] = INTERVENTION_KINDS) -> list[dict[str, Any]]:
    """Labels for every (seed, kind), each one checked by the second code path before it is returned."""
    out: list[dict[str, Any]] = []
    for seed in seeds:
        for kind in kinds:
            plan, result = run_storyline(seed, kind)
            one = labels_from_ledger(seed, plan, result)
            two = labels_from_log(seed, plan, result.meta, [e.to_dict() for e in result.events])
            if one != two:
                raise AssertionError(f"label paths disagree for seed {seed} {kind}: {one} vs {two}")
            out.append(one)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.referee.storylines", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    parser.add_argument("--print-hash", action="store_true", help="print the label set sha256 last")
    parser.add_argument("--out", help="write the labels as NDJSON here (binary, LF) instead of stdout")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    labels = storyline_labels(parse_seeds(args.seeds))
    body = b"".join(canonical_bytes(label) + b"\n" for label in labels)
    if args.out:
        parent = os.path.dirname(args.out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "wb") as fh:
            fh.write(body)
    else:
        sys.stdout.write(body.decode("ascii"))
    if args.print_hash:
        sys.stdout.write(f"labels {len(labels)} sha256 {sha256_hex(canonical_bytes(labels))}\n")
    sys.stderr.write(f"wall time {int((time.perf_counter() - started) * 1000)} ms total\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

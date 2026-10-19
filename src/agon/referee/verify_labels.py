"""Metric M3: re-derive every storyline label by a second code path; `--mutate` must make it fail.

Usage:
    python -m agon.referee.verify_labels --seeds 1,2,3
    python -m agon.referee.verify_labels --seeds 1,2,3 --mutate

For each seed and each storyline kind, the engine runs the match with that one intervention. Path
one builds the label from the engine's ledger; path two rebuilds it from the plan, the meta and
the event log alone. They must be identical.

`--mutate` corrupts every path-one label in one field (anchor event, clock, an effect count, the
turning-point flag, the team or the period, in rotation) before the comparison. Every corrupted label must be
rejected. That is the proof the comparison can fail.

Exit codes:
    plain run: 0 when every label matches, 1 on any mismatch.
    --mutate:  1 when every mutated label was rejected (the required failure),
               2 when any mutation went undetected (the gate has no teeth).
"""

from __future__ import annotations

import argparse
import copy
import sys
import time
from typing import Any, Sequence

from agon.referee.labels import labels_from_ledger, labels_from_log
from agon.referee.storylines import run_storyline
from agon.synth.interventions import INTERVENTION_KINDS
from agon.synth.manifest import parse_seeds
from agon.synth.schema import canonical_bytes, sha256_hex

DEFAULT_SEEDS = "1,2,3"
MUTATIONS: tuple[str, ...] = ("anchor_event_id", "at_clock", "effect", "turning_point", "team", "period")


def mutate(label: dict[str, Any], which: str, other_event_id: str) -> dict[str, Any]:
    """Return a copy of `label` with exactly one field corrupted."""
    out = copy.deepcopy(label)
    if which == "anchor_event_id":
        out["anchor_event_id"] = other_event_id if out["anchor_event_id"] != other_event_id else "f" * 16
    elif which == "at_clock":
        out["at_clock"] = (out["at_clock"] or 0) + 1
    elif which == "effect":
        key = sorted(out["effect"])[0]
        out["effect"][key] += 1
    elif which == "turning_point":
        out["turning_point"] = not out["turning_point"]
    elif which == "team":
        out["team"] = out["team"] + "x"
    elif which == "period":
        out["period"] = 1 if out["period"] != 1 else 2
    else:
        raise ValueError(which)
    return out


def verify(seeds: Sequence[int], do_mutate: bool) -> tuple[list[str], int, int, list[dict[str, Any]]]:
    """Return (report lines, labels checked, labels rejected, path-one labels)."""
    lines: list[str] = []
    checked = rejected = 0
    labels: list[dict[str, Any]] = []
    for seed in seeds:
        for kind in INTERVENTION_KINDS:
            plan, result = run_storyline(seed, kind)
            events = [e.to_dict() for e in result.events]
            one = labels_from_ledger(seed, plan, result)
            two = labels_from_log(seed, plan, result.meta, events)
            labels.append(one)
            which = ""
            if do_mutate:
                which = MUTATIONS[checked % len(MUTATIONS)]
                one = mutate(one, which, events[0]["event_id"])
            ok = one == two
            checked += 1
            rejected += 0 if ok else 1
            verdict = "MATCH" if ok else "MISMATCH"
            tag = f" mutated:{which}" if which else ""
            lines.append(
                f"seed {seed} {kind:18} {two['side']:4} period {two['period']} minute {two['minute']} anchor {two['anchor_event_id']} "
                f"turning_point {two['turning_point']} {verdict}{tag}"
            )
    return lines, checked, rejected, labels


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.referee.verify_labels", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    parser.add_argument("--mutate", action="store_true", help="corrupt every path-one label; the run must fail")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    lines, checked, rejected, labels = verify(parse_seeds(args.seeds), args.mutate)
    for line in lines:
        print(line)
    print(f"labels sha256 {sha256_hex(canonical_bytes(labels))}")
    elapsed = int((time.perf_counter() - started) * 1000)
    print(f"wall time {elapsed} ms total", file=sys.stderr)
    if args.mutate:
        if rejected == checked:
            print(f"M3 mutate: {rejected} of {checked} mutated labels rejected; verification FAILED as required")
            return 1
        print(f"M3 mutate: only {rejected} of {checked} mutated labels rejected; the gate has no teeth")
        return 2
    if rejected:
        print(f"M3: {rejected} of {checked} labels MISMATCH between the two code paths")
        return 1
    print(f"M3: {checked} of {checked} labels re-derived identically by the second code path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

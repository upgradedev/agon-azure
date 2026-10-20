"""python -m agon.whatif: reproduce any What-If scenario from its parameters, or the whole precomputed set.

One scenario from the fixed set (prints its canonical JSON, one line):
    python -m agon.whatif --seed 1 --scenario red_home_30 --k 200

Any scenario from parameters (the change starts at the first possession at or after MINUTE:00):
    python -m agon.whatif --seed 1 --side home --minute 30 --red-card --k 200
    python -m agon.whatif --seed 1 --side home --minute 60 --press-shift 300 --k 200
    python -m agon.whatif --seed 1 --side home --minute 60 --collapse 2000 --k 200
    python -m agon.whatif --seed 1 --side away --minute 60 --heroics 500 --k 200
    (--red-card, --press-shift, --collapse and --heroics combine, all for --side)

The precomputed set for the published seeds (what the determinism workflow regenerates):
    python -m agon.whatif --set --seeds 1,2,3 --k 200 --out src/agon/whatif/scenario_set --print-hash

Hash lines are ASCII, contain no paths or timings, and go to stdout; total wall time goes to stderr,
never a rate. Labels: synthetic engine, paired replays, not a prediction about real football.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Sequence

from agon.synth.interventions import Intervention
from agon.synth.manifest import parse_seeds
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.whatif.lab import Lab, Scenario
from agon.whatif.replay import Change
from agon.whatif.scenarios import DEFAULT_K, SCENARIO_IDS, command_for, scenario_set, whatif_payload


def file_name(seed: int) -> str:
    return f"whatif-{seed}.json"


def set_hash_lines(files: Sequence[tuple[str, bytes]], k: int) -> list[str]:
    """One line per file, then the scenario-set sha256: sha256 over the canonical list of {name, sha256}."""
    entries = [{"name": name, "sha256": sha256_hex(data)} for name, data in files]
    lines = [f"whatif file {e['name']} k {k} sha256 {e['sha256']}" for e in entries]
    lines.append(f"whatif set sha256 {sha256_hex(canonical_bytes(entries))}")
    return lines


def _custom(args: argparse.Namespace) -> Scenario:
    period = 1 if args.minute < 45 else 2
    ivs = []
    if args.red_card:
        ivs.append(Intervention("red_card", args.side, period, args.minute))
    if args.heroics:
        ivs.append(Intervention("goalkeeper_heroics", args.side, period, args.minute, args.heroics))
    change = Change(
        interventions=tuple(ivs),
        press_side=args.side if args.press_shift else None,
        press_shift=args.press_shift,
        collapse_side=args.side if args.collapse else None,
        collapse_magnitude=args.collapse,
    )
    if change == Change():
        raise SystemExit("name a change: --red-card, --press-shift, --collapse or --heroics")
    return Scenario("custom", args.side, period, args.minute, change, title="custom scenario")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.whatif", description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, help="published seed whose match is replayed")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help=f"paired replays per arm (default {DEFAULT_K})")
    parser.add_argument("--scenario", choices=SCENARIO_IDS, help="one scenario of the fixed set")
    parser.add_argument("--side", choices=("home", "away"), help="the side the custom change applies to")
    parser.add_argument("--minute", type=int, help="custom scenario start, engine minute 0..89")
    parser.add_argument("--red-card", action="store_true")
    parser.add_argument("--press-shift", type=int, default=0, help="press-height knob, per mille; negative presses less often")
    parser.add_argument("--collapse", type=int, default=0, help="late_collapse magnitude after the side's next goal (1200..4000)")
    parser.add_argument("--heroics", type=int, default=0, help="goalkeeper_heroics magnitude (100..900)")
    parser.add_argument("--set", action="store_true", help="the whole fixed set for --seeds")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--out", help="with --set: folder for whatif-<seed>.json; otherwise a file for the one scenario")
    parser.add_argument("--print-hash", action="store_true")
    parser.add_argument("--hash-out", help="with --set: also write the hash lines here (binary, LF)")
    args = parser.parse_args(argv)
    started = time.perf_counter()

    if args.set:
        files = []
        for seed in parse_seeds(args.seeds):
            files.append((file_name(seed), canonical_bytes(whatif_payload(seed, args.k)) + b"\n"))
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            for name, data in files:
                with open(os.path.join(args.out, name), "wb") as fh:
                    fh.write(data)
        text = ("\n".join(set_hash_lines(files, args.k)) + "\n").encode("ascii")
        if args.print_hash:
            sys.stdout.buffer.write(text)
            sys.stdout.buffer.flush()
        if args.hash_out:
            parent = os.path.dirname(args.hash_out)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(args.hash_out, "wb") as fh:
                fh.write(text)
        count = len(files) * len(SCENARIO_IDS)
    else:
        if args.seed is None:
            parser.error("--seed is required")
        lab = Lab(args.seed, args.k)
        if args.scenario:
            clubs = {c.club_id: c.name for c in lab.league.clubs}
            meta = lab.published.meta
            sc = next(s for s in scenario_set(clubs[meta["home"]], clubs[meta["away"]]) if s.scenario_id == args.scenario)
            out = lab.run(sc)
            out["command"] = command_for(args.seed, sc.scenario_id, args.k)
        else:
            if args.side is None or args.minute is None:
                parser.error("a custom scenario needs --side and --minute")
            out = lab.run(_custom(args))
        data = canonical_bytes(out) + b"\n"
        if args.out:
            with open(args.out, "wb") as fh:
                fh.write(data)
        else:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        count = 1
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    sys.stderr.write(f"wall time {elapsed_ms} ms total for {count} scenario(s)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

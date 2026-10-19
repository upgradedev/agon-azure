"""Generate seeded matches and print or write their hashes.

Usage:
    python -m agon.synth.manifest --seeds 1,2,3 --print-hash
    python -m agon.synth.manifest --seeds 1-3 --out build/synth --hash-out build/hash.txt

Why: the per-seed sha256 is the identity of a generated match. The cross-OS CI job compares these
hashes between ubuntu and windows, so the hash lines are ASCII, contain no paths, and are written
in binary with LF line endings. Wall time goes to stderr so stdout stays byte-comparable.

Per-seed sha256 = sha256 over the canonical JSON of {"world", "match", "events"} for that seed.
Combined sha256 = sha256 over the canonical JSON of [{"seed", "sha256"}, ...] sorted by seed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Sequence

from agon import __version__
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.synth.world import build_world

DEFAULT_SEEDS: tuple[int, ...] = (1, 2, 3)
DEFAULT_MATCH_INDEX = 0


@dataclass(frozen=True)
class SeedBundle:
    seed: int
    world: dict[str, Any]
    match: dict[str, Any]
    events: list[dict[str, Any]]

    def bundle_bytes(self) -> bytes:
        return canonical_bytes({"world": self.world, "match": self.match, "events": self.events})

    def sha256(self) -> str:
        return sha256_hex(self.bundle_bytes())

    def events_ndjson(self) -> bytes:
        return b"".join(canonical_bytes(e) + b"\n" for e in self.events)


def parse_seeds(text: str) -> list[int]:
    """Parse "1,2,3" or "1-3,7" into a sorted list of distinct non-negative seeds."""
    seeds: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if hi < lo:
                raise ValueError(f"empty seed range {part}")
            seeds.extend(range(lo, hi + 1))
        else:
            seeds.append(int(part))
    if not seeds:
        raise ValueError("no seeds given")
    if any(s < 0 for s in seeds):
        raise ValueError("seeds must be non-negative")
    return sorted(set(seeds))


def build_bundle(seed: int, match_index: int = DEFAULT_MATCH_INDEX) -> SeedBundle:
    league = build_world(seed)
    result = simulate_match(league, match_index)
    return SeedBundle(
        seed=seed,
        world=league.to_dict(),
        match=result.meta,
        events=[e.to_dict() for e in result.events],
    )


def combined_hash(per_seed: Sequence[tuple[int, str]]) -> str:
    rows = [{"seed": s, "sha256": h} for s, h in sorted(per_seed)]
    return sha256_hex(canonical_bytes(rows))


def hash_lines(bundles: Sequence[SeedBundle], match_index: int) -> list[str]:
    """ASCII lines that must be identical on every OS. No paths, no timings."""
    lines = [f"generator {GENERATOR_VERSION} package {__version__} match_index {match_index}"]
    pairs: list[tuple[int, str]] = []
    for b in bundles:
        digest = b.sha256()
        pairs.append((b.seed, digest))
        score = b.match["score"]
        lines.append(
            f"seed {b.seed} events {len(b.events)} score {score['home']}-{score['away']} sha256 {digest}"
        )
    lines.append(f"combined sha256 {combined_hash(pairs)}")
    return lines


def _write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write_outputs(out_dir: str, bundles: Sequence[SeedBundle], match_index: int) -> dict[str, Any]:
    """Write per-seed world.json, match.json, events.ndjson and a manifest.json. Binary, LF only."""
    files: list[dict[str, Any]] = []
    seeds: list[dict[str, Any]] = []
    for b in bundles:
        folder = f"seed-{b.seed:04d}"
        payloads = {
            f"{folder}/world.json": canonical_bytes(b.world) + b"\n",
            f"{folder}/match.json": canonical_bytes(b.match) + b"\n",
            f"{folder}/events.ndjson": b.events_ndjson(),
        }
        for rel, data in payloads.items():
            _write_bytes(os.path.join(out_dir, *rel.split("/")), data)
            files.append({"path": rel, "bytes": len(data), "sha256": sha256_hex(data)})
        seeds.append({"seed": b.seed, "sha256": b.sha256(), "events": len(b.events)})
    manifest = {
        "generator": GENERATOR_VERSION,
        "package_version": __version__,
        "match_index": match_index,
        "seeds": seeds,
        "combined_sha256": combined_hash([(s["seed"], s["sha256"]) for s in seeds]),
        "files": files,
    }
    _write_bytes(os.path.join(out_dir, "manifest.json"), canonical_bytes(manifest) + b"\n")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.synth.manifest", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=",".join(str(s) for s in DEFAULT_SEEDS), help="e.g. 1,2,3 or 1-3")
    parser.add_argument("--match-index", type=int, default=DEFAULT_MATCH_INDEX, help="fixture index 0..29")
    parser.add_argument("--print-hash", action="store_true", help="print per-seed and combined sha256 to stdout")
    parser.add_argument("--hash-out", help="also write the hash lines to this file (binary, LF)")
    parser.add_argument("--out", help="write NDJSON, JSON and manifest.json under this directory")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    seeds = parse_seeds(args.seeds)
    bundles = [build_bundle(s, args.match_index) for s in seeds]
    lines = hash_lines(bundles, args.match_index)
    if args.print_hash:
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
    if args.hash_out:
        _write_bytes(args.hash_out, ("\n".join(lines) + "\n").encode("ascii"))
    if args.out:
        write_outputs(args.out, bundles, args.match_index)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    sys.stderr.write(f"wall time {elapsed_ms} ms for {len(seeds)} seed(s)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

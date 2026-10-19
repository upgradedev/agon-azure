"""Write and validate the match files the web app reads (the web data contract).

Usage:
    python -m agon.webdata --seeds 1,2,3 --out web/data
    python -m agon.webdata --seeds 1 --max-minute 10 --out web/data-fixture

Why one module: the deploy step writes web/data/ and the committed development
fixture lives in web/data-fixture/. Both must have exactly the same shape, so
both are written by `write_web_data` and both are checked by `validate_manifest`.
The browser reads manifest.json, then each listed file, and the Verify page
recomputes every file's sha256 on the visitor's device.

Contract (also in web/data-fixture/README.txt):
    manifest.json = {"generator": str,
                     "files": [{"name": str, "sha256": str, "seed": int, "events": int}]}
    name    a bare file name next to manifest.json, "<seed>.ndjson"
    sha256  64 lowercase hex characters over the exact file bytes
    seed    the generator seed of that match
    events  the number of lines in the file, one canonical event JSON per line

The NDJSON carries events only. The home club is the team of the first event,
because the generator gives the first-half kick-off to the home club
(engine.MatchSimulator.run). The app needs it for `to_fixed_frame`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Sequence

from agon.synth.engine import GENERATOR_VERSION
from agon.synth.manifest import DEFAULT_MATCH_INDEX, build_bundle, parse_seeds
from agon.synth.schema import canonical_bytes, sha256_hex

MANIFEST_NAME = "manifest.json"
MANIFEST_KEYS = frozenset({"generator", "files"})
FILE_KEYS = frozenset({"name", "sha256", "seed", "events"})
# A bare file name: no folder, no "..", no scheme. The browser builds a URL from it.
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.ndjson$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def file_name(seed: int) -> str:
    return f"{seed}.ndjson"


def truncate_events(events: list[dict[str, Any]], max_minute: int | None) -> list[dict[str, Any]]:
    """Keep the opening `max_minute` minutes of the first half (minute < max_minute), or everything."""
    if max_minute is None:
        return list(events)
    return [e for e in events if e["period"] == 1 and e["minute"] < max_minute]


def ndjson_bytes(events: Sequence[dict[str, Any]]) -> bytes:
    """One canonical JSON event per line, LF endings, ASCII. Same encoding as the synth CLI."""
    return b"".join(canonical_bytes(e) + b"\n" for e in events)


def build_web_data(
    seeds: Sequence[int], max_minute: int | None = None, match_index: int = DEFAULT_MATCH_INDEX
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return (manifest, {file name: bytes}) without touching the disk."""
    files: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    for seed in sorted(set(seeds)):
        events = truncate_events(build_bundle(seed, match_index).events, max_minute)
        data = ndjson_bytes(events)
        name = file_name(seed)
        payloads[name] = data
        files.append({"name": name, "sha256": sha256_hex(data), "seed": seed, "events": len(events)})
    manifest = {"generator": GENERATOR_VERSION, "files": files}
    return manifest, payloads


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return canonical_bytes(manifest) + b"\n"


def write_web_data(
    out_dir: str, seeds: Sequence[int], max_minute: int | None = None, match_index: int = DEFAULT_MATCH_INDEX
) -> dict[str, Any]:
    """Write every NDJSON file and manifest.json under out_dir, binary, LF only."""
    manifest, payloads = build_web_data(seeds, max_minute, match_index)
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("refusing to write an invalid manifest: " + "; ".join(problems))
    os.makedirs(out_dir, exist_ok=True)
    for name, data in payloads.items():
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(out_dir, MANIFEST_NAME), "wb") as fh:
        fh.write(manifest_bytes(manifest))
    return manifest


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_manifest(manifest: Any) -> list[str]:
    """Problems with a manifest's shape; empty means it matches the contract."""
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    problems: list[str] = []
    missing = MANIFEST_KEYS - manifest.keys()
    extra = manifest.keys() - MANIFEST_KEYS
    if missing:
        problems.append(f"missing keys {sorted(missing)}")
    if extra:
        problems.append(f"unexpected keys {sorted(extra)}")
    if not isinstance(manifest.get("generator"), str) or not manifest.get("generator"):
        problems.append("generator must be a non-empty string")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        problems.append("files must be a non-empty list")
        return problems
    seen: set[str] = set()
    for i, entry in enumerate(files):
        where = f"files[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be an object")
            continue
        if entry.keys() != FILE_KEYS:
            problems.append(f"{where} keys must be exactly {sorted(FILE_KEYS)}")
            continue
        name = entry["name"]
        if not isinstance(name, str) or not FILE_NAME.match(name):
            problems.append(f"{where}.name must be a bare <seed>.ndjson file name")
        elif name in seen:
            problems.append(f"{where}.name {name} is listed twice")
        else:
            seen.add(name)
        if not isinstance(entry["sha256"], str) or not SHA256_HEX.match(entry["sha256"]):
            problems.append(f"{where}.sha256 must be 64 lowercase hex characters")
        if not _is_int(entry["seed"]) or entry["seed"] < 0:
            problems.append(f"{where}.seed must be a non-negative integer")
        if not _is_int(entry["events"]) or entry["events"] < 1:
            problems.append(f"{where}.events must be a positive integer")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webdata", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", required=True, help="e.g. 1,2,3 or 1-3")
    parser.add_argument("--out", required=True, help="directory for manifest.json and <seed>.ndjson")
    parser.add_argument("--max-minute", type=int, default=None, help="keep only the first-half minutes below this")
    parser.add_argument("--match-index", type=int, default=DEFAULT_MATCH_INDEX)
    args = parser.parse_args(argv)
    manifest = write_web_data(args.out, parse_seeds(args.seeds), args.max_minute, args.match_index)
    for entry in manifest["files"]:
        sys.stdout.write(f"{entry['name']} events {entry['events']} sha256 {entry['sha256']}\n")
    sys.stdout.write(f"generator {manifest['generator']} files {len(manifest['files'])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

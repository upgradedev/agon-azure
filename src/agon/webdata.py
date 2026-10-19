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
                     "files": [{"name": str, "sha256": str, "seed": int, "events": int,
                                "world": str, "world_sha256": str}]}
    name          a bare file name next to manifest.json, "<seed>.ndjson"
    sha256        64 lowercase hex characters over the exact file bytes
    seed          the generator seed of that match
    events        the number of lines in the file, one canonical event JSON per line
    world         "<seed>.world.json", the fictional names for that seed
    world_sha256  64 lowercase hex characters over the exact world file bytes

The NDJSON carries events only. The home club is the team of the first event,
because the generator gives the first-half kick-off to the home club
(engine.MatchSimulator.run). The app needs it for `to_fixed_frame`.

The world file carries the names the events refer to by id, so the page can say
"Pellmoor Harriers" and "Liro Ellow" instead of C1 and C6P10:
    {"generator": str, "seed": int, "match_id": str, "league": str,
     "home": club id, "away": club id,
     "clubs": [{"club_id", "name", "short", "kit_primary", "kit_primary_hex",
                "kit_secondary", "kit_secondary_hex",
                "players": [{"player_id", "name", "shirt", "position"}]}]}
It is a projection of agon.synth.world: no attributes and no style vectors,
only what a page shows. Every name in it is fictional and was screened by the
generator; validate_world checks the shape and tests/test_web_data.py runs the
Rights Guard name screen over every published name.
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
FILE_KEYS = frozenset({"name", "sha256", "seed", "events", "world", "world_sha256"})
# A bare file name: no folder, no "..", no scheme. The browser builds a URL from it.
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.ndjson$")
WORLD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.world\.json$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

WORLD_KEYS = frozenset({"generator", "seed", "match_id", "league", "home", "away", "clubs"})
CLUB_KEYS = frozenset({
    "club_id", "name", "short", "kit_primary", "kit_primary_hex", "kit_secondary", "kit_secondary_hex", "players",
})
PLAYER_KEYS = frozenset({"player_id", "name", "shirt", "position"})
CLUB_ID = re.compile(r"^C[0-9]{1,2}$")
PLAYER_ID = re.compile(r"^C[0-9]{1,2}P[0-9]{2}$")
SHORT_CODE = re.compile(r"^[A-Z]{3}$")
HEX_COLOUR = re.compile(r"^#[0-9a-f]{6}$")
POSITIONS = frozenset({"GK", "DF", "MF", "FW"})

# Display colour for each kit colour word in agon.synth.world.KIT_COLOURS. A test
# fails if the generator gains a colour with no entry here.
KIT_HEX: dict[str, str] = {
    "amber": "#e0a100",
    "teal": "#12808a",
    "claret": "#7b1e3a",
    "slate": "#5a6b7b",
    "emerald": "#1f8f55",
    "ochre": "#c47f17",
    "navy": "#1f2f5c",
    "coral": "#e5644e",
    "ivory": "#f4efdf",
    "plum": "#6d3a6b",
    "sky": "#5aa7de",
    "charcoal": "#33373d",
}


def file_name(seed: int) -> str:
    return f"{seed}.ndjson"


def world_file_name(seed: int) -> str:
    return f"{seed}.world.json"


def web_world(world: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    """Project the generator's world and match record onto what the page shows."""
    clubs = []
    for club in world["clubs"]:
        clubs.append({
            "club_id": club["club_id"],
            "name": club["name"],
            "short": club["short"],
            "kit_primary": club["kit_primary"],
            "kit_primary_hex": KIT_HEX[club["kit_primary"]],
            "kit_secondary": club["kit_secondary"],
            "kit_secondary_hex": KIT_HEX[club["kit_secondary"]],
            "players": [
                {"player_id": p["player_id"], "name": p["name"], "shirt": p["shirt"], "position": p["position"]}
                for p in club["players"]
            ],
        })
    return {
        "generator": match["generator"],
        "seed": match["seed"],
        "match_id": match["match_id"],
        "league": world["name"],
        "home": match["home"],
        "away": match["away"],
        "clubs": clubs,
    }


def world_bytes(world: dict[str, Any]) -> bytes:
    """Canonical JSON plus a final LF, like manifest.json."""
    return canonical_bytes(world) + b"\n"


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
        bundle = build_bundle(seed, match_index)
        events = truncate_events(bundle.events, max_minute)
        data = ndjson_bytes(events)
        name = file_name(seed)
        payloads[name] = data
        world = web_world(bundle.world, bundle.match)
        problems = validate_world(world)
        if problems:
            raise ValueError(f"seed {seed}: refusing an invalid world file: " + "; ".join(problems))
        world_data = world_bytes(world)
        world_name = world_file_name(seed)
        payloads[world_name] = world_data
        files.append({
            "name": name,
            "sha256": sha256_hex(data),
            "seed": seed,
            "events": len(events),
            "world": world_name,
            "world_sha256": sha256_hex(world_data),
        })
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
    seen_seeds: set[int] = set()
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
        seed = entry["seed"]
        if not _is_int(seed) or seed < 0:
            problems.append(f"{where}.seed must be a non-negative integer")
        else:
            if isinstance(name, str) and name != f"{seed}.ndjson":
                problems.append(f"{where}.name {name} does not match seed {seed}, expected {seed}.ndjson")
            if seed in seen_seeds:
                problems.append(f"{where}.seed {seed} is listed twice")
            else:
                seen_seeds.add(seed)
        if not _is_int(entry["events"]) or entry["events"] < 1:
            problems.append(f"{where}.events must be a positive integer")
        world = entry["world"]
        if not isinstance(world, str) or not WORLD_NAME.match(world):
            problems.append(f"{where}.world must be a bare <seed>.world.json file name")
        elif _is_int(seed) and world != world_file_name(seed):
            problems.append(f"{where}.world {world} does not match seed {seed}, expected {world_file_name(seed)}")
        if not isinstance(entry["world_sha256"], str) or not SHA256_HEX.match(entry["world_sha256"]):
            problems.append(f"{where}.world_sha256 must be 64 lowercase hex characters")
    return problems


def validate_world(world: Any) -> list[str]:
    """Problems with a world file's shape; empty means it matches the contract.

    The browser applies the same rules (AgonCore.validateWorld) before it shows
    a single name, and tests/js/world_cases.json holds both sides to one list.
    """
    if not isinstance(world, dict):
        return ["world must be a JSON object"]
    if set(world) != WORLD_KEYS:
        return ["world keys must be exactly " + ", ".join(sorted(WORLD_KEYS))]
    problems: list[str] = []
    for key in ("generator", "match_id", "league"):
        if not isinstance(world[key], str) or not world[key].strip():
            problems.append(f"world.{key} must be a non-empty string")
    if not _is_int(world["seed"]) or world["seed"] < 0:
        problems.append("world.seed must be a non-negative integer")
    clubs = world["clubs"]
    if not isinstance(clubs, list) or len(clubs) < 2:
        problems.append("world.clubs must list at least two clubs")
        return problems
    club_ids: set[str] = set()
    player_ids: set[str] = set()
    for i, club in enumerate(clubs):
        where = f"clubs[{i}]"
        if not isinstance(club, dict) or set(club) != CLUB_KEYS:
            problems.append(f"{where} keys must be exactly " + ", ".join(sorted(CLUB_KEYS)))
            continue
        cid = club["club_id"]
        if not isinstance(cid, str) or not CLUB_ID.match(cid):
            problems.append(f"{where}.club_id must look like C1")
            cid = ""
        elif cid in club_ids:
            problems.append(f"{where}.club_id {cid} is listed twice")
        else:
            club_ids.add(cid)
        if not isinstance(club["name"], str) or not club["name"].strip():
            problems.append(f"{where}.name must be a non-empty string")
        if not isinstance(club["short"], str) or not SHORT_CODE.match(club["short"]):
            problems.append(f"{where}.short must be three capital letters")
        for key in ("kit_primary", "kit_secondary"):
            if not isinstance(club[key], str) or not club[key]:
                problems.append(f"{where}.{key} must be a non-empty string")
            if not isinstance(club[key + "_hex"], str) or not HEX_COLOUR.match(club[key + "_hex"]):
                problems.append(f"{where}.{key}_hex must be #rrggbb in lowercase hex")
        players = club["players"]
        if not isinstance(players, list) or not players:
            problems.append(f"{where}.players must be a non-empty list")
            continue
        shirts: set[int] = set()
        for j, player in enumerate(players):
            pwhere = f"{where}.players[{j}]"
            if not isinstance(player, dict) or set(player) != PLAYER_KEYS:
                problems.append(f"{pwhere} keys must be exactly " + ", ".join(sorted(PLAYER_KEYS)))
                continue
            pid = player["player_id"]
            if not isinstance(pid, str) or not PLAYER_ID.match(pid) or not pid.startswith(cid + "P"):
                problems.append(f"{pwhere}.player_id must start with its club id and look like C1P01")
            elif pid in player_ids:
                problems.append(f"{pwhere}.player_id {pid} is listed twice")
            else:
                player_ids.add(pid)
            if not isinstance(player["name"], str) or not player["name"].strip():
                problems.append(f"{pwhere}.name must be a non-empty string")
            shirt = player["shirt"]
            if not _is_int(shirt) or not 1 <= shirt <= 99:
                problems.append(f"{pwhere}.shirt must be an integer from 1 to 99")
            elif shirt in shirts:
                problems.append(f"{pwhere}.shirt {shirt} is used twice in one club")
            else:
                shirts.add(shirt)
            if player["position"] not in POSITIONS:
                problems.append(f"{pwhere}.position must be one of DF, FW, GK, MF")
    for key in ("home", "away"):
        if not isinstance(world[key], str) or world[key] not in club_ids:
            problems.append(f"world.{key} must be one of the listed club ids")
    if world["home"] == world["away"]:
        problems.append("world.home and world.away must differ")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webdata", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", required=True, help="e.g. 1,2,3 or 1-3")
    parser.add_argument("--out", required=True, help="directory for manifest.json, <seed>.ndjson and <seed>.world.json")
    parser.add_argument("--max-minute", type=int, default=None, help="keep only the first-half minutes below this")
    parser.add_argument("--match-index", type=int, default=DEFAULT_MATCH_INDEX)
    args = parser.parse_args(argv)
    manifest = write_web_data(args.out, parse_seeds(args.seeds), args.max_minute, args.match_index)
    for entry in manifest["files"]:
        sys.stdout.write(f"{entry['name']} events {entry['events']} sha256 {entry['sha256']}\n")
        sys.stdout.write(f"{entry['world']} sha256 {entry['world_sha256']}\n")
    sys.stdout.write(f"generator {manifest['generator']} files {len(manifest['files'])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

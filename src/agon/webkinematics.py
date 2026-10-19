"""Write and validate the kinematics files the console and the cues read (the kinematics web contract).

Usage:
    python -m agon.webkinematics --data-dir web/data --out web/kinematics
    python -m agon.webkinematics --seeds 1,2,3 --out build/kinematics      # from freshly generated matches

At deploy, ci/stamp_web.py calls `write_kinematics_web` on the data the deploy just wrote (web/data)
and then `verify_kinematics_dir`, so every kinematics file is derived from exactly the event bytes
the site serves, and names their sha256.

Contract. One folder (web/kinematics/), every JSON file canonical (sorted keys, ASCII, integers
and strings only) plus a final LF. `README_TEXT` below is the plain-text description shipped as
README.txt beside them.

    manifest.json   {"schema", "generator", "model", "files": [{"name", "seed", "sha256",
                     "events_sha256"}], "readme": {"name": "README.txt", "sha256"}}
    <seed>.kinematics.json   one per published seed, keys in `FILE_KEYS`:
        source, events_sha256    the event file it was derived from and that file's sha256 (the
                                 same value data/manifest.json lists for it)
        periods                  per period: start_s, end_s (match clock seconds), ball [x, y, ...]
                                 one pair per second, and players: player_id, team, start_s and
                                 xy [x, y, ...] one pair per second from start_s
        ball_flights             every pass and shot: event_id, speed_kmh_x10, flight_ms, dist_dm ...
        players                  per player: distance_dm, top_speed_kmh_x10, sprints,
                                 top_speed_triggers and distance_milestones (period plus t_s)
        checks                   cap_violations (metric M9, must be 0), anchors, anchor_misses,
                                 max_anchor_miss_dm
    README.txt

The triggers come from `agon.interpret.kinematics.triggers_from_segments` over the published 1 Hz
samples, one segment per period: a sprint is 25.2 km/h or more, a top-speed trigger 30 km/h or
more, a milestone every 2 km. At 1 Hz every speed is a 1-second average.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Mapping, Sequence

from agon.interpret.kinematics import (
    MILESTONE_DM,
    SPRINT_KMH_X10,
    TOP_SPEED_KMH_X10,
    TRACKING_NOTE,
    triggers_from_segments,
)
from agon.synth import kinematics as kin
from agon.synth.engine import GENERATOR_VERSION
from agon.synth.manifest import parse_seeds
from agon.synth.schema import FIXED_FRAME, canonical_bytes, sha256_hex
from agon.synth.world import build_world

KINEMATICS_DIR = "kinematics"
MANIFEST_NAME = "manifest.json"
README_NAME = "README.txt"
SCHEMA = "agon.webkinematics/1"
FILE_NAME = re.compile(r"^([0-9]+)\.kinematics\.json$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
MATCH_ID = re.compile(r"^s([0-9]+)-m([0-9]+)$")

FILE_KEYS = frozenset({
    "schema", "seed", "match_id", "home", "away", "source", "events_sha256", "model", "generator",
    "rate_hz", "frame", "units", "note", "caps", "periods", "ball_flights", "players", "checks",
})
PLAYER_KEYS = frozenset({
    "player_id", "team", "distance_dm", "top_speed_kmh_x10", "sprints", "top_speed_triggers", "distance_milestones",
})
FLIGHT_KEYS = frozenset({
    "event_id", "type", "outcome", "qualifier", "period", "t_s", "dist_dm", "gap_ms", "flight_ms",
    "speed_kmh_x10", "under_pressure",
})
CHECK_KEYS = frozenset({"cap_violations", "anchors", "anchor_misses", "max_anchor_miss_dm"})
UNITS = {
    "position": "decimetres, fixed frame: the home club attacks towards x = 1050 in both periods",
    "time": "match clock seconds; the clock repeats across periods, so a moment is (period, second)",
    "speed": "tenths of km/h",
    "distance": "decimetres",
    "flight": "milliseconds",
}

README_TEXT = """Agon synthetic kinematics: what these files are

These files are SYNTHETIC. The match engine produces events, not tracking. agon.synth.kinematics
derives positions from a finished event log with a simple motion model: each club keeps a 4-3-3
shape that follows the ball and the phase of play, the actor of every event is placed at the event
location at the event second (and the receiver of a completed pass at the pass end when the ball
arrives), and every player stays within a speed cap of at most 9.5 m/s (34.2 km/h, lower for slower
players) and an acceleration cap of 4.5 m/s^2. They are not measured positions of real players.

Files
  manifest.json            lists every kinematics file with its sha256, its seed and the sha256
                           of the event file it was derived from, plus this README's sha256
  <seed>.kinematics.json   one published match
  README.txt               this text

Units and frame
  positions   decimetres; x from 0 to 1050, y from 0 to 680, players may stand up to 10 m
              beyond a line; fixed frame home_attacks_high_x_both_periods (the home club attacks
              towards x = 1050 in both periods; the generator never swaps ends)
  time        match clock seconds; period 2 starts at 2700, and first-half added time also runs
              past 2700, so a moment is always (period, second)
  rate        1 sample per second (1 Hz)
  speed       tenths of km/h (252 is 25.2 km/h); at 1 Hz every speed is a 1-second average
  distance    decimetres (20000 is 2 km)

How to read a track
  periods[i].players[j].xy holds x0, y0, x1, y1, ... with pair k at second start_s + k of that
  period. periods[i].ball holds the ball the same way from periods[i].start_s. A player who came
  on as a substitute starts at his start_s; a player sent off or replaced simply stops.

How to join
  ball_flights[].event_id    the event_id in <seed>.ndjson (pass and shot events)
  players[].player_id        the player_id in <seed>.ndjson and in <seed>.world.json (names)
  events_sha256              the sha256 of <seed>.ndjson as data/manifest.json lists it

Triggers (agon.interpret.kinematics.triggers_from_segments, one segment per period)
  sprint             a run of consecutive 1-second steps at 25.2 km/h or more
  top speed trigger  a 1-second step at 30 km/h or more
  milestone          every 2 km of the player's distance, counted across both periods

Ball speed
  speed_kmh_x10 is the pass or shot distance over its flight time. The flight always ends before
  the next event; when the drawn speed would not get there in time, the flight is shortened and the
  published speed is the faster average that results.

Checks (metric M9)
  cap_violations     published steps over 9.7 m/s, changes of step over 4.8 m/s^2, positions
                     outside the box, ball speeds outside their bounds or flights longer than
                     their gap; must be 0 (the caps add worst-case decimetre rounding)
  anchor_misses      events whose actor is not at the event location at the event second; the
                     engine picks some actors at random, so a few consecutive actions of one player
                     are physically impossible, and the model never breaks a cap to reach them
"""


def player_triggers(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Distance, top speed, sprints and milestones per player from the published 1 Hz samples."""
    streams: dict[str, list[tuple[int, list[tuple[int, int, int]]]]] = {}
    team: dict[str, str] = {}
    for per in record["periods"]:
        for track in per["players"]:
            xy = track["xy"]
            samples = [((track["start_s"] + k) * 1000, xy[2 * k], xy[2 * k + 1]) for k in range(len(xy) // 2)]
            streams.setdefault(track["player_id"], []).append((per["period"], samples))
            team[track["player_id"]] = track["team"]
    out = []
    for pid in sorted(streams):
        r = triggers_from_segments(pid, streams[pid])
        out.append({
            "player_id": pid,
            "team": team[pid],
            "distance_dm": r["distance_dm"],
            "top_speed_kmh_x10": r["top_speed_kmh_x10"],
            "sprints": [{"period": s["segment"], "start_s": s["start_ms"] // 1000, "end_s": s["end_ms"] // 1000} for s in r["sprints"]],
            "top_speed_triggers": [{"period": t["segment"], "t_s": t["t_ms"] // 1000} for t in r["top_speed_triggers"]],
            "distance_milestones": [{"km": m["km"], "period": m["segment"], "t_s": m["t_ms"] // 1000} for m in r["distance_milestones"]],
        })
    return out


def kinematics_payload(seed: int, events: Sequence[Mapping[str, Any]], events_sha256: str, source: str) -> dict[str, Any]:
    """The web kinematics file for one match, from its event dicts (in seq order) and the seed's world."""
    if not events:
        raise ValueError(f"seed {seed}: no events to derive kinematics from")
    found = MATCH_ID.match(events[0]["match_id"])
    if not found or int(found.group(1)) != seed:
        raise ValueError(f"seed {seed}: events belong to {events[0]['match_id']}")
    match_index = int(found.group(2))
    league = build_world(seed)
    home = events[0]["team"]  # the home club takes the first-half kick-off (agon.webdata)
    teams = sorted({e["team"] for e in events} - {home})
    away = teams[0] if teams else next(c.club_id for c in league.clubs if c.club_id != home)
    record = kin.simulate_tracking(league, events, home, away, seed, match_index)
    problems = kin.check(record)
    misses = kin.anchor_misses(record, events)
    anchors = sum(1 for e in events if e["player"] and e["type"] not in ("sub", "possession_change"))
    return {
        "schema": SCHEMA,
        "seed": seed,
        "match_id": record["match_id"],
        "home": home,
        "away": away,
        "source": source,
        "events_sha256": events_sha256,
        "model": kin.KINEMATICS_VERSION,
        "generator": GENERATOR_VERSION,
        "rate_hz": kin.RATE_HZ,
        "frame": FIXED_FRAME,
        "units": dict(UNITS),
        "note": TRACKING_NOTE,
        "caps": {
            "player_vmax_mm_s": kin.PLAYER_VMAX_MM_S,
            "player_amax_mm_s2": kin.PLAYER_AMAX_MM_S2,
            "check_vmax_dm_s": kin.CHECK_VMAX_DM_S,
            "check_amax_dm_s2": kin.CHECK_AMAX_DM_S2,
            "pass_kmh_x10": list(kin.PASS_BOUNDS_KMH_X10),
            "shot_kmh_x10": list(kin.SHOT_BOUNDS_KMH_X10),
            "sprint_kmh_x10": SPRINT_KMH_X10,
            "top_speed_kmh_x10": TOP_SPEED_KMH_X10,
            "milestone_dm": MILESTONE_DM,
        },
        "periods": record["periods"],
        "ball_flights": record["ball_flights"],
        "players": player_triggers(record),
        "checks": {
            "cap_violations": len(problems),
            "anchors": anchors,
            "anchor_misses": len(misses),
            "max_anchor_miss_dm": max((m["miss_dm"] or 0 for m in misses), default=0),
        },
    }


def file_bytes(payload: Any) -> bytes:
    return canonical_bytes(payload) + b"\n"


def _events_from_ndjson(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in data.decode("ascii").splitlines() if line.strip()]


def build_from_data_dir(data_dir: str | os.PathLike[str]) -> tuple[dict[str, Any], dict[str, bytes]]:
    """(manifest, {name: bytes}) derived from the shipped web data: data/manifest.json and its NDJSON files."""
    root = os.fspath(data_dir)
    with open(os.path.join(root, "manifest.json"), "rb") as fh:
        data_manifest = json.loads(fh.read().decode("ascii"))
    sources = []
    for entry in data_manifest["files"]:
        with open(os.path.join(root, entry["name"]), "rb") as fh:
            raw = fh.read()
        if sha256_hex(raw) != entry["sha256"]:
            raise ValueError(f"{entry['name']}: sha256 differs from data/manifest.json")
        sources.append((int(entry["seed"]), entry["name"], raw))
    return _build(sources)


def build_from_seeds(seeds: Sequence[int]) -> tuple[dict[str, Any], dict[str, bytes]]:
    """(manifest, {name: bytes}) for freshly generated full matches (match index 0), as the deploy publishes them."""
    from agon.synth.manifest import build_bundle

    sources = []
    for seed in sorted(set(seeds)):
        bundle = build_bundle(seed)
        sources.append((seed, f"{seed}.ndjson", bundle.events_ndjson()))
    return _build(sources)


def _build(sources: Sequence[tuple[int, str, bytes]]) -> tuple[dict[str, Any], dict[str, bytes]]:
    payloads: dict[str, bytes] = {}
    files = []
    for seed, name, raw in sorted(sources):
        body = kinematics_payload(seed, _events_from_ndjson(raw), sha256_hex(raw), name)
        problems = validate_payload(body)
        if problems:
            raise ValueError(f"seed {seed}: refusing an invalid kinematics file: " + "; ".join(problems))
        data = file_bytes(body)
        fname = f"{seed}.kinematics.json"
        payloads[fname] = data
        files.append({"name": fname, "seed": seed, "sha256": sha256_hex(data), "events_sha256": body["events_sha256"]})
    readme = README_TEXT.encode("ascii")
    payloads[README_NAME] = readme
    manifest = {
        "schema": SCHEMA,
        "generator": GENERATOR_VERSION,
        "model": kin.KINEMATICS_VERSION,
        "files": files,
        "readme": {"name": README_NAME, "sha256": sha256_hex(readme)},
    }
    return manifest, payloads


def _write(out_dir: str | os.PathLike[str], manifest: Mapping[str, Any], payloads: Mapping[str, bytes]) -> dict[str, Any]:
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("refusing an invalid kinematics manifest: " + "; ".join(problems))
    os.makedirs(out_dir, exist_ok=True)
    for name, data in payloads.items():
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(out_dir, MANIFEST_NAME), "wb") as fh:
        fh.write(file_bytes(manifest))
    return dict(manifest)


def write_kinematics_web(out_dir: str | os.PathLike[str], data_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Write the folder from the shipped web data. Binary, LF only. Returns the manifest."""
    return _write(out_dir, *build_from_data_dir(data_dir))


# ----------------------------------------------------------------- validation


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_manifest(manifest: Any) -> list[str]:
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "generator", "model", "files", "readme"}:
        return ["manifest keys must be exactly files, generator, model, readme, schema"]
    problems: list[str] = []
    if manifest["schema"] != SCHEMA:
        problems.append(f"manifest.schema must be {SCHEMA}")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        return problems + ["manifest.files must be a non-empty list"]
    seeds: set[int] = set()
    for i, entry in enumerate(files):
        where = f"files[{i}]"
        if not isinstance(entry, dict) or set(entry) != {"name", "seed", "sha256", "events_sha256"}:
            problems.append(f"{where} keys must be exactly events_sha256, name, seed, sha256")
            continue
        found = FILE_NAME.match(str(entry["name"]))
        if not found or not _is_int(entry["seed"]) or int(found.group(1)) != entry["seed"]:
            problems.append(f"{where}.name must be <seed>.kinematics.json for its seed")
        elif entry["seed"] in seeds:
            problems.append(f"{where}.seed {entry['seed']} is listed twice")
        else:
            seeds.add(entry["seed"])
        for key in ("sha256", "events_sha256"):
            if not isinstance(entry[key], str) or not SHA256_HEX.match(entry[key]):
                problems.append(f"{where}.{key} must be 64 lowercase hex characters")
    readme = manifest["readme"]
    if not isinstance(readme, dict) or readme.get("name") != README_NAME or not SHA256_HEX.match(str(readme.get("sha256", ""))):
        problems.append("manifest.readme must name README.txt with its sha256")
    return problems


def _pairs(xy: Any, where: str) -> list[str]:
    if not isinstance(xy, list) or len(xy) % 2 or not all(_is_int(v) for v in xy):
        return [f"{where} must be a flat list of integer x, y pairs"]
    return []


def validate_payload(body: Any) -> list[str]:
    """Problems with one <seed>.kinematics.json; empty means it matches the contract."""
    if not isinstance(body, dict) or set(body) != FILE_KEYS:
        return ["kinematics file keys must be exactly " + ", ".join(sorted(FILE_KEYS))]
    problems: list[str] = []
    if body["schema"] != SCHEMA or body["frame"] != FIXED_FRAME or body["rate_hz"] != 1:
        problems.append("schema, frame and rate_hz must be the contract's")
    if not isinstance(body["events_sha256"], str) or not SHA256_HEX.match(body["events_sha256"]):
        problems.append("events_sha256 must be 64 lowercase hex characters")
    if "not measured" not in str(body["note"]):
        problems.append("note must say the positions are synthetic and not measured")
    try:
        for i, per in enumerate(body["periods"]):
            where = f"periods[{i}]"
            if set(per) != {"period", "start_s", "end_s", "ball", "players"}:
                problems.append(f"{where} keys must be exactly ball, end_s, period, players, start_s")
                continue
            seconds = per["end_s"] - per["start_s"] + 1
            problems += _pairs(per["ball"], f"{where}.ball")
            if len(per["ball"]) != 2 * seconds:
                problems.append(f"{where}.ball must hold one pair per second from start_s to end_s")
            for j, track in enumerate(per["players"]):
                twhere = f"{where}.players[{j}]"
                if set(track) != {"player_id", "team", "start_s", "xy"}:
                    problems.append(f"{twhere} keys must be exactly player_id, start_s, team, xy")
                    continue
                problems += _pairs(track["xy"], f"{twhere}.xy")
                if not per["start_s"] <= track["start_s"] <= per["end_s"] or track["start_s"] + len(track["xy"]) // 2 - 1 > per["end_s"]:
                    problems.append(f"{twhere} runs outside its period")
        for i, f in enumerate(body["ball_flights"]):
            if set(f) != FLIGHT_KEYS:
                problems.append(f"ball_flights[{i}] keys must be exactly " + ", ".join(sorted(FLIGHT_KEYS)))
                break
        for i, p in enumerate(body["players"]):
            if set(p) != PLAYER_KEYS:
                problems.append(f"players[{i}] keys must be exactly " + ", ".join(sorted(PLAYER_KEYS)))
                break
        if set(body["checks"]) != CHECK_KEYS or not all(_is_int(v) for v in body["checks"].values()):
            problems.append("checks keys must be exactly " + ", ".join(sorted(CHECK_KEYS)))
        elif body["checks"]["cap_violations"] != 0:
            problems.append(f"checks.cap_violations is {body['checks']['cap_violations']}; metric M9 requires 0")
    except (AttributeError, KeyError, TypeError) as exc:
        problems.append(f"malformed ({type(exc).__name__}: {exc})")
    return problems


def verify_kinematics_dir(kin_dir: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None) -> int:
    """Re-hash and shape-check a written folder; with data_dir, each source hash must match the data manifest.

    Unlisted files fail. Returns the number of files verified (kinematics files plus README).
    """
    root = os.fspath(kin_dir)
    path = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(path):
        raise ValueError(f"{path} does not exist")
    with open(path, "rb") as fh:
        manifest = json.loads(fh.read().decode("ascii"))
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("kinematics manifest breaks the contract: " + "; ".join(problems))
    shipped: dict[int, str] | None = None
    if data_dir is not None:
        with open(os.path.join(os.fspath(data_dir), "manifest.json"), "rb") as fh:
            shipped = {int(e["seed"]): e["sha256"] for e in json.loads(fh.read().decode("ascii"))["files"]}
        if sorted(shipped) != sorted(e["seed"] for e in manifest["files"]):
            raise ValueError(f"kinematics seeds {sorted(e['seed'] for e in manifest['files'])} differ from the data seeds {sorted(shipped)}")
    listed = {MANIFEST_NAME, README_NAME}
    for entry in manifest["files"]:
        fpath = os.path.join(root, entry["name"])
        if not os.path.isfile(fpath):
            raise ValueError(f"kinematics manifest lists {entry['name']}, which does not exist")
        with open(fpath, "rb") as fh:
            data = fh.read()
        if sha256_hex(data) != entry["sha256"]:
            raise ValueError(f"kinematics/{entry['name']}: sha256 {sha256_hex(data)}, manifest says {entry['sha256']}")
        body = json.loads(data.decode("ascii"))
        problems = validate_payload(body)
        if problems:
            raise ValueError(f"kinematics/{entry['name']} breaks the contract: " + "; ".join(problems))
        if body["seed"] != entry["seed"] or body["events_sha256"] != entry["events_sha256"]:
            raise ValueError(f"kinematics/{entry['name']}: seed or events_sha256 differs from the manifest")
        if shipped is not None and shipped[entry["seed"]] != entry["events_sha256"]:
            raise ValueError(f"kinematics/{entry['name']} was derived from events other than the shipped {entry['seed']}.ndjson")
        listed.add(entry["name"])
    readme_path = os.path.join(root, README_NAME)
    if not os.path.isfile(readme_path):
        raise ValueError("kinematics README.txt does not exist")
    with open(readme_path, "rb") as fh:
        if sha256_hex(fh.read()) != manifest["readme"]["sha256"]:
            raise ValueError("kinematics/README.txt: sha256 differs from the manifest")
    present = {os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/") for d, _, names in os.walk(root) for n in names}
    stray = sorted(present - listed)
    if stray:
        raise ValueError(f"kinematics files not listed in the manifest: {', '.join(stray)}")
    return len(manifest["files"]) + 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webkinematics", description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--data-dir", help="the shipped web data folder (manifest.json and <seed>.ndjson)")
    source.add_argument("--seeds", help="generate the matches instead, e.g. 1,2,3")
    parser.add_argument("--out", required=True, help="the kinematics folder to write")
    args = parser.parse_args(argv)
    manifest, payloads = build_from_data_dir(args.data_dir) if args.data_dir else build_from_seeds(parse_seeds(args.seeds))
    _write(args.out, manifest, payloads)
    count = verify_kinematics_dir(args.out, args.data_dir)
    for entry in manifest["files"]:
        body = json.loads(payloads[entry["name"]].decode("ascii"))
        c = body["checks"]
        sys.stdout.write(
            f"{entry['name']} sha256 {entry['sha256']} events_sha256 {entry['events_sha256']}"
            f" cap_violations {c['cap_violations']} anchor_misses {c['anchor_misses']} of {c['anchors']}\n"
        )
    sys.stdout.write(f"kinematics files {count} model {manifest['model']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

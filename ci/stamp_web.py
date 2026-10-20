"""Stamp the deployed commit into the web shell and verify the shipped data.

Why: the page label "commit <sha>" is how a judge (and the readiness gate) ties
what is served to a commit in the repo, so the stamp must be exact and must
happen once. The recorded synthetic data under web/data/ is generated at deploy
time, so its manifest is re-hashed here before upload: a file that does not
match its manifest entry, or a stray file the manifest does not list, stops the
deploy instead of shipping.

Rules, each a hard failure:
* the sha must be a full 40-character lowercase hex commit id;
* index.html must hold the placeholder __AGON_COMMIT__ exactly once, and after
  stamping no file under the web directory may still hold it;
* with --data-dir: manifest.json must exist, list exactly the --seeds given,
  and every file it lists must exist with the stated sha256 and event count (the web data contract in agon.webdata);
  every world file it lists must exist with the stated sha256 and pass agon.webdata.validate_world;
  every file under the data directory other than manifest.json must be listed;
  then the season files (agon.webseason), the match analysis files (agon.webmatch, computed
  by agon.interpret from the exact event logs just verified) and the intel files (agon.webintel:
  the metrics pack, rhythm and milestones, from the same logs) are written and re-checked.

* when the web directory holds runs/: every run listed in runs/index.json must
  pass agon.webrun.validate_run and its sealed bundle must recompute to the
  bundle hash the run states (agon.webrun.verify_run_dir); a file in runs/
  that the index does not list fails.

Stdlib only. Writes are binary, so line endings never change.

Usage:
    python ci/stamp_web.py web --sha "$GITHUB_SHA" --data-dir web/data --seeds 1,2,3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PLACEHOLDER = "__AGON_COMMIT__"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
TEXT_SUFFIXES = (".html", ".js", ".css", ".json", ".svg", ".txt", ".xml", ".webmanifest")


class StampError(Exception):
    """A rule was broken; the message says which."""


def stamp_commit(web_dir: Path, sha: str) -> None:
    """Replace the single placeholder in index.html with sha, then prove none is left."""
    if not FULL_SHA.match(sha):
        raise StampError(f"commit sha must be 40 lowercase hex characters, got {sha!r}")
    index = web_dir / "index.html"
    if not index.is_file():
        raise StampError(f"{index} does not exist")
    raw = index.read_bytes()
    count = raw.count(PLACEHOLDER.encode("ascii"))
    if count != 1:
        raise StampError(f"index.html holds the placeholder {count} times; exactly 1 is required")
    index.write_bytes(raw.replace(PLACEHOLDER.encode("ascii"), sha.encode("ascii")))
    leftovers = [
        p.relative_to(web_dir).as_posix()
        for p in sorted(web_dir.rglob("*"))
        if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES and PLACEHOLDER.encode("ascii") in p.read_bytes()
    ]
    if leftovers:
        raise StampError(f"placeholder still present after stamping in: {', '.join(leftovers)}")


def parse_seeds(text: str) -> list[int]:
    """'1,2,3' to [1, 2, 3]; ranges are not accepted here, the published set is short."""
    try:
        seeds = sorted({int(part) for part in text.split(",") if part.strip()})
    except ValueError as exc:
        raise StampError(f"--seeds must be comma-separated integers: {exc}") from exc
    if not seeds:
        raise StampError("--seeds is empty")
    return seeds


def verify_data(data_dir: Path, seeds: list[int]) -> int:
    """Check the shipped data against the web data contract the browser enforces.

    The contract lives in agon.webdata (validate_manifest): the browser refuses any
    other shape and falls back to the fixture, so the deploy must refuse it first.
    Every listed file is re-hashed and its event lines counted, every listed
    world file is re-hashed and shape-checked; unlisted files fail.
    Returns the number of files verified (event files plus world files).
    """
    from agon.webdata import validate_manifest, validate_world  # the one definition of the contract

    manifest_path = data_dir / "manifest.json"
    if not manifest_path.is_file():
        raise StampError(f"{manifest_path} does not exist")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StampError(f"manifest.json is not valid JSON: {exc}") from exc
    problems = validate_manifest(manifest)
    if problems:
        raise StampError("manifest breaks the web data contract: " + "; ".join(problems))
    entries = manifest["files"]
    listed_seeds = sorted(int(entry["seed"]) for entry in entries)
    if listed_seeds != seeds:
        raise StampError(f"manifest lists seeds {listed_seeds}, expected {seeds}")
    listed: set[str] = set()
    for entry in entries:
        rel = str(entry["name"])
        path = data_dir / rel
        if not path.is_file():
            raise StampError(f"manifest lists {rel}, which does not exist")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"]:
            raise StampError(f"{rel}: sha256 {digest}, manifest says {entry['sha256']}")
        lines = len(data.splitlines())
        if lines != int(entry["events"]):
            raise StampError(f"{rel}: {lines} event lines, manifest says {entry['events']}")
        listed.add(rel)
        world_rel = str(entry["world"])
        world_path = data_dir / world_rel
        if not world_path.is_file():
            raise StampError(f"manifest lists {world_rel}, which does not exist")
        world_data = world_path.read_bytes()
        world_digest = hashlib.sha256(world_data).hexdigest()
        if world_digest != entry["world_sha256"]:
            raise StampError(f"{world_rel}: sha256 {world_digest}, manifest says {entry['world_sha256']}")
        try:
            world = json.loads(world_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StampError(f"{world_rel} is not UTF-8 JSON: {exc}") from exc
        world_problems = validate_world(world)
        if world_problems:
            raise StampError(f"{world_rel} breaks the world contract: " + "; ".join(world_problems))
        if world["seed"] != entry["seed"]:
            raise StampError(f"{world_rel} is for seed {world['seed']}, manifest entry is seed {entry['seed']}")
        listed.add(world_rel)
    present = {
        p.relative_to(data_dir).as_posix()
        for p in data_dir.rglob("*")
        if p.is_file() and p != manifest_path
    }
    stray = sorted(present - listed)
    if stray:
        raise StampError(f"files not listed in the manifest: {', '.join(stray)}")
    return len(listed)


def verify_runs(web_dir: Path) -> list[str]:
    """Verify web_dir/runs when it exists; returns the run ids verified (none without runs/)."""
    runs_dir = web_dir / "runs"
    if not runs_dir.is_dir():
        return []
    from agon.webrun import verify_run_dir  # the one definition of the run format

    try:
        return verify_run_dir(runs_dir)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StampError(f"runs: {exc}") from exc


def stamp_season(web_dir: Path) -> str:
    """Write web_dir/season (the season files agon.webseason defines), then re-hash and check them.

    The storyline index is built here, in CI, because it reads referee labels and nothing under
    src/agon outside the referee may import it (ci/check_referee_isolation.py).
    """
    from agon.referee.storyline_index import storyline_index
    from agon.webseason import SEASON_DIR, verify_season_dir, write_season_web

    season_dir = web_dir / SEASON_DIR
    try:
        write_season_web(season_dir, storyline_index())
        count = verify_season_dir(season_dir)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StampError(f"season: {exc}") from exc
    return f"wrote and verified {count} season files under {season_dir.as_posix()}"


def stamp_match(web_dir: Path, data_dir: Path) -> str:
    """Write web_dir/match (the match analysis files agon.webmatch defines) from the verified data, then re-check them."""
    from agon.webmatch import MATCH_DIR, verify_match_dir, write_match_web

    match_dir = web_dir / MATCH_DIR
    try:
        write_match_web(str(match_dir), str(data_dir))
        count = verify_match_dir(match_dir, data_dir)
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StampError(f"match: {exc}") from exc
    return f"wrote and verified {count} match files under {match_dir.as_posix()}"


CUES_DIR = "cues"
CUES_MANIFEST_SCHEMA = "agon.cues-manifest/1"
CUES_SCHEMA = "agon.cues/1"
CUE_FILE = re.compile(r"^[0-9]{1,9}\.json$")


def stamp_cues(web_dir: Path) -> str:
    """Write web_dir/cues/manifest.json listing the cue files present (none until a charter is approved).

    The console fetches the list and only then a seed's cue file, so a seed without cues costs no
    failed request. A cue file must be agon.cues/1 for its own seed and name a 64-hex charter hash.
    """
    cues_dir = web_dir / CUES_DIR
    cues_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(cues_dir.iterdir()):
        if path.name == "manifest.json":
            continue
        if not path.is_file() or not CUE_FILE.match(path.name):
            raise StampError(f"cues: unexpected file {path.name}")
        data = path.read_bytes()
        try:
            body = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StampError(f"cues: {path.name} is not UTF-8 JSON: {exc}") from exc
        seed = int(path.name.split(".")[0])
        charter = body.get("charter_sha256") if isinstance(body, dict) else None
        if not isinstance(body, dict) or body.get("schema") != CUES_SCHEMA or body.get("seed") != seed:
            raise StampError(f"cues: {path.name} is not {CUES_SCHEMA} for seed {seed}")
        if not isinstance(charter, str) or not re.fullmatch(r"[0-9a-f]{64}", charter):
            raise StampError(f"cues: {path.name} names no approved charter hash")
        files.append({"name": path.name, "seed": seed, "sha256": hashlib.sha256(data).hexdigest(), "charter_sha256": charter})
    manifest = {"schema": CUES_MANIFEST_SCHEMA, "files": files}
    (cues_dir / "manifest.json").write_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n")
    return f"listed {len(files)} cue files under {cues_dir.as_posix()}"


def stamp_intel(web_dir: Path, data_dir: Path, seeds: list[int]) -> str:
    """Write web_dir/intel (the files agon.webintel defines) from the verified data, then re-check them."""
    from agon.webintel import INTEL_DIR, verify_intel_dir, write_intel_web

    intel_dir = web_dir / INTEL_DIR
    try:
        write_intel_web(str(intel_dir), str(data_dir), seeds)
        count = verify_intel_dir(intel_dir, data_dir)
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StampError(f"intel: {exc}") from exc
    return f"wrote and verified {count} intel files under {intel_dir.as_posix()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("web_dir", type=Path)
    parser.add_argument("--sha", required=True, help="full commit sha to stamp")
    parser.add_argument("--data-dir", type=Path, help="generated data directory to verify")
    parser.add_argument("--seeds", default="", help="published seeds the manifest must list, e.g. 1,2,3")
    args = parser.parse_args(argv)
    try:
        stamp_commit(args.web_dir, args.sha)
        print(f"stamped commit {args.sha} into {args.web_dir.as_posix()}/index.html")
        if args.data_dir is not None:
            count = verify_data(args.data_dir, parse_seeds(args.seeds))
            print(f"verified {count} data files against {args.data_dir.as_posix()}/manifest.json")
            print(stamp_season(args.web_dir))
            print(stamp_match(args.web_dir, args.data_dir))
            print(stamp_cues(args.web_dir))
            print(stamp_intel(args.web_dir, args.data_dir, parse_seeds(args.seeds)))
        runs = verify_runs(args.web_dir)
        print(f"verified {len(runs)} runs under {args.web_dir.as_posix()}/runs" + (f": {', '.join(runs)}" if runs else ""))
    except (StampError, KeyError, TypeError, ValueError) as exc:
        print(f"stamp_web: FAIL: {exc}")
        return 1
    print("stamp_web: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

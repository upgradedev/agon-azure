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
  and every file it lists must exist with the stated byte count and sha256;
  every file under the data directory other than manifest.json must be listed.

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
    """Re-hash every manifest entry and refuse unlisted files. Returns files verified."""
    manifest_path = data_dir / "manifest.json"
    if not manifest_path.is_file():
        raise StampError(f"{manifest_path} does not exist")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StampError(f"manifest.json is not valid JSON: {exc}") from exc
    listed_seeds = sorted(int(entry["seed"]) for entry in manifest.get("seeds", []))
    if listed_seeds != seeds:
        raise StampError(f"manifest lists seeds {listed_seeds}, expected {seeds}")
    entries = manifest.get("files", [])
    if not entries:
        raise StampError("manifest lists no files")
    listed: set[str] = set()
    for entry in entries:
        rel = str(entry["path"])
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise StampError(f"manifest path escapes the data directory: {rel}")
        path = data_dir / rel
        if not path.is_file():
            raise StampError(f"manifest lists {rel}, which does not exist")
        data = path.read_bytes()
        if len(data) != int(entry["bytes"]):
            raise StampError(f"{rel}: {len(data)} bytes, manifest says {entry['bytes']}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"]:
            raise StampError(f"{rel}: sha256 {digest}, manifest says {entry['sha256']}")
        listed.add(rel)
    present = {
        p.relative_to(data_dir).as_posix()
        for p in data_dir.rglob("*")
        if p.is_file() and p != manifest_path
    }
    stray = sorted(present - listed)
    if stray:
        raise StampError(f"files not listed in the manifest: {', '.join(stray)}")
    return len(listed)


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
    except (StampError, KeyError, TypeError, ValueError) as exc:
        print(f"stamp_web: FAIL: {exc}")
        return 1
    print("stamp_web: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

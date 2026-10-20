"""Copy the recorded runs' execution evidence into the site, validated (the operations web data contract).

Usage:
    python -m agon.webops --evidence evidence --web dist

At deploy, ci/stamp_web.py calls `write_ops` after the runs are verified. The Media office view reads
the result to show when each Container Apps job execution of a recorded run started and ended, read
from Azure with the command the evidence file names, beside the approval time in the run file.

Source: evidence/<anything>.executions.json, schema agon.run-executions/1:
    {"schema", "run_id", "recorded_run", "bundle_sha256", "source_command", "captured_at",
     "executions": [{"job", "execution", "status", "start", "end", "role"}]}
Evidence for a run that runs/index.json does not list is not shipped (it describes no run on the
page); for a listed run, `bundle_sha256` must be the bundle hash that run states, or the stamp
stops, so the evidence can only ever describe the run the page shows. Times are ISO 8601
with a zone and an execution never ends before it starts.

Written: ops/manifest.json {"schema": "agon.ops-manifest/1", "files": [{"run_id", "name", "sha256"}]}
and ops/<recorded_run>.executions.json, the evidence bytes unchanged. Without evidence the manifest
lists no file, and the page says that the run has no executions file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Sequence

OPS_DIR = "ops"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "agon.ops-manifest/1"
EVIDENCE_SCHEMA = "agon.run-executions/1"
EVIDENCE_SUFFIX = ".executions.json"
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_KEYS = frozenset({"schema", "run_id", "recorded_run", "bundle_sha256", "source_command", "captured_at", "executions"})
EXECUTION_KEYS = frozenset({"job", "execution", "status", "start", "end", "role"})


class OpsError(ValueError):
    """The evidence cannot be shipped; the message says why."""


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def validate_executions(body: Any) -> list[str]:
    """Problems with one evidence file's shape; empty means it matches agon.run-executions/1."""
    if not isinstance(body, dict) or set(body) != EVIDENCE_KEYS:
        return [f"the evidence must hold exactly {', '.join(sorted(EVIDENCE_KEYS))}"]
    problems: list[str] = []
    if body["schema"] != EVIDENCE_SCHEMA:
        problems.append(f"schema must be {EVIDENCE_SCHEMA}")
    if not isinstance(body["recorded_run"], str) or not RUN_ID.match(body["recorded_run"]):
        problems.append("recorded_run must be a run id")
    if not isinstance(body["bundle_sha256"], str) or not SHA256_HEX.match(body["bundle_sha256"]):
        problems.append("bundle_sha256 must be 64 lowercase hex characters")
    for key in ("run_id", "source_command", "captured_at"):
        if not isinstance(body[key], str) or not body[key].strip():
            problems.append(f"{key} must be a non-empty string")
    rows = body["executions"]
    if not isinstance(rows, list) or not rows:
        problems.append("executions must be a non-empty list")
        return problems
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != EXECUTION_KEYS:
            problems.append(f"executions[{i}] must hold exactly {', '.join(sorted(EXECUTION_KEYS))}")
            continue
        for key in ("job", "execution", "status", "role"):
            if not isinstance(row[key], str) or not row[key].strip():
                problems.append(f"executions[{i}].{key} must be a non-empty string")
        start, end = _time(row["start"]), _time(row["end"])
        if start is None or end is None:
            problems.append(f"executions[{i}] start and end must be ISO 8601 times with a zone")
        elif end < start:
            problems.append(f"executions[{i}] ends before it starts")
    return problems


def _read_json(path: str) -> tuple[bytes, Any]:
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        return data, json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpsError(f"{os.path.basename(path)} is not UTF-8 JSON: {exc}") from exc


def _runs(web_dir: str) -> dict[str, str]:
    """run id to the bundle hash it states, for every run in runs/index.json."""
    index_path = os.path.join(web_dir, "runs", "index.json")
    if not os.path.isfile(index_path):
        return {}
    _, index = _read_json(index_path)
    out: dict[str, str] = {}
    for row in index.get("runs", []):
        _, run = _read_json(os.path.join(web_dir, "runs", row["file"]))
        out[row["run_id"]] = run["bundle"]["sha256"]
    return out


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def write_ops(web_dir: str, evidence_dir: str) -> dict[str, Any]:
    """Validate every evidence file against the shipped runs and write web_dir/ops. Returns the manifest."""
    runs = _runs(web_dir)
    files: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    names = sorted(n for n in os.listdir(evidence_dir) if n.endswith(EVIDENCE_SUFFIX)) if os.path.isdir(evidence_dir) else []
    for name in names:
        data, body = _read_json(os.path.join(evidence_dir, name))
        problems = validate_executions(body)
        if problems:
            raise OpsError(f"{name}: " + "; ".join(problems))
        run_id = body["recorded_run"]
        if run_id not in runs:
            continue
        if runs[run_id] != body["bundle_sha256"]:
            raise OpsError(f"{name} names bundle {body['bundle_sha256'][:12]}, but {run_id} states {runs[run_id][:12]}")
        out_name = f"{run_id}{EVIDENCE_SUFFIX}"
        if out_name in payloads:
            raise OpsError(f"two evidence files describe {run_id}")
        payloads[out_name] = data
        files.append({"run_id": run_id, "name": out_name, "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"schema": MANIFEST_SCHEMA, "files": files}
    ops_dir = os.path.join(web_dir, OPS_DIR)
    os.makedirs(ops_dir, exist_ok=True)
    for name, data in payloads.items():
        with open(os.path.join(ops_dir, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(ops_dir, MANIFEST_NAME), "wb") as fh:
        fh.write(manifest_bytes(manifest))
    return manifest


def verify_ops_dir(web_dir: str) -> int:
    """Re-hash and re-check what write_ops wrote; a file the manifest does not list fails. Returns the count."""
    ops_dir = os.path.join(web_dir, OPS_DIR)
    _, manifest = _read_json(os.path.join(ops_dir, MANIFEST_NAME))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("files"), list):
        raise OpsError(f"ops/manifest.json is not {MANIFEST_SCHEMA}")
    runs = _runs(web_dir)
    listed = set()
    for entry in manifest["files"]:
        data, body = _read_json(os.path.join(ops_dir, entry["name"]))
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise OpsError(f"ops/{entry['name']} does not hash to its manifest entry")
        problems = validate_executions(body)
        if problems or body["recorded_run"] != entry["run_id"] or runs.get(entry["run_id"]) != body["bundle_sha256"]:
            raise OpsError(f"ops/{entry['name']} does not describe a shipped run: " + "; ".join(problems))
        listed.add(entry["name"])
    stray = sorted(set(os.listdir(ops_dir)) - listed - {MANIFEST_NAME})
    if stray:
        raise OpsError(f"files not listed in ops/manifest.json: {', '.join(stray)}")
    return len(listed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webops", description=__doc__.splitlines()[0])
    parser.add_argument("--evidence", required=True, help="the evidence folder (evidence/*.executions.json)")
    parser.add_argument("--web", required=True, help="the site folder holding runs/")
    args = parser.parse_args(argv)
    try:
        write_ops(args.web, args.evidence)
        count = verify_ops_dir(args.web)
    except (OpsError, OSError, KeyError, TypeError) as exc:
        sys.stdout.write(f"webops: FAIL: {exc}\n")
        return 1
    sys.stdout.write(f"webops: wrote and verified {count} execution files\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

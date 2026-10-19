"""Run W-fulltime to its approval pause, or resume it from a checkpoint in a fresh process.

`start_run` drives the workflow until `approval` has asked for a decision, then PROVES the pause
before reporting it: no checkpoint save failed (the framework only logs those), the pause
checkpoint resolves, it loads under the allow-list, and it holds the pending request. Only then
does the run ledger move to AWAITING_APPROVAL. A worker may exit after that; nothing it held in
memory is needed again.

`resume_run` rebuilds the same graph in a new process and calls
``workflow.run(checkpoint_id=..., responses={request_id: DecisionEnvelope})``, the combined
restore-and-respond path of Agent Framework 1.22.0 (``_workflow.py`` ``_restore_and_send_responses``).
A checkpoint is not consumed by a resume, so exactly-once comes from the verifier's consume-once
write and the create-only publish queue, not from the framework.

Command line (fake ports only; the Container Apps worker wires the real ports from its
environment). This is what the kill-and-resume tests and the live Cosmos job drive:

    python -m agon.fleet.runner start  --run-dir DIR --seed 1 --grant-approver officer@media.example --mode hang
    python -m agon.fleet.runner resume --run-dir DIR --decision approve --approver officer@media.example --confirm-all

    python -m agon.fleet.runner delete-checkpoint --run-dir DIR   (negative control only)
    python -m agon.fleet.runner probe --cosmos-endpoint URL       (data-plane role check)

Exit codes: 0 ok; 2 bad arguments or missing handoff; 3 the pause could not be proven;
4 the resume raised (the error chain is printed); 5 the Cosmos role is missing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, AsyncIterator

from agon.fleet.checkpoints import (
    CheckpointSaveFailed,
    CosmosRoleMissing,
    GuardedCheckpointStorage,
    file_storage,
    open_cosmos_storage,
)
from agon.fleet.contracts import WORKFLOW_NAME, ApprovalRequest, DecisionEnvelope, RunInput, RunOutcome
from agon.fleet.deps import FleetDeps
from agon.fleet.local import fake_deps
from agon.fleet.stores import AWAITING_APPROVAL
from agon.fleet.workflow import build_workflow
from agon.governance import verifier

HANDOFF = "handoff.json"
CHECKPOINT_DIR = "checkpoints"


class PauseNotProven(RuntimeError):
    """The workflow asked for a decision, but its pause is not safely persisted."""


def _af_version() -> str:
    try:
        return metadata.version("agent-framework-core")
    except metadata.PackageNotFoundError:
        return "unknown"


def _outcomes(result: Any) -> list[dict[str, Any]]:
    return [asdict(o) for o in result.get_outputs() if isinstance(o, RunOutcome)]


async def start_run(deps: FleetDeps, storage: GuardedCheckpointStorage, run: RunInput) -> dict[str, Any]:
    started = time.perf_counter()
    workflow = build_workflow(deps, storage)
    result = await workflow.run(run)
    outcomes = _outcomes(result)
    requests = result.get_request_info_events()
    if not requests:
        storage.raise_if_failed()
        return {"state": "ended", "run_id": run.run_id, "outcomes": outcomes}
    if len(requests) != 1:
        raise PauseNotProven(f"expected one approval request, got {len(requests)}")
    event = requests[0]
    if not isinstance(event.data, ApprovalRequest):
        raise PauseNotProven(f"request payload has type {type(event.data).__name__}")
    storage.raise_if_failed()
    checkpoint_id = await workflow.resolve_pause_checkpoint_id([event.request_id], checkpoint_storage=storage)
    if checkpoint_id is None:
        raise PauseNotProven("no persisted checkpoint covers the pending request")
    checkpoint = await storage.load(checkpoint_id)
    if event.request_id not in checkpoint.pending_request_info_events:
        raise PauseNotProven("the resolved checkpoint does not hold the pending request")
    deps.ledger.advance(run.run_id, AWAITING_APPROVAL, f"pause checkpoint {checkpoint_id}", checkpoint_id=checkpoint_id)
    return {
        "state": "awaiting_approval",
        "agent_framework_core": _af_version(),
        "workflow_name": workflow.name,
        "run_id": run.run_id,
        "checkpoint_id": checkpoint_id,
        "request_id": event.request_id,
        "source_executor_id": event.source_executor_id,
        "card": asdict(event.data),
        "checkpoints_saved": len(storage.saved),
        "pid": os.getpid(),
        "seconds_to_pause": round(time.perf_counter() - started, 3),
    }


async def resume_run(
    deps: FleetDeps, storage: GuardedCheckpointStorage, handoff: dict[str, Any], envelope: DecisionEnvelope
) -> dict[str, Any]:
    started = time.perf_counter()
    workflow = build_workflow(deps, storage)
    result = await workflow.run(checkpoint_id=handoff["checkpoint_id"], responses={handoff["request_id"]: envelope})
    reemitted = [e.request_id for e in result.get_request_info_events() if e.request_id == handoff["request_id"]]
    return {
        "state": "resumed",
        "run_id": handoff["run_id"],
        "outcomes": _outcomes(result),
        "reemitted_request_ids": reemitted,
        "save_failures": list(storage.failures),
        "pid": os.getpid(),
        "seconds_to_outcome": round(time.perf_counter() - started, 3),
    }


def envelope_from_card(
    card: dict[str, Any],
    *,
    decision: str,
    approver: str,
    channel: str,
    confirm_all: bool,
    tamper_hash: bool = False,
) -> DecisionEnvelope:
    """What a channel adapter builds from a tap on the card. The time is set here, never by the card."""
    bundle = card["bundle_sha256"]
    if tamper_hash:
        bundle = bundle[:-1] + ("0" if bundle[-1] != "0" else "1")
    return DecisionEnvelope(
        bundle_sha256=bundle,
        nonce=card["nonce"],
        decision=decision,
        approver=approver,
        channel=channel,
        received_at=datetime.now(timezone.utc).isoformat(),
        confirmed_rows=list(card["confirm_rows"]) if confirm_all else [],
    )


def error_chain(exc: BaseException) -> list[str]:
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return chain


def _atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@contextlib.asynccontextmanager
async def _storage(args: argparse.Namespace) -> AsyncIterator[GuardedCheckpointStorage]:
    if args.storage == "file":
        yield GuardedCheckpointStorage(file_storage(args.run_dir / CHECKPOINT_DIR))
        return
    endpoint = args.cosmos_endpoint or os.environ.get("AGON_COSMOS_ENDPOINT", "")
    async with open_cosmos_storage(endpoint) as cosmos:
        yield GuardedCheckpointStorage(cosmos)


def _print(report: dict[str, Any]) -> None:
    print(json.dumps(report, sort_keys=True), flush=True)


async def _start(args: argparse.Namespace) -> int:
    args.run_dir.mkdir(parents=True, exist_ok=True)
    deps = fake_deps(args.run_dir)
    if args.grant_approver:
        role = verifier.TARGET_ROLE[args.target]
        if deps.store.read(verifier.APPROVERS, verifier.normalize_principal(args.grant_approver)) is None:
            verifier.grant_role(deps.store, args.grant_approver, [role])
    run = RunInput(
        run_id=args.run_id or f"run-{uuid.uuid4().hex[:12]}",
        match_ref={"source": "synth", "seed": args.seed, "match_index": args.match_index},
        target=args.target,
    )
    async with _storage(args) as storage:
        try:
            handoff = await start_run(deps, storage, run)
        except (PauseNotProven, CheckpointSaveFailed) as exc:
            _print({"process": "start", "ok": False, "error_chain": error_chain(exc)})
            return 3
        handoff["storage"] = args.storage
        _atomic_json(args.run_dir / HANDOFF, handoff)
        _print({"process": "start", "ok": True, **handoff})
        if args.mode == "hang" and handoff["state"] == "awaiting_approval":
            await asyncio.sleep(3600)
            _print({"process": "start", "ok": False, "error": "hang mode was never killed"})
            return 3
    return 0


async def _resume(args: argparse.Namespace) -> int:
    path = args.run_dir / HANDOFF
    if not path.is_file():
        _print({"process": "resume", "ok": False, "error": "handoff.json missing"})
        return 2
    handoff = json.loads(path.read_text(encoding="utf-8"))
    deps = fake_deps(args.run_dir)
    envelope = envelope_from_card(
        handoff["card"],
        decision=args.decision,
        approver=args.approver,
        channel=args.channel,
        confirm_all=args.confirm_all,
        tamper_hash=args.tamper_hash,
    )
    async with _storage(args) as storage:
        try:
            report = await resume_run(deps, storage, handoff, envelope)
        except Exception as exc:  # noqa: BLE001 - the caller judges the reason from the chain
            report = {"process": "resume", "label": args.label, "ok": False, "pid": os.getpid(), "error_chain": error_chain(exc)}
            _atomic_json(args.run_dir / f"resume-{args.label}.json", report)
            _print(report)
            return 4
    report = {"process": "resume", "label": args.label, "ok": True, **report}
    _atomic_json(args.run_dir / f"resume-{args.label}.json", report)
    _print(report)
    return 0


async def _delete_checkpoint(args: argparse.Namespace) -> int:
    """Negative control only: remove the pause checkpoint so a resume must fail."""
    handoff = json.loads((args.run_dir / HANDOFF).read_text(encoding="utf-8"))
    async with _storage(args) as storage:
        deleted = await storage.delete(handoff["checkpoint_id"])
    _print({"process": "delete-checkpoint", "ok": bool(deleted), "checkpoint_id": handoff["checkpoint_id"]})
    return 0 if deleted else 4


async def _probe(args: argparse.Namespace) -> int:
    """Prove this identity can read and write the checkpoints container (exit 5 with the reason if not)."""
    endpoint = args.cosmos_endpoint or os.environ.get("AGON_COSMOS_ENDPOINT", "")
    async with open_cosmos_storage(endpoint, probe=True):
        pass
    _print({"process": "probe", "ok": True, "detail": "read and write allowed on agon/checkpoints"})
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start or resume a W-fulltime run with fake ports.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "resume", "delete-checkpoint"):
        p = sub.add_parser(name)
        p.add_argument("--run-dir", required=True, type=Path)
        p.add_argument("--storage", choices=("file", "cosmos"), default="file")
        p.add_argument("--cosmos-endpoint", default="")
        p.add_argument("--ports", choices=("fake",), default="fake", help="only fake ports are wired here")
    start = sub.choices["start"]
    start.add_argument("--seed", type=int, default=1)
    start.add_argument("--match-index", type=int, default=0)
    start.add_argument("--target", choices=verifier.TARGETS, default="feed")
    start.add_argument("--run-id", default="")
    start.add_argument("--mode", choices=("exit", "hang"), default="exit")
    start.add_argument("--grant-approver", default="", help="test setup: grant this principal the target's approver role")
    probe = sub.add_parser("probe", help="check the Cosmos DB data-plane role on the checkpoints container")
    probe.add_argument("--cosmos-endpoint", default="")
    resume = sub.choices["resume"]
    resume.add_argument("--decision", choices=verifier.DECISIONS, required=True)
    resume.add_argument("--approver", required=True)
    resume.add_argument("--channel", choices=verifier.CHANNELS, default="teams")
    resume.add_argument("--confirm-all", action="store_true")
    resume.add_argument("--tamper-hash", action="store_true")
    resume.add_argument("--label", default="resume")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        commands = {"start": _start, "resume": _resume, "delete-checkpoint": _delete_checkpoint, "probe": _probe}
        return asyncio.run(commands[args.command](args))
    except CosmosRoleMissing as exc:
        _print({"process": args.command, "ok": False, "error": str(exc), "workflow_name": WORKFLOW_NAME})
        return 5


if __name__ == "__main__":
    sys.exit(main())

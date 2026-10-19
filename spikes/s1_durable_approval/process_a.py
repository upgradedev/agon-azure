"""Process A: run the workflow until the approval request is pending, record it, then die.

Modes:
  exit  return normally after writing handoff.json (graceful shutdown).
  hang  write handoff.json, then block until the driver kills the process (SIGKILL on POSIX,
        TerminateProcess on Windows). Nothing in this process's memory survives that.

Exit codes: 0 ok, 3 the pause checkpoint is missing or does not hold the request.
"""

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
import time
from importlib import metadata
from pathlib import Path

from s1_bundle import ApprovalRequest, atomic_write_json
from s1_workflow import build_workflow, make_storage

MATCH_ID = "synthetic-match-0001"


def _fail(message: str) -> int:
    print(json.dumps({"process": "A", "ok": False, "error": message}), flush=True)
    return 3


async def _main(run_dir: Path, mode: str) -> int:
    started = time.perf_counter()
    storage = make_storage(run_dir / "checkpoints")
    workflow = build_workflow(storage, run_dir, nonce=secrets.token_hex(16))

    result = await workflow.run(MATCH_ID)
    requests = result.get_request_info_events()
    if len(requests) != 1:
        return _fail(f"expected exactly one request_info event, got {len(requests)}")
    event = requests[0]
    if not isinstance(event.data, ApprovalRequest):
        return _fail(f"request payload has type {type(event.data).__name__}")

    # A checkpoint save that fails is only logged by the runner and never fails the run, so prove
    # the pause checkpoint exists and carries this exact request before telling anyone it does.
    checkpoint_id = await workflow.resolve_pause_checkpoint_id([event.request_id], checkpoint_storage=storage)
    if checkpoint_id is None:
        return _fail("no persisted checkpoint covers the pending request")
    checkpoint = await storage.load(checkpoint_id)
    if event.request_id not in checkpoint.pending_request_info_events:
        return _fail("resolved checkpoint does not hold the pending request")
    checkpoint_file = run_dir / "checkpoints" / f"{checkpoint_id}.json"
    if not checkpoint_file.is_file():
        return _fail(f"checkpoint file {checkpoint_file.name} not found on disk")

    handoff = {
        "agent_framework_core": metadata.version("agent-framework-core"),
        "bundle_sha256": event.data.bundle_sha256,
        "checkpoint_file": checkpoint_file.name,
        "checkpoint_id": checkpoint_id,
        "nonce": event.data.nonce,
        "pid": os.getpid(),
        "request_id": event.request_id,
        "source_executor_id": event.source_executor_id,
        "workflow_name": workflow.name,
        "workflow_status": str(workflow.status),
        "seconds_to_pending": round(time.perf_counter() - started, 3),
    }
    atomic_write_json(run_dir / "handoff.json", handoff)
    print(json.dumps({"process": "A", "ok": True, **handoff}, sort_keys=True), flush=True)

    if mode == "hang":
        # Keep the interpreter, the event loop and the workflow object alive until killed.
        await asyncio.sleep(3600)
        return _fail("hang mode was never killed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("exit", "hang"), default="exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    return asyncio.run(_main(args.run_dir, args.mode))


if __name__ == "__main__":
    sys.exit(main())

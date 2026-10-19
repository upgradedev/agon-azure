"""Process B: a fresh interpreter that restores the checkpoint and delivers the human decision.

It builds the workflow again from scratch, then calls
``workflow.run(checkpoint_id=..., responses={request_id: ApprovalDecision(...)})``, the combined
restore-and-respond path of Agent Framework 1.22.0. It never sees process A's memory: everything
it knows comes from handoff.json (what a Teams card would carry) and the checkpoint file.

Exit codes: 0 the run completed (the outcome is printed and judged by the driver),
4 the resume failed (the error chain is printed), 2 bad arguments or missing handoff.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from s1_bundle import ApprovalDecision, PublishOutcome
from s1_workflow import build_workflow, make_storage


def _error_chain(exc: BaseException) -> list[str]:
    """Collect messages along __cause__ and __context__, so the driver can check the real reason."""
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return chain


def _flip_last_hex(value: str) -> str:
    """Change one hex digit, so the tampered hash is well formed but wrong."""
    return value[:-1] + ("0" if value[-1] != "0" else "1")


async def _main(run_dir: Path, tamper_hash: bool, label: str) -> int:
    started = time.perf_counter()
    handoff_path = run_dir / "handoff.json"
    if not handoff_path.is_file():
        print(json.dumps({"process": "B", "ok": False, "error": "handoff.json missing"}), flush=True)
        return 2
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))

    bundle_sha256 = handoff["bundle_sha256"]
    if tamper_hash:
        bundle_sha256 = _flip_last_hex(bundle_sha256)
    decision = ApprovalDecision(approve=True, bundle_sha256=bundle_sha256, nonce=handoff["nonce"])

    storage = make_storage(run_dir / "checkpoints")
    workflow = build_workflow(storage, run_dir)
    try:
        result = await workflow.run(
            checkpoint_id=handoff["checkpoint_id"],
            responses={handoff["request_id"]: decision},
        )
    except Exception as exc:  # noqa: BLE001 - the driver judges the reason from the chain
        report = {"process": "B", "label": label, "ok": False, "pid": os.getpid(), "error_chain": _error_chain(exc)}
        print(json.dumps(report, sort_keys=True), flush=True)
        return 4

    outcomes = [asdict(o) for o in result.get_outputs() if isinstance(o, PublishOutcome)]
    reemitted = [e.request_id for e in result.get_request_info_events() if e.request_id == handoff["request_id"]]
    report = {
        "process": "B",
        "label": label,
        "ok": True,
        "pid": os.getpid(),
        "outcomes": outcomes,
        "other_outputs": len(result.get_outputs()) - len(outcomes),
        "reemitted_request_ids": reemitted,
        "workflow_status": str(workflow.status),
        "seconds_to_outcome": round(time.perf_counter() - started, 3),
    }
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--tamper-hash", action="store_true")
    parser.add_argument("--label", default="resume")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    return asyncio.run(_main(args.run_dir, args.tamper_hash, args.label))


if __name__ == "__main__":
    sys.exit(main())

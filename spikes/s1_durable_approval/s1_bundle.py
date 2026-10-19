"""Stdlib-only pieces of spike S1: message types, sealing, the decision check, create-only publish.

Why this module has no third-party imports: the workstation that writes this spike may not
install packages, so everything that can be proven without Microsoft Agent Framework lives here
and is unit tested locally. The framework-dependent graph is in ``s1_workflow.py``.

Why the dataclasses live in an importable module and never in an entry script: Agent Framework
pickles checkpoint values by ``module:qualname``. A type defined in ``process_a.py`` would be
``__main__:ApprovalRequest`` in process A and could not be found by process B, so the resume
would fail for a reason that has nothing to do with durability.
"""

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKFLOW_NAME = "agon_s1_approval"
"""Fixed workflow name. Checkpoints are grouped by it, so it must not be auto-generated."""

EXECUTOR_IDS: tuple[str, str, str] = ("draft", "seal", "approval")
"""Fixed executor ids. The checkpoint graph signature is keyed on them (spec trap 1)."""

STATE_KEY_CANONICAL = "sealed_canonical_json"
"""Workflow state key holding the exact sealed bytes, so the response handler can recompute."""


@dataclass
class DraftBundle:
    """What ``draft`` produces: a tiny fictional match bundle."""

    match_id: str
    lines: list[str] = field(default_factory=list)


@dataclass
class SealedBundle:
    """What ``seal`` sends to ``approval``: the canonical bytes and their sha256."""

    canonical_json: str
    bundle_sha256: str


@dataclass
class ApprovalRequest:
    """The payload of ``ctx.request_info``. It is what a Teams card would carry."""

    bundle_sha256: str
    nonce: str


@dataclass
class ApprovalDecision:
    """The human's answer, delivered by a different process after the first one died."""

    approve: bool
    bundle_sha256: str
    nonce: str


@dataclass
class PublishOutcome:
    """Workflow output of the response handler: published, or refused with a reason."""

    status: str
    bundle_sha256: str
    detail: str


CHECKPOINT_TYPES: tuple[type, ...] = (
    DraftBundle,
    SealedBundle,
    ApprovalRequest,
    ApprovalDecision,
    PublishOutcome,
)

ALLOWED_CHECKPOINT_TYPES: list[str] = [f"{t.__module__}:{t.__qualname__}" for t in CHECKPOINT_TYPES]
"""Allow-list passed to FileCheckpointStorage in both processes.

Why explicit: the storage refuses to save any checkpoint whose values it could not decode under
this list, and the runner only logs that refusal as a warning. A missing entry therefore means a
silently missing checkpoint, which process A checks for and treats as a failure.
"""

PUBLISHED = "published"
REFUSED = "refused"
REFUSED_ALREADY_PUBLISHED = "refused_already_published"


def canonical_json(value: Any) -> str:
    """Serialise deterministically, so the same bundle always yields the same hash."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    """Hex sha256 of the UTF-8 bytes of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_draft(match_id: str) -> DraftBundle:
    """Build the fixed synthetic bundle. Placeholder names, chosen so no real club can match."""
    return DraftBundle(
        match_id=match_id,
        lines=[
            "Synthetic Club Alpha 2, Synthetic Club Beta 1.",
            "The winner came in the 78th minute after a spell of sustained pressure.",
        ],
    )


def seal(draft: DraftBundle) -> SealedBundle:
    """Canonicalise the draft and hash it. Pure function."""
    canonical = canonical_json({"match_id": draft.match_id, "lines": list(draft.lines)})
    return SealedBundle(canonical_json=canonical, bundle_sha256=sha256_hex(canonical))


def verify_decision(
    request: ApprovalRequest,
    decision: ApprovalDecision,
    sealed_canonical_json: str | None,
) -> tuple[bool, str]:
    """Check a decision against the request and the sealed bytes.

    Why recompute instead of trusting a stored hash: the point of the seal is that what gets
    published is exactly what was approved. Comparing two stored strings would not notice the
    bytes changing between seal and publish.
    """
    if sealed_canonical_json is None:
        return False, "sealed bundle missing from workflow state"
    recomputed = sha256_hex(sealed_canonical_json)
    if not hmac.compare_digest(recomputed, request.bundle_sha256):
        return False, "sealed bytes no longer match the hash in the request"
    if not hmac.compare_digest(decision.bundle_sha256, request.bundle_sha256):
        return False, "decision names a different bundle hash"
    if not hmac.compare_digest(decision.nonce, request.nonce):
        return False, "decision nonce does not match the request"
    if decision.approve is not True:
        return False, "decision is not an approval"
    return True, "ok"


def publish_create_only(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` only if the file does not exist yet.

    Why O_EXCL: a checkpoint can be resumed more than once, so the guarantee of a single publish
    has to come from the write itself. A second call raises FileExistsError and leaves the first
    file untouched.
    """
    data = (canonical_json(payload) + "\n").encode("utf-8")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, value: Any) -> None:
    """Write JSON through a temp file and os.replace, so a reader never sees half a file."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def append_trace(run_dir: Path, executor_id: str, step: str) -> None:
    """Record which process ran which step, so the driver can prove where each step executed."""
    line = json.dumps({"pid": os.getpid(), "executor": executor_id, "step": step}, sort_keys=True)
    with open(run_dir / "trace.ndjson", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_trace(run_dir: Path) -> list[dict[str, Any]]:
    """Read the trace written by ``append_trace``; empty when nothing ran."""
    path = run_dir / "trace.ndjson"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]

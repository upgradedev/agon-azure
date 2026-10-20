"""The persisted contract of W-charter: its name, executor ids and checkpointed message types.

Everything here is part of what a W-charter checkpoint holds, so it is pinned by literal in
`tests/fleet/test_charter_contracts.py`, apart from W-fulltime's own contract, which this module
does not change:

* the workflow name (checkpoints are grouped by it);
* the executor ids (the graph signature is keyed on them);
* the module path and class name of every message type (the restore allow-list).

W-charter reuses W-fulltime's message types wherever the meaning is the same (the run input, the
working Draft, the sealed draft, the approval request, the decision, the publish request and the
outcome), so the approval and publish executors, the worker's pending-request check and the one
verifier are shared unchanged. Only the two internal hand-offs are new.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass

from agon.fleet.contracts import (
    ApprovalRequest,
    DecisionEnvelope,
    Draft,
    PublishRequest,
    RunInput,
    RunOutcome,
    SealedDraft,
)

WORKFLOW_NAME = "agon_w_charter"

EXECUTOR_IDS: tuple[str, ...] = (
    "charter_build",
    "charter_check",
    "seal",
    "approval",
    "publish_request",
)


@dataclass
class CharterBuilt:
    """`charter_build` to `charter_check`: the charter's bytes are in the artifact store, named in
    `draft.artifacts["charter.json"]` by sha256."""

    draft: Draft


@dataclass
class CharterChecked:
    """`charter_check` to `seal`: both gate verdicts are in `draft.gate_verdicts`."""

    draft: Draft


CHECKPOINT_TYPES: tuple[type, ...] = (
    RunInput,
    Draft,
    CharterBuilt,
    CharterChecked,
    SealedDraft,
    ApprovalRequest,
    DecisionEnvelope,
    PublishRequest,
    RunOutcome,
)

ALLOWED_CHECKPOINT_TYPES: list[str] = [f"{t.__module__}:{t.__qualname__}" for t in CHECKPOINT_TYPES]

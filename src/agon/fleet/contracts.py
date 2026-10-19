"""The persisted contract of W-fulltime: names, executor ids and every message type.

Everything in this module is part of what a checkpoint holds, so it is pinned by literal in
`tests/fleet/test_fleet_contracts.py`:

* the workflow name (checkpoints are grouped by it, so it is never generated);
* the executor ids (the checkpoint's graph signature is keyed on them);
* the module path and class name of every message type (Agent Framework pickles messages and the
  pending request by ``module:qualname``, and a restore only decodes types on the allow-list).

Why every field is JSON-native (str, int, bool, None, list, dict): a checkpoint must stay small
and must decode in any process. The event log never travels in a message; the `Draft` carries the
match reference and the event-log hash, and each step reloads the log and checks that hash.

This module is standard library only, so it imports without Agent Framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WORKFLOW_NAME = "agon_w_fulltime"

EXECUTOR_IDS: tuple[str, ...] = (
    "ingest",
    "interpret",
    "analyst",
    "writer",
    "triage",
    "factgate",
    "desk_editor",
    "localise_el",
    "localise_es",
    "localise_fr",
    "easy_read",
    "factgate_lang",
    "rights",
    "audio",
    "seal",
    "approval",
    "publish_request",
)

TRANSLATED_LANGUAGES: tuple[str, ...] = ("el", "es", "fr")
EASY_READ = "easy"
# Edition key -> sentence language. "easy" is the English easy-read edition.
EDITION_LANGUAGE: dict[str, str] = {"en": "en", "el": "el", "es": "es", "fr": "fr", EASY_READ: "en"}
FANOUT_EDITIONS: tuple[str, ...] = (*TRANSLATED_LANGUAGES, EASY_READ)

MAX_WRITER_ATTEMPTS = 2
"""The Writer runs once, and once more when the Fact Gate sends it back: one bounded retry edge."""

# RunOutcome.status values.
REFUSED_AT_INGEST = "refused_at_ingest"
REFUSED_BY_GATE = "refused_by_gate"
PUBLISH_REQUESTED = "publish_requested"
PUBLISH_ALREADY_REQUESTED = "publish_already_requested"
CHANGES_REQUESTED = "changes_requested"
DECISION_REFUSED = "decision_refused"
# A redelivered `runs` message (or a restarted runner) for a run that is already past the fact
# gate. Nothing is redone and no second nonce is issued; see `steps.ingest`.
ALREADY_SEALED = "already_sealed"
AWAITING_DECISION = "awaiting_approval"
ALREADY_ENDED = "already_ended"
# Two deliveries of one run raced past ingest; only the first to claim the approval issues a nonce.
APPROVAL_ALREADY_REQUESTED = "approval_already_requested"
OUTCOME_STATUSES: tuple[str, ...] = (
    REFUSED_AT_INGEST,
    REFUSED_BY_GATE,
    PUBLISH_REQUESTED,
    PUBLISH_ALREADY_REQUESTED,
    CHANGES_REQUESTED,
    DECISION_REFUSED,
    ALREADY_SEALED,
    AWAITING_DECISION,
    ALREADY_ENDED,
    APPROVAL_ALREADY_REQUESTED,
)


@dataclass
class RunInput:
    """What the `runs` queue message carries: which match, and which feed it is for."""

    run_id: str
    match_ref: dict[str, Any]
    target: str = "feed"


@dataclass
class Draft:
    """The run's working state, carried from step to step. Every step returns a new copy."""

    run_id: str
    target: str
    match_ref: dict[str, Any]
    match_id: str
    home_id: str
    away_id: str
    event_log_sha256: str
    seed_sha256: str
    interpret: dict[str, Any] = field(default_factory=dict)
    turning_points: list[dict[str, Any]] = field(default_factory=list)
    sentences: list[dict[str, Any]] = field(default_factory=list)
    writer_attempts: int = 0
    writer_feedback: dict[str, list[str]] = field(default_factory=dict)
    triage: dict[str, dict[str, Any]] = field(default_factory=dict)
    confirm_rows: list[str] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)
    gate_verdicts: dict[str, str] = field(default_factory=dict)
    gate_reasons: dict[str, list[str]] = field(default_factory=dict)
    editions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Ingested:
    draft: Draft


@dataclass
class Interpreted:
    draft: Draft


@dataclass
class Analysed:
    draft: Draft


@dataclass
class WriterRetry:
    """The Fact Gate's one bounded retry: the reasons are in `draft.writer_feedback`."""

    draft: Draft


@dataclass
class Written:
    draft: Draft


@dataclass
class Triaged:
    draft: Draft


@dataclass
class Checked:
    draft: Draft


@dataclass
class Edited:
    draft: Draft


@dataclass
class Localised:
    """One fan-out branch's edition. Sent even when the edition is not available, so the fan-in
    barrier never waits on a silent branch."""

    draft: Draft
    edition: str
    available: bool
    sentences: list[dict[str, Any]] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LanguageChecked:
    draft: Draft


@dataclass
class RightsChecked:
    draft: Draft


@dataclass
class Voiced:
    draft: Draft


@dataclass
class SealedDraft:
    draft: Draft
    bundle_sha256: str
    manifest_json: str


@dataclass
class ApprovalRequest:
    """The `request_info` payload: exactly what the Teams card (or any other channel) carries."""

    run_id: str
    target: str
    bundle_sha256: str
    nonce: str
    expiry: str
    attested_scope: dict[str, list[str]]
    confirm_rows: list[str]


@dataclass
class DecisionEnvelope:
    """A channel adapter's decision, as delivered to the waiting workflow.

    `channel` and `received_at` are set by the adapter, never read from the card payload. The
    response handler turns this into `verifier.ApprovalDecision` and calls `verifier.verify`.
    """

    bundle_sha256: str
    nonce: str
    decision: str
    approver: str
    channel: str
    received_at: str
    confirmed_rows: list[str] = field(default_factory=list)


@dataclass
class PublishRequest:
    """The `publish-requests` queue message. The Publisher recomputes everything from bytes."""

    run_id: str
    target: str
    bundle_sha256: str
    approval_id: str


@dataclass
class RunOutcome:
    """A workflow output: how the run ended this time."""

    run_id: str
    status: str
    bundle_sha256: str | None
    code: str
    detail: str


CHECKPOINT_TYPES: tuple[type, ...] = (
    RunInput,
    Draft,
    Ingested,
    Interpreted,
    Analysed,
    WriterRetry,
    Written,
    Triaged,
    Checked,
    Edited,
    Localised,
    LanguageChecked,
    RightsChecked,
    Voiced,
    SealedDraft,
    ApprovalRequest,
    DecisionEnvelope,
    PublishRequest,
    RunOutcome,
)

ALLOWED_CHECKPOINT_TYPES: list[str] = [f"{t.__module__}:{t.__qualname__}" for t in CHECKPOINT_TYPES]
"""The allow-list every checkpoint storage is built with, in every process.

Why explicit: file storage refuses to save a checkpoint it could not decode under this list, and
the runner only logs that as a warning, so a missing entry would mean a silently missing
checkpoint. The worker therefore proves the pause checkpoint loads before it reports a pause.
"""

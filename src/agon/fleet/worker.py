"""The fleet worker: `python -m agon.fleet.worker [--once | --idle-exit SECONDS] [--ensure-agents]`.

Runs as the Container Apps job `job-agon-fleet` with the managed identity id-fleet, woken by KEDA
when either of its two Storage queues holds a message (infra/apps.bicep):

    decisions  {"kind": "agon.decision/1", run_id, approval_id, bundle_sha256, decision, channel,
                code}: the intake's pointer (agon.intake.VerifiedDecision), sent after the one
                verifier recorded the decision. It is never a capability: it carries no nonce.
    runs       a RunInput as JSON {run_id, match_ref, target, kind}: start the workflow of its
                kind (agon.fleet.kinds: "fulltime" runs W-fulltime, the default when kind is
                absent; "charter" runs W-charter) and drive it to its approval pause, then post
                the approval card of that kind.

Settling rules, the same as the intake's (agon.intake.drain):

* a handler returned (any outcome, refusals included): the message is deleted and one JSON line
  says what happened;
* a handler raised (an outage, a record not readable yet, a card the flow did not accept): the
  message stays and comes back after its visibility timeout;
* delivered more than `MAX_ATTEMPTS` times: copied to `fleet-poison` with its queue name and
  delivery count, then deleted. Nothing is dropped silently.

The decisions path, in order. Each check that fails settles the pointer as refused and resumes
nothing; only a missing record or an unconsumed nonce is treated as "not readable yet" (retried):

1. the pointer parses, and the run ledger knows the run;
2. a run already past AWAITING_APPROVAL is a duplicate and is not resumed again. One exception:
   an APPROVED run whose ledger lacks `publish_enqueued` (a worker died between the decision and
   the send) gets its publish request re-sent from the approval record, still without a resume;
3. the run is AWAITING_APPROVAL with a checkpoint id; that checkpoint holds exactly one pending
   ApprovalRequest, whose bundle hash equals the pointer's;
4. `approvals/<approval_id>`, written only by `verifier.verify`, names the same bundle hash and
   target, and its `nonce_sha256` equals sha256 of the request's nonce;
5. the nonce document reads "consumed". This makes "the worker never replaces a nonce" structural:
   with the nonce consumed, `verify` inside the response handler answers "already_consumed"
   before it could reach its compare-and-set;
6. the DecisionEnvelope is built from the record plus the request's nonce and the run resumes.

id-fleet holds read on approvals and create-only on nonces (infra/modules/cosmos-access.bicep);
nothing in this module writes either container.

The runs path posts the card to the Teams approval Logic App, and to the Outlook one when its URL
is configured, only for target "feed". A charter card goes to Teams only (kinds.CARD_CHANNELS).
The decisions path needs no kind: the pause checkpoint names its own workflow, and `AfEngine`
resumes the graph of that name. A sandbox run is decided on the console stand-in and never
reaches Teams. Each posted channel is noted on the run ledger (`cards_posted`), so a redelivered
runs message re-posts only what is missing, and re-sending a runs message for a run that waits
without a card posts it.

Log lines never carry a nonce, a token, a flow URL or a message body.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Protocol, Sequence

from agon.fleet import steps, stores
from agon.fleet.card import (
    CardContext,
    CardError,
    FlowPostError,
    charter_teams_payload,
    load_charter_inputs,
    load_inputs,
    outlook_payload,
    teams_payload,
)
from agon.fleet.contracts import AWAITING_DECISION, ApprovalRequest, DecisionEnvelope, PublishRequest, RunInput
from agon.fleet.deps import FleetDeps
from agon.fleet.kinds import CARD_CHANNELS, CHARTER, FULLTIME, RUN_KINDS
from agon.governance import verifier
from agon.intake import VERIFIED_KIND, VerifiedDecision

RUNS_QUEUE = "runs"
DECISIONS_QUEUE = "decisions"
POISON_QUEUE = "fleet-poison"
MAX_ATTEMPTS = 5
# A full model run (Foundry agents, Translator, Speech) takes minutes; the message must stay
# invisible for longer than that, or a second execution would start the same run.
RUN_VISIBILITY_SECONDS = 1800
DECISION_VISIBILITY_SECONDS = 600
RUN_BATCH = 1
DECISION_BATCH = 16
MAX_MESSAGE_BYTES = 48 * 1024
_RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_-]{40,}")
TERMINAL = frozenset({stores.CHANGES_REQUESTED, stores.REFUSED})


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    pop_receipt: str
    text: str
    dequeue_count: int


class QueuePort(Protocol):
    def receive(self, queue: str, max_messages: int, visibility_timeout: int) -> list[InboundMessage]: ...

    def delete(self, queue: str, message: InboundMessage) -> None: ...

    def send(self, queue: str, text: str) -> None: ...


class Retry(RuntimeError):
    """Leave the message in its queue; it comes back after the visibility timeout."""


class MessageError(ValueError):
    """The message cannot be parsed into what its queue carries."""


class PendingError(ValueError):
    """The pause checkpoint does not hold exactly one pending ApprovalRequest."""


@dataclass(frozen=True)
class WorkerOutcome:
    status: str  # refused, duplicate, awaiting_approval, ended, resumed, publish_resent
    code: str
    detail: str
    run_id: str | None = None


@dataclass(frozen=True)
class Line:
    queue: str
    message_id: str
    status: str
    code: str
    detail: str
    run_id: str | None = None


class Engine(Protocol):
    """What the worker needs from Agent Framework (agon.fleet.runner over a checkpoint storage)."""

    async def start(self, run: RunInput) -> dict[str, Any]: ...

    async def resume(self, handoff: dict[str, Any], envelope: DecisionEnvelope) -> dict[str, Any]: ...

    async def pending(self, checkpoint_id: str) -> tuple[str, ApprovalRequest]: ...


def redact(text: str) -> str:
    """Mask any long token-shaped run (a nonce, a signature) in a log detail. Hashes stay readable."""

    def mask(match: re.Match[str]) -> str:
        value = match.group(0)
        return value if re.fullmatch(r"[0-9a-f]+", value) else "<redacted>"

    return _LONG_TOKEN.sub(mask, text)


# ----------------------------------------------------------------- parsing


def _json_object(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text or len(text) > MAX_MESSAGE_BYTES:
        raise MessageError("message missing or too large")
    raw = text.strip()
    if not raw.startswith("{"):
        try:
            raw = base64.b64decode(raw.encode("ascii"), validate=True).decode("utf-8")
        except (UnicodeError, binascii.Error, ValueError) as exc:
            raise MessageError("message is neither JSON nor base64 of JSON") from exc
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise MessageError("message is not JSON") from exc
    if not isinstance(body, dict):
        raise MessageError("message must be a JSON object")
    return body


def parse_run_input(text: str) -> RunInput:
    body = _json_object(text)
    if not {"run_id", "match_ref"} <= set(body) <= {"run_id", "match_ref", "target", "kind"}:
        raise MessageError("a runs message has run_id, match_ref and optionally target and kind")
    run_id, match_ref, target = body["run_id"], body["match_ref"], body.get("target", "feed")
    kind = body.get("kind", FULLTIME)
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise MessageError("run_id must be 1 to 128 characters from [A-Za-z0-9_.:-]")
    if not isinstance(match_ref, dict):
        raise MessageError("match_ref must be an object")
    if target not in verifier.TARGETS:
        raise MessageError(f"target must be one of {list(verifier.TARGETS)}")
    if kind not in RUN_KINDS:
        raise MessageError(f"kind must be one of {list(RUN_KINDS)}")
    return RunInput(run_id=run_id, match_ref=match_ref, target=target, kind=kind)


def parse_pointer(text: str) -> VerifiedDecision:
    body = _json_object(text)
    expected = set(VerifiedDecision.__dataclass_fields__)
    if set(body) != expected:
        raise MessageError(f"a decisions pointer has exactly the keys {sorted(expected)}")
    if not all(isinstance(body[k], str) for k in expected):
        raise MessageError("every pointer field is a string")
    pointer = VerifiedDecision(**body)
    if pointer.kind != VERIFIED_KIND:
        raise MessageError(f"pointer kind must be {VERIFIED_KIND}")
    if not _RUN_ID.match(pointer.run_id):
        raise MessageError("run_id must be 1 to 128 characters from [A-Za-z0-9_.:-]")
    if not _HEX32.match(pointer.approval_id) or not _HEX64.match(pointer.bundle_sha256):
        raise MessageError("approval_id is 32 hex and bundle_sha256 is 64 hex characters")
    if pointer.decision not in verifier.DECISIONS or pointer.channel not in verifier.CHANNELS:
        raise MessageError("unknown decision or channel")
    return pointer


def pending_approval(checkpoint: Any) -> tuple[str, ApprovalRequest]:
    """The one pending ApprovalRequest an Agent Framework pause checkpoint holds, with its id."""
    events = dict(getattr(checkpoint, "pending_request_info_events", None) or {})
    found = [(rid, getattr(ev, "data", None)) for rid, ev in events.items() if isinstance(getattr(ev, "data", None), ApprovalRequest)]
    if len(found) != 1 or len(events) != 1:
        raise PendingError(f"expected one pending ApprovalRequest, the checkpoint holds {len(events)} requests")
    return found[0]


# ----------------------------------------------------------------- the worker


@dataclass
class FleetWorker:
    deps: FleetDeps
    engine: Engine
    context: CardContext
    teams: Any = None  # FlowPoster for la-agon-teams-approval, or None
    outlook: Any = None  # FlowPoster for la-agon-outlook-approval, or None
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- runs

    async def handle_run(self, text: str) -> WorkerOutcome:
        try:
            run = parse_run_input(text)
        except MessageError as exc:
            return WorkerOutcome("refused", "malformed_run", str(exc))
        report = await self.engine.start(run)
        if report["state"] == "awaiting_approval":
            request = ApprovalRequest(**report["card"])
            posted = self._post_cards(run, request)
            return WorkerOutcome("awaiting_approval", posted, f"paused at checkpoint {report['checkpoint_id']}", run.run_id)
        outcomes = report.get("outcomes", [])
        if any(o.get("status") == AWAITING_DECISION for o in outcomes):
            # A redelivered or re-sent runs message for a run that already waits: post what is missing.
            record = self.deps.ledger.record(run.run_id) or {}
            checkpoint_id = record.get("checkpoint_id")
            if not checkpoint_id:
                return WorkerOutcome("refused", "no_checkpoint", "the run waits but its ledger names no checkpoint", run.run_id)
            try:
                _, request = await self.engine.pending(str(checkpoint_id))
            except PendingError as exc:
                return WorkerOutcome("refused", "no_pending_request", str(exc), run.run_id)
            posted = self._post_cards(run, request)
            return WorkerOutcome("awaiting_approval", posted, f"already paused at checkpoint {checkpoint_id}", run.run_id)
        first = outcomes[0] if outcomes else {"status": "ended", "code": "no_outcome", "detail": ""}
        return WorkerOutcome("ended", str(first.get("status")), f"{first.get('code')}: {first.get('detail')}", run.run_id)

    def _post_cards(self, run: RunInput, request: ApprovalRequest) -> str:
        """Post the approval card on every configured officer channel not yet posted. Returns a code."""
        if run.target != "feed":
            return "sandbox_console_only"
        kind = getattr(run, "kind", FULLTIME)
        wanted = CARD_CHANNELS.get(kind, ())
        channels = [
            (name, poster) for name, poster in (("teams", self.teams), ("outlook", self.outlook)) if poster is not None and name in wanted
        ]
        if not channels:
            return "no_card_channel"
        record = self.deps.ledger.record(run.run_id) or {}
        done = set(record.get("cards_posted", []))
        missing = [(name, poster) for name, poster in channels if name not in done]
        if not missing:
            return "cards_already_posted"
        loader = load_charter_inputs if kind == CHARTER else load_inputs
        try:
            inputs: Any = loader(self.deps.store, self.deps.artifacts, self.deps.matches.load(run.match_ref), request.bundle_sha256)
        except CardError as exc:
            raise Retry(f"card not built: {exc}") from None
        builders: dict[str, Any] = {"teams": charter_teams_payload} if kind == CHARTER else {"teams": teams_payload, "outlook": outlook_payload}
        failures: list[str] = []
        for name, poster in missing:
            payload = builders[name](request, inputs, run.match_ref, self.context)
            try:
                poster.post(payload)
            except FlowPostError as exc:
                failures.append(str(exc))
                continue
            done.add(name)
            self.deps.ledger.note(run.run_id, cards_posted=sorted(done))
        if failures:
            raise Retry("; ".join(failures))
        return "cards_posted:" + ",".join(sorted(done))

    # ------------------------------------------------------------- decisions

    async def handle_decision(self, text: str) -> WorkerOutcome:
        try:
            pointer = parse_pointer(text)
        except MessageError as exc:
            return WorkerOutcome("refused", "malformed_pointer", str(exc))
        run_id = pointer.run_id

        def refused(code: str, detail: str) -> WorkerOutcome:
            return WorkerOutcome("refused", code, detail, run_id)

        record = self.deps.ledger.record(run_id)
        if record is None:
            return refused("unknown_run", "the run ledger has no such run")
        status = record.get("status")
        if status == stores.APPROVED:
            if record.get("publish_enqueued"):
                return WorkerOutcome("duplicate", "already_resumed", "the run is APPROVED and its publish request was sent", run_id)
            return self._resend_publish(pointer, record)
        if status in TERMINAL:
            return WorkerOutcome("duplicate", "already_decided", f"the run already ended as {status}", run_id)
        checkpoint_id = record.get("checkpoint_id")
        if status != stores.AWAITING_APPROVAL or not checkpoint_id:
            return refused("not_awaiting_approval", f"the run is {status}, not AWAITING_APPROVAL with a checkpoint")
        try:
            request_id, request = await self.engine.pending(str(checkpoint_id))
        except PendingError as exc:
            return refused("no_pending_request", str(exc))
        if request.run_id != run_id:
            return refused("checkpoint_run_mismatch", "the pause checkpoint belongs to a different run")
        if pointer.bundle_sha256 != request.bundle_sha256:
            return refused("pointer_hash_mismatch", "the pointer names a different bundle than the pending request")
        approval = self._approval_record(pointer.approval_id)
        problem = record_problem(approval, request, pointer)
        if problem is not None:
            return refused(*problem)
        nonce_doc = self.deps.store.read(verifier.NONCES, request.nonce)
        if nonce_doc is None or nonce_doc.body.get("status") != "consumed":
            raise Retry("the approval record exists but the nonce does not read consumed yet")
        envelope = steps.envelope_from_record(approval, request.nonce)
        handoff = {"run_id": run_id, "checkpoint_id": str(checkpoint_id), "request_id": request_id}
        report = await self.engine.resume(handoff, envelope)
        outcomes = report.get("outcomes", [])
        if not outcomes:
            raise Retry("the resume produced no outcome")
        first = outcomes[0]
        return WorkerOutcome("resumed", str(first.get("status")), f"{first.get('code')}: {first.get('detail')}", run_id)

    def _approval_record(self, approval_id: str) -> dict[str, Any]:
        doc = self.deps.store.read(verifier.APPROVALS, approval_id)
        if doc is None:
            raise Retry("the approval record is not readable yet")
        return dict(doc.body)

    def _resend_publish(self, pointer: VerifiedDecision, record: Mapping[str, Any]) -> WorkerOutcome:
        """APPROVED without `publish_enqueued`: the send was lost; re-send it from the record, no resume."""
        approval_id = record.get("approval_id")
        if approval_id != pointer.approval_id:
            return WorkerOutcome("duplicate", "other_approval", "the run was approved under a different approval", pointer.run_id)
        approval = self._approval_record(pointer.approval_id)
        if (
            approval.get("decision") != "approve"
            or approval.get("bundle_sha256") != pointer.bundle_sha256
            or approval.get("bundle_sha256") != record.get("bundle_sha256")
            or approval.get("target") not in verifier.TARGETS
        ):
            return WorkerOutcome("refused", "record_mismatch", "the approval record does not back a publish of this bundle", pointer.run_id)
        request = PublishRequest(
            run_id=pointer.run_id, target=str(approval["target"]), bundle_sha256=pointer.bundle_sha256, approval_id=pointer.approval_id
        )
        self.deps.publish_queue.enqueue(request)
        self.deps.ledger.note(pointer.run_id, publish_enqueued=pointer.approval_id)
        return WorkerOutcome("publish_resent", "publish_requested", f"approval {pointer.approval_id}", pointer.run_id)


def record_problem(approval: Mapping[str, Any], request: ApprovalRequest, pointer: VerifiedDecision) -> tuple[str, str] | None:
    """Why an approval record cannot resume this pending request, or None when it can."""
    if approval.get("approval_id") != pointer.approval_id:
        return "record_id_mismatch", "the approval record does not carry the pointer's approval id"
    if approval.get("bundle_sha256") != request.bundle_sha256:
        return "record_hash_mismatch", "the approval record names a different bundle than the pending request"
    if approval.get("nonce_sha256") != hashlib.sha256(request.nonce.encode("utf-8")).hexdigest():
        return "record_nonce_mismatch", "the approval record was not made for this request's nonce"
    if approval.get("target") != request.target:
        return "record_target_mismatch", "the approval record is for a different target"
    if approval.get("decision") not in verifier.DECISIONS or approval.get("decision") != pointer.decision:
        return "record_decision_mismatch", "the approval record's decision differs from the pointer"
    for key in ("approver", "channel", "received_at"):
        if not isinstance(approval.get(key), str) or not approval.get(key):
            return "record_incomplete", f"the approval record has no {key}"
    return None


# ----------------------------------------------------------------- the drain


async def drain_once(port: QueuePort, worker: FleetWorker, *, log: Callable[[Line], None] = lambda line: None) -> list[Line]:
    """Decisions first (an officer is waiting), then at most one run."""
    lines: list[Line] = []

    def record(line: Line) -> None:
        lines.append(line)
        log(line)

    plan: tuple[tuple[str, int, int, Callable[[str], Awaitable[WorkerOutcome]]], ...] = (
        (DECISIONS_QUEUE, DECISION_BATCH, DECISION_VISIBILITY_SECONDS, worker.handle_decision),
        (RUNS_QUEUE, RUN_BATCH, RUN_VISIBILITY_SECONDS, worker.handle_run),
    )
    for queue, batch, visibility, handler in plan:
        for message in port.receive(queue, batch, visibility):
            if message.dequeue_count > MAX_ATTEMPTS:
                poison = {"queue": queue, "message_id": message.message_id, "dequeue_count": message.dequeue_count, "text": message.text}
                port.send(POISON_QUEUE, json.dumps(poison, sort_keys=True, separators=(",", ":")))
                port.delete(queue, message)
                record(Line(queue, message.message_id, "poisoned", "max_attempts", f"moved to {POISON_QUEUE} after {message.dequeue_count} deliveries"))
                continue
            try:
                outcome = await handler(message.text)
            except Retry as exc:
                record(Line(queue, message.message_id, "retry", "left_in_queue", redact(str(exc))[:300]))
                continue
            except Exception as exc:  # noqa: BLE001 - any failure leaves the message for a retry
                record(Line(queue, message.message_id, "error", type(exc).__name__, redact(str(exc))[:300]))
                continue
            try:
                port.delete(queue, message)
            except Exception as exc:  # noqa: BLE001 - a redelivery is safe: every path above is idempotent
                record(Line(queue, message.message_id, "error", "delete_failed", f"{outcome.status}; {type(exc).__name__}"))
                continue
            record(Line(queue, message.message_id, outcome.status, outcome.code, redact(outcome.detail)[:300], outcome.run_id))
    return lines


# ----------------------------------------------------------------- configuration and Azure wiring


class ConfigError(ValueError):
    """A required setting is missing or malformed."""


@dataclass(frozen=True)
class WorkerConfig:
    queue_account_url: str
    blob_account_url: str
    cosmos_endpoint: str
    cosmos_database: str
    client_id: str
    foundry_project_endpoint: str
    foundry_structured_model: str
    foundry_agent_models: dict[str, str]
    foundry_agent_versions: dict[str, str]
    translator_endpoint: str
    speech_tts_url: str
    speech_resource_id: str
    feed_base_url: str
    sandbox_base_url: str
    site_url: str
    teams_flow_url: str
    outlook_flow_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, ensure_agents: bool) -> "WorkerConfig":
        from agon.agents.ports import PROMPT_AGENT_ROLES

        required = (
            "AGON_QUEUE_ACCOUNT_URL", "AGON_BLOB_ACCOUNT_URL", "AGON_COSMOS_ENDPOINT", "AGON_FOUNDRY_PROJECT_ENDPOINT",
            "AGON_FOUNDRY_STRUCTURED_MODEL", "AGON_TRANSLATOR_ENDPOINT", "AGON_SPEECH_ENDPOINT", "AGON_SPEECH_RESOURCE_ID",
            "AGON_FEED_BASE_URL",
        ) + (("AGON_FOUNDRY_AGENT_MODELS",) if ensure_agents else ("AGON_FOUNDRY_AGENT_VERSIONS",))
        missing = [k for k in required if not env.get(k)]
        if missing:
            raise ConfigError(f"missing settings: {missing}")

        def role_map(name: str) -> dict[str, str]:
            raw = env.get(name, "")
            if not raw:
                return {}
            try:
                value = json.loads(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} is not JSON") from exc
            if not isinstance(value, dict) or set(value) != set(PROMPT_AGENT_ROLES):
                raise ConfigError(f"{name} must map exactly {list(PROMPT_AGENT_ROLES)}")
            return {k: str(v) for k, v in value.items()}

        for name in ("AGON_TEAMS_FLOW_URL", "AGON_OUTLOOK_FLOW_URL"):
            if env.get(name):
                from agon.fleet.card import validate_flow_url

                try:
                    validate_flow_url(env[name])
                except ValueError as exc:
                    raise ConfigError(f"{name}: {exc}") from None
        speech = env["AGON_SPEECH_ENDPOINT"].rstrip("/")
        return cls(
            queue_account_url=env["AGON_QUEUE_ACCOUNT_URL"],
            blob_account_url=env["AGON_BLOB_ACCOUNT_URL"],
            cosmos_endpoint=env["AGON_COSMOS_ENDPOINT"],
            cosmos_database=env.get("AGON_COSMOS_DATABASE") or "agon",
            client_id=env.get("AZURE_CLIENT_ID", ""),
            foundry_project_endpoint=env["AGON_FOUNDRY_PROJECT_ENDPOINT"],
            foundry_structured_model=env["AGON_FOUNDRY_STRUCTURED_MODEL"],
            foundry_agent_models=role_map("AGON_FOUNDRY_AGENT_MODELS"),
            foundry_agent_versions=role_map("AGON_FOUNDRY_AGENT_VERSIONS"),
            translator_endpoint=env["AGON_TRANSLATOR_ENDPOINT"],
            speech_tts_url=env.get("AGON_SPEECH_TTS_URL") or f"{speech}/tts/cognitiveservices/v1",
            speech_resource_id=env["AGON_SPEECH_RESOURCE_ID"],
            feed_base_url=env["AGON_FEED_BASE_URL"],
            sandbox_base_url=env.get("AGON_SANDBOX_BASE_URL", ""),
            site_url=env.get("AGON_SITE_URL", ""),
            teams_flow_url=env.get("AGON_TEAMS_FLOW_URL", ""),
            outlook_flow_url=env.get("AGON_OUTLOOK_FLOW_URL", ""),
        )


class AfEngine:
    """`Engine` over agon.fleet.runner and one checkpoint storage opened once per process.

    A fresh `GuardedCheckpointStorage` wraps the shared storage for every run, so a save failure
    in one run can never block the next run's pause proof.
    """

    def __init__(self, deps: FleetDeps, storage: Any) -> None:
        self.deps = deps
        self.storage = storage

    def _guarded(self) -> Any:
        from agon.fleet.checkpoints import GuardedCheckpointStorage

        return GuardedCheckpointStorage(self.storage)

    async def start(self, run: RunInput) -> dict[str, Any]:
        from agon.fleet.runner import start_run

        return await start_run(self.deps, self._guarded(), run)

    async def resume(self, handoff: dict[str, Any], envelope: DecisionEnvelope) -> dict[str, Any]:
        """Resume the graph the pause checkpoint names (W-fulltime or W-charter), never a guess."""
        from agon.fleet.runner import resume_run

        if "workflow_name" not in handoff:
            checkpoint = await self.storage.load(handoff["checkpoint_id"])
            handoff = {**handoff, "workflow_name": str(checkpoint.workflow_name)}
        return await resume_run(self.deps, self._guarded(), handoff, envelope)

    async def pending(self, checkpoint_id: str) -> tuple[str, ApprovalRequest]:
        return pending_approval(await self.storage.load(checkpoint_id))


async def foundry_model(config: WorkerConfig, credential: Any, *, ensure_agents: bool, log: Callable[[str], None]) -> Any:
    """FoundryModelPort with verified prompt provenance. Raises PromptProvenanceError (fail closed)."""
    from agon.agents.foundry import FoundryModelPort, FoundrySettings

    versions = dict(config.foundry_agent_versions)
    if ensure_agents:
        from azure.ai.projects.aio import AIProjectClient

        from agon.agents.publish import ensure_prompt_agents

        project = AIProjectClient(endpoint=config.foundry_project_endpoint, credential=credential)
        try:
            refs = await ensure_prompt_agents(project, config.foundry_project_endpoint, credential, config.foundry_agent_models)
        finally:
            await project.close()
        versions = {ref.role: ref.version for ref in refs}
        for ref in refs:
            log(json.dumps({"agon": "fleet", "startup": "prompt_agent", **asdict(ref)}, sort_keys=True))
    settings = FoundrySettings(
        project_endpoint=config.foundry_project_endpoint,
        agent_versions=versions,
        structured_model=config.foundry_structured_model,
    )
    port = FoundryModelPort(settings, credential)
    verified = await port.verify_deployed()
    log(json.dumps({"agon": "fleet", "startup": "prompt_provenance_verified", "sha256": verified}, sort_keys=True))
    return port


@contextlib.asynccontextmanager
async def open_azure_worker(
    config: WorkerConfig, *, ensure_agents: bool, log: Callable[[str], None]
) -> AsyncIterator[tuple[FleetWorker, QueuePort]]:
    """Build every real port from the configuration, verify the prompts, open the checkpoints once."""
    from agon.agents.speech import AzureSpeech, voices_from_env
    from agon.agents.translator import AzureTranslator
    from agon.fleet import azure_ports
    from agon.fleet.card import FlowPoster
    from agon.fleet.checkpoints import open_cosmos_storage
    from agon.fleet.interpret_port import default_interpret
    from agon.fleet.sources import SynthMatchSource
    from agon.governance.store_cosmos import connect

    client_id = config.client_id or None
    sync_cred = azure_ports.sync_credential(client_id)
    async_cred = azure_ports.async_credential(client_id)
    try:
        model = await foundry_model(config, async_cred, ensure_agents=ensure_agents, log=log)
        voices = voices_from_env()
        deployment_ids = {
            **model.deployment_ids(),
            "translator": "azure-translator-nmt/text-v3.0",
            "speech": "azure-speech-rest/" + ",".join(f"{k}={v}" for k, v in sorted(voices.items())),
        }
        deps = FleetDeps(
            model=model,
            translator=AzureTranslator(config.translator_endpoint, azure_ports.token_getter(sync_cred, azure_ports.COGNITIVE_SCOPE)),
            speech=AzureSpeech(config.speech_tts_url, azure_ports.speech_auth_header(sync_cred, config.speech_resource_id), voices=voices),
            interpret=default_interpret(),
            matches=SynthMatchSource(),
            artifacts=azure_ports.BlobArtifactStore.connect(config.blob_account_url, sync_cred),
            store=connect(config.cosmos_endpoint, config.cosmos_database, sync_cred),
            publish_queue=azure_ports.QueuePublishQueue.connect(config.queue_account_url, sync_cred),
            deployment_ids=deployment_ids,
        )
        arm = azure_ports.token_getter(sync_cred, azure_ports.ARM_SCOPE)
        teams = FlowPoster("teams", config.teams_flow_url, arm) if config.teams_flow_url else None
        outlook = FlowPoster("outlook", config.outlook_flow_url, arm) if config.outlook_flow_url else None
        context = CardContext(feed_base_url=config.feed_base_url, sandbox_base_url=config.sandbox_base_url, site_url=config.site_url)
        log(json.dumps({"agon": "fleet", "startup": "card_channels", "teams": teams is not None, "outlook": outlook is not None}, sort_keys=True))
        async with open_cosmos_storage(config.cosmos_endpoint, database=config.cosmos_database, credential=async_cred) as cosmos:
            worker = FleetWorker(deps=deps, engine=AfEngine(deps, cosmos), context=context, teams=teams, outlook=outlook)
            yield worker, azure_ports.StorageQueuePort(config.queue_account_url, sync_cred)
    finally:
        await async_cred.close()


def _print(line: Line) -> None:
    print(json.dumps({"agon": "fleet", **asdict(line)}, sort_keys=True), flush=True)


async def serve(config: WorkerConfig, args: argparse.Namespace) -> int:
    from agon.agents.foundry import PromptProvenanceError
    from agon.agents.publish import PublishError
    from agon.fleet.checkpoints import CosmosRoleMissing

    def say(text: str) -> None:
        print(text, flush=True)

    try:
        async with open_azure_worker(config, ensure_agents=args.ensure_agents, log=say) as (worker, port):
            if args.once:
                await drain_once(port, worker, log=_print)
                return 0
            idle_since = time.monotonic()
            while True:
                if await drain_once(port, worker, log=_print):
                    idle_since = time.monotonic()
                elif args.idle_exit and time.monotonic() - idle_since >= args.idle_exit:
                    return 0
                await asyncio.sleep(args.poll_seconds)
    except (PromptProvenanceError, PublishError) as exc:
        code = "prompt_provenance" if isinstance(exc, PromptProvenanceError) else "prompt_agent_publish"
        say(json.dumps({"agon": "fleet", "startup": "refused", "code": code, "detail": redact(str(exc))[:600]}))
        return 3
    except CosmosRoleMissing as exc:
        say(json.dumps({"agon": "fleet", "startup": "refused", "code": "cosmos_role_missing", "detail": str(exc)[:300]}))
        return 5


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drain the runs and decisions queues through W-fulltime and W-charter.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="drain each queue once and exit")
    mode.add_argument("--idle-exit", type=int, default=0, help="loop, and exit after this many seconds with no message")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument(
        "--ensure-agents",
        action="store_true",
        help="find or create the Foundry prompt agent versions for the pinned prompts (agon.agents.publish) before verifying them",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = WorkerConfig.from_env(os.environ, ensure_agents=args.ensure_agents)
    except ConfigError as exc:
        print(f"agon.fleet.worker: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(serve(config, args))


if __name__ == "__main__":
    sys.exit(main())

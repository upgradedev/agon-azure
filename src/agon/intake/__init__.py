"""Decision intake: one Storage queue per officer channel, drained into the one verifier.

The Logic Apps that carry the Teams card and the Outlook approval email never call an HTTP
endpoint. Each writes the connector's response, unchanged, to its own queue with its managed
identity (`id-logicapp` holds Storage Queue Data Message Sender on exactly these queues, nothing
else, see infra/rbac.bicep). This module drains those queues:

    decisions-teams    -> channels.teams.handle    -> verifier.verify
    decisions-outlook  -> channels.outlook.handle  -> verifier.verify

Rules, each pinned by the M16 audit (`agon.governance.routes.audit_intake`) and by tests/intake:

* The channel is taken from the queue the message was read from (`ROUTES`), never from the
  message body. Queue RBAC is the trust boundary for the channel and the responder field.
* Every route reaches the verifier only through its channel adapter's `handle`, so the adapter's
  own rules (channel and `received_at` set by the adapter, refusal-only results) still hold.
* This module never builds a `VerifyResult` and never writes the store. Its one store call is a
  read of an existing approval record in `_replay`, which can re-send a pointer for a decision
  `verify` already recorded and can never create one.
* The console stand-in and the signed link are HTTP routes on the api, not queues; no anonymous
  path can write to these queues.

Outcomes of one message:

* verified (approve or changes): a `VerifiedDecision` pointer goes to the `decisions` queue for
  the waiting workflow, then the inbound message is deleted;
* refused by the adapter or the verifier: the inbound message is deleted and the refusal logged.
  A refusal never burns the nonce (see verifier), so the officer can still decide on another
  channel;
* redelivered after a crash between `verify` and the send: `verify` answers "already_consumed",
  and if this route's own approval record exists the pointer is sent again ("verified_replay").
  A crash between the nonce write and the approval record inside `verify` ends the same way: the
  redelivery's own `verify` completes the record from the consumed nonce first (same channel and
  bundle only; see the verifier);
* any exception (store or queue outage): the message is left in place and comes back after its
  visibility timeout. After `MAX_ATTEMPTS` deliveries it moves to `intake-poison`, never dropped.

Message body on the inbound queues: base64 of UTF-8 JSON, written by the Logic App,

    {"run_id": "<run id from the card>", "payload": <the channel adapter's payload>}

`run_id` is only a lookup hint for the waiting workflow; the worker re-checks the approval record
against its own pending request before it resumes, so a wrong `run_id` resumes nothing.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from types import ModuleType
from typing import Any, Protocol

from agon.governance import verifier
from agon.governance.channels import outlook, teams

# Inbound queue -> channel adapter. The adapter's CHANNEL must equal the key's channel; the audit
# checks it. Adding a queue here without an adapter case in tests/intake fails the tests.
ROUTES: dict[str, ModuleType] = {
    "decisions-teams": teams,
    "decisions-outlook": outlook,
}
VERIFIED_QUEUE = "decisions"
POISON_QUEUE = "intake-poison"
MAX_ATTEMPTS = 5
MAX_MESSAGE_BYTES = 48 * 1024
VERIFIED_KIND = "agon.decision/1"
_RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class IntakeError(ValueError):
    """The inbound message cannot be decoded into a run id and a channel payload."""


@dataclass(frozen=True)
class VerifiedDecision:
    """The `decisions` queue message: a pointer to the approval record, never a capability.

    It carries no nonce. The waiting workflow loads `approvals/<approval_id>`, checks that the
    record's bundle hash and nonce hash match its own pending request, and only then resumes.
    """

    kind: str
    run_id: str
    approval_id: str
    bundle_sha256: str
    decision: str
    channel: str
    code: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class IntakeOutcome:
    queue: str
    status: str  # "verified", "refused", "undecodable", "unknown_queue"
    code: str
    detail: str
    run_id: str | None = None
    forwarded: VerifiedDecision | None = None


class Sender(Protocol):
    def send(self, queue: str, text: str) -> None: ...


def encode_message(run_id: str, payload: dict[str, Any]) -> str:
    """What the Logic App writes. Used by tests and by the local driver; production is the flow."""
    raw = json.dumps({"run_id": run_id, "payload": payload}, sort_keys=True, separators=(",", ":"))
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def decode_message(text: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(text, str) or not text or len(text) > MAX_MESSAGE_BYTES:
        raise IntakeError("message missing or too large")
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, binascii.Error, ValueError) as exc:
        raise IntakeError("message is not base64 of UTF-8 JSON") from exc
    if not isinstance(body, dict) or set(body) != {"run_id", "payload"}:
        raise IntakeError("message must have exactly the keys run_id and payload")
    run_id, payload = body["run_id"], body["payload"]
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise IntakeError("run_id must be 1 to 128 characters from [A-Za-z0-9_.:-]")
    if not isinstance(payload, dict):
        raise IntakeError("payload must be an object")
    return run_id, payload


def handle_message(queue: str, text: str, store: Any, now: datetime, sender: Sender) -> IntakeOutcome:
    """Process one inbound message. Raises only on infrastructure failure (caller retries)."""
    adapter = ROUTES.get(queue)
    if adapter is None:
        return IntakeOutcome(queue=queue, status="unknown_queue", code="unknown_queue", detail=f"no route for queue {queue!r}")
    try:
        run_id, payload = decode_message(text)
    except IntakeError as exc:
        return IntakeOutcome(queue=queue, status="undecodable", code="malformed_message", detail=str(exc))

    result = adapter.handle(payload, store, now)

    if not result.ok:
        if result.code == "already_consumed":
            replayed = _replay(adapter, payload, store, now, run_id)
            if replayed is not None:
                sender.send(VERIFIED_QUEUE, replayed.to_json())
                return IntakeOutcome(
                    queue=queue, status="verified_replay", code=replayed.code,
                    detail="this message was verified before; its pointer is sent again", run_id=run_id, forwarded=replayed,
                )
        return IntakeOutcome(queue=queue, status="refused", code=result.code, detail=result.detail, run_id=run_id)
    forwarded = VerifiedDecision(
        kind=VERIFIED_KIND,
        run_id=run_id,
        approval_id=str(result.approval_id),
        bundle_sha256=str(result.bundle_sha256),
        decision=str(result.decision),
        channel=str(result.channel),
        code=result.code,
    )
    sender.send(VERIFIED_QUEUE, forwarded.to_json())
    return IntakeOutcome(queue=queue, status="verified", code=result.code, detail=result.detail, run_id=run_id, forwarded=forwarded)


def _replay(adapter: ModuleType, payload: dict[str, Any], store: Any, now: datetime, run_id: str) -> VerifiedDecision | None:
    """Redelivery after a crash between verify and send: re-send the pointer, approve nothing.

    If the process died after `verify` consumed the nonce and before the pointer reached the
    `decisions` queue, the redelivered message now meets "already_consumed". The approval record
    is the only evidence used here (written by that earlier `verify`, or by this redelivery's own
    `verify` when the first pass stopped between the nonce write and the record): it must exist,
    name this route's channel and the same bundle hash. A message for a nonce another channel
    consumed, or any message without a record, stays refused. The worker deduplicates pointers by
    approval id. This function only reads; it never writes the store.
    """
    try:
        decision = adapter.parse(payload, received_at=now)
    except ValueError:
        return None
    record = store.read(verifier.APPROVALS, verifier.approval_id_for(decision.nonce))
    if record is None:
        return None
    body = record.body
    if body.get("channel") != adapter.CHANNEL or body.get("bundle_sha256") != decision.bundle_sha256:
        return None
    return VerifiedDecision(
        kind=VERIFIED_KIND,
        run_id=run_id,
        approval_id=str(body.get("approval_id")),
        bundle_sha256=str(body.get("bundle_sha256")),
        decision=str(body.get("decision")),
        channel=str(body.get("channel")),
        code="approved" if body.get("decision") == "approve" else "changes_requested",
    )

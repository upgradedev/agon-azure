"""Teams Adaptive Card channel: the response of the Logic Apps "post card and wait" action.

Payload, forwarded unchanged by the Logic App (infra/flows.bicep, la-agon-teams-approval) to the
decisions-teams queue. Gate T confirmed the responder and data fields against a live response;
the `dest:` toggles follow the card that flow renders:

    {
      "responder": {"userPrincipalName": "...", "email": "...", "objectId": "..."},
      "data": {
        "decision": "approve" | "changes" | "request_changes",
        "bundle_sha256": "<64 hex>",
        "nonce": "<nonce>",
        "confirm:<row_id>": "true" | "false",
        "dest:<destination id>": "true" | "false"
      }
    }

One `dest:<id>` toggle per offered destination (the flow pre-ticks each). A toggle reading "true"
is a ticked destination; the verifier refuses any id the sealed bundle does not offer, and an
approve with nothing ticked. A "changes" response carries no destinations: its toggles are
ignored here.

The approver identity is the responder field the Teams connector fills in; that field is a stated
trust boundary (the endpoint trusts the Logic App's managed identity to forward it unchanged).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agon.governance import verifier
from agon.governance.channels._common import PayloadError, refused_payload, require_dict, require_str

CHANNEL = "teams"
CONFIRM_PREFIX = "confirm:"
DESTINATION_PREFIX = "dest:"
DECISION_MAP = {"approve": "approve", "changes": "changes", "request_changes": "changes"}


def _ticked(data: dict[str, Any], prefix: str) -> frozenset[str]:
    return frozenset(
        key[len(prefix):]
        for key, value in data.items()
        if key.startswith(prefix) and str(value).strip().lower() == "true"
    )


def parse(payload: Any, received_at: datetime) -> verifier.ApprovalDecision:
    data = require_dict(payload, "data", "payload")
    responder = require_dict(payload, "responder", "payload")
    raw_decision = require_str(data, "decision", "data")
    if raw_decision not in DECISION_MAP:
        raise PayloadError(f"unknown card decision {raw_decision!r}")
    approver = responder.get("userPrincipalName") or responder.get("email")
    if not isinstance(approver, str) or not approver.strip():
        raise PayloadError("responder has no userPrincipalName or email")
    decision = DECISION_MAP[raw_decision]
    return verifier.ApprovalDecision(
        bundle_sha256=require_str(data, "bundle_sha256", "data"),
        nonce=require_str(data, "nonce", "data"),
        decision=decision,
        approver=approver,
        channel=CHANNEL,
        received_at=received_at,
        confirmed_rows=_ticked(data, CONFIRM_PREFIX),
        destinations=_ticked(data, DESTINATION_PREFIX) if decision == "approve" else frozenset(),
    )


def handle(payload: Any, store: Any, now: datetime) -> verifier.VerifyResult:
    try:
        decision = parse(payload, received_at=now)
    except PayloadError as exc:
        return refused_payload(CHANNEL, str(exc))
    return verifier.verify(decision, store, now)

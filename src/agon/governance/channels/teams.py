"""Teams Adaptive Card channel: the response of the Logic Apps "post card and wait" action.

ASSUMED payload, forwarded unchanged by the Logic App to the decisions endpoint (to be confirmed
by gate T against a live response):

    {
      "responder": {"userPrincipalName": "...", "email": "...", "objectId": "..."},
      "data": {
        "decision": "approve" | "changes" | "request_changes",
        "bundle_sha256": "<64 hex>",
        "nonce": "<nonce>",
        "confirm:<row_id>": "true" | "false"
      }
    }

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
DECISION_MAP = {"approve": "approve", "changes": "changes", "request_changes": "changes"}


def parse(payload: Any, received_at: datetime) -> verifier.ApprovalDecision:
    data = require_dict(payload, "data", "payload")
    responder = require_dict(payload, "responder", "payload")
    raw_decision = require_str(data, "decision", "data")
    if raw_decision not in DECISION_MAP:
        raise PayloadError(f"unknown card decision {raw_decision!r}")
    approver = responder.get("userPrincipalName") or responder.get("email")
    if not isinstance(approver, str) or not approver.strip():
        raise PayloadError("responder has no userPrincipalName or email")
    confirmed = frozenset(
        key[len(CONFIRM_PREFIX):]
        for key, value in data.items()
        if key.startswith(CONFIRM_PREFIX) and str(value).strip().lower() == "true"
    )
    return verifier.ApprovalDecision(
        bundle_sha256=require_str(data, "bundle_sha256", "data"),
        nonce=require_str(data, "nonce", "data"),
        decision=DECISION_MAP[raw_decision],
        approver=approver,
        channel=CHANNEL,
        received_at=received_at,
        confirmed_rows=confirmed,
    )


def handle(payload: Any, store: Any, now: datetime) -> verifier.VerifyResult:
    try:
        decision = parse(payload, received_at=now)
    except PayloadError as exc:
        return refused_payload(CHANNEL, str(exc))
    return verifier.verify(decision, store, now)

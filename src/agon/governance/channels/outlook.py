"""Outlook approval email channel: the Office 365 Outlook "Send approval email" response.

The connector's response carries only the chosen option and the responder. The Logic App that sent
the email knows which bundle and nonce it was for and forwards them alongside. ASSUMED payload,
to be confirmed against a live response:

    {
      "bundle_sha256": "<64 hex>",
      "nonce": "<nonce>",
      "confirm_rows": ["s3", "s7"],
      "response": {"SelectedOption": "<one of OPTIONS>", "UserEmailAddress": "officer@..."}
    }

An email cannot carry tick boxes, so the options name the Confirm rows in their own words. Only
"Approve and confirm flagged sentences" confirms them; plain "Approve" confirms nothing and is
refused by the verifier when the card had Confirm rows. Any other option text is refused here; it
never defaults to approve.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agon.governance import verifier
from agon.governance.channels._common import PayloadError, refused_payload, require_dict, require_str

CHANNEL = "outlook"
OPTION_APPROVE_CONFIRM = "Approve and confirm flagged sentences"
OPTION_APPROVE = "Approve"
OPTION_CHANGES = "Request changes"
OPTIONS: tuple[str, ...] = (OPTION_APPROVE_CONFIRM, OPTION_APPROVE, OPTION_CHANGES)


def parse(payload: Any, received_at: datetime) -> verifier.ApprovalDecision:
    response = require_dict(payload, "response", "payload")
    option = response.get("SelectedOption")  # compared raw: no trimming, no case folding
    if not isinstance(option, str) or option not in OPTIONS:
        raise PayloadError(f"unknown approval option {option!r}")
    rows = payload.get("confirm_rows", [])
    if not isinstance(rows, list) or not all(isinstance(r, str) for r in rows):
        raise PayloadError("payload.confirm_rows must be a list of strings")
    confirmed = frozenset(rows) if option == OPTION_APPROVE_CONFIRM else frozenset()
    return verifier.ApprovalDecision(
        bundle_sha256=require_str(payload, "bundle_sha256", "payload"),
        nonce=require_str(payload, "nonce", "payload"),
        decision="changes" if option == OPTION_CHANGES else "approve",
        approver=require_str(response, "UserEmailAddress", "response"),
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

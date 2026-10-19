"""Agon console stand-in card: the anonymous judge path.

It is labelled on the page, in the same sentence, as a stand-in for the Microsoft Teams card that
calls the same decision endpoint. The approver is the console session's principal, injected by the
API from the session, never read from the request body. Nonces for the public `feed` target refuse
this channel in the verifier, so the stand-in can only ever approve a `sandbox` publish.

Request body: {"bundle_sha256": "<64 hex>", "nonce": "<nonce>", "decision": "approve" | "changes",
"confirmed_rows": ["s3", ...]}.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agon.governance import verifier
from agon.governance.channels._common import PayloadError, refused_payload, require_str

CHANNEL = "console"


def parse(body: Any, *, principal: str, received_at: datetime) -> verifier.ApprovalDecision:
    if not isinstance(body, dict):
        raise PayloadError("body must be an object")
    extra = set(body) - {"bundle_sha256", "nonce", "decision", "confirmed_rows"}
    if extra:
        raise PayloadError(f"unexpected body fields {sorted(extra)}")
    decision = require_str(body, "decision", "body")
    if decision not in verifier.DECISIONS:
        raise PayloadError(f"unknown decision {decision!r}")
    rows = body.get("confirmed_rows", [])
    if not isinstance(rows, list) or not all(isinstance(r, str) for r in rows):
        raise PayloadError("body.confirmed_rows must be a list of strings")
    if not isinstance(principal, str) or not principal.strip():
        raise PayloadError("no console session principal")
    return verifier.ApprovalDecision(
        bundle_sha256=require_str(body, "bundle_sha256", "body"),
        nonce=require_str(body, "nonce", "body"),
        decision=decision,
        approver=principal,
        channel=CHANNEL,
        received_at=received_at,
        confirmed_rows=frozenset(rows),
    )


def handle(body: Any, store: Any, now: datetime, *, principal: str) -> verifier.VerifyResult:
    try:
        decision = parse(body, principal=principal, received_at=now)
    except PayloadError as exc:
        return refused_payload(CHANNEL, str(exc))
    return verifier.verify(decision, store, now)

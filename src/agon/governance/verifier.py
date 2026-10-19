"""The one approval verifier. Every channel (Teams, Outlook, signed link, console) ends here.

A channel adapter translates its payload into an `ApprovalDecision` and calls `verify()`. Only
`verify()` can turn a decision into an approval, and it does so in one place, in this order:

1. The decision is well formed (known decision and channel, 64-hex hash, aware timestamps).
2. The nonce exists and has not been consumed.
3. The decision's bundle hash equals the hash the nonce was issued for.
4. The sealed manifest for that hash is in the store, its bytes still hash to it, it carries a
   verdict from every gate its kind requires (`seal.required_gates`), and every verdict is "pass" (a refused or
   unchecked bundle has no Approve button, server side too).
5. The nonce has not expired, judged by the injected `now`, never by the payload's own time.
6. The channel is allowed for the nonce's target (the console stand-in can never approve `feed`).
7. The nonce's stored role equals `TARGET_ROLE[target]`, and the approver holds that role. The
   role is derived from the target; a nonce that names any other role is refused.
8. An approval ticks every Confirm row the card carried.
9. Destinations (`agon.governance.destinations`): an approval names only destinations the sealed
   manifest offers, and ticks at least one when any is offered. A channel that cannot tick
   (Outlook) approves the destinations as offered, and only that channel may. A "changes"
   decision carries no destinations (refused as malformed in step 1 if it does).
10. Consume-once: compare-and-set on the nonce document's etag. Exactly one decision wins. The
   approval record, keyed by approval id and naming the bundle hash, stores the approved subset;
   the Publisher and every destination adapter publish to nothing else.

Every refusal before step 10 leaves the nonce unconsumed, so a malformed or forged decision cannot
burn a nonce. A valid "changes" decision consumes the nonce too, so Teams "changes" followed by an
Outlook "approve" on the same card is refused.

A bundle sealed with no `destinations` table offers nothing: an approve then carries no
destination and publishes the sealed bundle only, as before destinations existed (the Teams card
says "No destination choice on this bundle" for exactly this case).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from agon.governance import seal as seal_mod
from agon.governance.store import Conflict, PreconditionFailed, Store

BUNDLES = "bundles"
NONCES = "nonces"
APPROVALS = "approvals"
APPROVERS = "approvers"

DECISIONS: tuple[str, ...] = ("approve", "changes")
CHANNELS: tuple[str, ...] = ("teams", "outlook", "signed_link", "console")
TARGETS: tuple[str, ...] = ("feed", "sandbox")
TARGET_CHANNELS: dict[str, frozenset[str]] = {
    "feed": frozenset({"teams", "outlook", "signed_link"}),
    "sandbox": frozenset(CHANNELS),
}
APPROVER_ROLE = "approver"
SANDBOX_ROLE = "sandbox_approver"
# The role a target demands is fixed here, never chosen by the caller of issue_nonce() and never
# read on trust from the nonce document: the console visitor holds sandbox_approver, so a feed
# nonce that asked for that role would hand the anonymous identity the public feed.
TARGET_ROLE: dict[str, str] = {"feed": APPROVER_ROLE, "sandbox": SANDBOX_ROLE}
# Channels whose response cannot carry tick boxes. Their approve covers the destinations as
# offered; every other channel must tick. Pinned in tests/governance/test_verifier.py.
AS_OFFERED_CHANNELS: frozenset[str] = frozenset({"outlook"})
NONCE_MAX_LEN = 128
DEFAULT_TTL_SECONDS = 30 * 60


class IssueError(ValueError):
    """Raised when a nonce cannot be issued for a bundle."""


@dataclass(frozen=True)
class ApprovalDecision:
    bundle_sha256: str
    nonce: str
    decision: str  # "approve" or "changes"
    approver: str
    channel: str  # set by the adapter, never read from the payload
    received_at: datetime  # set by the adapter
    confirmed_rows: frozenset[str] = field(default_factory=frozenset)
    # The destination ids the officer ticked. Empty on a "changes" decision.
    destinations: frozenset[str] = field(default_factory=frozenset)
    # True only from a channel in AS_OFFERED_CHANNELS: the approve covers every offered destination.
    destinations_as_offered: bool = False


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    code: str
    detail: str
    approval_id: str | None = None
    decision: str | None = None
    channel: str | None = None
    bundle_sha256: str | None = None

    @property
    def approved(self) -> bool:
        return self.ok and self.decision == "approve"


def normalize_principal(principal: str) -> str:
    return principal.strip().casefold()


def approval_id_for(nonce: str) -> str:
    return hashlib.sha256(("agon-approval|" + nonce).encode("utf-8")).hexdigest()[:32]


def _epoch(moment: datetime) -> int:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("timestamps must be timezone-aware")
    return int(moment.timestamp())


def _iso(moment: datetime) -> str:
    return datetime.fromtimestamp(_epoch(moment), tz=moment.tzinfo).isoformat()


# ----------------------------------------------------------------- setup helpers (not channels)


def register_sealed_bundle(store: Store, manifest_bytes: bytes) -> str:
    """Store a sealed manifest under its own hash, create-only. Returns the bundle hash."""
    bundle_sha = seal_mod.sha256_hex(manifest_bytes)
    try:
        store.create(BUNDLES, bundle_sha, {"bundle_sha256": bundle_sha, "manifest": manifest_bytes.decode("utf-8")})
    except Conflict:
        pass  # same key means same bytes: the key is the hash of the bytes
    return bundle_sha


def grant_role(store: Store, principal: str, roles: Iterable[str]) -> None:
    store.create(APPROVERS, normalize_principal(principal), {"roles": sorted(set(roles))})


def issue_nonce(
    store: Store,
    bundle_sha256: str,
    *,
    target: str,
    now: datetime,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    confirm_rows: Iterable[str] = (),
) -> str:
    """Create the single-use nonce a card, email or link carries. Refuses a bundle a gate refused."""
    if target not in TARGETS:
        raise IssueError(f"unknown target {target!r}")
    doc = store.read(BUNDLES, bundle_sha256)
    if doc is None:
        raise IssueError("bundle is not sealed in the store")
    check = seal_mod.verify_manifest(doc.body["manifest"].encode("utf-8"), expected_sha256=bundle_sha256)
    if not check.ok or check.manifest is None:
        raise IssueError("; ".join(check.reasons))
    if not seal_mod.gates_all_pass(check.manifest):
        raise IssueError("a gate refused this bundle; no approval can be requested")
    nonce = secrets.token_urlsafe(32)
    store.create(
        NONCES,
        nonce,
        {
            "bundle_sha256": bundle_sha256,
            "purpose": check.manifest["kind"],
            "target": target,
            "role": TARGET_ROLE[target],
            "issued_at": _epoch(now),
            "expires_at": _epoch(now) + ttl_seconds,
            "confirm_rows": sorted(set(confirm_rows)),
            "status": "pending",
        },
    )
    return nonce


# ----------------------------------------------------------------- the verifier


def _refuse(code: str, detail: str, decision: ApprovalDecision | None = None) -> VerifyResult:
    return VerifyResult(
        ok=False,
        code=code,
        detail=detail,
        decision=None,
        channel=decision.channel if decision else None,
        bundle_sha256=decision.bundle_sha256 if decision else None,
    )


def _shape_problem(d: ApprovalDecision, now: datetime) -> str | None:
    if d.decision not in DECISIONS:
        return f"unknown decision {d.decision!r}"
    if d.channel not in CHANNELS:
        return f"unknown channel {d.channel!r}"
    if not seal_mod.is_sha256_hex(d.bundle_sha256):
        return "bundle hash must be 64 lowercase hex characters"
    if not isinstance(d.nonce, str) or not d.nonce or len(d.nonce) > NONCE_MAX_LEN:
        return "nonce missing or too long"
    if not isinstance(d.approver, str) or not d.approver.strip():
        return "approver missing"
    if not isinstance(d.confirmed_rows, frozenset):
        return "confirmed rows must be a frozenset"
    if not isinstance(d.destinations, frozenset) or not all(isinstance(x, str) for x in d.destinations):
        return "destinations must be a frozenset of ids"
    if not isinstance(d.destinations_as_offered, bool):
        return "destinations_as_offered must be a bool"
    if d.decision != "approve" and (d.destinations or d.destinations_as_offered):
        return "a changes decision carries no destinations"
    if d.destinations_as_offered and d.destinations:
        return "a decision either ticks destinations or approves them as offered, not both"
    try:
        _epoch(d.received_at)
        _epoch(now)
    except (ValueError, AttributeError):
        return "timestamps must be timezone-aware datetimes"
    return None


def verify(decision: ApprovalDecision, store: Store, now: datetime) -> VerifyResult:
    """Turn one channel decision into at most one approval. See the module docstring for order."""
    problem = _shape_problem(decision, now)
    if problem:
        return _refuse("malformed_decision", problem, decision)

    nonce_doc = store.read(NONCES, decision.nonce)
    if nonce_doc is None:
        return _refuse("unknown_nonce", "no approval request carries this nonce", decision)
    record = nonce_doc.body
    if record.get("status") != "pending":
        by = record.get("consumed", {})
        return _refuse("already_consumed", f"already decided on {by.get('channel')} at {by.get('verified_at')}", decision)
    if record.get("bundle_sha256") != decision.bundle_sha256:
        return _refuse("hash_mismatch", "the decision names a different bundle than the request", decision)

    bundle_doc = store.read(BUNDLES, decision.bundle_sha256)
    if bundle_doc is None:
        return _refuse("bundle_not_sealed", "no sealed manifest for this hash", decision)
    check = seal_mod.verify_manifest(bundle_doc.body["manifest"].encode("utf-8"), expected_sha256=decision.bundle_sha256)
    if not check.ok or check.manifest is None:
        return _refuse(check.refusal_code(decision.bundle_sha256), "; ".join(check.reasons), decision)
    if not seal_mod.gates_all_pass(check.manifest):
        return _refuse("bundle_refused_by_gate", "a gate refused this bundle", decision)

    if _epoch(now) >= int(record["expires_at"]):
        return _refuse("expired", "the approval request has expired", decision)
    target = record.get("target")
    if decision.channel not in TARGET_CHANNELS.get(target, frozenset()):
        return _refuse("channel_not_allowed", f"channel {decision.channel} cannot approve target {target}", decision)

    required_role = TARGET_ROLE.get(target)
    if required_role is None or record.get("role") != required_role:
        return _refuse(
            "role_mismatch",
            f"target {target} requires the {required_role} role; the request names {record.get('role')}",
            decision,
        )
    principal = normalize_principal(decision.approver)
    role_doc = store.read(APPROVERS, principal)
    if role_doc is None or required_role not in role_doc.body.get("roles", []):
        return _refuse("not_approver", f"{principal} does not hold the {required_role} role", decision)

    approved_destinations: tuple[str, ...] = ()
    if decision.decision == "approve":
        missing = sorted(set(record.get("confirm_rows", [])) - set(decision.confirmed_rows))
        if missing:
            return _refuse("unconfirmed_rows", f"tick these rows before approving: {', '.join(missing)}", decision)
        offered = seal_mod.offered_destinations(check.manifest)
        if decision.destinations_as_offered:
            if decision.channel not in AS_OFFERED_CHANNELS:
                return _refuse("destinations_not_ticked", f"channel {decision.channel} must tick the destinations it approves", decision)
            approved_destinations = offered
        else:
            outside = sorted(decision.destinations - set(offered))
            if outside:
                return _refuse("destination_not_offered", f"not offered by the sealed bundle: {', '.join(outside)}", decision)
            if offered and not decision.destinations:
                return _refuse("no_destination_selected", "tick at least one destination before approving", decision)
            approved_destinations = tuple(d for d in offered if d in decision.destinations)

    approval_id = approval_id_for(decision.nonce)
    consumed = {
        "approval_id": approval_id,
        "decision": decision.decision,
        "approver": principal,
        "channel": decision.channel,
        "received_at": _iso(decision.received_at),
        "verified_at": _iso(now),
        "confirmed_rows": sorted(decision.confirmed_rows),
        "destinations": list(approved_destinations),
        "destinations_as_offered": decision.destinations_as_offered,
    }
    try:
        store.replace(NONCES, decision.nonce, {**record, "status": "consumed", "consumed": consumed}, if_match=nonce_doc.etag)
    except PreconditionFailed:
        latest = store.read(NONCES, decision.nonce)
        by = latest.body.get("consumed", {}) if latest else {}
        return _refuse("already_consumed", f"already decided on {by.get('channel')} at {by.get('verified_at')}", decision)

    try:
        store.create(
            APPROVALS,
            approval_id,
            {
                **consumed,
                "bundle_sha256": decision.bundle_sha256,
                "nonce_sha256": hashlib.sha256(decision.nonce.encode("utf-8")).hexdigest(),
                "purpose": record.get("purpose"),
                "target": target,
            },
        )
    except Conflict:
        # Unreachable while the store honours etags; a second line of defence if it does not.
        return _refuse("already_consumed", "an approval is already recorded for this request", decision)
    return VerifyResult(
        ok=True,
        code="approved" if decision.decision == "approve" else "changes_requested",
        detail=f"{decision.decision} by {principal} on {decision.channel}",
        approval_id=approval_id,
        decision=decision.decision,
        channel=decision.channel,
        bundle_sha256=decision.bundle_sha256,
    )

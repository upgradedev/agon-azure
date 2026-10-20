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

The approval record is a projection of the consumed nonce (`_project_approval`): built from the
stored nonce document alone, never from `now` or a payload, and created create-only. The nonce
write and the record are two writes, so a process that dies between them, or a create that fails
with a transient error, leaves a consumed nonce with no record. The queue then redelivers the same
decision; it meets the consumed nonce, and the record is projected before the usual
"already_consumed" refusal, so the intake finds it and re-sends the pointer, but only when the
stored consumed block is exactly the block this verifier would write for the redelivered decision
(`_is_this_decision`): steps 3 to 9 run again against the nonce document, the sealed bundle and the
approver's current role, with expiry judged at the stored verified_at, and every decided field
(approval id, decision, normalised approver, channel, confirmed rows, destinations, as offered)
must equal the stored one as JSON, with no other field present; only the two times may differ,
and they must be canonical, fall inside the request (issued_at <= received_at <= verified_at <
expires_at), not lie after the redelivery and be at most `RECOVERY_WINDOW_SECONDS` older than it.
A stored block proves nothing by itself: the fleet may create nonces, RBAC cannot constrain a
field, so a nonce can be created already consumed. Anything that does not match writes nothing.
What stays open: a planted block that equals the officer's real decision is recorded with times
the planter chose inside those bounds; closing that needs a consumed block only id-api can sign.

Every refusal before step 10 leaves the nonce unconsumed, so a malformed or forged decision cannot
burn a nonce. A valid "changes" decision consumes the nonce too, so Teams "changes" followed by an
Outlook "approve" on the same card is refused.

A bundle sealed with no `destinations` table offers nothing: an approve then carries no
destination and publishes the sealed bundle only, as before destinations existed (the Teams card
says "No destination choice on this bundle" for exactly this case).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from agon.governance import seal as seal_mod
from agon.governance.store import Conflict, Document, PreconditionFailed, Store

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


PROJECTED = "projected"  # the approval record was created by this call
ALREADY_PROJECTED = "already_projected"  # the record existed: create-only met a Conflict
NOT_PROJECTABLE = "not_projectable"  # the nonce document holds no consumed decision for this nonce


def _project_approval(store: Store, nonce: str, nonce_doc: Document) -> str:
    """Create `approvals/<approval_id>` from the consumed nonce document alone.

    The body is the stored `consumed` block (the decision as verified, with its own received_at
    and verified_at) plus the document's `bundle_sha256`, `purpose` and `target` and the sha256 of
    the nonce. It never reads `now` or a redelivered payload, so a redelivery can complete the
    record but never change it. `nonce` is the key the document was read under: the body never
    holds the nonce itself, only `consumed.approval_id`, which must be derived from that key.

    Create-only: a Conflict means the record is already there (PROJECTED versus ALREADY_PROJECTED
    tells the caller which). A document that is not consumed, or whose consumed block names
    another approval id, writes nothing (NOT_PROJECTABLE).
    """
    record = nonce_doc.body
    consumed = record.get("consumed")
    approval_id = approval_id_for(nonce)
    if record.get("status") != "consumed" or not isinstance(consumed, dict) or consumed.get("approval_id") != approval_id:
        return NOT_PROJECTABLE
    body = {
        **consumed,
        "bundle_sha256": record.get("bundle_sha256"),
        "nonce_sha256": hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
        "purpose": record.get("purpose"),
        "target": record.get("target"),
    }
    try:
        store.create(APPROVALS, approval_id, body)
    except Conflict:
        return ALREADY_PROJECTED
    return PROJECTED


@dataclass(frozen=True)
class _Checked:
    """What steps 3 to 9 settle for a decision that passes them."""

    principal: str
    destinations: tuple[str, ...]


# The consumed block's fields that the decision itself settles, and the two times it also holds.
DECIDED_FIELDS: tuple[str, ...] = (
    "approval_id", "decision", "approver", "channel", "confirmed_rows", "destinations", "destinations_as_offered",
)
TIME_FIELDS: tuple[str, ...] = ("received_at", "verified_at")
# How long after its first pass a redelivered decision may still complete its record. A real
# redelivery comes back after the queue's visibility timeout, or later if the intake was down; a
# day covers an outage, and it bounds how far a planted block's times can sit from the real tap.
RECOVERY_WINDOW_SECONDS = 24 * 3600


def _decided(decision: ApprovalDecision, checked: _Checked) -> dict[str, object]:
    return {
        "approval_id": approval_id_for(decision.nonce),
        "decision": decision.decision,
        "approver": checked.principal,
        "channel": decision.channel,
        "confirmed_rows": sorted(decision.confirmed_rows),
        "destinations": list(checked.destinations),
        "destinations_as_offered": decision.destinations_as_offered,
    }


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None and moment.tzinfo.utcoffset(moment) is not None else None


def _check_decision(decision: ApprovalDecision, store: Store, record: dict, at: datetime) -> VerifyResult | _Checked:
    """Steps 3 to 9 against a nonce document, with expiry judged at `at`. A refusal or what they settle."""
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

    if _epoch(at) >= int(record["expires_at"]):
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
    return _Checked(principal, approved_destinations)


def _times_hold(record: dict, block: dict, now: datetime) -> bool:
    """The block's two times are ones verify() could have written for this nonce, before `now`.

    verify() writes both with `_iso`, takes `received_at` from the decision and `verified_at` from
    its clock, and only while the request is open, so: canonical strings, issued_at <= received_at
    <= verified_at < expires_at, verified_at not after `now`, and no older than the recovery window.
    The fleet chooses issued_at and expires_at when it plants a document, so these bounds cannot
    make a planted time true; they keep it inside the request it names and within a day of the tap.
    """
    received_at = _aware(block.get("received_at"))
    verified_at = _aware(block.get("verified_at"))
    if received_at is None or verified_at is None:
        return False
    if block["received_at"] != _iso(received_at) or block["verified_at"] != _iso(verified_at):
        return False
    received, verified, at = _epoch(received_at), _epoch(verified_at), _epoch(now)
    return (
        int(record["issued_at"]) <= received <= verified < int(record["expires_at"])
        and verified <= at
        and at - verified <= RECOVERY_WINDOW_SECONDS
    )


def _is_this_decision(decision: ApprovalDecision, store: Store, record: dict, block: dict, now: datetime) -> bool:
    """True only when `block` is the consumed block verify() would have written for `decision`.

    A nonce can be created already consumed (the fleet holds create on nonces, and RBAC cannot
    constrain a field), so a stored block proves nothing by itself. Every check runs again against
    the nonce document, the sealed bundle and the approver's current role, with expiry judged at
    the stored verified_at, and the block must hold exactly the decided fields those checks give,
    equal as JSON, plus two times that pass `_times_hold`. The times are the only fields that may
    differ from what a fresh pass would decide.
    """
    if set(block) != set(DECIDED_FIELDS) | set(TIME_FIELDS):
        return False
    verified_at = _aware(block.get("verified_at"))
    if verified_at is None:
        return False
    try:
        if not _times_hold(record, block, now):
            return False
        checked = _check_decision(decision, store, record, verified_at)
    except (KeyError, TypeError, ValueError):
        return False  # a malformed planted document is refused, not retried into the poison queue
    if isinstance(checked, VerifyResult):
        return False
    stored = {name: block[name] for name in DECIDED_FIELDS}
    return json.dumps(stored, sort_keys=True) == json.dumps(_decided(decision, checked), sort_keys=True)


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
        by = record.get("consumed") if isinstance(record.get("consumed"), dict) else {}
        if (
            by.get("channel") == decision.channel
            and record.get("bundle_sha256") == decision.bundle_sha256
            and store.read(APPROVALS, approval_id_for(decision.nonce)) is None
            and _is_this_decision(decision, store, record, by, now)
        ):
            # The same decision redelivered and no record yet: the first pass stopped between the
            # nonce write and the approval record, so complete the record from the stored nonce.
            # The refusal below stands either way; the intake's _replay then finds the record and
            # re-sends its pointer. Anything else (another channel, bundle, approver, decision,
            # destination or row, a failing check, a planted block) writes nothing. The approvals
            # read comes before the full re-check because a caller that holds only read on
            # approvals (the fleet worker re-verifies a decision the intake already recorded, see
            # agon.fleet.steps.decide) must never reach a write.
            _project_approval(store, decision.nonce, nonce_doc)
        return _refuse("already_consumed", f"already decided on {by.get('channel')} at {by.get('verified_at')}", decision)
    checked = _check_decision(decision, store, record, now)
    if isinstance(checked, VerifyResult):
        return checked
    principal = checked.principal

    approval_id = approval_id_for(decision.nonce)
    consumed = {
        **_decided(decision, checked),
        "received_at": _iso(decision.received_at),
        "verified_at": _iso(now),
    }
    try:
        stored = store.replace(NONCES, decision.nonce, {**record, "status": "consumed", "consumed": consumed}, if_match=nonce_doc.etag)
    except PreconditionFailed:
        latest = store.read(NONCES, decision.nonce)
        by = latest.body.get("consumed", {}) if latest else {}
        return _refuse("already_consumed", f"already decided on {by.get('channel')} at {by.get('verified_at')}", decision)

    projected = _project_approval(store, decision.nonce, stored)
    if projected == ALREADY_PROJECTED:
        # A second line of defence: reached when the store ignores etags (the M15 negative
        # control), or when a redelivery of this same decision completed the record between our
        # two writes. Either way the record exists, and the intake's _replay re-sends its pointer.
        return _refuse("already_consumed", "an approval is already recorded for this request", decision)
    if projected != PROJECTED:
        # The store answered the replace with a document that does not hold this decision. Raise,
        # so the intake leaves the message; the redelivery projects from the stored nonce.
        raise RuntimeError(f"nonce write for approval {approval_id} returned a document without its consumed decision")
    return VerifyResult(
        ok=True,
        code="approved" if decision.decision == "approve" else "changes_requested",
        detail=f"{decision.decision} by {principal} on {decision.channel}",
        approval_id=approval_id,
        decision=decision.decision,
        channel=decision.channel,
        bundle_sha256=decision.bundle_sha256,
    )

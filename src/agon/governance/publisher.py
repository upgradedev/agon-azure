"""Publisher core: the only writer of published bytes, and it writes only approved bytes.

Rules, in order. The first is the seal check, and it is never skipped:

1. Recompute the bundle hash from the manifest bytes and every artifact's hash from its bytes
   (`seal.verify_manifest`). It must equal the hash the approval names. Anything else is refused.
2. The approval is read back from the store, where only `verifier.verify` writes it; an in-memory
   verdict object is never trusted. It must be an "approve" for this hash and this target.
3. Every gate its kind requires (`seal.required_gates`: the report gates, or for a charter
   `charter_check` and `rights`) has a verdict in the manifest, and every verdict is "pass".
4. Destinations: the approval record lists the approved subset. Every listed destination must be
   offered by the sealed manifest, a bundle that offers destinations needs at least one approved,
   and each approved destination needs its adapter. Nothing is ever delivered to a destination the
   record does not list, and each adapter refuses one itself as well (`require_approved`).
5. A bundle is published once. A second publish of the same bundle is refused, not repeated.
6. Writes are create-only. A path that already holds different bytes is refused, never
   overwritten. Identical bytes at the same content-addressed path count as already written, so a
   publish interrupted half way can be resumed without overwriting anything.
7. An erratum names its parent. The parent must be published on this target, for the same
   match_id (a receipt without one fails closed), and not yet superseded; a new supersession
   record links parent to child. The parent's receipt and bytes are never touched.
8. Nothing public is written until every refusal that can be decided has been: every adapter
   plans its files first (it reads, never writes), every target path is checked for foreign bytes,
   and an erratum claims its parent (create-only supersession record, state "claimed") BEFORE any
   blob write, so of two concurrent errata of one parent the loser writes nothing. The record
   moves to state "published" after the receipt exists. A "claimed" record whose child has no
   receipt is a publish that stopped half way; the same child can resume it, no other child can
   take the parent, and readers must join the record with the child's receipt before showing a
   correction.

What is written, under `<target>/`:

* `<bundle_sha256>/`: the sealed bundle, manifest plus every artifact, so Verify can recompute the
  hash. The payload of a destination the officer did not tick is the one exception: it stays out,
  and the receipt says so (the manifest still names it by hash, as offered).
* each approved destination's own files, planned by its adapter and confined to the adapter's
  `prefix` folder (least privilege inside the one container the job's identity may write).
* one pointer per adapter that asks for it (the feeds' `latest.json`). A pointer is the only
  blob ever replaced, and only by compare-and-set on the etag read with it; it holds a path and
  hashes of create-only bytes, never content.

The receipt is a create-only record keyed by the bundle hash, with its own sha256 and one entry
per approved destination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol

from agon.governance import destinations as dest_mod
from agon.governance import seal as seal_mod
from agon.governance.store import Conflict, PreconditionFailed, Store
from agon.governance.verifier import APPROVALS, TARGETS

PUBLISHED = "published"
SUPERSESSIONS = "supersessions"
MANIFEST_NAME = seal_mod.MANIFEST_NAME
POINTER_ATTEMPTS = 3


class AlreadyExists(Exception):
    """put_new() on a path that already holds bytes."""


class PointerConflict(Exception):
    """write_pointer() lost a compare-and-set: the pointer changed since it was read."""


class PublishSink(Protocol):
    def put_new(self, path: str, data: bytes, media_type: str) -> None: ...

    def get(self, path: str) -> bytes | None: ...


class PointerSink(Protocol):
    """The optional capability a sink needs for adapters that keep a pointer (feeds)."""

    def read_pointer(self, path: str) -> tuple[bytes, str] | None: ...

    def write_pointer(self, path: str, data: bytes, media_type: str, etag: str | None) -> None: ...


class InMemorySink:
    """Create-only blob sink, standing in for a storage container written with If-None-Match: *.

    Pointers live in the same namespace; `write_pointer` is compare-and-set on an etag, and
    creating one (etag None) is create-only like any other blob.
    """

    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, str]] = {}
        self.etags: dict[str, str] = {}
        self._version = 0

    def put_new(self, path: str, data: bytes, media_type: str) -> None:
        if path in self.blobs:
            raise AlreadyExists(path)
        self._store(path, data, media_type)

    def get(self, path: str) -> bytes | None:
        entry = self.blobs.get(path)
        return None if entry is None else entry[0]

    def read_pointer(self, path: str) -> tuple[bytes, str] | None:
        entry = self.blobs.get(path)
        return None if entry is None else (entry[0], self.etags[path])

    def write_pointer(self, path: str, data: bytes, media_type: str, etag: str | None) -> None:
        current = self.etags.get(path) if path in self.blobs else None
        if current != etag:
            raise PointerConflict(path)
        self._store(path, data, media_type)

    def _store(self, path: str, data: bytes, media_type: str) -> None:
        self._version += 1
        self.blobs[path] = (bytes(data), media_type)
        self.etags[path] = f'"v{self._version}"'


# ----------------------------------------------------------------- destination adapters


class DestinationRefused(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PlannedFile:
    path: str
    data: bytes
    media_type: str


@dataclass(frozen=True)
class PointerWrite:
    path: str
    data: bytes
    media_type: str
    expected_etag: str | None


@dataclass(frozen=True)
class DeliveryPlan:
    destination: str
    files: tuple[PlannedFile, ...]
    entry: Mapping[str, Any]
    pointer: PointerWrite | None = None


@dataclass(frozen=True)
class DeliveryContext:
    """Everything an adapter may know. It holds no store and no writable sink."""

    target: str
    bundle_sha256: str
    approval_id: str
    manifest: Mapping[str, Any]
    approval: Mapping[str, Any]
    published_at: str
    now: datetime
    approved_destinations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def approved_at(self) -> str:
        """The verifier's time on the approval record. Adapters date their files with it, never
        with the publish clock, so a retried publish plans the same bytes and resumes cleanly."""
        value = self.approval.get("verified_at")
        return value if isinstance(value, str) and value else self.published_at

    def path(self, *parts: str) -> str:
        return "/".join((self.target, *parts))


class BlobReader(Protocol):
    def get(self, path: str) -> bytes | None: ...

    def read_pointer(self, path: str) -> tuple[bytes, str] | None: ...


class DestinationAdapter(Protocol):
    destination: str
    prefix: str  # the one folder under the target this adapter's files may land in

    def plan(self, ctx: DeliveryContext, payload: bytes, reader: BlobReader) -> DeliveryPlan: ...


def require_approved(ctx: DeliveryContext, destination: str) -> None:
    """Every adapter calls this first: it refuses a destination the approval record does not list."""
    listed = ctx.approval.get("destinations")
    if not isinstance(listed, list) or destination not in listed:
        raise DestinationRefused("destination_not_approved", f"the approval record does not list {destination}")
    if destination not in seal_mod.offered_destinations(ctx.manifest):
        raise DestinationRefused("destination_not_offered", f"the sealed bundle does not offer {destination}")


class _ReadOnly:
    """What an adapter sees of the sink: reads only."""

    def __init__(self, sink: Any) -> None:
        self._sink = sink

    def get(self, path: str) -> bytes | None:
        return self._sink.get(path)

    def read_pointer(self, path: str) -> tuple[bytes, str] | None:
        reader = getattr(self._sink, "read_pointer", None)
        return None if reader is None else reader(path)


# ----------------------------------------------------------------- the publisher


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    code: str
    detail: str
    receipt: dict[str, Any] | None = None


def _refuse(code: str, detail: str) -> PublishResult:
    return PublishResult(ok=False, code=code, detail=detail)


class Publisher:
    def __init__(
        self,
        store: Store,
        sink: PublishSink,
        target: str,
        adapters: Mapping[str, DestinationAdapter] | None = None,
    ) -> None:
        if target not in TARGETS:
            raise ValueError(f"unknown target {target!r}")
        self.store = store
        self.sink = sink
        self.target = target
        self.adapters: dict[str, DestinationAdapter] = dict(adapters or {})
        for dest_id, adapter in self.adapters.items():
            if dest_id not in dest_mod.LABELS or getattr(adapter, "destination", None) != dest_id:
                raise ValueError(f"adapter registered for {dest_id!r} serves {getattr(adapter, 'destination', None)!r}")

    def _write(self, path: str, data: bytes, media_type: str) -> str | None:
        """Create-only write. Returns a refusal detail, or None when the bytes are in place."""
        try:
            self.sink.put_new(path, data, media_type)
        except AlreadyExists:
            if self.sink.get(path) != data:
                return f"{path} already holds different bytes; overwrite refused"
        return None

    def _mark_supersession_published(self, parent: str, child: str) -> None:
        """Move the parent's supersession record from claimed to published, by etag."""
        link = self.store.read(SUPERSESSIONS, parent)
        if link is None or link.body.get("child") != child or link.body.get("state") == "published":
            return
        try:
            self.store.replace(SUPERSESSIONS, parent, {**link.body, "state": "published"}, if_match=link.etag)
        except PreconditionFailed:
            pass  # a concurrent publish of the same child already moved it

    def _plan_destination(self, dest_id: str, ctx: DeliveryContext, payload: bytes) -> DeliveryPlan | PublishResult:
        adapter = self.adapters[dest_id]
        try:
            plan = adapter.plan(ctx, payload, _ReadOnly(self.sink))
        except DestinationRefused as exc:
            return _refuse(exc.code, f"{dest_id}: {exc.detail}")
        if plan.destination != dest_id:
            return _refuse("adapter_mismatch", f"the {dest_id} adapter planned for {plan.destination}")
        allowed = ctx.path(adapter.prefix) + "/"
        paths = [f.path for f in plan.files] + ([plan.pointer.path] if plan.pointer else [])
        outside = [p for p in paths if not p.startswith(allowed) or ".." in p.split("/")]
        if outside:
            return _refuse("adapter_path_outside_prefix", f"{dest_id} may write only under {allowed}: {outside}")
        if plan.entry.get("delivery") != dest_mod.DELIVERY[dest_id]:
            return _refuse("adapter_mismatch", f"{dest_id} must report delivery {dest_mod.DELIVERY[dest_id]}")
        return plan

    def publish(
        self,
        *,
        bundle_sha256: str,
        approval_id: str,
        manifest_bytes: bytes,
        artifacts: Mapping[str, bytes],
        now: datetime,
    ) -> PublishResult:
        check = seal_mod.verify_manifest(manifest_bytes, artifacts, expected_sha256=bundle_sha256)
        if not check.ok or check.manifest is None:
            code = check.refusal_code(bundle_sha256)
            return _refuse("required_gate_missing" if code == "required_gate_missing" else "seal_mismatch", "; ".join(check.reasons))
        manifest = check.manifest

        approval_doc = self.store.read(APPROVALS, approval_id)
        if approval_doc is None:
            return _refuse("no_approval", "no approval record with this id")
        approval = approval_doc.body
        if approval.get("decision") != "approve":
            return _refuse("not_approved", f"the decision was {approval.get('decision')!r}")
        if approval.get("bundle_sha256") != check.bundle_sha256:
            return _refuse("approved_hash_mismatch", "the approval is for a different bundle hash")
        if approval.get("target") != self.target:
            return _refuse("wrong_target", f"approval is for {approval.get('target')}, this publisher writes {self.target}")
        if not seal_mod.gates_all_pass(manifest):
            return _refuse("bundle_refused_by_gate", "a gate refused this bundle")

        offered = seal_mod.offered_destinations(manifest)
        listed = approval.get("destinations", [])
        if not isinstance(listed, list) or not all(isinstance(d, str) for d in listed) or len(set(listed)) != len(listed):
            return _refuse("approval_invalid", "the approval record's destinations must be a list of distinct ids")
        outside = sorted(set(listed) - set(offered))
        if outside:
            return _refuse("approval_destination_not_offered", f"approved but not offered by the sealed bundle: {outside}")
        if offered and not listed:
            return _refuse("no_destination_approved", "the bundle offers destinations and the approval lists none")
        approved = dest_mod.in_order(listed)
        missing = [d for d in approved if d not in self.adapters]
        if missing:
            return _refuse("no_adapter", f"no adapter for approved destinations {missing}")

        if self.store.read(PUBLISHED, check.bundle_sha256) is not None:
            return _refuse("already_published", "this bundle is already published")

        parent = manifest.get("parent_bundle_sha256")
        if parent is not None:
            parent_receipt = self.store.read(PUBLISHED, parent)
            if parent_receipt is None or parent_receipt.body.get("target") != self.target:
                return _refuse("parent_not_published", f"parent {parent} is not published on {self.target}")
            if parent_receipt.body.get("match_id") != manifest["match_id"]:
                return _refuse(
                    "parent_match_mismatch",
                    f"parent {parent} is for match {parent_receipt.body.get('match_id')}, the erratum is for {manifest['match_id']}",
                )
            superseded = self.store.read(SUPERSESSIONS, parent)
            if superseded is not None and superseded.body.get("child") != check.bundle_sha256:
                return _refuse("parent_already_superseded", f"parent {parent} is already superseded by {superseded.body.get('child')}")

        published_at = datetime.fromtimestamp(int(now.timestamp()), tz=now.tzinfo).isoformat()
        listed_artifacts = manifest["artifacts"]
        unticked = {dest_mod.artifact_name(d) for d in offered if d not in approved}
        archived = sorted(n for n in artifacts if n not in unticked)
        prefix = f"{self.target}/{check.bundle_sha256}"
        plan = [PlannedFile(f"{prefix}/{name}", artifacts[name], listed_artifacts[name]["media_type"]) for name in archived]
        plan.append(PlannedFile(f"{prefix}/{MANIFEST_NAME}", manifest_bytes, "application/json"))

        ctx = DeliveryContext(
            target=self.target,
            bundle_sha256=check.bundle_sha256,
            approval_id=approval_id,
            manifest=manifest,
            approval=dict(approval),
            published_at=published_at,
            now=now,
            approved_destinations=approved,
        )
        deliveries: dict[str, DeliveryPlan] = {}
        for dest_id in approved:
            planned = self._plan_destination(dest_id, ctx, artifacts[dest_mod.artifact_name(dest_id)])
            if isinstance(planned, PublishResult):
                return planned
            deliveries[dest_id] = planned
            plan.extend(planned.files)

        for item in plan:
            existing = self.sink.get(item.path)
            if existing is not None and existing != item.data:
                if parent is not None:
                    # A concurrent erratum of the same parent may have won since the pre-check and
                    # written its marker beside the parent: say so, rather than a bare overwrite.
                    link = self.store.read(SUPERSESSIONS, parent)
                    if link is not None and link.body.get("child") != check.bundle_sha256:
                        return _refuse("parent_already_superseded", f"parent {parent} was superseded by another bundle")
                return _refuse("overwrite_refused", f"{item.path} already holds different bytes; overwrite refused")

        if parent is not None:
            try:
                self.store.create(
                    SUPERSESSIONS,
                    parent,
                    {
                        "parent": parent,
                        "child": check.bundle_sha256,
                        "approval_id": approval_id,
                        "at": published_at,
                        "state": "claimed",
                    },
                )
            except Conflict:
                existing_link = self.store.read(SUPERSESSIONS, parent)
                if existing_link is None or existing_link.body.get("child") != check.bundle_sha256:
                    return _refuse("parent_already_superseded", f"parent {parent} was superseded by another bundle")

        for item in plan:
            problem = self._write(item.path, item.data, item.media_type)
            if problem:
                return _refuse("overwrite_refused", problem)

        for dest_id in approved:
            settled = self._settle_pointer(dest_id, ctx, deliveries, artifacts[dest_mod.artifact_name(dest_id)])
            if settled is not None:
                return settled

        entries: dict[str, Any] = {}
        for dest_id in approved:
            delivery = deliveries[dest_id]
            entries[dest_id] = {
                **dict(delivery.entry),
                "label": dest_mod.LABELS[dest_id],
                "payload_sha256": manifest["destinations"][dest_id]["sha256"],
                "files": {f.path: seal_mod.sha256_hex(f.data) for f in delivery.files},
                "pointer": None if delivery.pointer is None else delivery.pointer.path,
            }
        body = {
            "bundle_sha256": check.bundle_sha256,
            "approval_id": approval_id,
            "approver": approval.get("approver"),
            "channel": approval.get("channel"),
            "target": self.target,
            "kind": manifest["kind"],
            "match_id": manifest["match_id"],
            "published_at": published_at,
            "manifest_path": f"{prefix}/{MANIFEST_NAME}",
            "files": {name: listed_artifacts[name]["sha256"] for name in archived},
            "parent_bundle_sha256": parent,
            "destinations_offered": list(offered),
            "destinations_approved": list(approved),
            "destinations": entries,
        }
        receipt = {**body, "receipt_sha256": seal_mod.sha256_hex(seal_mod.canonical_utf8(body))}
        try:
            self.store.create(PUBLISHED, check.bundle_sha256, receipt)
        except Conflict:
            return _refuse("already_published", "this bundle was published by a concurrent publish")
        if parent is not None:
            self._mark_supersession_published(parent, check.bundle_sha256)
        return PublishResult(ok=True, code="published", detail=f"published {check.bundle_sha256} to {self.target}", receipt=receipt)

    def _settle_pointer(
        self, dest_id: str, ctx: DeliveryContext, deliveries: dict[str, DeliveryPlan], payload: bytes
    ) -> PublishResult | None:
        """Advance an adapter's pointer by compare-and-set; on a lost race, plan again from the new one.

        The replanned files are new create-only paths (content addressed), so nothing written by
        the first attempt is touched. After POINTER_ATTEMPTS lost races the publish stops before
        the receipt with "pointer_conflict", which the job treats as retryable.
        """
        for attempt in range(POINTER_ATTEMPTS):
            delivery = deliveries[dest_id]
            if delivery.pointer is None:
                return None
            writer = getattr(self.sink, "write_pointer", None)
            if writer is None:
                return _refuse("pointer_unsupported", f"{dest_id} keeps a pointer and this sink cannot write one")
            try:
                writer(delivery.pointer.path, delivery.pointer.data, delivery.pointer.media_type, delivery.pointer.expected_etag)
                return None
            except PointerConflict:
                if attempt == POINTER_ATTEMPTS - 1:
                    break
            replanned = self._plan_destination(dest_id, ctx, payload)
            if isinstance(replanned, PublishResult):
                return replanned
            for item in replanned.files:
                problem = self._write(item.path, item.data, item.media_type)
                if problem:
                    return _refuse("overwrite_refused", problem)
            deliveries[dest_id] = replanned
        return _refuse("pointer_conflict", f"{dest_id} pointer changed {POINTER_ATTEMPTS} times while publishing; retry later")

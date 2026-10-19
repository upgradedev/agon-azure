"""Publisher core: the only writer of published bytes, and it writes only approved bytes.

Rules, in order. The first is the seal check, and it is never skipped:

1. Recompute the bundle hash from the manifest bytes and every artifact's hash from its bytes
   (`seal.verify_manifest`). It must equal the hash the approval names. Anything else is refused.
2. The approval is read back from the store, where only `verifier.verify` writes it; an in-memory
   verdict object is never trusted. It must be an "approve" for this hash and this target.
3. Every gate in `seal.REQUIRED_GATES` has a verdict in the manifest, and every verdict is "pass".
4. A bundle is published once. A second publish of the same bundle is refused, not repeated.
5. Writes are create-only. A path that already holds different bytes is refused, never
   overwritten. Identical bytes at the same content-addressed path count as already written, so a
   publish interrupted half way can be resumed without overwriting anything.
6. An erratum names its parent. The parent must be published and not yet superseded; a new
   supersession record links parent to child. The parent's receipt and bytes are never touched.

The receipt is a create-only record keyed by the bundle hash, with its own sha256.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

from agon.governance import seal as seal_mod
from agon.governance.store import Conflict, Store
from agon.governance.verifier import APPROVALS, TARGETS

PUBLISHED = "published"
SUPERSESSIONS = "supersessions"
MANIFEST_NAME = "manifest.json"


class AlreadyExists(Exception):
    """put_new() on a path that already holds bytes."""


class PublishSink(Protocol):
    def put_new(self, path: str, data: bytes, media_type: str) -> None: ...

    def get(self, path: str) -> bytes | None: ...


class InMemorySink:
    """Create-only blob sink, standing in for a storage container written with If-None-Match: *."""

    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, str]] = {}

    def put_new(self, path: str, data: bytes, media_type: str) -> None:
        if path in self.blobs:
            raise AlreadyExists(path)
        self.blobs[path] = (bytes(data), media_type)

    def get(self, path: str) -> bytes | None:
        entry = self.blobs.get(path)
        return None if entry is None else entry[0]


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    code: str
    detail: str
    receipt: dict[str, Any] | None = None


def _refuse(code: str, detail: str) -> PublishResult:
    return PublishResult(ok=False, code=code, detail=detail)


class Publisher:
    def __init__(self, store: Store, sink: PublishSink, target: str) -> None:
        if target not in TARGETS:
            raise ValueError(f"unknown target {target!r}")
        self.store = store
        self.sink = sink
        self.target = target

    def _write(self, path: str, data: bytes, media_type: str) -> str | None:
        """Create-only write. Returns a refusal detail, or None when the bytes are in place."""
        try:
            self.sink.put_new(path, data, media_type)
        except AlreadyExists:
            if self.sink.get(path) != data:
                return f"{path} already holds different bytes; overwrite refused"
        return None

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
        if self.store.read(PUBLISHED, check.bundle_sha256) is not None:
            return _refuse("already_published", "this bundle is already published")

        parent = manifest.get("parent_bundle_sha256")
        if parent is not None:
            parent_receipt = self.store.read(PUBLISHED, parent)
            if parent_receipt is None or parent_receipt.body.get("target") != self.target:
                return _refuse("parent_not_published", f"parent {parent} is not published on {self.target}")
            superseded = self.store.read(SUPERSESSIONS, parent)
            if superseded is not None and superseded.body.get("child") != check.bundle_sha256:
                return _refuse("parent_already_superseded", f"parent {parent} is already superseded by {superseded.body.get('child')}")

        prefix = f"{self.target}/{check.bundle_sha256}"
        listed = manifest["artifacts"]
        for name in sorted(artifacts):
            problem = self._write(f"{prefix}/{name}", artifacts[name], listed[name]["media_type"])
            if problem:
                return _refuse("overwrite_refused", problem)
        problem = self._write(f"{prefix}/{MANIFEST_NAME}", manifest_bytes, "application/json")
        if problem:
            return _refuse("overwrite_refused", problem)

        published_at = datetime.fromtimestamp(int(now.timestamp()), tz=now.tzinfo).isoformat()
        if parent is not None:
            try:
                self.store.create(
                    SUPERSESSIONS,
                    parent,
                    {"parent": parent, "child": check.bundle_sha256, "approval_id": approval_id, "at": published_at},
                )
            except Conflict:
                existing = self.store.read(SUPERSESSIONS, parent)
                if existing is None or existing.body.get("child") != check.bundle_sha256:
                    return _refuse("parent_already_superseded", f"parent {parent} was superseded by another bundle")

        body = {
            "bundle_sha256": check.bundle_sha256,
            "approval_id": approval_id,
            "approver": approval.get("approver"),
            "channel": approval.get("channel"),
            "target": self.target,
            "kind": manifest.get("kind"),
            "published_at": published_at,
            "manifest_path": f"{prefix}/{MANIFEST_NAME}",
            "files": {name: listed[name]["sha256"] for name in sorted(listed)},
            "parent_bundle_sha256": parent,
        }
        receipt = {**body, "receipt_sha256": seal_mod.sha256_hex(seal_mod.canonical_utf8(body))}
        try:
            self.store.create(PUBLISHED, check.bundle_sha256, receipt)
        except Conflict:
            return _refuse("already_published", "this bundle was published by a concurrent publish")
        return PublishResult(ok=True, code="published", detail=f"published {check.bundle_sha256} to {self.target}", receipt=receipt)

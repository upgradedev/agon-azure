"""The Publisher job: drain one target's publish queue into the governed Publisher.

One job per target, each with its own identity and its own queue:

    feed     publish-requests          id-publisher          writes container feed
    sandbox  sandbox-publish-requests  id-publisher-sandbox  writes container sandbox

Message body: the JSON of `agon.fleet.contracts.PublishRequest`, as raw text (no SDK encode
policy, like the decisions queue):

    {"run_id": str, "target": "feed" | "sandbox", "bundle_sha256": "<64 hex>", "approval_id": str}

It carries no capability: the approval id is only a pointer to the record `verifier.verify` wrote,
and the Publisher reads that record back from the store.

One message, in this order:

1. Decode with exactly those keys; the target must be this job's target.
2. Read `bundles/<bundle_sha256>` from Cosmos and recompute sha256 over the manifest bytes before
   anything else. A mismatch is refused here, before a single artifact is fetched (rule 1).
3. Fetch every artifact the manifest lists from the private `artifacts` container, by sha256. A
   missing artifact is an error to retry (the fleet may still be uploading), never a refusal.
4. `Publisher.publish` recomputes every hash again from the bytes and decides everything else.

Settling: published, already published and refused messages are deleted (a refusal is logged with
its code; resending the same request cannot change the answer). An exception or a retryable
refusal ("pointer_conflict") leaves the message for another delivery. After `MAX_ATTEMPTS`
deliveries the message is deleted and logged in full as "gave_up", so it can be re-sent by hand: the
publisher identities may not send to any queue, so there is no poison queue for this job.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from agon.governance import seal as seal_mod
from agon.governance.publisher import Publisher
from agon.governance.verifier import BUNDLES, TARGETS
from agon.intake.drain import QueuePort
from agon.publish.sources import ArtifactSource

PUBLISH_QUEUES: dict[str, str] = {"feed": "publish-requests", "sandbox": "sandbox-publish-requests"}
REQUEST_KEYS = frozenset({"run_id", "target", "bundle_sha256", "approval_id"})
RETRYABLE_CODES = frozenset({"pointer_conflict"})
MAX_ATTEMPTS = 5
MAX_MESSAGE_BYTES = 8 * 1024
VISIBILITY_TIMEOUT_SECONDS = 120
BATCH = 8
_RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_APPROVAL_ID = re.compile(r"^[0-9a-f]{32}$")


class RequestError(ValueError):
    """The message is not a PublishRequest for this job."""


class ArtifactMissing(RuntimeError):
    """An artifact the sealed manifest lists is not in the artifact store (yet)."""


@dataclass(frozen=True)
class PublishOutcome:
    status: str  # "published", "already_published", "refused", "undecodable", "retry"
    code: str
    detail: str
    bundle_sha256: str | None = None
    receipt_sha256: str | None = None


def decode_request(text: str, target: str) -> dict[str, str]:
    if not isinstance(text, str) or not text or len(text) > MAX_MESSAGE_BYTES:
        raise RequestError("message missing or too large")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RequestError("message is not JSON") from exc
    if not isinstance(body, dict) or set(body) != REQUEST_KEYS or not all(isinstance(v, str) for v in body.values()):
        raise RequestError(f"message must hold exactly the string fields {sorted(REQUEST_KEYS)}")
    if body["target"] != target:
        raise RequestError(f"request for target {body['target']!r} on the {target} queue")
    if not seal_mod.is_sha256_hex(body["bundle_sha256"]):
        raise RequestError("bundle_sha256 must be 64 lowercase hex characters")
    if not _RUN_ID.match(body["run_id"]) or not _APPROVAL_ID.match(body["approval_id"]):
        raise RequestError("run_id or approval_id is malformed")
    return body


def load_bundle(store: Any, source: ArtifactSource, bundle_sha256: str) -> tuple[bytes, dict[str, bytes]] | PublishOutcome:
    """The manifest from Cosmos, hash checked first, then every listed artifact by sha256."""
    doc = store.read(BUNDLES, bundle_sha256)
    if doc is None or not isinstance(doc.body.get("manifest"), str):
        return PublishOutcome("refused", "bundle_not_sealed", "no sealed manifest for this hash", bundle_sha256)
    manifest_bytes = doc.body["manifest"].encode("utf-8")
    if seal_mod.sha256_hex(manifest_bytes) != bundle_sha256:
        return PublishOutcome("refused", "seal_mismatch", "the stored manifest does not hash to the requested bundle", bundle_sha256)
    check = seal_mod.verify_manifest(manifest_bytes, expected_sha256=bundle_sha256)
    if not check.ok or check.manifest is None:
        return PublishOutcome("refused", "seal_mismatch", "; ".join(check.reasons), bundle_sha256)
    artifacts: dict[str, bytes] = {}
    for name, entry in sorted(check.manifest["artifacts"].items()):
        data = source.get(entry["sha256"])
        if data is None:
            raise ArtifactMissing(f"artifact {name} ({entry['sha256']}) is not in the artifact store")
        artifacts[name] = data
    return manifest_bytes, artifacts


def handle_request(text: str, *, target: str, store: Any, source: ArtifactSource, publisher: Publisher, now: datetime) -> PublishOutcome:
    """Process one message. Raises only on infrastructure failure (the caller retries)."""
    try:
        request = decode_request(text, target)
    except RequestError as exc:
        return PublishOutcome("undecodable", "malformed_request", str(exc))
    loaded = load_bundle(store, source, request["bundle_sha256"])
    if isinstance(loaded, PublishOutcome):
        return loaded
    manifest_bytes, artifacts = loaded
    result = publisher.publish(
        bundle_sha256=request["bundle_sha256"],
        approval_id=request["approval_id"],
        manifest_bytes=manifest_bytes,
        artifacts=artifacts,
        now=now,
    )
    if result.ok:
        receipt = result.receipt or {}
        return PublishOutcome("published", result.code, result.detail, request["bundle_sha256"], receipt.get("receipt_sha256"))
    if result.code == "already_published":
        return PublishOutcome("already_published", result.code, result.detail, request["bundle_sha256"])
    if result.code in RETRYABLE_CODES:
        return PublishOutcome("retry", result.code, result.detail, request["bundle_sha256"])
    return PublishOutcome("refused", result.code, result.detail, request["bundle_sha256"])


@dataclass(frozen=True)
class JobLine:
    queue: str
    message_id: str
    status: str
    code: str
    detail: str
    bundle_sha256: str | None = None


def drain_once(
    port: QueuePort,
    *,
    target: str,
    store: Any,
    source: ArtifactSource,
    publisher: Publisher,
    clock: Callable[[], datetime],
    log: Callable[[JobLine], None] = lambda line: None,
) -> list[JobLine]:
    if target not in TARGETS or publisher.target != target:
        raise ValueError(f"the job target {target!r} must match the publisher's target {publisher.target!r}")
    queue = PUBLISH_QUEUES[target]
    lines: list[JobLine] = []

    def record(line: JobLine) -> None:
        lines.append(line)
        log(line)

    for message in port.receive(queue, BATCH, VISIBILITY_TIMEOUT_SECONDS):
        if message.dequeue_count > MAX_ATTEMPTS:
            port.delete(queue, message)
            record(JobLine(queue, message.message_id, "gave_up", "max_attempts", f"deleted after {message.dequeue_count} deliveries; message: {message.text[:600]}"))
            continue
        try:
            outcome = handle_request(message.text, target=target, store=store, source=source, publisher=publisher, now=clock())
        except Exception as exc:  # noqa: BLE001 - any infrastructure failure leaves the message for a retry
            record(JobLine(queue, message.message_id, "error", type(exc).__name__, str(exc)[:300]))
            continue
        if outcome.status == "retry":
            record(JobLine(queue, message.message_id, "retry", outcome.code, outcome.detail, outcome.bundle_sha256))
            continue
        try:
            port.delete(queue, message)
        except Exception as exc:  # noqa: BLE001 - redelivery is safe: the Publisher answers already_published
            record(JobLine(queue, message.message_id, "error", "delete_failed", f"{outcome.status}; {type(exc).__name__}", outcome.bundle_sha256))
            continue
        record(JobLine(queue, message.message_id, outcome.status, outcome.code, outcome.detail, outcome.bundle_sha256))
    return lines

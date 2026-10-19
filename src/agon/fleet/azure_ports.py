"""Azure adapters for the fleet worker, keyless (managed identity id-fleet).

* `BlobArtifactStore`: the `ArtifactStore` port over the PRIVATE blob container `artifacts`. The
  blob name is the sha256 hex of its bytes, and every write is create-only: the upload sends
  ``If-None-Match: *``, so an existing blob is never overwritten. The Publisher jobs (id-publisher
  and id-publisher-sandbox, Storage Blob Data Reader on this container only) read the bytes back
  from ``artifacts/<sha256>`` and the manifest from the Cosmos `bundles` container.
* `QueuePublishQueue`: the `PublishQueue` port over the Storage queues `publish-requests` (target
  feed) and `sandbox-publish-requests` (target sandbox). The message is the `PublishRequest` as
  plain JSON text, no base64. A queue cannot refuse a duplicate, so a repeated request is absorbed
  by the Publisher's create-only receipt keyed by the bundle hash ("already_published").
* `StorageQueuePort`: the worker's own inbound queues (`runs`, `decisions`) and its poison queue,
  read and written as raw text, as the intake does for its queues.
* Credentials and token getters for Translator, Speech and the Logic App triggers.

The Azure SDK is imported only when an adapter or credential is built, so this module imports
with the standard library alone. Errors are mapped by HTTP status code (`status_code`), never by
SDK exception class, so the adapters are unit tested with plain fakes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from typing import Any, Callable, Mapping

from agon.fleet.contracts import PublishRequest
from agon.fleet.worker import InboundMessage

ARTIFACTS_CONTAINER = "artifacts"
PUBLISH_QUEUES: dict[str, str] = {"feed": "publish-requests", "sandbox": "sandbox-publish-requests"}
ARM_SCOPE = "https://management.azure.com/.default"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
EXISTS_STATUSES = frozenset({409, 412})


class ArtifactMismatch(RuntimeError):
    """A blob under a sha256 name holds bytes that do not hash to that name."""


def _status(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    return int(code) if isinstance(code, int) else None


def _if_missing() -> Any:
    from azure.core import MatchConditions  # imported here on purpose

    return MatchConditions.IfMissing


class BlobArtifactStore:
    """Content addressed, create-only, private. `container` is an azure ContainerClient or a fake."""

    def __init__(self, container: Any, *, match_condition: Any = None) -> None:
        self._container = container
        self._match_condition = match_condition

    @classmethod
    def connect(cls, account_url: str, credential: Any, container: str = ARTIFACTS_CONTAINER) -> "BlobArtifactStore":
        from azure.storage.blob import BlobServiceClient  # imported here on purpose

        service = BlobServiceClient(account_url=account_url, credential=credential)
        return cls(service.get_container_client(container))

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        condition = self._match_condition if self._match_condition is not None else _if_missing()
        try:
            # match_condition IfMissing sends If-None-Match: * (azure-storage-blob 12.31.0,
            # _serialize._get_match_headers); overwrite=False alone would set the same header.
            self._container.upload_blob(name=sha, data=bytes(data), overwrite=False, match_condition=condition)
        except Exception as exc:
            if _status(exc) not in EXISTS_STATUSES:
                raise
            existing = self.get(sha)
            if existing is None:
                raise
        return sha

    def get(self, sha256: str) -> bytes | None:
        if not isinstance(sha256, str) or not _HEX64.match(sha256):
            raise ValueError("an artifact name is 64 lowercase hex characters")
        try:
            data = self._container.download_blob(sha256).readall()
        except Exception as exc:
            if _status(exc) == 404:
                return None
            raise
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ArtifactMismatch(f"blob {ARTIFACTS_CONTAINER}/{sha256} does not hash to its name")
        return bytes(data)


def _raw_text_client(service: Any, queue: str) -> Any:
    return service.get_queue_client(queue, message_encode_policy=None, message_decode_policy=None)


class QueuePublishQueue:
    """`publish-requests` or `sandbox-publish-requests`, chosen by the request's target."""

    def __init__(self, clients: Mapping[str, Any]) -> None:
        if set(clients) != set(PUBLISH_QUEUES):
            raise ValueError(f"one queue client per target {sorted(PUBLISH_QUEUES)}")
        self._clients = dict(clients)

    @classmethod
    def connect(cls, account_url: str, credential: Any) -> "QueuePublishQueue":
        from azure.storage.queue import QueueServiceClient  # imported here on purpose

        service = QueueServiceClient(account_url=account_url, credential=credential)
        return cls({target: _raw_text_client(service, queue) for target, queue in PUBLISH_QUEUES.items()})

    @staticmethod
    def message(request: PublishRequest) -> str:
        return json.dumps(asdict(request), sort_keys=True, separators=(",", ":"))

    def enqueue(self, request: PublishRequest) -> bool:
        client = self._clients.get(request.target)
        if client is None:
            raise ValueError(f"no publish queue for target {request.target!r}")
        client.send_message(self.message(request))
        return True

    def items(self) -> list[dict[str, Any]]:
        raise NotImplementedError("a Storage queue is not listed here; read it with the Publisher job")


class StorageQueuePort:
    """The worker's queues as raw text: receive, delete, send (the `QueuePort` of agon.fleet.worker)."""

    def __init__(self, account_url: str, credential: Any) -> None:
        from azure.storage.queue import QueueServiceClient  # imported here on purpose

        self._service = QueueServiceClient(account_url=account_url, credential=credential)
        self._clients: dict[str, Any] = {}

    def _client(self, queue: str) -> Any:
        if queue not in self._clients:
            self._clients[queue] = _raw_text_client(self._service, queue)
        return self._clients[queue]

    def receive(self, queue: str, max_messages: int, visibility_timeout: int) -> list[InboundMessage]:
        pages = self._client(queue).receive_messages(
            messages_per_page=max_messages, visibility_timeout=visibility_timeout, max_messages=max_messages
        )
        return [
            InboundMessage(message_id=m.id, pop_receipt=m.pop_receipt, text=m.content, dequeue_count=int(m.dequeue_count or 0))
            for m in pages
        ]

    def delete(self, queue: str, message: InboundMessage) -> None:
        self._client(queue).delete_message(message.message_id, message.pop_receipt)

    def send(self, queue: str, text: str) -> None:
        self._client(queue).send_message(text)


# ----------------------------------------------------------------- credentials and tokens


def sync_credential(client_id: str | None) -> Any:
    """id-fleet in Container Apps (AZURE_CLIENT_ID), or the environment's chain in CI."""
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def async_credential(client_id: str | None) -> Any:
    """The async twin, for the Foundry clients and the Cosmos checkpoint storage."""
    from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def token_getter(credential: Any, scope: str) -> Callable[[], str]:
    """A fresh Entra access token per call; the SDK credential caches and refreshes it."""
    return lambda: credential.get_token(scope).token


def speech_auth_header(credential: Any, resource_id: str) -> Callable[[], str]:
    """Speech REST with Microsoft Entra ID: ``Bearer aad#<resource id>#<token>`` (learn.microsoft.com,
    rest-text-to-speech, "Use Microsoft Entra authentication", read 2026-10-19). Not yet exercised
    against our resource (spike S4); a failure only leaves captions without audio."""
    if not resource_id.startswith("/subscriptions/"):
        raise ValueError("the Speech resource id is an ARM id starting with /subscriptions/")
    get = token_getter(credential, COGNITIVE_SCOPE)
    return lambda: f"Bearer aad#{resource_id}#{get()}"

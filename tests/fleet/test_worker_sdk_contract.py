"""The Azure calls the fleet worker makes exist, with these keyword names, in the pinned SDK.

The worker's adapters (`agon.fleet.azure_ports`, `agon.agents.publish`) import the SDK lazily and
are unit tested against fakes, so a renamed keyword would pass every other test and fail only in
Azure. This file reads the real signatures from the packages pinned in requirements/worker.txt
and builds the real clients with no network: a transport that raises with the request it was
handed proves the exact headers and body that would go on the wire.

Without the SDK the tests skip; the fleet CI job sets AGON_REQUIRE_AZURE_SDK=1, so there a missing
package is a failure, never a skip. The worker-sdk-guard job runs this file without the SDK and
with the variable set, and must see the failure message (negative control).
"""

from __future__ import annotations

import inspect
import os
import time

import pytest

REQUIRED = os.environ.get("AGON_REQUIRE_AZURE_SDK") == "1"
MISSING_MESSAGE = "AGON_REQUIRE_AZURE_SDK=1 but the fleet worker's Azure SDK packages are not installed"

try:
    import azure.ai.projects  # noqa: F401
    import azure.core  # noqa: F401
    import azure.identity  # noqa: F401
    import azure.storage.blob  # noqa: F401
    import azure.storage.queue  # noqa: F401

    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False

if not HAVE_SDK:
    if REQUIRED:
        pytest.fail(MISSING_MESSAGE, pytrace=False)
    pytest.skip("fleet worker Azure SDK not installed (runs in the fleet CI job)", allow_module_level=True)

from azure.core.credentials import AccessToken  # noqa: E402
from azure.core.pipeline.transport import HttpTransport  # noqa: E402

ACCOUNT = "https://agonexample.blob.core.windows.net"
QUEUE_ACCOUNT = "https://agonexample.queue.core.windows.net"


class _FakeToken:
    """A credential that hands out a dummy token, so the bearer policy lets the request through."""

    def get_token(self, *scopes, **kwargs):
        return AccessToken("dummy-token-for-signature-test", int(time.time()) + 3600)


class Captured(Exception):
    def __init__(self, request):
        super().__init__("captured")
        self.request = request


class CaptureTransport(HttpTransport):
    """Never sends anything: the first request reaching the transport is raised back to the test."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def open(self):
        return None

    def close(self):
        return None

    def send(self, request, **kwargs):
        raise Captured(request)


def _headers(request) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in request.headers.items()}


def _body(request) -> str:
    data = getattr(request, "data", None) or getattr(request, "body", None) or b""
    if hasattr(request, "content") and not data:
        data = request.content
    return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)


def _params(func) -> set[str]:
    return set(inspect.signature(func).parameters)


def _has_kwargs(func) -> bool:
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(func).parameters.values())


# ----------------------------------------------------------------- blob artifacts


def test_artifact_put_sends_if_none_match_star_to_the_artifacts_container():
    from azure.storage.blob import BlobServiceClient

    from agon.fleet.azure_ports import ARTIFACTS_CONTAINER, BlobArtifactStore

    service = BlobServiceClient(account_url=ACCOUNT, credential=_FakeToken(), transport=CaptureTransport())
    store = BlobArtifactStore(service.get_container_client(ARTIFACTS_CONTAINER))
    with pytest.raises(Captured) as caught:
        store.put(b"agon artifact bytes")
    request = caught.value.request
    headers = _headers(request)
    assert request.method == "PUT"
    assert f"/{ARTIFACTS_CONTAINER}/" in request.url
    assert request.url.split("?")[0].endswith("/" + __import__("hashlib").sha256(b"agon artifact bytes").hexdigest())
    assert headers.get("if-none-match") == "*", headers
    assert headers.get("x-ms-blob-type") == "BlockBlob"
    assert headers.get("authorization", "").startswith("Bearer ")


def test_negative_control_an_overwriting_upload_sends_no_if_none_match():
    """Proves the header check above can fail: overwrite=True carries no create-only condition."""
    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient(account_url=ACCOUNT, credential=_FakeToken(), transport=CaptureTransport())
    with pytest.raises(Captured) as caught:
        service.get_container_client("artifacts").upload_blob(name="x", data=b"x", overwrite=True)
    assert "if-none-match" not in _headers(caught.value.request)


def test_artifact_get_calls_download_blob_and_readall():
    from azure.storage.blob import ContainerClient, StorageStreamDownloader

    assert "blob" in _params(ContainerClient.download_blob)
    assert hasattr(StorageStreamDownloader, "readall")
    upload = ContainerClient.upload_blob
    assert {"name", "data"} <= _params(upload)
    assert "overwrite" in _params(upload) or _has_kwargs(upload)


def test_match_conditions_if_missing_exists():
    from azure.core import MatchConditions

    from agon.fleet.azure_ports import _if_missing

    assert _if_missing() is MatchConditions.IfMissing


# ----------------------------------------------------------------- queues


def test_publish_queue_sends_raw_json_to_publish_requests():
    from azure.storage.queue import QueueServiceClient

    from agon.fleet.azure_ports import PUBLISH_QUEUES, QueuePublishQueue
    from agon.fleet.contracts import PublishRequest

    service = QueueServiceClient(account_url=QUEUE_ACCOUNT, credential=_FakeToken(), transport=CaptureTransport())
    queue = QueuePublishQueue(
        {t: service.get_queue_client(q, message_encode_policy=None, message_decode_policy=None) for t, q in PUBLISH_QUEUES.items()}
    )
    with pytest.raises(Captured) as caught:
        queue.enqueue(PublishRequest(run_id="run-1", target="feed", bundle_sha256="a" * 64, approval_id="b" * 32))
    request = caught.value.request
    assert request.method == "POST" and "/publish-requests/messages" in request.url
    body = _body(request)
    assert "approval_id" in body and "b" * 32 in body, "the message is plain JSON text, not base64"


def test_the_storage_queue_port_builds_against_the_real_sdk():
    from azure.storage.queue import QueueClient

    from agon.fleet.azure_ports import StorageQueuePort

    port = StorageQueuePort(QUEUE_ACCOUNT, _FakeToken())
    assert port._client("runs").queue_name == "runs"
    receive = QueueClient.receive_messages
    assert {"messages_per_page", "visibility_timeout", "max_messages"} <= _params(receive) or _has_kwargs(receive)
    assert {"message", "pop_receipt"} <= _params(QueueClient.delete_message)
    assert "content" in _params(QueueClient.send_message)


# ----------------------------------------------------------------- credentials


def test_credentials_build_without_a_token_request():
    from azure.identity import ManagedIdentityCredential
    from azure.identity.aio import ManagedIdentityCredential as AsyncManagedIdentityCredential

    from agon.fleet.azure_ports import async_credential, sync_credential

    client_id = "00000000-0000-0000-0000-000000000000"
    assert isinstance(sync_credential(client_id), ManagedIdentityCredential)
    assert isinstance(async_credential(client_id), AsyncManagedIdentityCredential)


# ----------------------------------------------------------------- Foundry agents


class _AsyncFakeToken:
    async def get_token(self, *scopes, **kwargs):
        return AccessToken("dummy", int(time.time()) + 3600)

    async def close(self):
        return None


def test_agents_operations_take_the_keywords_publish_passes():
    from azure.ai.projects.aio import AIProjectClient

    client = AIProjectClient(endpoint="https://agonexample.services.ai.azure.com/api/projects/agon", credential=_AsyncFakeToken())
    agents = client.agents
    assert "agent_name" in _params(agents.list_versions)
    create = agents.create_version
    names = _params(create)
    assert "agent_name" in names
    assert {"definition", "description", "metadata"} <= names or _has_kwargs(create), names
    assert {"agent_name", "agent_version"} <= _params(agents.get_version)


def test_to_prompt_agent_keeps_the_prompt_bytes_and_the_model():
    """verify_deployed compares sha256 of the deployed instructions with the prompt file: the
    definition we publish must carry the prompt text unchanged, or every startup fails closed."""
    from fleet_support import require_agent_framework

    require_agent_framework()
    from agon.agents.prompts import load_prompt
    from agon.agents.publish import prompt_agent_definition

    for role, model in (("analyst", "gpt-5.6-luna"), ("writer", "gpt-5.6-terra"), ("localiser_editor", "gpt-5.6-luna")):
        prompt = load_prompt(role)
        definition = prompt_agent_definition(
            "https://agonexample.services.ai.azure.com/api/projects/agon", _AsyncFakeToken(), role, prompt, model
        )
        assert definition.instructions == prompt.text, role
        assert definition.model == model, role

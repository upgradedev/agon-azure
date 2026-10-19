"""The calls the Azure adapters make exist, with these keyword names, in the pinned SDK.

The adapters (`agon.intake.azure_queues`, `agon.governance.store_cosmos`) import the SDK lazily and
are unit-tested against fakes, so a renamed keyword in the real SDK would pass every other test
and fail only in Azure. This test reads the real signatures from the packages pinned in
requirements/intake.txt and builds the real clients without any network call.

Without the SDK the tests skip; CI sets AGON_REQUIRE_AZURE_SDK=1 in the job that installs it, so
there a missing package is a failure, never a skip. The governance job, which has no SDK, runs this
file once with the variable set and must see the failure message (negative control).
"""

from __future__ import annotations

import inspect
import os

import pytest

REQUIRED = os.environ.get("AGON_REQUIRE_AZURE_SDK") == "1"
MISSING_MESSAGE = "AGON_REQUIRE_AZURE_SDK=1 but the Azure SDK packages are not installed"

try:
    import azure.core  # noqa: F401
    import azure.cosmos  # noqa: F401
    import azure.identity  # noqa: F401
    import azure.storage.queue  # noqa: F401

    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False

if not HAVE_SDK:
    if REQUIRED:
        pytest.fail(MISSING_MESSAGE, pytrace=False)
    pytest.skip("Azure SDK not installed (runs in the intake-sdk CI job)", allow_module_level=True)


class _NoCredential:
    """Never asked for a token: no test here touches the network."""

    def get_token(self, *scopes, **kwargs):
        raise AssertionError("no network call is expected in a signature test")


def _params(func) -> set[str]:
    return set(inspect.signature(func).parameters)


def test_queue_client_accepts_the_raw_text_policies_and_our_keywords():
    from azure.storage.queue import QueueClient, QueueServiceClient

    service = QueueServiceClient(account_url="https://example.queue.core.windows.net", credential=_NoCredential())
    client = service.get_queue_client("decisions-teams", message_encode_policy=None, message_decode_policy=None)
    assert isinstance(client, QueueClient)
    receive = inspect.signature(QueueClient.receive_messages)
    names = set(receive.parameters)
    assert {"messages_per_page", "visibility_timeout", "max_messages"} <= names or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in receive.parameters.values()
    ), names
    assert {"message", "pop_receipt"} <= _params(QueueClient.delete_message)
    assert "content" in _params(QueueClient.send_message)


def test_the_azure_queue_port_builds_against_the_real_sdk():
    from agon.intake.azure_queues import AzureQueuePort

    port = AzureQueuePort("https://example.queue.core.windows.net", _NoCredential())
    client = port._client("decisions-outlook")
    assert client.queue_name == "decisions-outlook"


def test_cosmos_container_calls_take_the_keywords_the_store_passes():
    from azure.cosmos import ContainerProxy, CosmosClient, DatabaseProxy

    assert {"item", "partition_key"} <= _params(ContainerProxy.read_item)
    assert "body" in _params(ContainerProxy.create_item)
    replace = inspect.signature(ContainerProxy.replace_item)
    names = set(replace.parameters)
    has_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in replace.parameters.values())
    assert {"item", "body"} <= names
    assert {"etag", "match_condition"} <= names or has_kwargs, names
    assert "get_container_client" in dir(DatabaseProxy)
    assert "get_database_client" in dir(CosmosClient)


def test_cosmos_errors_carry_the_status_codes_the_store_maps():
    from azure.cosmos import exceptions

    for cls, code in (
        (exceptions.CosmosResourceNotFoundError, 404),
        (exceptions.CosmosResourceExistsError, 409),
        (exceptions.CosmosAccessConditionFailedError, 412),
    ):
        err = cls(status_code=code, message="x")
        assert err.status_code == code


def test_match_condition_is_the_azure_core_enum():
    from azure.core import MatchConditions

    from agon.governance.store_cosmos import _if_not_modified

    assert _if_not_modified() is MatchConditions.IfNotModified


def test_managed_identity_credential_builds_without_a_token_request():
    from azure.identity import ManagedIdentityCredential

    from agon.intake.azure_queues import managed_identity_credential

    assert isinstance(managed_identity_credential("00000000-0000-0000-0000-000000000000"), ManagedIdentityCredential)

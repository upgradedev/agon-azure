"""The Publisher job's Azure calls exist, with these keywords and these headers, in the pinned SDK.

`agon.publish.azure_blob` imports the SDK lazily and is unit-tested against fakes, so a renamed
keyword or a condition the SDK maps differently would pass every other test and fail only in
Azure. This test builds the real clients from requirements/publish.txt and sends every request into
a transport that records it and raises, so the headers the service would see are asserted without
any network call or token.

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
    import azure.identity  # noqa: F401
    import azure.storage.blob  # noqa: F401
    import azure.storage.queue  # noqa: F401
    from azure.core.pipeline.transport import HttpTransport

    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False

if not HAVE_SDK:
    if REQUIRED:
        pytest.fail(MISSING_MESSAGE, pytrace=False)
    pytest.skip("Azure SDK not installed (runs in the publish-sdk CI job)", allow_module_level=True)

from agon.governance.publisher import AlreadyExists, PointerConflict  # noqa: E402
from agon.publish import azure_blob  # noqa: E402

ACCOUNT = "https://example.blob.core.windows.net"


class Captured(Exception):
    """Raised by the transport instead of sending: it is not an AzureError, so nothing retries it."""


class CaptureTransport(HttpTransport):
    def __init__(self) -> None:
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def open(self):
        return None

    def close(self):
        return None

    def send(self, request, **kwargs):
        self.requests.append(request)
        raise Captured(request.method)


class StaticToken:
    """A credential that never calls Entra: the bearer policy just needs a token object."""

    def get_token(self, *scopes, **kwargs):
        from azure.core.credentials import AccessToken

        return AccessToken("synthetic-test-token", 4_102_444_800)


def _service():
    transport = CaptureTransport()
    service = azure_blob.blob_service(ACCOUNT, StaticToken(), transport=transport, retry_total=0)
    return service, transport


def _last_headers(transport):
    request = transport.requests[-1]
    return request.method, request.url, {k.lower(): v for k, v in request.headers.items()}


def test_put_new_sends_if_none_match_star():
    service, transport = _service()
    with pytest.raises(Captured):
        azure_blob.BlobSink(service, "feed").put_new("feed/site/abc/index.html", b"<p>x</p>", "text/html; charset=utf-8")
    method, url, headers = _last_headers(transport)
    assert method == "PUT" and url.split("?")[0].endswith("/feed/site/abc/index.html")
    assert headers.get("if-none-match") == "*"
    assert "if-match" not in headers
    assert headers.get("x-ms-blob-content-type") == "text/html; charset=utf-8"


def test_a_pointer_replace_sends_if_match_with_the_etag_read():
    service, transport = _service()
    sink = azure_blob.BlobSink(service, "feed")
    with pytest.raises(Captured):
        sink.write_pointer("feed/feeds/latest.json", b"{}", "application/json", '"0x8DCETAG"')
    _, _, headers = _last_headers(transport)
    assert headers.get("if-match") == '"0x8DCETAG"' and "if-none-match" not in headers
    assert headers.get("x-ms-blob-cache-control") == "no-cache"
    with pytest.raises(Captured):
        sink.write_pointer("feed/feeds/latest.json", b"{}", "application/json", None)
    _, _, headers = _last_headers(transport)
    assert headers.get("if-none-match") == "*" and "if-match" not in headers


def test_the_artifact_source_reads_the_sha256_blob_from_the_private_container():
    service, transport = _service()
    with pytest.raises(Captured):
        azure_blob.BlobArtifactSource(service).get("a" * 64)
    method, url, _ = _last_headers(transport)
    assert method == "GET" and url.split("?")[0].endswith("/artifacts/" + "a" * 64)


def test_the_sdk_errors_carry_the_status_codes_the_sink_maps():
    from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceModifiedError, ResourceNotFoundError

    for cls in (ResourceExistsError, ResourceModifiedError, ResourceNotFoundError):
        assert issubclass(cls, HttpResponseError)
    assert "status_code" in dir(HttpResponseError("x"))
    assert azure_blob._status(ResourceExistsError("exists")) in (None, 409)
    assert issubclass(AlreadyExists, Exception) and issubclass(PointerConflict, Exception)


def test_the_blob_calls_take_the_keywords_the_sink_passes():
    from azure.core import MatchConditions
    from azure.storage.blob import BlobClient, ContentSettings, StorageStreamDownloader

    params = set(inspect.signature(BlobClient.upload_blob).parameters)
    has_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(BlobClient.upload_blob).parameters.values())
    assert "data" in params and has_kwargs
    assert {"content_type", "cache_control"} <= set(inspect.signature(ContentSettings).parameters)
    assert {"IfMissing", "IfNotModified"} <= set(MatchConditions.__members__)
    assert "properties" in dir(StorageStreamDownloader) or "properties" in StorageStreamDownloader.__init__.__code__.co_names
    assert "download_blob" in dir(BlobClient)


def test_the_queue_port_and_credential_still_build_with_the_publish_pins():
    from azure.identity import ManagedIdentityCredential

    from agon.intake.azure_queues import AzureQueuePort, managed_identity_credential

    port = AzureQueuePort("https://example.queue.core.windows.net", StaticToken())
    assert port._client("publish-requests").queue_name == "publish-requests"
    assert isinstance(managed_identity_credential("00000000-0000-0000-0000-000000000000"), ManagedIdentityCredential)

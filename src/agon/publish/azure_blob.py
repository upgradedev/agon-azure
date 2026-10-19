"""Azure Blob Storage behind the Publisher's sink and the artifact source, keyless.

The job authenticates with its user-assigned managed identity: id-publisher for the `feed`
target, id-publisher-sandbox for `sandbox` (infra/rbac.bicep grants each Storage Blob Data
Contributor on its one container, and read on the private `artifacts` container). The SDK is
imported when an adapter is built, so `agon.publish` imports with the standard library only.

`BlobSink` implements `agon.governance.publisher.PublishSink` and `PointerSink`. Sink paths are
`<container>/<blob name>`; the sink refuses any container other than the one it was built for, so
the code holds the same line RBAC holds.

* `put_new` uploads with `overwrite=False` and `match_condition=IfMissing`, which sends
  `If-None-Match: *`: the service refuses to replace an existing blob (409 or 412), and that
  becomes `AlreadyExists`, never an overwrite.
* `write_pointer` with an etag uploads with `match_condition=IfNotModified` and that etag
  (`If-Match`); without one it is created like any other blob (`If-None-Match: *`). A lost race
  (409 or 412) becomes `PointerConflict`.
* `get` and `read_pointer` treat 404 as absent.

tests/publish/test_azure_sdk_contract.py checks these calls against the pinned SDK, including the
conditional headers on the wire, through a transport that never reaches the network.
"""

from __future__ import annotations

from typing import Any

from agon.governance.publisher import AlreadyExists, PointerConflict
from agon.publish.sources import ARTIFACT_CONTAINER, check_name


def _status(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    return int(code) if isinstance(code, int) else None


def _match_conditions() -> Any:
    from azure.core import MatchConditions  # imported here on purpose

    return MatchConditions


def _content_settings(media_type: str, cache_control: str | None = None) -> Any:
    from azure.storage.blob import ContentSettings  # imported here on purpose

    return ContentSettings(content_type=media_type, cache_control=cache_control)


def blob_service(account_url: str, credential: Any, **kwargs: Any) -> Any:
    from azure.storage.blob import BlobServiceClient  # imported here on purpose

    return BlobServiceClient(account_url=account_url, credential=credential, **kwargs)


class BlobSink:
    def __init__(self, service: Any, container: str) -> None:
        self._container_name = container
        self._container = service.get_container_client(container)

    def _blob(self, path: str) -> Any:
        container, _, name = path.partition("/")
        if container != self._container_name or not name or ".." in name.split("/"):
            raise ValueError(f"{path!r} is outside container {self._container_name}")
        return self._container.get_blob_client(name)

    def put_new(self, path: str, data: bytes, media_type: str) -> None:
        client = self._blob(path)
        try:
            client.upload_blob(
                data,
                overwrite=False,
                match_condition=_match_conditions().IfMissing,
                content_settings=_content_settings(media_type),
            )
        except Exception as exc:
            if _status(exc) in (409, 412):
                raise AlreadyExists(path) from exc
            raise

    def get(self, path: str) -> bytes | None:
        try:
            return self._blob(path).download_blob().readall()
        except Exception as exc:
            if _status(exc) == 404:
                return None
            raise

    def read_pointer(self, path: str) -> tuple[bytes, str] | None:
        try:
            downloader = self._blob(path).download_blob()
        except Exception as exc:
            if _status(exc) == 404:
                return None
            raise
        return downloader.readall(), downloader.properties.etag

    def write_pointer(self, path: str, data: bytes, media_type: str, etag: str | None) -> None:
        client = self._blob(path)
        conditions = _match_conditions()
        settings = _content_settings(media_type, cache_control="no-cache")
        try:
            if etag is None:
                client.upload_blob(data, overwrite=False, match_condition=conditions.IfMissing, content_settings=settings)
            else:
                client.upload_blob(data, overwrite=True, etag=etag, match_condition=conditions.IfNotModified, content_settings=settings)
        except Exception as exc:
            if _status(exc) in (409, 412):
                raise PointerConflict(path) from exc
            raise


class BlobArtifactSource:
    """Read-only access to the private `artifacts` container, one blob per sha256."""

    def __init__(self, service: Any, container: str = ARTIFACT_CONTAINER) -> None:
        self._container = service.get_container_client(container)

    def get(self, sha256: str) -> bytes | None:
        try:
            return self._container.get_blob_client(check_name(sha256)).download_blob().readall()
        except Exception as exc:
            if _status(exc) == 404:
                return None
            raise

"""BlobSink and BlobArtifactSource on fakes: the calls, the conditions and the status mapping.

The real SDK signatures and the conditional headers on the wire are checked in
tests/publish/test_publish_sdk_contract.py, in the CI job that installs requirements/publish.txt.
"""

from __future__ import annotations

import pytest

from agon.governance.publisher import AlreadyExists, PointerConflict
from agon.publish import azure_blob as B


class Err(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class FakeBlob:
    def __init__(self, store: dict, name: str, fail: dict) -> None:
        self.store, self.name, self.fail = store, name, fail
        self.calls: list[dict] = []

    def upload_blob(self, data, **kwargs):
        self.calls.append(kwargs)
        code = self.fail.get(("upload", self.name))
        if code:
            raise Err(code)
        self.store[self.name] = (bytes(data), '"e2"')

    def download_blob(self):
        code = self.fail.get(("download", self.name))
        if code:
            raise Err(code)
        if self.name not in self.store:
            raise Err(404)
        data, etag = self.store[self.name]

        class Downloader:
            properties = type("Props", (), {"etag": etag})()

            def readall(self_inner):
                return data

        return Downloader()


class FakeContainer:
    def __init__(self) -> None:
        self.store: dict = {}
        self.fail: dict = {}
        self.blobs: dict[str, FakeBlob] = {}

    def get_blob_client(self, name):
        self.blobs.setdefault(name, FakeBlob(self.store, name, self.fail))
        return self.blobs[name]


class FakeService:
    def __init__(self) -> None:
        self.containers: dict[str, FakeContainer] = {}

    def get_container_client(self, name):
        self.containers.setdefault(name, FakeContainer())
        return self.containers[name]


@pytest.fixture(autouse=True)
def no_sdk(monkeypatch):
    class Conditions:
        IfMissing = "IfMissing"
        IfNotModified = "IfNotModified"

    monkeypatch.setattr(B, "_match_conditions", lambda: Conditions)
    monkeypatch.setattr(B, "_content_settings", lambda media_type, cache_control=None: {"content_type": media_type, "cache_control": cache_control})


def test_put_new_is_create_only_with_if_none_match():
    service = FakeService()
    sink = B.BlobSink(service, "feed")
    sink.put_new("feed/a/b.json", b"{}", "application/json")
    call = service.containers["feed"].blobs["a/b.json"].calls[0]
    assert call["overwrite"] is False and call["match_condition"] == "IfMissing"
    assert call["content_settings"]["content_type"] == "application/json"
    for code in (409, 412):
        service.containers["feed"].fail[("upload", "a/b.json")] = code
        with pytest.raises(AlreadyExists):
            sink.put_new("feed/a/b.json", b"{}", "application/json")
    service.containers["feed"].fail[("upload", "a/b.json")] = 503
    with pytest.raises(Err):
        sink.put_new("feed/a/b.json", b"{}", "application/json")


@pytest.mark.parametrize("path", ["sandbox/a.json", "feed", "feed/", "feed/../sandbox/a.json", "artifacts/x"])
def test_the_sink_refuses_any_other_container_or_a_climbing_path(path):
    with pytest.raises(ValueError):
        B.BlobSink(FakeService(), "feed").put_new(path, b"x", "text/plain")


def test_get_treats_404_as_absent_and_raises_on_anything_else():
    service = FakeService()
    sink = B.BlobSink(service, "feed")
    assert sink.get("feed/missing") is None
    sink.put_new("feed/here", b"x", "text/plain")
    assert sink.get("feed/here") == b"x"
    service.containers["feed"].fail[("download", "here")] = 500
    with pytest.raises(Err):
        sink.get("feed/here")


def test_the_pointer_is_compare_and_set_on_its_etag():
    service = FakeService()
    sink = B.BlobSink(service, "feed")
    assert sink.read_pointer("feed/feeds/latest.json") is None
    sink.write_pointer("feed/feeds/latest.json", b"v1", "application/json", None)
    blob = service.containers["feed"].blobs["feeds/latest.json"]
    assert blob.calls[-1]["overwrite"] is False and blob.calls[-1]["match_condition"] == "IfMissing"
    assert blob.calls[-1]["content_settings"]["cache_control"] == "no-cache"
    data, etag = sink.read_pointer("feed/feeds/latest.json")
    assert data == b"v1" and etag == '"e2"'
    sink.write_pointer("feed/feeds/latest.json", b"v2", "application/json", etag)
    assert blob.calls[-1]["overwrite"] is True and blob.calls[-1]["etag"] == etag and blob.calls[-1]["match_condition"] == "IfNotModified"
    for code in (409, 412):
        service.containers["feed"].fail[("upload", "feeds/latest.json")] = code
        with pytest.raises(PointerConflict):
            sink.write_pointer("feed/feeds/latest.json", b"v3", "application/json", etag)


def test_the_artifact_source_reads_one_blob_per_sha256_from_the_private_container():
    service = FakeService()
    source = B.BlobArtifactSource(service)
    sha = "a" * 64
    assert source.get(sha) is None
    service.containers["artifacts"].store[sha] = (b"bytes", '"e"')
    assert source.get(sha) == b"bytes"
    for bad in ("A" * 64, "a" * 63, "../" + "a" * 61, "a" * 64 + ".json"):
        with pytest.raises(ValueError):
            source.get(bad)
    service.containers["artifacts"].fail[("download", sha)] = 403
    with pytest.raises(Err):
        source.get(sha)

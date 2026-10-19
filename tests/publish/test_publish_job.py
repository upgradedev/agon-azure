"""The Publisher job: queue message to receipt, on fakes for the queue, Cosmos and both containers.

Pins the order in `agon.publish.job`: the manifest hash is recomputed from the Cosmos bytes before
a single artifact is fetched; a missing artifact is retried, never refused; every artifact hash is
recomputed by the Publisher; refusals are settled, retryable outcomes are left on the queue, and a
message delivered too often is given up loudly.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

import govkit
from agon.fleet.contracts import PublishRequest
from agon.governance import publisher as P
from agon.governance import verifier
from agon.governance.channels import teams
from agon.intake.drain import InboundMessage
from agon.publish import job as J
from agon.publish.sources import InMemoryArtifactSource

NOW = govkit.later(60)


class FakeQueue:
    def __init__(self) -> None:
        self.messages: dict[str, list[InboundMessage]] = {}
        self.deleted: list[str] = []
        self.received_from: list[str] = []
        self._n = 0

    def put(self, queue: str, text: str, dequeue_count: int = 1) -> str:
        self._n += 1
        mid = f"m{self._n}"
        self.messages.setdefault(queue, []).append(InboundMessage(mid, f"pop-{mid}", text, dequeue_count))
        return mid

    def receive(self, queue, max_messages, visibility_timeout):
        self.received_from.append(queue)
        return list(self.messages.get(queue, []))[:max_messages]

    def delete(self, queue, message):
        self.messages[queue] = [m for m in self.messages[queue] if m.message_id != message.message_id]
        self.deleted.append(message.message_id)

    def send(self, queue, text):
        raise AssertionError("the publisher job never sends")


class CountingSource(InMemoryArtifactSource):
    def __init__(self, blobs=()) -> None:
        super().__init__(blobs)
        self.fetches = 0

    def get(self, sha256):
        self.fetches += 1
        return super().get(sha256)


def _approved(target="feed", **kw):
    env = govkit.make_env(target=target, **kw)
    if target == "feed":
        result = teams.handle(govkit.teams_payload(env), env.store, govkit.later(30))
    else:
        from agon.governance.channels import console

        result = console.handle(govkit.console_body(env), env.store, govkit.later(30), principal=govkit.VISITOR)
    assert result.approved
    request = PublishRequest(run_id="run-1", target=target, bundle_sha256=env.bundle.bundle_sha256, approval_id=result.approval_id)
    return env, json.dumps(asdict(request), sort_keys=True)


def _drain(env, queue, target="feed", source=None, sink=None):
    source = source if source is not None else CountingSource(env.files.values())
    sink = sink if sink is not None else P.InMemorySink()
    pub = govkit.publisher(env.store, sink, target)
    lines = J.drain_once(queue, target=target, store=env.store, source=source, publisher=pub, clock=lambda: NOW)
    return lines, sink, source


def test_the_queue_names_are_pinned():
    assert J.PUBLISH_QUEUES == {"feed": "publish-requests", "sandbox": "sandbox-publish-requests"}
    assert J.REQUEST_KEYS == frozenset(f.name for f in dataclasses.fields(PublishRequest))


def test_a_publish_request_becomes_a_receipt_and_the_message_is_deleted():
    env, text = _approved()
    queue = FakeQueue()
    mid = queue.put("publish-requests", text)
    lines, sink, _ = _drain(env, queue)
    assert [(l.status, l.code) for l in lines] == [("published", "published")]
    assert queue.deleted == [mid] and queue.received_from == ["publish-requests"]
    assert env.store.read(P.PUBLISHED, env.bundle.bundle_sha256) is not None
    assert sink.get(f"feed/{env.bundle.bundle_sha256}/manifest.json") == env.bundle.manifest_bytes


def test_a_replayed_request_is_already_published_and_deleted():
    env, text = _approved()
    queue = FakeQueue()
    queue.put("publish-requests", text)
    _, sink, _ = _drain(env, queue)
    queue.put("publish-requests", text)
    lines, _, _ = _drain(env, queue, sink=sink)
    assert [l.status for l in lines] == ["already_published"] and queue.messages["publish-requests"] == []


@pytest.mark.parametrize("mutate, detail", [
    (lambda r: "not json", "not JSON"),
    (lambda r: json.dumps({**r, "target": "sandbox"}), "target"),
    (lambda r: json.dumps({**r, "extra": "x"}), "exactly"),
    (lambda r: json.dumps({**r, "bundle_sha256": "ABC"}), "64 lowercase hex"),
    (lambda r: json.dumps({**r, "approval_id": "../x"}), "malformed"),
])
def test_a_malformed_request_is_settled_as_undecodable(mutate, detail):
    env, text = _approved()
    queue = FakeQueue()
    queue.put("publish-requests", mutate(json.loads(text)))
    lines, sink, source = _drain(env, queue)
    assert lines[0].status == "undecodable" and detail in lines[0].detail
    assert sink.blobs == {} and source.fetches == 0 and queue.messages["publish-requests"] == []


def test_negative_control_a_tampered_manifest_is_refused_before_any_artifact_is_fetched():
    env, text = _approved()
    doc = env.store.read(verifier.BUNDLES, env.bundle.bundle_sha256)
    env.store.replace(verifier.BUNDLES, env.bundle.bundle_sha256, {**doc.body, "manifest": doc.body["manifest"] + " "}, if_match=doc.etag)
    queue = FakeQueue()
    queue.put("publish-requests", text)
    lines, sink, source = _drain(env, queue)
    assert (lines[0].status, lines[0].code) == ("refused", "seal_mismatch")
    assert source.fetches == 0 and sink.blobs == {}


def test_a_corrupted_artifact_blob_is_refused_by_the_publishers_seal_check():
    env, text = _approved()
    source = CountingSource()
    for name, data in env.files.items():
        sha = env.bundle.manifest["artifacts"][name]["sha256"]
        source.blobs[sha] = data + b"!" if name == "report-en.txt" else data
    queue = FakeQueue()
    queue.put("publish-requests", text)
    lines, sink, _ = _drain(env, queue, source=source)
    assert (lines[0].status, lines[0].code) == ("refused", "seal_mismatch") and sink.blobs == {}


def test_a_missing_artifact_is_retried_then_given_up_loudly():
    env, text = _approved()
    source = CountingSource(v for k, v in env.files.items() if k != "audio-el.mp3")
    queue = FakeQueue()
    queue.put("publish-requests", text)
    lines, sink, _ = _drain(env, queue, source=source)
    assert (lines[0].status, lines[0].code) == ("error", "ArtifactMissing") and sink.blobs == {}
    assert len(queue.messages["publish-requests"]) == 1  # left for another delivery
    queue.messages["publish-requests"] = []
    queue.put("publish-requests", text, dequeue_count=J.MAX_ATTEMPTS + 1)
    lines, _, _ = _drain(env, queue, source=source)
    assert lines[0].status == "gave_up" and env.bundle.bundle_sha256 in lines[0].detail
    assert queue.messages["publish-requests"] == []


def test_a_pointer_conflict_is_left_on_the_queue():
    env, text = _approved()

    class Moving(P.InMemorySink):
        def write_pointer(self, path, data, media_type, etag):
            raise P.PointerConflict(path)

    queue = FakeQueue()
    queue.put("publish-requests", text)
    lines, _, _ = _drain(env, queue, sink=Moving())
    assert (lines[0].status, lines[0].code) == ("retry", "pointer_conflict")
    assert len(queue.messages["publish-requests"]) == 1


def test_the_sandbox_job_drains_only_the_sandbox_queue_into_the_sandbox_container():
    env, text = _approved(target="sandbox")
    queue = FakeQueue()
    queue.put("publish-requests", "must not be read")
    queue.put("sandbox-publish-requests", text)
    lines, sink, _ = _drain(env, queue, target="sandbox")
    assert queue.received_from == ["sandbox-publish-requests"]
    assert [l.status for l in lines] == ["published"] and all(k.startswith("sandbox/") for k in sink.blobs)


def test_the_job_target_must_match_the_publisher_target():
    env, _ = _approved()
    with pytest.raises(ValueError):
        J.drain_once(FakeQueue(), target="sandbox", store=env.store, source=CountingSource(),
                     publisher=govkit.publisher(env.store, P.InMemorySink(), "feed"), clock=lambda: NOW)


def test_the_cli_refuses_to_start_without_its_configuration(monkeypatch, capsys):
    from agon.publish import __main__ as cli

    for name in ("AGON_PUBLISH_TARGET", "AGON_QUEUE_ACCOUNT_URL", "AGON_BLOB_ACCOUNT_URL", "AGON_COSMOS_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["--once"]) == 2
    assert "AGON_BLOB_ACCOUNT_URL" in capsys.readouterr().err


def test_the_publish_package_imports_without_the_azure_sdk():
    code = (
        "import sys\n"
        "for name in ('azure', 'azure.storage', 'azure.storage.blob', 'azure.core', 'azure.identity', 'azure.cosmos'):\n"
        "    sys.modules[name] = None\n"
        "import agon.publish, agon.publish.render, agon.publish.adapters, agon.publish.job, agon.publish.sources\n"
        "import agon.publish.azure_blob, agon.publish.webview, agon.publish.__main__\n"
        "print('ok')\n"
    )
    src = str(Path(__file__).resolve().parents[2] / "src")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env={**os.environ, "PYTHONPATH": src})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"

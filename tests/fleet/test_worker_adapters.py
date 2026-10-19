"""The worker's Azure adapters and the prompt-agent publisher against plain fakes (no SDK, no network).

tests/fleet/test_worker_sdk_contract.py checks the same calls against the pinned SDK.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fleet_support import run

from agon.agents import publish
from agon.agents.prompts import load_prompt
from agon.fleet import azure_ports
from agon.fleet.contracts import PublishRequest

IF_MISSING = object()


class HttpError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FakeDownload:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def readall(self) -> bytes:
        return self.data


class FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.calls: list[dict] = []

    def upload_blob(self, name, data, overwrite, match_condition):
        self.calls.append({"name": name, "overwrite": overwrite, "match_condition": match_condition})
        if name in self.blobs:
            raise HttpError(409)
        self.blobs[name] = data

    def download_blob(self, blob):
        if blob not in self.blobs:
            raise HttpError(404)
        return FakeDownload(self.blobs[blob])


def test_artifacts_are_content_addressed_and_create_only():
    container = FakeContainer()
    store = azure_ports.BlobArtifactStore(container, match_condition=IF_MISSING)
    sha = store.put(b"report bytes")
    assert sha == hashlib.sha256(b"report bytes").hexdigest() and container.blobs[sha] == b"report bytes"
    assert container.calls[0] == {"name": sha, "overwrite": False, "match_condition": IF_MISSING}
    assert store.put(b"report bytes") == sha, "the same bytes again are already in place"
    assert store.get(sha) == b"report bytes"
    assert store.get("0" * 64) is None


def test_an_artifact_whose_bytes_do_not_hash_to_its_name_is_refused():
    container = FakeContainer()
    store = azure_ports.BlobArtifactStore(container, match_condition=IF_MISSING)
    sha = hashlib.sha256(b"good").hexdigest()
    container.blobs[sha] = b"bad"
    with pytest.raises(azure_ports.ArtifactMismatch):
        store.get(sha)
    with pytest.raises(azure_ports.ArtifactMismatch):
        store.put(b"good")
    with pytest.raises(ValueError):
        store.get("../feed/x")


def test_an_upload_outage_is_raised_not_swallowed():
    class Down(FakeContainer):
        def upload_blob(self, **kw):
            raise HttpError(503)

    with pytest.raises(HttpError):
        azure_ports.BlobArtifactStore(Down(), match_condition=IF_MISSING).put(b"x")


class FakeQueue:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, content):
        self.sent.append(content)


def test_publish_requests_go_to_the_queue_of_their_target_as_plain_json():
    feed, sandbox = FakeQueue(), FakeQueue()
    queue = azure_ports.QueuePublishQueue({"feed": feed, "sandbox": sandbox})
    request = PublishRequest(run_id="run-1", target="sandbox", bundle_sha256="a" * 64, approval_id="b" * 32)
    assert queue.enqueue(request) is True
    assert feed.sent == [] and json.loads(sandbox.sent[0]) == {
        "run_id": "run-1", "target": "sandbox", "bundle_sha256": "a" * 64, "approval_id": "b" * 32,
    }
    with pytest.raises(ValueError):
        queue.enqueue(PublishRequest(run_id="r", target="elsewhere", bundle_sha256="a" * 64, approval_id="b" * 32))
    with pytest.raises(ValueError):
        azure_ports.QueuePublishQueue({"feed": feed})


def test_the_speech_header_names_the_resource_and_the_token():
    class Credential:
        def get_token(self, scope):
            assert scope == azure_ports.COGNITIVE_SCOPE

            class T:
                token = "tok"

            return T()

    rid = "/subscriptions/s/resourceGroups/g/providers/Microsoft.CognitiveServices/accounts/a"
    assert azure_ports.speech_auth_header(Credential(), rid)() == f"Bearer aad#{rid}#tok"
    with pytest.raises(ValueError):
        azure_ports.speech_auth_header(Credential(), "spch-agon")


# ----------------------------------------------------------------- prompt agents

MODELS = {"analyst": "gpt-5.6-luna", "writer": "gpt-5.6-terra", "localiser_editor": "gpt-5.6-luna"}


class Version:
    def __init__(self, version, description, model, instructions=None):
        self.version = version
        self.description = description
        self.definition = {"model": model, "instructions": instructions}


class FakeAgents:
    def __init__(self, versions=None, missing=()):
        self.versions = {k: list(v) for k, v in (versions or {}).items()}
        self.missing = set(missing)
        self.created: list[dict] = []

    def list_versions(self, agent_name):
        agents = self

        class Pager:
            def __aiter__(self):
                if agent_name in agents.missing:
                    raise HttpError(404)
                self._items = iter(agents.versions.get(agent_name, []))
                return self

            async def __anext__(self):
                try:
                    return next(self._items)
                except StopIteration:
                    raise StopAsyncIteration from None

        return Pager()

    async def create_version(self, agent_name, definition, description, metadata):
        version = str(len(self.versions.get(agent_name, [])) + 1)
        self.created.append({"agent_name": agent_name, "definition": definition, "description": description, "metadata": metadata})
        self.versions.setdefault(agent_name, []).append(Version(version, description, definition["model"]))
        return Version(version, description, definition["model"])


class FakeProject:
    def __init__(self, agents):
        self.agents = agents


def fake_definition(endpoint, credential, role, prompt, model):
    return {"model": model, "instructions": prompt.text}


def _desc(role):
    return publish.description_for(role, load_prompt(role), MODELS[role])


def test_existing_versions_that_carry_the_prompt_hash_are_found_newest_first():
    agents = FakeAgents({
        "agon-analyst": [Version("1", _desc("analyst"), "gpt-5.6-luna"), Version("3", _desc("analyst"), "gpt-5.6-luna"), Version("2", "old prompt", "gpt-5.6-luna")],
        "agon-writer": [Version("4", _desc("writer"), "gpt-5.6-terra")],
        "agon-localiser-editor": [Version("1", _desc("localiser_editor"), "gpt-5.6-luna")],
    })
    refs = run(publish.ensure_prompt_agents(FakeProject(agents), "https://e", None, MODELS, definition=fake_definition))
    assert [(r.role, r.version, r.action) for r in refs] == [("analyst", "3", "found"), ("writer", "4", "found"), ("localiser_editor", "1", "found")]
    assert agents.created == []


def test_a_missing_agent_or_a_stale_prompt_or_another_model_gets_a_new_version():
    agents = FakeAgents(
        {
            "agon-writer": [Version("1", "Agon writer prompt v0, sha256 deadbeef", "gpt-5.6-terra")],
            "agon-localiser-editor": [Version("1", _desc("localiser_editor"), "gpt-5.6-terra")],
        },
        missing={"agon-analyst"},
    )
    refs = run(publish.ensure_prompt_agents(FakeProject(agents), "https://e", None, MODELS, definition=fake_definition))
    assert [(r.role, r.version, r.action) for r in refs] == [("analyst", "1", "created"), ("writer", "2", "created"), ("localiser_editor", "2", "created")]
    for created in agents.created:
        role = next(r for r, n in publish.DEFAULT_AGENT_NAMES.items() if n == created["agent_name"])
        prompt = load_prompt(role)
        assert prompt.sha256 in created["description"] and created["metadata"]["agon_prompt_sha256"] == prompt.sha256
        assert created["definition"]["instructions"] == prompt.text


def test_no_create_refuses_instead_of_publishing():
    with pytest.raises(publish.PublishError, match="no version"):
        run(publish.ensure_prompt_agents(FakeProject(FakeAgents()), "https://e", None, MODELS, create=False, definition=fake_definition))
    with pytest.raises(publish.PublishError, match="must map exactly"):
        run(publish.ensure_prompt_agents(FakeProject(FakeAgents()), "https://e", None, {"writer": "x"}, definition=fake_definition))


def test_publish_refuses_to_start_without_configuration(monkeypatch):
    monkeypatch.delenv("AGON_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AGON_FOUNDRY_AGENT_MODELS", raising=False)
    assert publish.main([]) == 2
    monkeypatch.setenv("AGON_FOUNDRY_PROJECT_ENDPOINT", "https://e")
    monkeypatch.setenv("AGON_FOUNDRY_AGENT_MODELS", json.dumps({"writer": "x"}))
    assert publish.main([]) == 2

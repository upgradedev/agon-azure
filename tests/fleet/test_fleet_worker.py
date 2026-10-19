"""The fleet worker's queue handling, offline: every outcome settles, refusals resume nothing.

The engine here is the plain step sequence (tests/fleet/fleet_reference.py) plus the approval
executor's response path (`steps.decide`, `steps.enqueue_publish`), so these run without Agent
Framework on any machine. tests/fleet/test_fleet_worker_af.py drives the same worker through the
real Agent Framework graph and file checkpoints in the fleet CI job.

The decisions in these tests are verified the way production verifies them: the api's intake
(`agon.intake.handle_message`, through the Teams channel adapter) consumes the nonce and sends
the pointer; the worker only reads the record it wrote.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from typing import Any

import pytest
from fleet_reference import run_to_request
from fleet_support import OFFICER, PYTHON, run, subprocess_env

from worker_support import CONTEXT, FakePoster, MemQueues, SpyStore, intake_decides, runs_message

from agon import intake
from agon.fleet import stores, worker
from agon.fleet.contracts import (
    CHANGES_REQUESTED,
    PUBLISH_REQUESTED,
    ApprovalRequest,
    DecisionEnvelope,
    PublishRequest,
    RunInput,
)
from agon.fleet.local import fake_deps
from agon.governance import verifier


class ReferenceEngine:
    """The plain step sequence standing in for Agent Framework, with an in-memory 'checkpoint'."""

    def __init__(self, deps) -> None:
        self.deps = deps
        self.checkpoints: dict[str, tuple[str, ApprovalRequest, str]] = {}
        self.resumes = 0

    async def start(self, run_in: RunInput) -> dict[str, Any]:
        ref = await run_to_request(self.deps, run_in)
        if ref.request is None:
            return {"state": "ended", "run_id": run_in.run_id, "outcomes": [asdict(ref.outcome)]}
        checkpoint_id = f"cp-{run_in.run_id}"
        self.checkpoints[checkpoint_id] = (f"req-{run_in.run_id}", ref.request, ref.sealed.manifest_json)
        self.deps.ledger.advance(run_in.run_id, stores.AWAITING_APPROVAL, "test pause", checkpoint_id=checkpoint_id)
        return {"state": "awaiting_approval", "run_id": run_in.run_id, "checkpoint_id": checkpoint_id, "card": asdict(ref.request)}

    async def resume(self, handoff: dict[str, Any], envelope: DecisionEnvelope) -> dict[str, Any]:
        from agon.fleet import steps

        self.resumes += 1
        request_id, request, manifest_json = self.checkpoints[handoff["checkpoint_id"]]
        assert handoff["request_id"] == request_id
        result = await steps.decide(self.deps, request, envelope, manifest_json)
        if isinstance(result, PublishRequest):
            result = await steps.enqueue_publish(self.deps, result)
        return {"state": "resumed", "outcomes": [asdict(result)]}

    async def pending(self, checkpoint_id: str) -> tuple[str, ApprovalRequest]:
        if checkpoint_id not in self.checkpoints:
            raise worker.PendingError("no such checkpoint")
        request_id, request, _ = self.checkpoints[checkpoint_id]
        return request_id, request


def make(teams: bool = True, outlook: bool = True, teams_fail: int = 0):
    deps = fake_deps()
    deps.store = SpyStore()
    deps.ledger = stores.RunLedger(deps.store)  # fake_deps binds the ledger to its own store
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    engine = ReferenceEngine(deps)
    w = worker.FleetWorker(
        deps=deps,
        engine=engine,
        context=CONTEXT,
        teams=FakePoster("teams", fail=teams_fail) if teams else None,
        outlook=FakePoster("outlook") if outlook else None,
    )
    return w, engine, MemQueues()


def drain(w, queues) -> list[worker.Line]:
    return run(worker.drain_once(queues, w))


def start_and_pause(w, queues, run_id: str = "run-1") -> ApprovalRequest:
    queues.send(worker.RUNS_QUEUE, runs_message(run_id))
    lines = drain(w, queues)
    assert [ln.status for ln in lines] == ["awaiting_approval"], lines
    _, request = run(w.engine.pending(f"cp-{run_id}"))
    return request


# ----------------------------------------------------------------- runs


def test_a_run_pauses_and_both_cards_are_posted_once():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    assert queues.texts(worker.RUNS_QUEUE) == [], "a settled message is deleted"
    assert [p["bundle_sha256"] for p in w.teams.payloads] == [request.bundle_sha256]
    assert [p["subject"].startswith("Approve") for p in w.outlook.payloads] == [True]
    assert w.deps.ledger.record("run-1")["cards_posted"] == ["outlook", "teams"]
    queues.send(worker.RUNS_QUEUE, runs_message())
    lines = drain(w, queues)
    assert lines[0].code == "cards_already_posted" and len(w.teams.payloads) == 1


def test_a_sandbox_run_never_posts_to_teams():
    w, engine, queues = make()
    queues.send(worker.RUNS_QUEUE, runs_message(target="sandbox"))
    lines = drain(w, queues)
    assert lines[0].status == "awaiting_approval" and lines[0].code == "sandbox_console_only"
    assert w.teams.payloads == [] and w.outlook.payloads == []


def test_no_configured_flow_settles_with_a_visible_code():
    w, engine, queues = make(teams=False, outlook=False)
    queues.send(worker.RUNS_QUEUE, runs_message())
    assert drain(w, queues)[0].code == "no_card_channel"
    assert queues.texts(worker.RUNS_QUEUE) == []


def test_a_flow_failure_leaves_the_message_and_the_redelivery_posts_only_what_is_missing():
    w, engine, queues = make(teams_fail=1)
    queues.send(worker.RUNS_QUEUE, runs_message())
    first = drain(w, queues)
    assert first[0].status == "retry" and "503" in first[0].detail
    assert len(queues.texts(worker.RUNS_QUEUE)) == 1, "a failed post leaves the runs message"
    assert w.deps.ledger.record("run-1")["cards_posted"] == ["outlook"]
    second = drain(w, queues)
    assert second[0].status == "awaiting_approval" and second[0].code == "cards_posted:outlook,teams"
    assert len(w.teams.payloads) == 1 and len(w.outlook.payloads) == 1
    assert queues.texts(worker.RUNS_QUEUE) == []


@pytest.mark.parametrize(
    "text",
    ["not json", json.dumps(["list"]), json.dumps({"run_id": "bad id!", "match_ref": {}}), json.dumps({"run_id": "r", "match_ref": {}, "target": "elsewhere"})],
)
def test_a_malformed_runs_message_is_refused_and_deleted(text):
    w, engine, queues = make()
    queues.send(worker.RUNS_QUEUE, text)
    lines = drain(w, queues)
    assert lines[0].status == "refused" and lines[0].code == "malformed_run"
    assert queues.texts(worker.RUNS_QUEUE) == []


def test_a_base64_runs_message_is_accepted():
    import base64

    w, engine, queues = make()
    queues.send(worker.RUNS_QUEUE, base64.b64encode(runs_message().encode("utf-8")).decode("ascii"))
    assert drain(w, queues)[0].status == "awaiting_approval"


# ----------------------------------------------------------------- decisions


def test_an_approval_resumes_once_and_publishes_once_without_writing_approvals_or_nonces():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    intake_decides(w, request, queues)
    w.deps.store.writes.clear()
    lines = drain(w, queues)
    assert [(ln.status, ln.code) for ln in lines] == [("resumed", PUBLISH_REQUESTED)]
    assert engine.resumes == 1 and len(w.deps.publish_queue.items()) == 1
    assert w.deps.ledger.status("run-1") == stores.APPROVED
    touched = {c for _, c in w.deps.store.writes}
    assert verifier.APPROVALS not in touched and verifier.NONCES not in touched, w.deps.store.writes
    assert queues.texts(worker.DECISIONS_QUEUE) == []


def test_a_duplicate_pointer_does_not_resume_twice():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    pointer = intake_decides(w, request, queues)
    drain(w, queues)
    queues.send(worker.DECISIONS_QUEUE, json.dumps(pointer))
    lines = drain(w, queues)
    assert [(ln.status, ln.code) for ln in lines] == [("duplicate", "already_resumed")]
    assert engine.resumes == 1 and len(w.deps.publish_queue.items()) == 1


def test_changes_resumes_as_changes_requested_and_publishes_nothing():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    intake_decides(w, request, queues, decision="changes")
    lines = drain(w, queues)
    assert [(ln.status, ln.code) for ln in lines] == [("resumed", CHANGES_REQUESTED)]
    assert w.deps.ledger.status("run-1") == stores.CHANGES_REQUESTED and w.deps.publish_queue.items() == []
    queues.send(worker.DECISIONS_QUEUE, json.dumps(_pointer(request, "changes")))
    assert drain(w, queues)[0].status == "duplicate" and engine.resumes == 1


def _pointer(request: ApprovalRequest, decision: str = "approve", **over) -> dict[str, Any]:
    body = {
        "kind": intake.VERIFIED_KIND, "run_id": request.run_id, "approval_id": verifier.approval_id_for(request.nonce),
        "bundle_sha256": request.bundle_sha256, "decision": decision, "channel": "teams",
        "code": "approved" if decision == "approve" else "changes_requested",
    }
    body.update(over)
    return body


def _tamper_record(w, approval_id: str, **fields) -> None:
    doc = w.deps.store.read(verifier.APPROVALS, approval_id)
    w.deps.store.replace(verifier.APPROVALS, approval_id, {**doc.body, **fields}, if_match=doc.etag)


def test_a_record_whose_bundle_hash_differs_is_refused_and_resumes_nothing():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    pointer = intake_decides(w, request, queues)
    _tamper_record(w, pointer["approval_id"], bundle_sha256="0" * 64)
    lines = drain(w, queues)
    assert [(ln.status, ln.code) for ln in lines] == [("refused", "record_hash_mismatch")]
    assert engine.resumes == 0 and w.deps.ledger.status("run-1") == stores.AWAITING_APPROVAL
    assert queues.texts(worker.DECISIONS_QUEUE) == [], "a refusal settles the pointer"


def test_a_record_whose_nonce_hash_differs_is_refused_and_resumes_nothing():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    pointer = intake_decides(w, request, queues)
    _tamper_record(w, pointer["approval_id"], nonce_sha256=hashlib.sha256(b"another nonce").hexdigest())
    lines = drain(w, queues)
    assert [(ln.status, ln.code) for ln in lines] == [("refused", "record_nonce_mismatch")]
    assert engine.resumes == 0 and w.deps.publish_queue.items() == []


def test_a_pointer_for_another_bundle_is_refused():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    queues.send(worker.DECISIONS_QUEUE, json.dumps(_pointer(request, bundle_sha256="1" * 64)))
    assert drain(w, queues)[0].code == "pointer_hash_mismatch" and engine.resumes == 0


def test_a_pointer_for_an_unknown_run_is_refused():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    queues.send(worker.DECISIONS_QUEUE, json.dumps(_pointer(request, run_id="run-unknown")))
    assert drain(w, queues)[0].code == "unknown_run"


@pytest.mark.parametrize("over", [{"kind": "other/1"}, {"approval_id": "XYZ"}, {"decision": "maybe"}, {"extra": "1"}])
def test_a_malformed_pointer_is_refused(over):
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    queues.send(worker.DECISIONS_QUEUE, json.dumps(_pointer(request, **over)))
    lines = drain(w, queues)
    assert lines[0].status == "refused" and lines[0].code == "malformed_pointer"


def test_a_pointer_before_its_record_is_readable_is_retried_then_poisoned_never_dropped():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    queues.send(worker.DECISIONS_QUEUE, json.dumps(_pointer(request)))  # no verify ran: no record
    for _ in range(worker.MAX_ATTEMPTS):
        lines = drain(w, queues)
        assert lines[0].status == "retry"
    lines = drain(w, queues)
    assert lines[0].status == "poisoned"
    poisoned = json.loads(queues.texts(worker.POISON_QUEUE)[0])
    assert poisoned["queue"] == worker.DECISIONS_QUEUE and poisoned["dequeue_count"] == worker.MAX_ATTEMPTS + 1
    assert queues.texts(worker.DECISIONS_QUEUE) == [] and engine.resumes == 0


def test_an_unconsumed_nonce_is_never_resumed_so_verify_cannot_replace_it():
    """A record without a consumed nonce cannot come from verify(); the worker waits, never resumes."""
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    approval_id = verifier.approval_id_for(request.nonce)
    w.deps.store.create(verifier.APPROVALS, approval_id, {
        "approval_id": approval_id, "decision": "approve", "approver": OFFICER, "channel": "teams",
        "received_at": "2026-10-19T15:00:00+00:00", "verified_at": "2026-10-19T15:00:00+00:00",
        "confirmed_rows": list(request.confirm_rows), "bundle_sha256": request.bundle_sha256,
        "nonce_sha256": hashlib.sha256(request.nonce.encode("utf-8")).hexdigest(), "purpose": "fulltime", "target": "feed",
    })
    queues.send(worker.DECISIONS_QUEUE, json.dumps(_pointer(request)))
    w.deps.store.writes.clear()
    lines = drain(w, queues)
    assert lines[0].status == "retry" and "nonce" in lines[0].detail
    assert engine.resumes == 0 and w.deps.store.writes == []
    assert w.deps.store.read(verifier.NONCES, request.nonce).body["status"] == "pending"


def test_an_approved_run_whose_publish_send_was_lost_gets_it_resent_without_a_resume():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    pointer = intake_decides(w, request, queues)
    # The worker died after the ledger moved to APPROVED and before the publish request was sent.
    w.deps.ledger.advance("run-1", stores.APPROVED, "test crash", approval_id=pointer["approval_id"])
    lines = drain(w, queues)
    assert [(ln.status, ln.code) for ln in lines] == [("publish_resent", "publish_requested")]
    assert engine.resumes == 0
    assert [i["approval_id"] for i in w.deps.publish_queue.items()] == [pointer["approval_id"]]
    assert w.deps.ledger.record("run-1")["publish_enqueued"] == pointer["approval_id"]
    queues.send(worker.DECISIONS_QUEUE, json.dumps(pointer))
    assert drain(w, queues)[0].status == "duplicate"


def test_decisions_drain_before_runs():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    intake_decides(w, request, queues)
    queues.send(worker.RUNS_QUEUE, runs_message("run-2"))
    lines = drain(w, queues)
    assert [ln.queue for ln in lines] == [worker.DECISIONS_QUEUE, worker.RUNS_QUEUE]


# ----------------------------------------------------------------- logging, configuration, imports


def test_redact_masks_a_nonce_and_keeps_hashes():
    nonce = "Abc_def-0123456789abcdefghijklmnopqrstuvwxyzQ"
    sha = "a" * 64
    text = worker.redact(f"read nonces/{nonce}: CosmosHttpResponseError on {sha}")
    assert nonce not in text and "<redacted>" in text and sha in text


def test_no_log_line_carries_the_nonce():
    w, engine, queues = make()
    request = start_and_pause(w, queues)
    intake_decides(w, request, queues)
    printed = []
    run(worker.drain_once(queues, w, log=lambda line: printed.append(json.dumps(asdict(line)))))
    assert printed and all(request.nonce not in p for p in printed)


ENV = {
    "AGON_QUEUE_ACCOUNT_URL": "https://a.queue.core.windows.net",
    "AGON_BLOB_ACCOUNT_URL": "https://a.blob.core.windows.net",
    "AGON_COSMOS_ENDPOINT": "https://c.documents.azure.com:443/",
    "AGON_FOUNDRY_PROJECT_ENDPOINT": "https://f.services.ai.azure.com/api/projects/agon",
    "AGON_FOUNDRY_STRUCTURED_MODEL": "gpt-5.6-luna",
    "AGON_FOUNDRY_AGENT_MODELS": json.dumps({"analyst": "gpt-5.6-luna", "writer": "gpt-5.6-terra", "localiser_editor": "gpt-5.6-luna"}),
    "AGON_TRANSLATOR_ENDPOINT": "https://t.cognitiveservices.azure.com",
    "AGON_SPEECH_ENDPOINT": "https://s.cognitiveservices.azure.com/",
    "AGON_SPEECH_RESOURCE_ID": "/subscriptions/x/resourceGroups/y/providers/Microsoft.CognitiveServices/accounts/s",
    "AGON_FEED_BASE_URL": "https://a.blob.core.windows.net/feed/",
}


def test_config_reads_the_environment_and_derives_the_speech_url():
    config = worker.WorkerConfig.from_env(ENV, ensure_agents=True)
    assert config.speech_tts_url == "https://s.cognitiveservices.azure.com/tts/cognitiveservices/v1"
    assert config.foundry_agent_models["writer"] == "gpt-5.6-terra" and config.cosmos_database == "agon"
    assert config.teams_flow_url == "" and config.outlook_flow_url == ""


def test_config_refuses_missing_settings_and_a_sas_flow_url():
    with pytest.raises(worker.ConfigError, match="AGON_FOUNDRY_AGENT_VERSIONS"):
        worker.WorkerConfig.from_env(ENV, ensure_agents=False)
    with pytest.raises(worker.ConfigError, match="SAS"):
        worker.WorkerConfig.from_env(
            {**ENV, "AGON_TEAMS_FLOW_URL": "https://x.logic.azure.com/w?api-version=2016-10-01&sp=a&sv=1.0&sig=s"}, ensure_agents=True
        )
    with pytest.raises(worker.ConfigError, match="must map exactly"):
        worker.WorkerConfig.from_env({**ENV, "AGON_FOUNDRY_AGENT_MODELS": json.dumps({"writer": "x"})}, ensure_agents=True)


def test_the_worker_refuses_to_start_without_configuration(monkeypatch):
    for key in list(ENV) + ["AGON_FOUNDRY_AGENT_VERSIONS"]:
        monkeypatch.delenv(key, raising=False)
    assert worker.main(["--once"]) == 2


def test_importing_the_worker_loads_no_azure_sdk_and_no_agent_framework():
    code = (
        "import sys, agon.fleet.worker, agon.fleet.azure_ports, agon.fleet.card, agon.agents.publish\n"
        "heavy = sorted(m for m in sys.modules if m.split('.')[0] in ('azure', 'agent_framework'))\n"
        "print(heavy)\n"
        "sys.exit(1 if heavy else 0)\n"
    )
    done = subprocess.run([PYTHON, "-c", code], capture_output=True, text=True, env=subprocess_env(), timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_negative_control_the_import_check_sees_a_heavy_import():
    code = "import sys, json\nsys.modules['azure.fake'] = json\nheavy = [m for m in sys.modules if m.split('.')[0] == 'azure']\nsys.exit(1 if heavy else 0)\n"
    assert subprocess.run([PYTHON, "-c", code], env=subprocess_env(), timeout=60).returncode == 1


def test_pending_approval_reads_exactly_one_request():
    class Event:
        def __init__(self, data):
            self.data = data

    class Checkpoint:
        def __init__(self, events):
            self.pending_request_info_events = events

    request = ApprovalRequest("r", "feed", "a" * 64, "n", "2026-10-19T15:30:00+00:00", {"attested": [], "gate_cleared": []}, [])
    assert worker.pending_approval(Checkpoint({"id-1": Event(request)})) == ("id-1", request)
    for events in ({}, {"a": Event(request), "b": Event(request)}, {"a": Event({"not": "a request"})}):
        with pytest.raises(worker.PendingError):
            worker.pending_approval(Checkpoint(events))

"""W-charter offline: its pinned contract, its steps, the one verifier, the Publisher and the worker routing.

Everything here runs without Agent Framework (tests/fleet/test_charter_workflow_af.py drives the
real graph in the fleet CI job). The decisions are verified the way production verifies them: the
api's intake consumes the nonce through the Teams channel adapter and sends the pointer.
"""

from __future__ import annotations

import dataclasses
import json
import pickle
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import pytest
from charter_reference import charter_to_request
from fleet_reference import run_to_request
from fleet_support import OFFICER, run, run_input
from test_fleet_contracts import JSON_NATIVE_ANNOTATIONS, PINNED_ALLOWED_CHECKPOINT_TYPES
from worker_support import CONTEXT, FakePoster, MemQueues, SpyStore, intake_decides, runs_message

from agon import charter as charter_mod
from agon.fleet import charter_contracts, charter_steps, contracts, kinds, steps, stores, worker
from agon.fleet.contracts import (
    APPROVAL_ALREADY_REQUESTED,
    AWAITING_DECISION,
    CHANGES_REQUESTED,
    PUBLISH_REQUESTED,
    REFUSED_AT_INGEST,
    REFUSED_BY_GATE,
    ApprovalRequest,
    DecisionEnvelope,
    PublishRequest,
    RunInput,
)
from agon.fleet.local import fake_deps
from agon.governance import seal as seal_mod
from agon.governance import verifier
from agon.governance.publisher import InMemorySink, Publisher
from agon.publish.adapters import default_adapters

PINNED_WORKFLOW_NAME = "agon_w_charter"
PINNED_EXECUTOR_IDS = ("charter_build", "charter_check", "seal", "approval", "publish_request")
PINNED_CHARTER_CHECKPOINT_TYPES = [
    "agon.fleet.contracts:RunInput",
    "agon.fleet.contracts:Draft",
    "agon.fleet.charter_contracts:CharterBuilt",
    "agon.fleet.charter_contracts:CharterChecked",
    "agon.fleet.contracts:SealedDraft",
    "agon.fleet.contracts:ApprovalRequest",
    "agon.fleet.contracts:DecisionEnvelope",
    "agon.fleet.contracts:PublishRequest",
    "agon.fleet.contracts:RunOutcome",
]
PINNED_RUN_KINDS = ("fulltime", "charter")


def charter_input(run_id: str = "charter-1", seed: int = 1, target: str = "feed") -> RunInput:
    return RunInput(run_id=run_id, match_ref={"source": "synth", "seed": seed, "match_index": 0}, target=target, kind="charter")


def _deps():
    deps = fake_deps()
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    return deps


# ----------------------------------------------------------------- the pinned contract


def test_w_charter_contract_is_pinned() -> None:
    assert charter_contracts.WORKFLOW_NAME == PINNED_WORKFLOW_NAME
    assert charter_contracts.EXECUTOR_IDS == PINNED_EXECUTOR_IDS
    assert charter_contracts.ALLOWED_CHECKPOINT_TYPES == PINNED_CHARTER_CHECKPOINT_TYPES


def test_w_fulltime_contract_is_unchanged_and_the_storage_decodes_both() -> None:
    assert contracts.WORKFLOW_NAME == "agon_w_fulltime"
    assert contracts.ALLOWED_CHECKPOINT_TYPES == PINNED_ALLOWED_CHECKPOINT_TYPES
    assert kinds.STORAGE_ALLOWED_CHECKPOINT_TYPES == PINNED_ALLOWED_CHECKPOINT_TYPES + [
        "agon.fleet.charter_contracts:CharterBuilt",
        "agon.fleet.charter_contracts:CharterChecked",
    ]


def test_every_charter_message_field_is_json_native() -> None:
    bad = [
        f"{cls.__name__}.{f.name}: {f.type}"
        for cls in charter_contracts.CHECKPOINT_TYPES
        for f in dataclasses.fields(cls)
        if f.type not in JSON_NATIVE_ANNOTATIONS
    ]
    assert bad == []


def test_run_kinds_are_an_extensible_table() -> None:
    assert kinds.RUN_KINDS == PINNED_RUN_KINDS
    assert set(kinds.WORKFLOW_NAMES) == set(kinds.RUN_KINDS) == set(kinds.CARD_CHANNELS)
    assert kinds.WORKFLOW_NAMES == {"fulltime": "agon_w_fulltime", "charter": "agon_w_charter"}
    assert kinds.CARD_CHANNELS["charter"] == ("teams",)
    assert kinds.kind_for_workflow("agon_w_charter") == "charter"
    with pytest.raises(ValueError):
        kinds.kind_for_workflow("agon_w_unknown")


def test_run_input_kind_defaults_to_fulltime_and_is_checkpoint_safe() -> None:
    assert run_input().kind == "fulltime"
    old = RunInput(run_id="r", match_ref={"source": "synth", "seed": 1, "match_index": 0})
    del old.__dict__["kind"]  # an instance pickled before the field existed
    restored = pickle.loads(pickle.dumps(old))
    assert restored.kind == "fulltime"


# ----------------------------------------------------------------- the steps, the verifier, the Publisher


def test_a_charter_run_reaches_an_approval_request_with_the_charter_purpose() -> None:
    deps = _deps()
    ref = run(charter_to_request(deps, charter_input()))
    assert ref.request is not None, ref.outcome
    request = ref.request
    assert request.confirm_rows == [] and request.destinations == []
    manifest = json.loads(deps.store.read(verifier.BUNDLES, request.bundle_sha256).body["manifest"])
    assert manifest["kind"] == "charter"
    assert set(manifest["artifacts"]) == {"charter.json"}
    assert manifest["event_log_sha256"] == charter_mod.EMPTY_EVENT_LOG_SHA256
    assert manifest["gate_verdicts"] == {"charter_check": "pass", "rights": "pass"}
    assert "prompts" not in manifest["deployment_ids"], "no model wrote the charter"
    assert deps.store.read(verifier.NONCES, request.nonce).body["purpose"] == "charter"
    record = deps.ledger.record("charter-1")
    assert record["kind"] == "charter" and record["status"] == stores.SEALED
    charter = json.loads(deps.artifacts.get(manifest["artifacts"]["charter.json"]["sha256"]))
    assert charter == charter_mod.build_charter(deps.matches.load(charter_input().match_ref))


def _intake(deps, request: ApprovalRequest, decision: str = "approve") -> dict[str, Any]:
    class W:  # intake_decides reads only w.deps
        pass

    w = W()
    w.deps = deps
    queues = MemQueues()
    return intake_decides(w, request, queues, decision=decision)


def _envelope(deps, request: ApprovalRequest, pointer: dict[str, Any]) -> DecisionEnvelope:
    record = deps.store.read(verifier.APPROVALS, pointer["approval_id"]).body
    return steps.envelope_from_record(record, request.nonce)


def test_an_approved_charter_is_published_to_the_feed_archive_only() -> None:
    deps = _deps()
    ref = run(charter_to_request(deps, charter_input()))
    request = ref.request
    pointer = _intake(deps, request)
    result = run(steps.decide(deps, request, _envelope(deps, request, pointer), ref.sealed.manifest_json))
    assert isinstance(result, PublishRequest) and result.target == "feed"
    outcome = run(steps.enqueue_publish(deps, result))
    assert outcome.status == PUBLISH_REQUESTED

    approval = deps.store.read(verifier.APPROVALS, result.approval_id).body
    assert approval["purpose"] == "charter" and approval["destinations"] == []
    manifest = json.loads(deps.store.read(verifier.BUNDLES, request.bundle_sha256).body["manifest"])
    files = {name: deps.artifacts.get(entry["sha256"]) for name, entry in manifest["artifacts"].items()}
    sink = InMemorySink()
    publisher = Publisher(deps.store, sink, "feed", default_adapters(""))
    published = publisher.publish(
        bundle_sha256=request.bundle_sha256,
        approval_id=result.approval_id,
        manifest_bytes=ref.sealed.manifest_json.encode("utf-8"),
        artifacts=files,
        now=datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert published.ok, published
    assert sorted(sink.blobs) == [f"feed/{request.bundle_sha256}/charter.json", f"feed/{request.bundle_sha256}/manifest.json"]
    receipt = published.receipt
    assert receipt["kind"] == "charter" and receipt["destinations_offered"] == [] and receipt["destinations_approved"] == []
    assert seal_mod.sha256_hex(sink.get(f"feed/{request.bundle_sha256}/manifest.json")) == request.bundle_sha256


def test_changes_requested_on_a_charter_publishes_nothing() -> None:
    deps = _deps()
    ref = run(charter_to_request(deps, charter_input()))
    pointer = _intake(deps, ref.request, decision="changes")
    result = run(steps.decide(deps, ref.request, _envelope(deps, ref.request, pointer), ref.sealed.manifest_json))
    assert result.status == CHANGES_REQUESTED
    assert deps.publish_queue.items() == []


def test_a_charter_the_gate_refuses_gets_no_nonce(monkeypatch) -> None:
    def planted(record, voices=None):
        charter = charter_mod.build_charter(record, voices)
        charter["templates"]["goal"]["text"]["en"] = "GOAL {minute}: {player} {secret_slot}"
        return charter

    monkeypatch.setattr(charter_steps, "build_charter", planted)
    deps = _deps()
    ref = run(charter_to_request(deps, charter_input()))
    assert ref.request is None and ref.outcome.status == REFUSED_BY_GATE
    assert "unknown slot 'secret_slot'" in ref.outcome.detail
    manifest = json.loads(ref.sealed.manifest_json)
    assert manifest["gate_verdicts"] == {"charter_check": "refused", "rights": "refused"}
    assert deps.ledger.record("charter-1").get("approval_claim") is None, "no approval was ever requested"
    assert deps.ledger.status("charter-1") == stores.REFUSED


def test_each_workflow_refuses_the_other_kind_and_run_ids_never_cross_kinds() -> None:
    deps = _deps()
    wrong = run(steps.ingest(deps, charter_input()))
    assert wrong.status == REFUSED_AT_INGEST and wrong.code == "wrong_workflow"
    wrong = run(charter_steps.charter_build(deps, run_input("charter-1")))
    assert wrong.status == REFUSED_AT_INGEST and wrong.code == "wrong_workflow"

    run(charter_to_request(deps, charter_input("shared-id")))
    reused = run(steps.ingest(deps, run_input("shared-id")))
    assert reused.status == REFUSED_AT_INGEST and reused.code == "run_id_reused"

    run(run_to_request(deps, run_input("report-id")))
    reused = run(charter_steps.charter_build(deps, charter_input("report-id")))
    assert reused.status == REFUSED_AT_INGEST and reused.code == "run_id_reused"


def test_a_redelivered_charter_run_issues_no_second_nonce() -> None:
    deps = _deps()
    first = run(charter_to_request(deps, charter_input()))
    deps.ledger.advance("charter-1", stores.AWAITING_APPROVAL, "test pause", checkpoint_id="cp-1")
    again = run(charter_steps.charter_build(deps, charter_input()))
    assert again.status == AWAITING_DECISION and again.bundle_sha256 == first.request.bundle_sha256
    raced = run(steps.prepare_approval(deps, first.sealed))
    assert raced.status == APPROVAL_ALREADY_REQUESTED


# ----------------------------------------------------------------- the worker routes by kind


class KindEngine:
    """The plain step sequences standing in for Agent Framework, one per kind, recording the routing."""

    def __init__(self, deps) -> None:
        self.deps = deps
        self.started: list[tuple[str, str]] = []
        self.checkpoints: dict[str, tuple[str, ApprovalRequest, str, str]] = {}
        self.resumed: list[str] = []

    async def start(self, run_in: RunInput) -> dict[str, Any]:
        self.started.append((run_in.run_id, run_in.kind))
        sequence = charter_to_request if run_in.kind == "charter" else run_to_request
        ref = await sequence(self.deps, run_in)
        if ref.request is None:
            return {"state": "ended", "run_id": run_in.run_id, "outcomes": [asdict(ref.outcome)]}
        checkpoint_id = f"cp-{run_in.run_id}"
        self.checkpoints[checkpoint_id] = (f"req-{run_in.run_id}", ref.request, ref.sealed.manifest_json, kinds.WORKFLOW_NAMES[run_in.kind])
        self.deps.ledger.advance(run_in.run_id, stores.AWAITING_APPROVAL, "test pause", checkpoint_id=checkpoint_id)
        return {"state": "awaiting_approval", "run_id": run_in.run_id, "checkpoint_id": checkpoint_id, "card": asdict(ref.request)}

    async def resume(self, handoff: dict[str, Any], envelope: DecisionEnvelope) -> dict[str, Any]:
        _, request, manifest_json, workflow_name = self.checkpoints[handoff["checkpoint_id"]]
        self.resumed.append(workflow_name)
        result = await steps.decide(self.deps, request, envelope, manifest_json)
        if isinstance(result, PublishRequest):
            result = await steps.enqueue_publish(self.deps, result)
        return {"state": "resumed", "outcomes": [asdict(result)]}

    async def pending(self, checkpoint_id: str) -> tuple[str, ApprovalRequest]:
        if checkpoint_id not in self.checkpoints:
            raise worker.PendingError("no such checkpoint")
        request_id, request, _, _ = self.checkpoints[checkpoint_id]
        return request_id, request


def charter_message(run_id: str = "charter-1", target: str = "feed") -> str:
    return json.dumps({"run_id": run_id, "match_ref": charter_input(run_id).match_ref, "target": target, "kind": "charter"})


def _worker():
    deps = fake_deps()
    deps.store = SpyStore()
    deps.ledger = stores.RunLedger(deps.store)
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    engine = KindEngine(deps)
    w = worker.FleetWorker(deps=deps, engine=engine, context=CONTEXT, teams=FakePoster("teams"), outlook=FakePoster("outlook"))
    return w, engine, MemQueues()


def test_the_runs_message_parser_knows_the_kinds() -> None:
    assert worker.parse_run_input(runs_message()).kind == "fulltime"
    assert worker.parse_run_input(charter_message()).kind == "charter"
    body = json.loads(charter_message())
    for kind in ("halftime", "", 3, None):
        body["kind"] = kind
        with pytest.raises(worker.MessageError):
            worker.parse_run_input(json.dumps(body))


def test_the_worker_routes_a_charter_and_posts_the_charter_card_to_teams_only() -> None:
    w, engine, queues = _worker()
    queues.send(worker.RUNS_QUEUE, charter_message())
    queues.send(worker.RUNS_QUEUE, runs_message("report-1"))
    first = run(worker.drain_once(queues, w))
    second = run(worker.drain_once(queues, w))
    assert [ln.status for ln in first + second] == ["awaiting_approval", "awaiting_approval"]
    assert engine.started == [("charter-1", "charter"), ("report-1", "fulltime")]

    charter_card, report_card = w.teams.payloads
    assert charter_card["title"].startswith("Match-Day Charter: ") and charter_card["title"].endswith(", approve the live cue rules")
    assert charter_card["destinations"] == [] and charter_card["confirm_rows"] == []
    assert "not a report" in charter_card["attested"] and "not a report" in charter_card["match_line"]
    assert report_card["title"].startswith("Full-time report ready")
    assert [p["run_id"] for p in w.outlook.payloads] == ["report-1"], "the charter is never emailed"
    assert w.deps.ledger.record("charter-1")["cards_posted"] == ["teams"]


def test_a_verified_charter_decision_resumes_the_charter_workflow_once() -> None:
    w, engine, queues = _worker()
    queues.send(worker.RUNS_QUEUE, charter_message())
    run(worker.drain_once(queues, w))
    _, request = run(engine.pending("cp-charter-1"))
    pointer = intake_decides(w, request, queues)
    w.deps.store.writes.clear()
    lines = run(worker.drain_once(queues, w))
    assert [(ln.status, ln.code) for ln in lines] == [("resumed", PUBLISH_REQUESTED)], lines
    assert engine.resumed == ["agon_w_charter"]
    items = w.deps.publish_queue.items()
    assert [(i["approval_id"], i["bundle_sha256"], i["target"]) for i in items] == [(pointer["approval_id"], request.bundle_sha256, "feed")]
    touched = {container for _, container in w.deps.store.writes}
    assert verifier.APPROVALS not in touched and verifier.NONCES not in touched, w.deps.store.writes


def test_a_sandbox_charter_never_reaches_teams() -> None:
    w, engine, queues = _worker()
    queues.send(worker.RUNS_QUEUE, charter_message(target="sandbox"))
    lines = run(worker.drain_once(queues, w))
    assert lines[0].code == "sandbox_console_only" and w.teams.payloads == []


def test_the_charter_modules_import_without_agent_framework() -> None:
    import subprocess

    from fleet_support import PYTHON, subprocess_env

    code = (
        "import sys\n"
        "for name in ('agent_framework', 'agent_framework_azure_cosmos', 'azure', 'azure.identity'):\n"
        "    sys.modules[name] = None\n"
        "import agon.charter, agon.fleet.kinds, agon.fleet.charter_contracts, agon.fleet.charter_steps, agon.fleet.worker\n"
        "print('ok')\n"
    )
    out = subprocess.run([PYTHON, "-c", code], capture_output=True, text=True, env=subprocess_env(), timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"

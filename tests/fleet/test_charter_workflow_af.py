"""W-charter inside Microsoft Agent Framework 1.22.0: file checkpoints, fake ports, the real graph.

The graph reaches `request_info` at `approval`, the pause checkpoint names W-charter, and a fresh
workflow resumes it after a verified decision with exactly one publish request. The fleet worker
routes a kind "charter" runs message to this graph, posts the charter card to Teams and resumes
the graph the checkpoint names when the intake's pointer arrives.

Skipped where agent-framework-core is not installed; the fleet CI job sets AGON_REQUIRE_AF=1,
which turns that skip into a failure.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fleet_support import OFFICER, require_agent_framework, run, run_input

require_agent_framework()

from charter_reference import charter_to_request  # noqa: E402
from worker_support import CONTEXT, FakePoster, MemQueues, intake_decides  # noqa: E402

from agon import charter as charter_mod  # noqa: E402
from agon.fleet import charter_steps, stores, worker  # noqa: E402
from agon.fleet.charter_contracts import EXECUTOR_IDS, WORKFLOW_NAME  # noqa: E402
from agon.fleet.charter_workflow import build_charter_workflow  # noqa: E402
from agon.fleet.checkpoints import GuardedCheckpointStorage, file_storage  # noqa: E402
from agon.fleet.contracts import (  # noqa: E402
    CHANGES_REQUESTED,
    PUBLISH_ALREADY_REQUESTED,
    PUBLISH_REQUESTED,
    REFUSED_BY_GATE,
    RunInput,
)
from agon.fleet.deps import read_trace  # noqa: E402
from agon.fleet.local import fake_deps  # noqa: E402
from agon.fleet.runner import build_for_kind, envelope_from_card, resume_run, start_run  # noqa: E402
from agon.fleet.workflow import build_workflow  # noqa: E402
from agon.governance import verifier  # noqa: E402


def charter_input(run_id: str = "charter-1") -> RunInput:
    return RunInput(run_id=run_id, match_ref={"source": "synth", "seed": 1, "match_index": 0}, target="feed", kind="charter")


def _deps(root: Path):
    deps = fake_deps(root / "state")
    if deps.store.read(verifier.APPROVERS, OFFICER) is None:
        verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    return deps


def _storage(root: Path) -> GuardedCheckpointStorage:
    return GuardedCheckpointStorage(file_storage(root / "checkpoints"))


def _start(root: Path, run_in: RunInput | None = None):
    deps = _deps(root)
    storage = _storage(root)
    return deps, storage, run(start_run(deps, storage, run_in or charter_input()))


def _resume(root: Path, handoff: dict, decision: str = "approve"):
    envelope = envelope_from_card(handoff["card"], decision=decision, approver=OFFICER, channel="teams", confirm_all=True)
    return run(resume_run(_deps(root), _storage(root), handoff, envelope))


def test_the_charter_graph_has_exactly_the_pinned_executor_ids(tmp_path: Path) -> None:
    workflow = build_charter_workflow(fake_deps(), file_storage(tmp_path))
    assert workflow.name == WORKFLOW_NAME == "agon_w_charter"
    assert sorted(workflow.executors) == sorted(EXECUTOR_IDS)
    assert build_for_kind("charter", fake_deps(), file_storage(tmp_path)).name == WORKFLOW_NAME
    fulltime = build_workflow(fake_deps(), file_storage(tmp_path))
    assert workflow.graph_signature_hash != fulltime.graph_signature_hash


def test_a_charter_run_pauses_at_request_info_in_a_charter_checkpoint(tmp_path: Path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        deps, storage, handoff = _start(tmp_path)
    assert handoff["state"] == "awaiting_approval", handoff
    assert handoff["workflow_name"] == WORKFLOW_NAME and handoff["source_executor_id"] == "approval"
    assert storage.failures == [] and "Failed to create checkpoint" not in caplog.text
    checkpoint = run(storage.load(handoff["checkpoint_id"]))
    assert checkpoint.workflow_name == WORKFLOW_NAME
    request_id, request = worker.pending_approval(checkpoint)
    assert request_id == handoff["request_id"] and request.bundle_sha256 == handoff["card"]["bundle_sha256"]
    manifest = json.loads(deps.store.read(verifier.BUNDLES, request.bundle_sha256).body["manifest"])
    assert manifest["kind"] == "charter" and set(manifest["artifacts"]) == {"charter.json"}
    assert deps.store.read(verifier.NONCES, request.nonce).body["purpose"] == "charter"
    assert deps.ledger.status("charter-1") == stores.AWAITING_APPROVAL
    ran = {row["executor"] for row in read_trace(tmp_path / "state" / "trace.ndjson")}
    assert ran == set(EXECUTOR_IDS) - {"publish_request"}


def test_agent_framework_and_the_plain_sequence_seal_the_same_charter(tmp_path: Path) -> None:
    _, _, handoff = _start(tmp_path)
    reference = run(charter_to_request(fake_deps(), charter_input()))
    assert handoff["card"]["bundle_sha256"] == reference.sealed.bundle_sha256


def test_a_verified_approval_resumes_in_a_fresh_workflow_with_one_publish_request(tmp_path: Path) -> None:
    deps, _, handoff = _start(tmp_path)
    first = _resume(tmp_path, handoff)
    assert [o["status"] for o in first["outcomes"]] == [PUBLISH_REQUESTED], first
    assert first["reemitted_request_ids"] == []
    replay = _resume(tmp_path, handoff)
    assert [o["status"] for o in replay["outcomes"]] == [PUBLISH_ALREADY_REQUESTED]
    items = deps.publish_queue.items()
    assert len(items) == 1 and items[0]["bundle_sha256"] == handoff["card"]["bundle_sha256"]
    assert deps.ledger.status("charter-1") == stores.APPROVED


def test_changes_requested_on_a_charter_publishes_nothing(tmp_path: Path) -> None:
    deps, _, handoff = _start(tmp_path)
    out = _resume(tmp_path, handoff, decision="changes")
    assert [o["status"] for o in out["outcomes"]] == [CHANGES_REQUESTED]
    assert deps.publish_queue.items() == []


def test_the_fulltime_graph_cannot_resume_a_charter_checkpoint(tmp_path: Path) -> None:
    _, _, handoff = _start(tmp_path)
    wrong = {**handoff, "workflow_name": "agon_w_fulltime"}
    with pytest.raises(Exception):
        _resume(tmp_path, wrong)


def test_a_charter_the_gate_refuses_ends_without_a_request(tmp_path: Path, monkeypatch) -> None:
    def planted(record, voices=None):
        charter = charter_mod.build_charter(record, voices)
        charter["templates"]["red_card"]["text"]["es"] = "Tarjeta roja {minute}: {player}, dijo el árbitro."
        return charter

    monkeypatch.setattr(charter_steps, "build_charter", planted)
    deps, _, handoff = _start(tmp_path)
    assert handoff["state"] == "ended"
    assert [o["status"] for o in handoff["outcomes"]] == [REFUSED_BY_GATE]
    assert deps.store.keys(verifier.NONCES) == []


def test_a_fulltime_run_still_pauses_on_the_shared_storage(tmp_path: Path) -> None:
    deps, storage, handoff = _start(tmp_path, run_input("report-1"))
    assert handoff["state"] == "awaiting_approval" and handoff["workflow_name"] == "agon_w_fulltime"
    assert storage.failures == []


# ----------------------------------------------------------------- the worker on the real graph


def test_the_worker_routes_a_charter_through_the_real_graph_and_resumes_it_once(tmp_path: Path) -> None:
    deps = fake_deps(tmp_path / "stores")
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    engine = worker.AfEngine(deps, file_storage(tmp_path / "checkpoints"))
    w = worker.FleetWorker(deps=deps, engine=engine, context=CONTEXT, teams=FakePoster("teams"), outlook=FakePoster("outlook"))
    queues = MemQueues()
    body = {"run_id": "charter-1", "match_ref": charter_input().match_ref, "target": "feed", "kind": "charter"}
    queues.send(worker.RUNS_QUEUE, json.dumps(body))
    lines = run(worker.drain_once(queues, w))
    assert [ln.status for ln in lines] == ["awaiting_approval"], lines
    record = deps.ledger.record("charter-1")
    assert record["kind"] == "charter" and record["cards_posted"] == ["teams"]
    assert w.outlook.payloads == []
    payload = w.teams.payloads[0]
    assert payload["title"].startswith("Match-Day Charter: ") and payload["destinations"] == []

    _, request = run(engine.pending(record["checkpoint_id"]))
    assert payload["bundle_sha256"] == request.bundle_sha256 and payload["nonce"] == request.nonce
    pointer = intake_decides(w, request, queues)
    resumed = run(worker.drain_once(queues, w))
    assert [(ln.status, ln.code) for ln in resumed] == [("resumed", PUBLISH_REQUESTED)], resumed
    items = deps.publish_queue.items()
    assert [(i["approval_id"], i["bundle_sha256"]) for i in items] == [(pointer["approval_id"], request.bundle_sha256)]

    queues.send(worker.DECISIONS_QUEUE, json.dumps(pointer))
    again = run(worker.drain_once(queues, w))
    assert [(ln.status, ln.code) for ln in again] == [("duplicate", "already_resumed")]

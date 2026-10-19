"""The fleet worker end to end on the real Agent Framework graph, file checkpoints and fake ports.

A runs message drives W-fulltime to its approval pause and posts the card to a fake Teams poster.
The api's intake then verifies the officer's tap through the Teams channel adapter and sends the
pointer; the worker reads the pending ApprovalRequest from the real pause checkpoint, checks the
approval record against it, and resumes the graph: exactly one publish request. A duplicate
pointer resumes nothing. Runs in the fleet CI job (Agent Framework installed); skips elsewhere.
"""

from __future__ import annotations

import json
from pathlib import Path

from fleet_support import OFFICER, require_agent_framework, run
from worker_support import CONTEXT, FakePoster, MemQueues, intake_decides, runs_message

require_agent_framework()

from agon.fleet import stores, worker  # noqa: E402
from agon.fleet.checkpoints import file_storage  # noqa: E402
from agon.fleet.contracts import CHANGES_REQUESTED, PUBLISH_REQUESTED  # noqa: E402
from agon.fleet.local import fake_deps  # noqa: E402
from agon.governance import verifier  # noqa: E402


class CountingEngine(worker.AfEngine):
    def __init__(self, deps, storage) -> None:
        super().__init__(deps, storage)
        self.starts = 0
        self.resumes = 0

    async def start(self, run_in):
        self.starts += 1
        return await super().start(run_in)

    async def resume(self, handoff, envelope):
        self.resumes += 1
        return await super().resume(handoff, envelope)


def make(tmp_path: Path):
    deps = fake_deps(tmp_path / "stores")
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    engine = CountingEngine(deps, file_storage(tmp_path / "checkpoints"))
    w = worker.FleetWorker(deps=deps, engine=engine, context=CONTEXT, teams=FakePoster("teams"), outlook=None)
    return w, engine, MemQueues()


def pause(w, queues):
    queues.send(worker.RUNS_QUEUE, runs_message())
    lines = run(worker.drain_once(queues, w))
    assert [ln.status for ln in lines] == ["awaiting_approval"], lines
    record = w.deps.ledger.record("run-1")
    assert record["status"] == stores.AWAITING_APPROVAL and record["cards_posted"] == ["teams"]
    request_id, request = run(w.engine.pending(record["checkpoint_id"]))
    assert request_id and request.run_id == "run-1"
    payload = w.teams.payloads[0]
    assert payload["bundle_sha256"] == request.bundle_sha256 and payload["nonce"] == request.nonce
    return request


def test_a_runs_message_and_a_verified_pointer_give_exactly_one_publish_request(tmp_path: Path) -> None:
    w, engine, queues = make(tmp_path)
    request = pause(w, queues)
    pointer = intake_decides(w, request, queues)
    lines = run(worker.drain_once(queues, w))
    assert [(ln.status, ln.code) for ln in lines] == [("resumed", PUBLISH_REQUESTED)], lines
    assert engine.resumes == 1
    items = w.deps.publish_queue.items()
    assert [(i["approval_id"], i["bundle_sha256"]) for i in items] == [(pointer["approval_id"], request.bundle_sha256)]
    assert w.deps.ledger.status("run-1") == stores.APPROVED
    assert w.deps.ledger.record("run-1")["publish_enqueued"] == pointer["approval_id"]

    queues.send(worker.DECISIONS_QUEUE, json.dumps(pointer))
    again = run(worker.drain_once(queues, w))
    assert [(ln.status, ln.code) for ln in again] == [("duplicate", "already_resumed")]
    assert engine.resumes == 1 and len(w.deps.publish_queue.items()) == 1


def test_a_changes_pointer_ends_the_graph_as_changes_requested(tmp_path: Path) -> None:
    w, engine, queues = make(tmp_path)
    request = pause(w, queues)
    intake_decides(w, request, queues, decision="changes")
    lines = run(worker.drain_once(queues, w))
    assert [(ln.status, ln.code) for ln in lines] == [("resumed", CHANGES_REQUESTED)], lines
    assert w.deps.ledger.status("run-1") == stores.CHANGES_REQUESTED
    assert w.deps.publish_queue.items() == []


def test_a_redelivered_runs_message_starts_nothing_new_and_posts_no_second_card(tmp_path: Path) -> None:
    w, engine, queues = make(tmp_path)
    pause(w, queues)
    queues.send(worker.RUNS_QUEUE, runs_message())
    lines = run(worker.drain_once(queues, w))
    assert [(ln.status, ln.code) for ln in lines] == [("awaiting_approval", "cards_already_posted")], lines
    assert len(w.teams.payloads) == 1
    assert len(w.deps.store.keys(verifier.NONCES)) == 1, "no second nonce"

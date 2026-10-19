"""W-fulltime inside Microsoft Agent Framework 1.22.0, one process, file checkpoints, fake ports.

Skipped where agent-framework-core is not installed; the fleet CI job sets AGON_REQUIRE_AF=1,
which turns that skip into a failure.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fleet_support import OFFICER, planted_model, require_agent_framework, run, run_input

require_agent_framework()

from fleet_reference import run_to_request  # noqa: E402

from agon.agents.fakes import FakeModel, FakeTranslator  # noqa: E402
from agon.fleet import stores  # noqa: E402
from agon.fleet.checkpoints import GuardedCheckpointStorage, file_storage  # noqa: E402
from agon.fleet.contracts import (  # noqa: E402
    CHANGES_REQUESTED,
    DECISION_REFUSED,
    EXECUTOR_IDS,
    PUBLISH_REQUESTED,
    REFUSED_AT_INGEST,
    REFUSED_BY_GATE,
    WORKFLOW_NAME,
    RunInput,
)
from agon.fleet.deps import read_trace  # noqa: E402
from agon.fleet.local import fake_deps  # noqa: E402
from agon.fleet.runner import envelope_from_card, resume_run, start_run  # noqa: E402
from agon.fleet.workflow import build_workflow  # noqa: E402
from agon.governance import verifier  # noqa: E402
from agon.synth.fixtures import seed_fixtures  # noqa: E402

MAX_CHECKPOINT_BYTES = 1_500_000  # Cosmos DB items cap at 2 MB; file storage would never show it


def _deps(root: Path, **kw):
    deps = fake_deps(root / "state", **kw)
    if deps.store.read(verifier.APPROVERS, OFFICER) is None:
        verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    return deps


def _storage(root: Path) -> GuardedCheckpointStorage:
    return GuardedCheckpointStorage(file_storage(root / "checkpoints"))


def _start(root: Path, run_in: RunInput | None = None, **kw):
    deps = _deps(root, **kw)
    storage = _storage(root)
    handoff = run(start_run(deps, storage, run_in or run_input()))
    return deps, storage, handoff


def _resume(root: Path, handoff: dict, decision: str = "approve", **kw):
    envelope = envelope_from_card(handoff["card"], decision=decision, approver=OFFICER, channel="teams", confirm_all=True, **kw)
    return run(resume_run(_deps(root), _storage(root), handoff, envelope))


def _artifact(deps, bundle_sha256: str, name: str) -> dict:
    manifest = json.loads(deps.store.read(verifier.BUNDLES, bundle_sha256).body["manifest"])
    return json.loads(deps.artifacts.get(manifest["artifacts"][name]["sha256"]))


def test_graph_has_exactly_the_pinned_executor_ids(tmp_path: Path) -> None:
    workflow = build_workflow(fake_deps(), file_storage(tmp_path))
    assert workflow.name == WORKFLOW_NAME
    assert sorted(workflow.executors) == sorted(EXECUTOR_IDS)


def test_graph_signature_does_not_depend_on_configuration(tmp_path: Path) -> None:
    on = build_workflow(fake_deps(), file_storage(tmp_path))
    off_deps = fake_deps()
    off_deps.desk_editor_enabled = False
    off = build_workflow(off_deps, file_storage(tmp_path))
    assert on.graph_signature_hash == off.graph_signature_hash


def test_ft_event_reaches_awaiting_approval_with_a_sealed_bundle(tmp_path: Path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        deps, storage, handoff = _start(tmp_path)
    assert handoff["state"] == "awaiting_approval"
    assert storage.failures == [] and "Failed to create checkpoint" not in caplog.text
    card = handoff["card"]
    assert deps.store.read(verifier.BUNDLES, card["bundle_sha256"]) is not None
    assert deps.store.read(verifier.NONCES, card["nonce"]).body["status"] == "pending"
    assert deps.ledger.status("run-1") == stores.AWAITING_APPROVAL
    sizes = [p.stat().st_size for p in (tmp_path / "checkpoints").glob("*.json")]
    assert sizes and max(sizes) < MAX_CHECKPOINT_BYTES, max(sizes)
    ran = {row["executor"] for row in read_trace(tmp_path / "state" / "trace.ndjson")}
    assert ran == set(EXECUTOR_IDS) - {"publish_request"}


def test_agent_framework_and_the_plain_sequence_seal_the_same_bundle(tmp_path: Path) -> None:
    _, _, handoff = _start(tmp_path)
    reference = run(run_to_request(fake_deps(), run_input()))
    assert handoff["card"]["bundle_sha256"] == reference.sealed.bundle_sha256


def test_approve_in_a_fresh_workflow_gives_exactly_one_publish_request(tmp_path: Path) -> None:
    deps, _, handoff = _start(tmp_path)
    first = _resume(tmp_path, handoff)
    assert [o["status"] for o in first["outcomes"]] == [PUBLISH_REQUESTED]
    assert first["reemitted_request_ids"] == []
    replay = _resume(tmp_path, handoff)
    assert [o["code"] for o in replay["outcomes"]] == ["already_consumed"]
    assert [o["status"] for o in replay["outcomes"]] == [DECISION_REFUSED]
    assert len(deps.publish_queue.items()) == 1
    assert deps.ledger.status("run-1") == stores.APPROVED


def test_changes_requested_publishes_nothing(tmp_path: Path) -> None:
    deps, _, handoff = _start(tmp_path)
    out = _resume(tmp_path, handoff, decision="changes")
    assert [o["status"] for o in out["outcomes"]] == [CHANGES_REQUESTED]
    assert deps.publish_queue.items() == []
    assert deps.ledger.status("run-1") == stores.CHANGES_REQUESTED


def test_tampered_hash_is_refused_and_the_real_approval_still_works(tmp_path: Path) -> None:
    deps, _, handoff = _start(tmp_path)
    bad = _resume(tmp_path, handoff, tamper_hash=True)
    assert [o["code"] for o in bad["outcomes"]] == ["hash_mismatch"]
    good = _resume(tmp_path, handoff)
    assert [o["status"] for o in good["outcomes"]] == [PUBLISH_REQUESTED]


def test_writer_outage_reaches_approval_with_a_flagged_template_recap(tmp_path: Path) -> None:
    deps, _, handoff = _start(tmp_path, model=FakeModel(fail_roles={"writer"}))
    assert handoff["state"] == "awaiting_approval"
    card = _artifact(deps, handoff["card"]["bundle_sha256"], "card.json")
    assert any(f["code"] == "writer_unavailable" for f in card["flags"])
    report = _artifact(deps, handoff["card"]["bundle_sha256"], "report-en.json")
    assert {s["author"] for s in report["sentences"]} == {"template"}


def test_the_retry_edge_runs_the_writer_twice_and_fixes_the_planted_number(tmp_path: Path) -> None:
    model, fixture = planted_model()
    deps, _, handoff = _start(tmp_path, model=model)
    writer_steps = [r["step"] for r in read_trace(tmp_path / "state" / "trace.ndjson") if r["executor"] == "writer"]
    assert writer_steps == ["first", "retry"]
    report = _artifact(deps, handoff["card"]["bundle_sha256"], "report-en.json")
    fixed = next(s for s in report["sentences"] if s["sentence_id"] == "sp")
    assert fixed["claims"][0]["value"] == fixture["true"] and fixed["author"] == "model"


def test_a_translator_outage_does_not_stall_the_fan_in(tmp_path: Path) -> None:
    deps, _, handoff = _start(tmp_path, translator=FakeTranslator(fail_languages={"el", "fr"}))
    assert handoff["state"] == "awaiting_approval"
    card = _artifact(deps, handoff["card"]["bundle_sha256"], "card.json")
    assert card["editions"] == ["en", "es", "easy"]


def test_a_rights_refusal_ends_the_run_without_a_request(tmp_path: Path) -> None:
    bait = seed_fixtures(1)["real_club_bait"]["text"]
    deps, _, handoff = _start(tmp_path, model=FakeModel(extra_sentences=[{"sentence_id": "sx", "text": bait, "claims": []}]))
    assert handoff["state"] == "ended"
    assert [o["status"] for o in handoff["outcomes"]] == [REFUSED_BY_GATE]
    assert deps.store.keys(verifier.NONCES) == []


def test_an_unknown_match_is_refused_at_ingest(tmp_path: Path) -> None:
    bad = RunInput(run_id="run-x", match_ref={"source": "synth", "seed": -5, "match_index": 0})
    _, _, handoff = _start(tmp_path, run_in=bad)
    assert [o["status"] for o in handoff["outcomes"]] == [REFUSED_AT_INGEST]

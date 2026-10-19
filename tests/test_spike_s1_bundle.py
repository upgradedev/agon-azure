"""Unit tests for spike S1.

The stdlib tests run anywhere. The framework tests skip when agent_framework is not importable
(a workstation without installs) and run in the spike-s1 CI job, where it is installed.
"""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

SPIKE_DIR = Path(__file__).resolve().parents[1] / "spikes" / "s1_durable_approval"
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

import s1_bundle as b  # noqa: E402


def _request_and_sealed() -> tuple[b.ApprovalRequest, b.SealedBundle]:
    sealed = b.seal(b.make_draft("m1"))
    return b.ApprovalRequest(bundle_sha256=sealed.bundle_sha256, nonce="n" * 32), sealed


def test_sha256_known_vector() -> None:
    assert b.sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_canonical_json_ignores_key_order() -> None:
    assert b.canonical_json({"b": 1, "a": [2, 3]}) == b.canonical_json({"a": [2, 3], "b": 1})
    assert b.canonical_json({"a": 1}) == '{"a":1}'


def test_seal_is_deterministic_and_hash_matches_bytes() -> None:
    first = b.seal(b.make_draft("m1"))
    second = b.seal(b.make_draft("m1"))
    assert first == second
    assert first.bundle_sha256 == b.sha256_hex(first.canonical_json)
    assert b.seal(b.make_draft("m2")).bundle_sha256 != first.bundle_sha256


def test_verify_accepts_matching_approval() -> None:
    request, sealed = _request_and_sealed()
    decision = b.ApprovalDecision(approve=True, bundle_sha256=request.bundle_sha256, nonce=request.nonce)
    assert b.verify_decision(request, decision, sealed.canonical_json) == (True, "ok")


@pytest.mark.parametrize(
    ("change", "reason_part"),
    [
        ({"bundle_sha256": "0" * 64}, "different bundle hash"),
        ({"nonce": "x" * 32}, "nonce"),
        ({"approve": False}, "not an approval"),
    ],
)
def test_verify_refuses_bad_decision(change: dict, reason_part: str) -> None:
    request, sealed = _request_and_sealed()
    good = b.ApprovalDecision(approve=True, bundle_sha256=request.bundle_sha256, nonce=request.nonce)
    ok, reason = b.verify_decision(request, replace(good, **change), sealed.canonical_json)
    assert ok is False
    assert reason_part in reason


def test_verify_refuses_changed_sealed_bytes() -> None:
    request, sealed = _request_and_sealed()
    decision = b.ApprovalDecision(approve=True, bundle_sha256=request.bundle_sha256, nonce=request.nonce)
    ok, reason = b.verify_decision(request, decision, sealed.canonical_json.replace("2", "3", 1))
    assert ok is False
    assert "sealed bytes" in reason
    assert b.verify_decision(request, decision, None)[0] is False


def test_publish_is_create_only(tmp_path: Path) -> None:
    target = tmp_path / "publish.json"
    b.publish_create_only(target, {"v": 1})
    first = target.read_bytes()
    with pytest.raises(FileExistsError):
        b.publish_create_only(target, {"v": 2})
    assert target.read_bytes() == first


def test_atomic_write_json_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "handoff.json"
    b.atomic_write_json(target, {"k": "v"})
    b.atomic_write_json(target, {"k": "w"})
    assert target.read_text(encoding="utf-8").count('"w"') == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["handoff.json"]


def test_trace_round_trip(tmp_path: Path) -> None:
    assert b.read_trace(tmp_path) == []
    b.append_trace(tmp_path, "draft", "handler")
    rows = b.read_trace(tmp_path)
    assert [(r["executor"], r["step"]) for r in rows] == [("draft", "handler")]


def test_pinned_ids_and_allowed_checkpoint_types() -> None:
    # Pinned literals, not derived values: changing a type or an id must break this test,
    # because either change invalidates every checkpoint already written (spec trap 1).
    assert b.EXECUTOR_IDS == ("draft", "seal", "approval")
    assert b.WORKFLOW_NAME == "agon_s1_approval"
    assert b.ALLOWED_CHECKPOINT_TYPES == [
        "s1_bundle:DraftBundle",
        "s1_bundle:SealedBundle",
        "s1_bundle:ApprovalRequest",
        "s1_bundle:ApprovalDecision",
        "s1_bundle:PublishOutcome",
    ]


def test_workflow_uses_fixed_executor_ids(tmp_path: Path) -> None:
    pytest.importorskip("agent_framework")
    import s1_workflow

    workflow = s1_workflow.build_workflow(s1_workflow.make_storage(tmp_path / "ck"), tmp_path)
    assert sorted(workflow.executors) == sorted(b.EXECUTOR_IDS)
    assert workflow.name == b.WORKFLOW_NAME
    rebuilt = s1_workflow.build_workflow(s1_workflow.make_storage(tmp_path / "ck"), tmp_path, nonce="other")
    assert rebuilt.graph_signature_hash == workflow.graph_signature_hash

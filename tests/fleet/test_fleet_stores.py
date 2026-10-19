"""File stores, the run ledger, the interpret stand-in and the deterministic templates."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fleet_support import OFFICER

from agon.agents.fakes import FakeModel
from agon.fleet import stores, templates
from agon.fleet.contracts import Draft, PublishRequest
from agon.fleet.interpret_port import STANDIN_SOURCE, LocalInterpret
from agon.fleet.sources import MatchSourceError, SynthMatchSource, load_facts
from agon.governance import verifier
from agon.governance.claims import Sentence
from agon.governance.factgate import check_sentence, check_translation
from agon.governance.store import Conflict, NotFound, PreconditionFailed
from agon.synth.engine import GENERATOR_VERSION

SEEDS = (1, 2, 3, 9)

# ----------------------------------------------------------------- FileStore


def test_file_store_create_read_replace(tmp_path: Path) -> None:
    store = stores.FileStore(tmp_path)
    doc = store.create("nonces", "k/1", {"a": 1})
    assert store.read("nonces", "k/1").body == {"a": 1}
    with pytest.raises(Conflict):
        store.create("nonces", "k/1", {"a": 2})
    new = store.replace("nonces", "k/1", {"a": 3}, if_match=doc.etag)
    with pytest.raises(PreconditionFailed):
        store.replace("nonces", "k/1", {"a": 4}, if_match=doc.etag)
    with pytest.raises(NotFound):
        store.replace("nonces", "missing", {}, if_match=new.etag)
    assert stores.FileStore(tmp_path).read("nonces", "k/1").body == {"a": 3}, "a second instance sees the same state"
    assert store.keys("nonces") == ["k/1"]


def test_file_store_compare_and_set_has_one_winner(tmp_path: Path) -> None:
    store = stores.FileStore(tmp_path)
    doc = store.create("c", "k", {"n": 0})
    wins, losses, barrier = [], [], threading.Barrier(12)

    def worker(i: int) -> None:
        barrier.wait()
        try:
            store.replace("c", "k", {"n": i}, if_match=doc.etag)
            wins.append(i)
        except PreconditionFailed:
            losses.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and len(losses) == 11


def test_the_verifier_consumes_once_over_the_file_store(tmp_path: Path) -> None:
    """50 concurrent approvals of one nonce through the one verifier: exactly one approval."""
    from datetime import datetime, timezone

    from agon.governance.seal import seal, Artifact

    store = stores.FileStore(tmp_path)
    verifier.grant_role(store, OFFICER, [verifier.APPROVER_ROLE])
    sealed = seal(
        kind="fulltime", match_id="s1-m0", seed_hash="a" * 64, event_log_hash="b" * 64, charter_hash=None,
        artifacts=[Artifact("report-en.json", b"{}", "application/json", "edition", "en")],
        gate_verdicts={"factgate": "pass", "factgate_lang": "pass", "rights": "pass"},
        author_tags={}, attested=["report-en.json"], gate_cleared=[],
    )
    verifier.register_sealed_bundle(store, sealed.manifest_bytes)
    now = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
    nonce = verifier.issue_nonce(store, sealed.bundle_sha256, target="feed", now=now)
    results, barrier = [], threading.Barrier(50)

    def worker() -> None:
        barrier.wait()
        decision = verifier.ApprovalDecision(sealed.bundle_sha256, nonce, "approve", OFFICER, "teams", now)
        results.append(verifier.verify(decision, store, now + timedelta(seconds=5)))

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 50, "every worker must finish; a crashed thread is a failure"
    assert sum(r.ok for r in results) == 1
    assert {r.code for r in results if not r.ok} == {"already_consumed"}


def test_artifact_store_is_content_addressed_and_create_only(tmp_path: Path) -> None:
    arts = stores.FileArtifactStore(tmp_path)
    sha = arts.put(b"abc")
    assert sha == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert arts.put(b"abc") == sha and arts.get(sha) == b"abc" and arts.get("0" * 64) is None
    (tmp_path / sha).write_bytes(b"abd")
    with pytest.raises(RuntimeError):
        arts.get(sha)


def test_publish_queue_takes_one_request_per_approval(tmp_path: Path) -> None:
    queue = stores.FilePublishQueue(tmp_path)
    req = PublishRequest(run_id="r", target="feed", bundle_sha256="c" * 64, approval_id="abc123")
    assert queue.enqueue(req) is True
    assert queue.enqueue(req) is False
    assert len(queue.items()) == 1


def test_run_ledger_follows_the_transition_table() -> None:
    from agon.governance.store import InMemoryStore

    ledger = stores.RunLedger(InMemoryStore())
    for status in (stores.DRAFT, stores.CHECKED, stores.SEALED, stores.AWAITING_APPROVAL, stores.APPROVED):
        ledger.advance("r", status)
        ledger.advance("r", status)  # idempotent
    assert ledger.status("r") == stores.APPROVED
    assert [h["status"] for h in ledger.history("r")] == ["DRAFT", "CHECKED", "SEALED", "AWAITING_APPROVAL", "APPROVED"]
    with pytest.raises(stores.LedgerError):
        ledger.advance("r", stores.DRAFT)
    with pytest.raises(stores.LedgerError):
        stores.RunLedger(InMemoryStore()).advance("x", stores.SEALED)


def test_run_ledger_note_records_facts_without_moving_the_status() -> None:
    from agon.governance.store import InMemoryStore

    ledger = stores.RunLedger(InMemoryStore())
    ledger.advance("r", stores.DRAFT)
    ledger.note("r", cards_posted=["teams"])
    ledger.note("r", cards_posted=["teams"])  # same value: no write
    assert ledger.status("r") == stores.DRAFT and len(ledger.history("r")) == 1
    assert ledger.record("r")["cards_posted"] == ["teams"]
    for reserved in ("status", "history", "run_id"):
        with pytest.raises(stores.LedgerError):
            ledger.note("r", **{reserved: "x"})
    with pytest.raises(stores.LedgerError):
        ledger.note("missing", cards_posted=[])


# ----------------------------------------------------------------- sources and interpret


def test_load_facts_refuses_a_log_that_changed() -> None:
    source = SynthMatchSource()
    ref = {"source": "synth", "seed": 1, "match_index": 0}
    record = source.load(ref)
    assert load_facts(source, ref, record.event_log_sha256) is record
    with pytest.raises(MatchSourceError, match="hashes to"):
        load_facts(source, ref, "0" * 64)
    with pytest.raises(MatchSourceError):
        source.load({"source": "cosmos", "seed": 1, "match_index": 0})
    assert record.meta["generator"] == GENERATOR_VERSION


@pytest.mark.parametrize("seed", SEEDS)
def test_interpret_standin_ranks_moments_and_says_what_it_is(seed: int) -> None:
    record = SynthMatchSource().load({"source": "synth", "seed": seed, "match_index": 0})
    out = LocalInterpret().interpret(record)
    assert out["source"] == STANDIN_SOURCE and "not a win-probability model" in out["note"]
    assert out["final_score"] == record.meta["score"]
    swings = [c["swing"] for c in out["candidates"]]
    assert swings == sorted(swings, reverse=True)
    goals = [c for c in out["candidates"] if c["kind"] == "goal"]
    assert len(goals) == record.meta["score"]["home"] + record.meta["score"]["away"]


# ----------------------------------------------------------------- templates


def _draft(record) -> Draft:
    return Draft("r", "feed", {}, record.match_id, record.meta["home"], record.meta["away"], record.event_log_sha256, record.seed_sha256)


@pytest.mark.parametrize("seed", SEEDS)
def test_template_recap_passes_the_fact_gate(seed: int) -> None:
    record = SynthMatchSource().load({"source": "synth", "seed": seed, "match_index": 0})
    for s in templates.template_recap(record):
        assert check_sentence(s, record.facts()).passed, s
        assert s.author == "template"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("language", ["en", "el", "es", "fr"])
def test_render_claims_keeps_every_claim_checkable_in_every_language(seed: int, language: str) -> None:
    import asyncio
    import json

    record = SynthMatchSource().load({"source": "synth", "seed": seed, "match_index": 0})
    brief = templates.writer_brief(record, _draft(record))
    answer = json.loads(asyncio.run(FakeModel().complete("writer", None, brief, {})))
    for row in answer["sentences"]:
        source = Sentence.from_dict({**row, "language": "en"})
        assert check_sentence(source, record.facts()).passed, row
        text = templates.render_claims(source.claims, language, record)
        rendered = Sentence(f"{source.sentence_id}-x", text, language, source.claims, "template")
        assert check_translation(source, rendered, record.facts()).passed, (text, check_translation(source, rendered, record.facts()).reasons)

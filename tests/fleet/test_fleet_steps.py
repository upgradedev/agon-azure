"""Every W-fulltime step and failure rule, run in plain sequence with fake ports (no Agent Framework).

The same steps run inside the Agent Framework graph in tests/fleet/test_fleet_workflow_af.py,
which also asserts that both paths seal the same bundle hash.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fleet_reference import run_to_request
from fleet_support import OFFICER, SEED, planted_model, run, run_input

from agon.agents.fakes import FakeModel, FakeSpeech, FakeTranslator
from agon.fleet import steps, stores
from agon.fleet.contracts import (
    ALREADY_ENDED,
    ALREADY_SEALED,
    APPROVAL_ALREADY_REQUESTED,
    AWAITING_DECISION,
    CHANGES_REQUESTED,
    PUBLISH_ALREADY_REQUESTED,
    PUBLISH_REQUESTED,
    REFUSED_AT_INGEST,
    REFUSED_BY_GATE,
    DecisionEnvelope,
    PublishRequest,
    RunInput,
)
from agon.fleet.local import fake_deps
from agon.governance import verifier
from agon.governance.channels import outlook, teams
from agon.governance.claims import Claim, Sentence
from agon.governance.factgate import GateReport, SentenceVerdict, check_translation
from agon.governance.recompute import recompute_stat
from agon.governance.seal import verify_manifest
from agon.synth.fixtures import seed_fixtures


def _deps(**kw):
    deps = fake_deps(**kw)
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    return deps


def _codes(draft) -> list[str]:
    return [f["code"] for f in draft.flags]


def _manifest_ok(deps, sealed) -> dict:
    manifest = json.loads(sealed.manifest_json)
    files = {name: deps.artifacts.get(entry["sha256"]) for name, entry in manifest["artifacts"].items()}
    check = verify_manifest(sealed.manifest_json.encode("utf-8"), files, expected_sha256=sealed.bundle_sha256)
    assert check.ok, check.reasons
    return manifest


def _envelope(request, decision: str = "approve", confirm: bool = True, **over) -> DecisionEnvelope:
    fields = {
        "bundle_sha256": request.bundle_sha256,
        "nonce": request.nonce,
        "decision": decision,
        "approver": OFFICER,
        "channel": "teams",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "confirmed_rows": list(request.confirm_rows) if confirm else [],
        "destinations": [d["id"] for d in request.destinations] if decision == "approve" else [],
    }
    fields.update(over)
    return DecisionEnvelope(**fields)


# ----------------------------------------------------------------- the happy path


def test_full_time_event_reaches_an_approval_request_with_a_sealed_bundle() -> None:
    deps = _deps()
    ref = run(run_to_request(deps, run_input()))
    assert ref.outcome is None and ref.request is not None
    d = ref.sealed.draft
    manifest = _manifest_ok(deps, ref.sealed)
    assert manifest["gate_verdicts"] == {"factgate": "pass", "factgate_lang": "pass", "rights": "pass"}
    assert manifest["deployment_ids"]["ports"] == "fake"
    assert manifest["deployment_ids"]["interpret"] == "fleet-local-standin/1"
    assert sorted(d.editions) == ["easy", "el", "en", "es", "fr"]
    for edition in ("report-en.json", "report-el.json", "report-es.json", "report-fr.json", "report-easy.json", "card.json"):
        assert edition in manifest["artifacts"]
    assert ref.request.bundle_sha256 == ref.sealed.bundle_sha256
    assert ref.request.attested_scope["attested"][0] == "report-en.json"
    assert set(ref.request.confirm_rows) <= set(ref.request.attested_scope["attested"])
    assert ref.request.confirm_rows, "the booking line must be a Confirm row"
    assert deps.ledger.status("run-1") == stores.SEALED
    nonce = deps.store.read(verifier.NONCES, ref.request.nonce)
    assert nonce is not None and nonce.body["bundle_sha256"] == ref.sealed.bundle_sha256
    assert ref.writer_runs == 1 and d.flags == []


def test_every_translated_sentence_carries_the_english_claims() -> None:
    deps = _deps()
    d = run(run_to_request(deps, run_input())).sealed.draft
    english = {s["sentence_id"]: s for s in d.editions["en"]}
    for edition in ("el", "es", "fr", "easy"):
        assert len(d.editions[edition]) == len(english)
        for row in d.editions[edition]:
            source = english[row["sentence_id"].removesuffix(f"-{edition}")]
            assert row["claims"] == source["claims"]


def test_the_same_inputs_seal_the_same_bundle_hash() -> None:
    first = run(run_to_request(_deps(), run_input())).sealed.bundle_sha256
    second = run(run_to_request(_deps(), run_input())).sealed.bundle_sha256
    assert first == second


def test_card_artifact_lists_flags_and_confirm_rows() -> None:
    deps = _deps()
    sealed = run(run_to_request(deps, run_input())).sealed
    manifest = json.loads(sealed.manifest_json)
    card = json.loads(deps.artifacts.get(manifest["artifacts"]["card.json"]["sha256"]))
    assert card["confirm_rows"] == sealed.draft.confirm_rows
    assert card["interpret_source"] == "fleet-local-standin/1"
    assert "not a win-probability model" in card["interpret_note"]


# ----------------------------------------------------------------- section 4 failure rules


def test_writer_outage_gives_a_flagged_template_recap() -> None:
    deps = _deps(model=FakeModel(fail_roles={"writer"}))
    ref = run(run_to_request(deps, run_input()))
    d = ref.sealed.draft
    assert ref.request is not None, "a template recap is still checked, sealed and approvable"
    assert "writer_unavailable" in _codes(d)
    assert {s["author"] for s in d.sentences} == {"template"}
    manifest = _manifest_ok(deps, ref.sealed)
    assert set(manifest["author_tags"][s["sentence_id"]] for s in d.sentences) == {"template"}


def test_writer_invalid_answer_gives_a_flagged_template_recap() -> None:
    d = run(run_to_request(_deps(model=FakeModel(invalid_json_roles={"writer"})), run_input())).sealed.draft
    assert "writer_output_invalid" in _codes(d)
    assert {s["author"] for s in d.sentences} == {"template"}


def test_factgate_catches_a_planted_wrong_number_and_the_retry_fixes_it() -> None:
    model, fixture = planted_model()
    deps = _deps(model=model)
    ref = run(run_to_request(deps, run_input()))
    d = ref.sealed.draft
    record = deps.matches.load(run_input().match_ref)
    true_value = recompute_stat(record.facts(), fixture["stat"], fixture["team"])
    assert true_value == fixture["true"] != fixture["claimed"]
    first = model.calls_for("writer")[0]
    assert "feedback" not in first
    retry = model.calls_for("writer")[1]
    assert [f["sentence_id"] for f in retry["feedback"]] == ["sp"]
    assert f"claimed {fixture['claimed']}, log says {true_value}" in " ".join(retry["feedback"][0]["problems"])
    fixed = next(s for s in d.sentences if s["sentence_id"] == "sp")
    assert fixed["claims"][0]["value"] == true_value and fixed["author"] == "model"
    assert str(true_value) in fixed["text"]
    assert ref.writer_runs == 2 and "factgate_retry" in _codes(d)
    assert "factgate_template_line" not in _codes(d)
    assert d.gate_verdicts["factgate"] == "pass" and ref.request is not None


def test_a_retry_that_does_not_fix_the_number_becomes_a_flagged_template_line() -> None:
    model, fixture = planted_model(fix_on_retry=False)
    deps = _deps(model=model)
    ref = run(run_to_request(deps, run_input()))
    d = ref.sealed.draft
    line = next(s for s in d.sentences if s["sentence_id"] == "sp")
    assert line["author"] == "template"
    assert line["claims"][0]["value"] == fixture["true"]
    assert ref.writer_runs == 2, "the retry edge is bounded: one retry, never a third Writer call"
    assert {"factgate_retry", "factgate_template_line"} <= set(_codes(d))
    assert d.gate_verdicts["factgate"] == "pass"


def test_a_second_pass_mismatch_seals_a_refused_bundle_and_issues_no_nonce(monkeypatch) -> None:
    """Spec 4: a gate refusal is shown on the card, never a crash. Forces the second pass to fail."""
    real = steps.run_factgate
    calls = {"n": 0}

    def second_pass_fails(sentences, facts):
        calls["n"] += 1
        report = real(sentences, facts)
        if calls["n"] < 2:
            return report
        first, *rest = report.verdicts
        broken = SentenceVerdict(first.sentence_id, first.language, "mismatch", ("forced by the test: second pass mismatch",))
        return GateReport(status="mismatch", verdicts=(broken, *rest))

    monkeypatch.setattr(steps, "run_factgate", second_pass_fails)
    deps = _deps()
    ref = run(run_to_request(deps, run_input()))
    assert calls["n"] == 2, "no retry: the first pass was clean, so the second pass is the next call"
    assert ref.request is None and ref.outcome is not None
    assert ref.outcome.status == REFUSED_BY_GATE and ref.outcome.code == "bundle_refused_by_gate"
    assert ref.sealed is not None and ref.outcome.bundle_sha256 == ref.sealed.bundle_sha256
    manifest = _manifest_ok(deps, ref.sealed)
    assert manifest["gate_verdicts"]["factgate"] == "mismatch"
    card = json.loads(deps.artifacts.get(manifest["artifacts"]["card.json"]["sha256"]))
    assert any("forced by the test" in r for r in card["gate_reasons"]["factgate"])
    assert "factgate_mismatch" in [f["code"] for f in card["flags"]]
    assert deps.store.keys(verifier.NONCES) == [], "a refused bundle never gets an Approve button"
    assert [h["status"] for h in deps.ledger.history("run-1")] == [
        stores.DRAFT, stores.CHECKED, stores.SEALED, stores.REFUSED,
    ]


def test_analyst_invented_event_ids_are_retried_once_then_fall_back() -> None:
    once = _deps(model=FakeModel(analyst_invented_answers=1))
    d = run(run_to_request(once, run_input())).sealed.draft
    assert "analyst_fallback" not in _codes(d) and d.turning_points[0]["author"] == "model"
    twice = _deps(model=FakeModel(analyst_invented_answers=2))
    d = run(run_to_request(twice, run_input())).sealed.draft
    assert {"analyst_rejected", "analyst_fallback"} <= set(_codes(d))
    assert d.turning_points and {tp["author"] for tp in d.turning_points} == {"template"}


def test_triage_code_table_overrides_the_model_label() -> None:
    booking = Sentence("s1", "A player was booked.", "en", (Claim(type="event_ref", value="yellow_card", event_ids=("e1",)),))
    assert steps._route(booking, {"type": "neutral_colour", "grounding_ids": ["e1"]})["route"] == "confirm"
    bare = Sentence("s2", "The keeper is carrying a knock.", "en", ())
    assert steps._route(bare, {"type": "injury_medical", "grounding_ids": []})["route"] == "ungrounded"
    numeric = Sentence("s3", "They had 4 corners.", "en", (Claim(type="stat", value=4, stat="corners", subject="C1"),))
    assert steps._route(numeric, {"type": "neutral_colour", "grounding_ids": []})["route"] == "factgate"
    assert steps._route(numeric, None)["route"] == "factgate"


def test_an_ungrounded_sentence_is_sent_back_then_withheld() -> None:
    class InjuryTriage(FakeModel):
        def _triage(self, payload):
            out = super()._triage(payload)
            for item in out["items"]:
                if item["sentence_id"] == "sx":
                    item["type"] = "injury_medical"
            return out

    extra = [{"sentence_id": "sx", "text": "The keeper is carrying a knock.", "claims": []}]
    deps = _deps(model=InjuryTriage(extra_sentences=extra))
    ref = run(run_to_request(deps, run_input()))
    line = next(s for s in ref.sealed.draft.sentences if s["sentence_id"] == "sx")
    assert line["author"] == "template" and line["claims"] == []
    assert ref.writer_runs == 2


def test_desk_editor_may_add_flags_and_never_clear_one() -> None:
    model, _ = planted_model(fix_on_retry=False)
    model.desk_flags = [
        {"sentence_id": "s2", "code": "unsupported_emphasis", "detail": "the line overstates the goal"},
        {"sentence_id": "nope", "code": "tone", "detail": "no such sentence"},
    ]
    deps = _deps(model=model)
    d = run(run_to_request(deps, run_input())).sealed.draft
    codes = _codes(d)
    assert "factgate_template_line" in codes, "earlier flags survive the critic"
    assert "desk_unsupported_emphasis" in codes and "desk_editor_unknown_sentence" in codes
    assert d.triage["s2"]["route"] == "factgate"
    assert "s2" not in d.confirm_rows, "a desk flag is a flag, never a Confirm row"


def test_desk_editor_flags_never_widen_the_confirm_rows_or_the_attested_scope() -> None:
    """Spec 4 row 7: the critic adds flags only. Confirm rows come from the triage table alone.

    The expected rows and scope come from a separate run with no desk flag, never from the
    flagged run itself, so a critic that widened the scope would fail here.
    """
    baseline = run(run_to_request(_deps(), run_input()))
    assert baseline.request is not None
    detail = "the line overstates the result"
    deps = _deps(model=FakeModel(desk_flags=[{"sentence_id": "s1", "code": "unsupported_emphasis", "detail": detail}]))
    ref = run(run_to_request(deps, run_input()))
    d = ref.sealed.draft
    assert d.triage["s1"]["type"] == "match_stat" and d.triage["s1"]["route"] == "factgate"
    flag = {"code": "desk_unsupported_emphasis", "detail": detail, "by": "desk_editor", "sentence_id": "s1"}
    assert flag in d.flags
    manifest = _manifest_ok(deps, ref.sealed)
    card = json.loads(deps.artifacts.get(manifest["artifacts"]["card.json"]["sha256"]))
    assert flag in card["flags"]
    assert "s1" not in d.confirm_rows
    assert d.confirm_rows == baseline.sealed.draft.confirm_rows == card["confirm_rows"]
    assert manifest["attested_scope"] == json.loads(baseline.sealed.manifest_json)["attested_scope"]
    assert ref.request is not None, "a flagged bundle still seals and gets its card"
    assert ref.request.confirm_rows == baseline.request.confirm_rows
    assert ref.request.attested_scope == baseline.request.attested_scope
    assert deps.store.read(verifier.NONCES, ref.request.nonce) is not None


def test_desk_editor_outage_and_switch_off_are_on_the_card() -> None:
    down = run(run_to_request(_deps(model=FakeModel(fail_roles={"desk_editor"})), run_input())).sealed.draft
    assert "desk_editor_unavailable" in _codes(down)
    off = _deps()
    off.desk_editor_enabled = False
    assert "desk_editor_not_run" in _codes(run(run_to_request(off, run_input())).sealed.draft)


def test_translator_outage_marks_the_edition_not_available() -> None:
    deps = _deps(translator=FakeTranslator(fail_languages={"el"}))
    ref = run(run_to_request(deps, run_input()))
    d = ref.sealed.draft
    assert "el" not in d.editions and "report-el.json" not in json.loads(ref.sealed.manifest_json)["artifacts"]
    flag = next(f for f in d.flags if f["code"] == "edition_unavailable")
    assert "el edition not available in this bundle" in flag["detail"]
    assert ref.request is not None


def test_a_translation_that_changes_a_number_falls_back_to_a_template_line() -> None:
    deps = _deps(translator=FakeTranslator(corrupt={"es": 0}))
    ref = run(run_to_request(deps, run_input()))
    d = ref.sealed.draft
    row = d.editions["es"][0]
    assert row["author"] == "template" and "Marcador" in row["text"]
    record = deps.matches.load(run_input().match_ref)
    source = Sentence.from_dict(d.editions["en"][0])
    assert check_translation(source, Sentence.from_dict(row), record.facts()).passed
    assert any(f["code"] == "translation_template_line" and f["sentence_id"] == "s1-es" for f in d.flags)
    assert d.gate_verdicts["factgate_lang"] == "pass"


def test_post_edit_outage_keeps_the_machine_translation() -> None:
    d = run(run_to_request(_deps(model=FakeModel(fail_roles={"localiser_editor"})), run_input())).sealed.draft
    assert sum(f["code"] == "post_edit_unavailable" for f in d.flags) == 3
    assert d.editions["el"][0]["text"].startswith("(EL) ")


def test_easy_read_rewrites_a_long_sentence_with_the_same_claims() -> None:
    record = fake_deps().matches.load(run_input().match_ref)
    final = record.meta["score"]
    long_text = (
        "In a match that swung back and forth for the whole afternoon in front of a very loud home crowd "
        f"the two sides finished {final['home']}-{final['away']}."
    )
    extra = [{"sentence_id": "sl", "text": long_text, "claims": [{"type": "score", "value": final, "event_ids": [], "stat": None, "subject": None}]}]
    d = run(run_to_request(_deps(model=FakeModel(extra_sentences=extra)), run_input())).sealed.draft
    easy = next(r for r in d.editions["easy"] if r["sentence_id"] == "sl-easy")
    english = next(r for r in d.editions["en"] if r["sentence_id"] == "sl")
    assert easy["author"] == "template" and easy["claims"] == english["claims"]
    assert english["claims"][0]["value"] == final
    assert len(easy["text"].split()) <= steps.EASY_READ_MAX_WORDS
    assert any(f["code"] == "easy_read_template_line" for f in d.flags)


def test_rights_refusal_seals_but_issues_no_nonce() -> None:
    bait = seed_fixtures(SEED)["real_club_bait"]
    deps = _deps(model=FakeModel(extra_sentences=[{"sentence_id": "sx", "text": bait["text"], "claims": []}]))
    ref = run(run_to_request(deps, run_input()))
    assert ref.request is None and ref.outcome.status == REFUSED_BY_GATE
    assert ref.sealed.draft.gate_verdicts["rights"] == "refused"
    assert "rights_refusal" in _codes(ref.sealed.draft) and "audio_not_produced" in _codes(ref.sealed.draft)
    assert deps.store.keys(verifier.NONCES) == [], "a refused bundle never gets an Approve button"
    assert deps.ledger.status("run-1") == stores.REFUSED


def test_speech_outage_publishes_captions_without_audio() -> None:
    deps = _deps(speech=FakeSpeech(fail_languages={"el"}))
    manifest = json.loads(run(run_to_request(deps, run_input())).sealed.manifest_json)
    assert "audio-el.mp3" not in manifest["artifacts"] and "captions-el.json" in manifest["artifacts"]
    captions = json.loads(deps.artifacts.get(manifest["artifacts"]["captions-el.json"]["sha256"]))
    assert captions["word_timings"] is None and captions["disclosure"]


def test_ingest_refuses_an_unknown_match_or_target() -> None:
    deps = _deps()
    bad_ref = run(steps.ingest(deps, RunInput("r", {"source": "synth", "seed": -1, "match_index": 0})))
    assert bad_ref.status == REFUSED_AT_INGEST and bad_ref.code == "match_not_found"
    bad_target = run(steps.ingest(deps, RunInput("r", {"source": "synth", "seed": 1, "match_index": 0}, target="elsewhere")))
    assert bad_target.code == "unknown_target"


# ----------------------------------------------------------------- the decision


def _awaiting(**kw):
    deps = _deps(**kw)
    ref = run(run_to_request(deps, run_input()))
    deps.ledger.advance("run-1", stores.AWAITING_APPROVAL, "test")
    return deps, ref


def test_approve_gives_exactly_one_publish_request_and_a_replay_enqueues_nothing() -> None:
    deps, ref = _awaiting()
    first = run(steps.decide(deps, ref.request, _envelope(ref.request), ref.sealed.manifest_json))
    assert isinstance(first, PublishRequest)
    assert run(steps.enqueue_publish(deps, first)).status == PUBLISH_REQUESTED
    replay = run(steps.decide(deps, ref.request, _envelope(ref.request), ref.sealed.manifest_json))
    assert isinstance(replay, PublishRequest) and replay.approval_id == first.approval_id
    again = run(steps.enqueue_publish(deps, replay))
    assert again.status == PUBLISH_ALREADY_REQUESTED and again.code == "already_enqueued"
    assert len(deps.publish_queue.items()) == 1
    assert deps.ledger.status("run-1") == stores.APPROVED


def test_a_crash_between_consume_and_enqueue_is_recovered_without_a_second_publish() -> None:
    """The verifier consumed the nonce, then the worker died before publish_request ran."""
    deps, ref = _awaiting()
    lost = run(steps.decide(deps, ref.request, _envelope(ref.request), ref.sealed.manifest_json))
    assert isinstance(lost, PublishRequest) and deps.publish_queue.items() == []
    recovered = run(steps.decide(deps, ref.request, _envelope(ref.request), ref.sealed.manifest_json))
    assert isinstance(recovered, PublishRequest) and recovered.approval_id == lost.approval_id
    assert run(steps.enqueue_publish(deps, recovered)).status == PUBLISH_REQUESTED
    assert run(steps.enqueue_publish(deps, recovered)).status == PUBLISH_ALREADY_REQUESTED
    assert len(deps.publish_queue.items()) == 1


def test_changes_requested_consumes_the_nonce_and_publishes_nothing() -> None:
    deps, ref = _awaiting()
    out = run(steps.decide(deps, ref.request, _envelope(ref.request, "changes", confirm=False), ref.sealed.manifest_json))
    assert out.status == CHANGES_REQUESTED
    later = run(steps.decide(deps, ref.request, _envelope(ref.request, channel="outlook"), ref.sealed.manifest_json))
    assert later.code == "already_consumed"
    assert deps.publish_queue.items() == [] and deps.ledger.status("run-1") == stores.CHANGES_REQUESTED


def test_a_tampered_hash_is_refused_without_burning_the_nonce() -> None:
    deps, ref = _awaiting()
    other = ref.request.bundle_sha256[:-1] + ("0" if ref.request.bundle_sha256[-1] != "0" else "1")
    out = run(steps.decide(deps, ref.request, _envelope(ref.request, bundle_sha256=other), ref.sealed.manifest_json))
    assert out.code == "hash_mismatch"
    assert deps.store.read(verifier.NONCES, ref.request.nonce).body["status"] == "pending"
    assert isinstance(run(steps.decide(deps, ref.request, _envelope(ref.request), ref.sealed.manifest_json)), PublishRequest)


def test_unticked_confirm_rows_refuse_an_approval() -> None:
    deps, ref = _awaiting()
    out = run(steps.decide(deps, ref.request, _envelope(ref.request, confirm=False), ref.sealed.manifest_json))
    assert out.code == "unconfirmed_rows"


def test_changed_sealed_state_is_refused_before_the_verifier() -> None:
    deps, ref = _awaiting()
    out = run(steps.decide(deps, ref.request, _envelope(ref.request), ref.sealed.manifest_json + " "))
    assert out.code == "sealed_state_mismatch"
    assert deps.store.read(verifier.NONCES, ref.request.nonce).body["status"] == "pending"


def test_the_console_stand_in_cannot_approve_the_public_feed() -> None:
    deps, ref = _awaiting()
    out = run(steps.decide(deps, ref.request, _envelope(ref.request, channel="console"), ref.sealed.manifest_json))
    assert out.code == "channel_not_allowed"


# ----------------------------------------------------------------- decided first by the intake (spec 3.4 step 5)


TICKED = 2  # the officer ticks the first two offered destinations, not all of them


def _intake_decides(deps, request, decision: str):
    """What the api's intake does before any pointer reaches the worker: the Teams adapter verifies."""
    data = {"decision": decision, "bundle_sha256": request.bundle_sha256, "nonce": request.nonce}
    if decision == "approve":
        data.update({f"{teams.CONFIRM_PREFIX}{row}": "true" for row in request.confirm_rows})
        offered = [d["id"] for d in request.destinations]
        data.update({f"{teams.DESTINATION_PREFIX}{d}": "true" if i < TICKED else "false" for i, d in enumerate(offered)})
    result = teams.handle({"responder": {"userPrincipalName": OFFICER}, "data": data}, deps.store, datetime.now(timezone.utc))
    assert result.ok, result
    return deps.store.read(verifier.APPROVALS, result.approval_id).body


def test_changes_verified_by_the_intake_ends_the_run_as_changes_requested() -> None:
    deps, ref = _awaiting()
    record = _intake_decides(deps, ref.request, "changes")
    out = run(steps.decide(deps, ref.request, steps.envelope_from_record(record, ref.request.nonce), ref.sealed.manifest_json))
    assert out.status == CHANGES_REQUESTED and out.code == "changes_requested"
    assert deps.ledger.status("run-1") == stores.CHANGES_REQUESTED
    assert deps.publish_queue.items() == []


def test_approve_verified_by_the_intake_gives_one_publish_request_and_notes_it() -> None:
    deps, ref = _awaiting()
    record = _intake_decides(deps, ref.request, "approve")
    assert record["destinations"] == [d["id"] for d in ref.request.destinations][:TICKED]
    envelope = steps.envelope_from_record(record, ref.request.nonce)
    assert envelope.destinations == record["destinations"] and envelope.destinations_as_offered is False
    out = run(steps.decide(deps, ref.request, envelope, ref.sealed.manifest_json))
    assert isinstance(out, PublishRequest) and out.approval_id == record["approval_id"]
    assert deps.ledger.status("run-1") == stores.APPROVED
    assert "publish_enqueued" not in deps.ledger.record("run-1")
    assert run(steps.enqueue_publish(deps, out)).status == PUBLISH_REQUESTED
    assert deps.ledger.record("run-1")["publish_enqueued"] == record["approval_id"]


def test_an_outlook_approve_as_offered_resumes_with_no_ticks_and_the_offered_flag() -> None:
    """The record lists every offered id; the envelope must carry the flag and no ids, the only
    form the verifier's shape check accepts for an approve as offered."""
    deps, ref = _awaiting()
    payload = {
        "bundle_sha256": ref.request.bundle_sha256, "nonce": ref.request.nonce, "confirm_rows": list(ref.request.confirm_rows),
        "response": {"SelectedOption": outlook.OPTION_APPROVE_CONFIRM, "UserEmailAddress": OFFICER},
    }
    result = outlook.handle(payload, deps.store, datetime.now(timezone.utc))
    assert result.ok, result
    record = deps.store.read(verifier.APPROVALS, result.approval_id).body
    assert record["destinations_as_offered"] is True and record["destinations"] == [d["id"] for d in ref.request.destinations]
    envelope = steps.envelope_from_record(record, ref.request.nonce)
    assert envelope.destinations == [] and envelope.destinations_as_offered is True
    out = run(steps.decide(deps, ref.request, envelope, ref.sealed.manifest_json))
    assert isinstance(out, PublishRequest) and deps.ledger.status("run-1") == stores.APPROVED


def test_a_recorded_approve_with_other_destinations_is_not_rebuilt() -> None:
    """Negative control: the recovery rebuilds only the recorded subset, never a wider one."""
    deps, ref = _awaiting()
    record = _intake_decides(deps, ref.request, "approve")
    wider = steps.envelope_from_record({**record, "destinations": [d["id"] for d in ref.request.destinations]}, ref.request.nonce)
    out = run(steps.decide(deps, ref.request, wider, ref.sealed.manifest_json))
    assert out.code == "already_consumed" and deps.ledger.status("run-1") == stores.AWAITING_APPROVAL


def test_a_different_decision_on_a_nonce_the_intake_consumed_stays_refused() -> None:
    """Negative control for the recovery: only the recorded decision is rebuilt, nothing else."""
    deps, ref = _awaiting()
    record = _intake_decides(deps, ref.request, "changes")
    other = steps.envelope_from_record({**record, "decision": "approve"}, ref.request.nonce)
    out = run(steps.decide(deps, ref.request, other, ref.sealed.manifest_json))
    assert out.code == "already_consumed"
    assert deps.ledger.status("run-1") == stores.AWAITING_APPROVAL and deps.publish_queue.items() == []


# ----------------------------------------------------------------- redelivery (spec 3.5)


def _clean_bundle() -> str:
    return run(run_to_request(_deps(), run_input())).sealed.bundle_sha256


async def _through_factgate(deps, run_in):
    d = await steps.ingest(deps, run_in)
    d = await steps.interpret(deps, d)
    d = await steps.analyst(deps, d)
    d = await steps.writer(deps, d)
    d = await steps.triage(deps, d)
    kind, d = await steps.factgate(deps, d)
    assert kind == "checked"
    return d


def test_a_redelivery_at_draft_runs_again_and_seals_the_clean_bundle() -> None:
    deps = _deps()
    first = run(steps.ingest(deps, run_input()))
    assert deps.ledger.status("run-1") == stores.DRAFT
    again = run(steps.ingest(deps, run_input()))
    assert again == first, "re-entry returns the same Draft and leaves nothing of the redelivery in it"
    ref = run(run_to_request(deps, run_input()))
    assert ref.request is not None and ref.sealed.bundle_sha256 == _clean_bundle()
    assert len(deps.store.keys(verifier.NONCES)) == 1


def test_a_redelivery_at_checked_runs_again_and_seals_the_clean_bundle() -> None:
    deps = _deps()
    run(_through_factgate(deps, run_input()))
    assert deps.ledger.status("run-1") == stores.CHECKED
    ref = run(run_to_request(deps, run_input()))
    assert ref.request is not None and ref.sealed.bundle_sha256 == _clean_bundle()
    assert len(deps.store.keys(verifier.NONCES)) == 1
    assert [h["status"] for h in deps.ledger.history("run-1")] == [stores.DRAFT, stores.CHECKED, stores.SEALED]


def test_a_redelivery_at_sealed_redoes_nothing_and_issues_no_second_nonce() -> None:
    deps = _deps()
    ref = run(run_to_request(deps, run_input()))
    assert deps.ledger.status("run-1") == stores.SEALED
    out = run(steps.ingest(deps, run_input()))
    assert out.status == ALREADY_SEALED and out.bundle_sha256 == ref.sealed.bundle_sha256
    again = run(run_to_request(deps, run_input()))
    assert again.outcome.status == ALREADY_SEALED and again.sealed is None
    assert deps.store.keys(verifier.NONCES) == [ref.request.nonce]


def test_a_redelivery_after_the_pause_or_the_decision_reports_the_state() -> None:
    deps = _deps()
    ref = run(run_to_request(deps, run_input()))
    deps.ledger.advance("run-1", stores.AWAITING_APPROVAL, "test", checkpoint_id="cp-1")
    waiting = run(steps.ingest(deps, run_input()))
    assert waiting.status == AWAITING_DECISION and "cp-1" in waiting.detail
    run(steps.decide(deps, ref.request, _envelope(ref.request), ref.sealed.manifest_json))
    ended = run(steps.ingest(deps, run_input()))
    assert ended.status == ALREADY_ENDED and ended.code == "approved"
    assert len(deps.store.keys(verifier.NONCES)) == 1 and deps.publish_queue.items() == []


def test_two_deliveries_racing_past_ingest_issue_one_nonce() -> None:
    deps = _deps()
    ref = run(run_to_request(deps, run_input()))
    second = run(steps.prepare_approval(deps, ref.sealed))
    assert second.status == APPROVAL_ALREADY_REQUESTED and second.code == "approval_already_claimed"
    assert deps.store.keys(verifier.NONCES) == [ref.request.nonce]


def test_a_reused_run_id_for_another_match_is_refused() -> None:
    deps = _deps()
    run(steps.ingest(deps, run_input()))
    other = RunInput("run-1", {"source": "synth", "seed": SEED, "match_index": 1})
    out = run(steps.ingest(deps, other))
    assert out.status == REFUSED_AT_INGEST and out.code == "run_id_reused"


# ----------------------------------------------------------------- publish destinations (owner decision 2026-10-19)


def test_the_sealed_bundle_offers_every_destination_and_the_card_lists_them() -> None:
    from agon.governance import destinations as D

    deps = _deps()
    ref = run(run_to_request(deps, run_input()))
    manifest = _manifest_ok(deps, ref.sealed)
    assert list(manifest["destinations"]) == sorted(D.DESTINATION_IDS)
    for dest_id, entry in manifest["destinations"].items():
        payload = deps.artifacts.get(entry["sha256"])
        assert payload is not None and json.loads(payload)["destination"] == dest_id
        assert manifest["artifacts"][entry["artifact"]]["role"] == "destination"
    assert [d["id"] for d in ref.request.destinations] == list(D.DESTINATION_IDS)
    assert all(d["label"] == D.LABELS[d["id"]] for d in ref.request.destinations)
    assert all(d["sha256"] == manifest["destinations"][d["id"]]["sha256"] for d in ref.request.destinations)
    report = json.loads(deps.artifacts.get(manifest["artifacts"]["report-en.json"]["sha256"]))
    club = json.loads(deps.artifacts.get(manifest["destinations"]["club_site"]["sha256"]))
    assert club["edition"] == report and club["title"].endswith(": match report")


def test_a_deployment_can_offer_a_subset_of_destinations() -> None:
    deps = _deps()
    deps.offered_destinations = ("club_site", "feeds")
    ref = run(run_to_request(deps, run_input()))
    assert sorted(json.loads(ref.sealed.manifest_json)["destinations"]) == ["club_site", "feeds"]
    assert [d["id"] for d in ref.request.destinations] == ["club_site", "feeds"]


def test_a_bundle_the_rights_guard_refused_offers_no_destination() -> None:
    bait = seed_fixtures(SEED)["real_club_bait"]
    deps = _deps(model=FakeModel(extra_sentences=[{"sentence_id": "sx", "text": bait["text"], "claims": []}]))
    ref = run(run_to_request(deps, run_input()))
    assert "destinations" not in json.loads(ref.sealed.manifest_json)


def test_the_decision_records_the_ticked_subset_and_outlook_approves_as_offered() -> None:
    deps, ref = _awaiting()
    out = run(steps.decide(deps, ref.request, _envelope(ref.request, destinations=["youtube", "club_site"]), ref.sealed.manifest_json))
    assert isinstance(out, PublishRequest)
    assert deps.store.read(verifier.APPROVALS, out.approval_id).body["destinations"] == ["club_site", "youtube"]

    deps2, ref2 = _awaiting()
    nothing = run(steps.decide(deps2, ref2.request, _envelope(ref2.request, destinations=[]), ref2.sealed.manifest_json))
    assert nothing.code == "no_destination_selected"
    outside = run(steps.decide(deps2, ref2.request, _envelope(ref2.request, destinations=["tiktok"]), ref2.sealed.manifest_json))
    assert outside.code == "destination_not_offered"
    by_mail = _envelope(ref2.request, channel="outlook", destinations=[], destinations_as_offered=True)
    approved = run(steps.decide(deps2, ref2.request, by_mail, ref2.sealed.manifest_json))
    assert isinstance(approved, PublishRequest)
    record = deps2.store.read(verifier.APPROVALS, approved.approval_id).body
    assert record["destinations"] == [d["id"] for d in ref2.request.destinations] and record["destinations_as_offered"] is True

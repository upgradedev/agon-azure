"""Seal: canonical UTF-8 manifest, bundle hash pinned, verify_manifest recomputes from bytes."""

from __future__ import annotations

import json

import pytest

import govkit
from agon.governance import seal as S
from agon.synth.manifest import build_bundle

GREEK_LINE = "Γκολ στο 71ο λεπτό."
# Pinned: if canonical form, key order, escaping or the schema drifts, this hash changes.
# Re-pinned once when the golden input gained the factgate_lang verdict (REQUIRED_GATES); the
# canonical form did not change. Previous value 122dc3c3...0181e over {factgate, rights}.
GOLDEN_BUNDLE_SHA256 = "a88925cec247652ae5fefce35b44f67b569e28011713204ceb7cb46ab1c56076"


def _golden():
    return S.seal(
        kind="fulltime",
        match_id="s9-m0",
        seed_hash="a" * 64,
        event_log_hash="b" * 64,
        charter_hash="c" * 64,
        artifacts=[S.Artifact("report-el.txt", GREEK_LINE.encode("utf-8"), "text/plain", "edition", "el")],
        gate_verdicts={"factgate": "pass", "factgate_lang": "pass", "rights": "pass"},
        author_tags={"s1-el": "model"},
        attested=["report-el.txt"],
        gate_cleared=[],
    )


def test_canonical_utf8_is_sorted_compact_and_raw_utf8():
    omega = chr(0x3A9)
    data = S.canonical_utf8({"b": omega, "a": 1})
    assert data == b'{"a":1,"b":"' + omega.encode("utf-8") + b'"}'
    assert b"\\u" not in data


def test_floats_and_nan_are_refused():
    with pytest.raises(S.SealError):
        S.canonical_utf8({"x": 0.5})
    with pytest.raises(S.SealError):
        S.canonical_utf8({"x": float("nan")})


def test_golden_bundle_hash_is_pinned():
    sealed = _golden()
    assert sealed.bundle_sha256 == GOLDEN_BUNDLE_SHA256
    assert GREEK_LINE.encode("utf-8") not in sealed.manifest_bytes  # artifacts are hashed, not embedded
    assert sealed.bundle_sha256 == S.sha256_hex(sealed.manifest_bytes)
    assert "bundle_sha256" not in sealed.manifest


def test_event_log_hash_matches_the_generator_ndjson():
    _, result, _ = govkit.match()
    expected = S.sha256_hex(build_bundle(govkit.MAIN_SEED).events_ndjson())
    assert S.event_log_sha256(result.events) == expected


def test_verify_manifest_recomputes_from_bytes():
    sealed, files = govkit.make_bundle()
    ok = S.verify_manifest(sealed.manifest_bytes, files, expected_sha256=sealed.bundle_sha256)
    assert ok.ok, ok.reasons
    tampered = dict(files, **{"report-el.txt": files["report-el.txt"] + b" "})
    assert not S.verify_manifest(sealed.manifest_bytes, tampered).ok
    missing = {k: v for k, v in files.items() if k != "audio-el.mp3"}
    assert any("missing" in r for r in S.verify_manifest(sealed.manifest_bytes, missing).reasons)
    extra = dict(files, **{"extra.txt": b"x"})
    assert any("not listed" in r for r in S.verify_manifest(sealed.manifest_bytes, extra).reasons)
    assert not S.verify_manifest(sealed.manifest_bytes, files, expected_sha256="0" * 64).ok


def test_non_canonical_bytes_of_the_same_manifest_are_refused():
    sealed, files = govkit.make_bundle()
    pretty = json.dumps(sealed.manifest, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    check = S.verify_manifest(pretty, files)
    assert not check.ok and "not in canonical form" in check.reasons[0]
    escaped = json.dumps(sealed.manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if escaped != sealed.manifest_bytes:
        assert not S.verify_manifest(escaped, files).ok


def test_seal_refuses_inconsistent_inputs():
    base = dict(
        kind="fulltime",
        match_id="m",
        seed_hash="a" * 64,
        event_log_hash="b" * 64,
        charter_hash=None,
        artifacts=[S.Artifact("a.txt", b"x", "text/plain", "edition", "en")],
        gate_verdicts={"factgate": "pass", "factgate_lang": "pass", "rights": "pass"},
        author_tags={"s1": "model"},
        attested=["a.txt"],
        gate_cleared=[],
    )
    S.seal(**base)
    bad_cases = [
        dict(kind="erratum"),  # no parent
        dict(parent_bundle_sha256="d" * 64),  # parent on a non-erratum
        dict(gate_cleared=["a.txt"]),  # attested and gate-cleared at once
        dict(author_tags={"s1": "robot"}),
        dict(artifacts=[S.Artifact("../x", b"x", "text/plain", "edition")]),
        dict(artifacts=[S.Artifact("a.txt", b"x", "t", "edition"), S.Artifact("a.txt", b"y", "t", "edition")]),
        dict(seed_hash="A" * 64),
        dict(gate_verdicts={}),
        dict(gate_verdicts={"anything": "pass"}),  # the reviewer's probe: no required gate at all
        dict(gate_verdicts={"factgate": "pass", "factgate_lang": "pass"}),  # rights never ran
        dict(attested=["nope.txt"]),
        dict(artifacts=[S.Artifact("manifest.json", b"{}", "application/json", "edition")], attested=[]),
    ]
    for override in bad_cases:
        with pytest.raises(S.SealError):
            S.seal(**{**base, **override})


# The required gate set, written out as a literal so that a change to seal.REQUIRED_GATES has to be
# made here too, on purpose. Never derive this from the constant under test.
PINNED_REQUIRED_GATES = ("factgate", "factgate_lang", "rights")
ALL_PASS = {"factgate": "pass", "factgate_lang": "pass", "rights": "pass"}


def test_required_gate_set_is_pinned():
    assert S.REQUIRED_GATES == PINNED_REQUIRED_GATES
    assert set(govkit.PASS_VERDICTS) == set(PINNED_REQUIRED_GATES)


def test_gates_all_pass():
    assert S.gates_all_pass({"gate_verdicts": dict(ALL_PASS)})
    assert S.gates_all_pass({"gate_verdicts": {**ALL_PASS, "charter_check": "pass"}})  # superset is fine
    assert not S.gates_all_pass({"gate_verdicts": {"a": "pass"}})  # no required gate listed
    assert not S.gates_all_pass({"gate_verdicts": {"anything": "pass"}})
    for gate in PINNED_REQUIRED_GATES:
        missing = {k: v for k, v in ALL_PASS.items() if k != gate}
        assert not S.gates_all_pass({"gate_verdicts": missing}), gate
    assert not S.gates_all_pass({"gate_verdicts": {**ALL_PASS, "rights": "refused"}})
    assert not S.gates_all_pass({"gate_verdicts": {**ALL_PASS, "charter_check": "refused"}})
    assert not S.gates_all_pass({"gate_verdicts": {}})
    assert not S.gates_all_pass({})


def test_seal_refuses_a_bundle_missing_any_required_gate():
    for gate in PINNED_REQUIRED_GATES:
        verdicts = {k: v for k, v in ALL_PASS.items() if k != gate}
        with pytest.raises(S.SealError, match=gate):
            govkit.make_bundle(verdicts=verdicts)
    with pytest.raises(S.SealError, match="rights"):
        govkit.make_bundle(verdicts={"anything": "pass"})


def test_seal_still_seals_a_refused_bundle_so_the_card_can_show_the_reason():
    sealed, _ = govkit.make_bundle(verdicts={**ALL_PASS, "rights": "refused"})
    assert S.manifest_shape_problems(sealed.manifest) == []
    assert not S.gates_all_pass(sealed.manifest)


def test_shape_validator_and_verify_manifest_refuse_a_missing_required_gate():
    sealed, files = govkit.make_bundle()
    d = json.loads(sealed.manifest_bytes)
    del d["gate_verdicts"]["rights"]
    problems = S.manifest_shape_problems(d)
    assert any("rights" in p for p in problems), problems
    data = S.canonical_utf8(d)
    check = S.verify_manifest(data, files, expected_sha256=S.sha256_hex(data))
    assert not check.ok and check.refusal_code(S.sha256_hex(data)) == "required_gate_missing"


def test_seal_output_always_passes_the_shared_shape_validator():
    sealed, _ = govkit.make_bundle()
    assert S.manifest_shape_problems(sealed.manifest) == []
    erratum, _ = govkit.make_bundle(kind="erratum", parent="d" * 64)
    assert S.manifest_shape_problems(erratum.manifest) == []


def test_shape_validator_rejects_what_seal_would_never_build():
    sealed, _ = govkit.make_bundle()
    m = json.loads(sealed.manifest_bytes)
    broken = [
        lambda d: d.pop("gate_verdicts"),
        lambda d: d.update(extra=1),
        lambda d: d.update(kind="press_release"),
        lambda d: d.update(parent_bundle_sha256="d" * 64),
        lambda d: d["artifacts"].update({"UPPER.txt": d["artifacts"]["audio-el.mp3"]}),
        lambda d: d["artifacts"]["audio-el.mp3"].update(bytes=-1),
        lambda d: d["artifacts"]["audio-el.mp3"].update(sha256="xyz"),
        lambda d: d["author_tags"].update({"s9": "intern"}),
        lambda d: d["attested_scope"].update(attested=["ghost.txt"]),
        lambda d: d["artifacts"].update({"manifest.json": d["artifacts"]["audio-el.mp3"]}),
    ]
    for i, mutate in enumerate(broken):
        d = json.loads(json.dumps(m))
        mutate(d)
        assert S.manifest_shape_problems(d), i
        data = S.canonical_utf8(d)
        assert not S.verify_manifest(data).ok, i


# ----------------------------------------------------------------- the charter kind (agon.charter)
# Written out as literals, like PINNED_REQUIRED_GATES: a report keeps its three gates, and only a
# bundle of kind "charter" reads the kind table.
PINNED_KIND_REQUIRED_GATES = {"charter": ("charter_check", "rights")}
CHARTER_PASS = {"charter_check": "pass", "rights": "pass"}


def _charter(**overrides):
    base = dict(
        kind="charter",
        match_id="s1-m0",
        seed_hash="a" * 64,
        event_log_hash=S.sha256_hex(b""),
        charter_hash=None,
        artifacts=[S.Artifact("charter.json", b'{"schema":"agon.charter/1"}', "application/json", "charter")],
        gate_verdicts=dict(CHARTER_PASS),
        author_tags={},
        attested=["charter.json"],
        gate_cleared=[],
    )
    return S.seal(**{**base, **overrides})


def test_kind_required_gates_are_pinned_and_reports_are_untouched():
    assert S.KIND_REQUIRED_GATES == PINNED_KIND_REQUIRED_GATES
    assert S.required_gates("charter") == ("charter_check", "rights")
    for kind in ("fulltime", "erratum", None, "press_release", 7):
        assert S.required_gates(kind) == PINNED_REQUIRED_GATES, kind
    # The golden fulltime bundle still hashes to its pinned value (test_golden_bundle_hash_is_pinned).
    assert _golden().bundle_sha256 == GOLDEN_BUNDLE_SHA256


def test_a_charter_bundle_seals_with_its_own_gates_and_verifies():
    sealed = _charter()
    assert S.manifest_shape_problems(sealed.manifest) == []
    assert S.gates_all_pass(sealed.manifest)
    check = S.verify_manifest(sealed.manifest_bytes, {"charter.json": b'{"schema":"agon.charter/1"}'}, sealed.bundle_sha256)
    assert check.ok, check.reasons


def test_a_charter_bundle_missing_a_charter_gate_is_refused():
    for gate in ("charter_check", "rights"):
        verdicts = {k: v for k, v in CHARTER_PASS.items() if k != gate}
        with pytest.raises(S.SealError, match=gate):
            _charter(gate_verdicts=verdicts)
    # The report gates do not stand in for the charter's own gate.
    with pytest.raises(S.SealError, match="charter_check"):
        _charter(gate_verdicts=dict(ALL_PASS))


def test_a_refused_charter_still_seals_but_no_gate_passes():
    sealed = _charter(gate_verdicts={"charter_check": "refused", "rights": "refused"})
    assert S.manifest_shape_problems(sealed.manifest) == []
    assert not S.gates_all_pass(sealed.manifest)


def test_a_charter_holds_only_charter_json_and_offers_no_destination():
    with pytest.raises(S.SealError, match="exactly one artifact"):
        _charter(artifacts=[
            S.Artifact("charter.json", b"{}", "application/json", "charter"),
            S.Artifact("report-en.json", b"{}", "application/json", "edition", "en"),
        ])
    with pytest.raises(S.SealError, match="exactly one artifact"):
        _charter(artifacts=[S.Artifact("charter.json", b"{}", "application/json", "edition")])
    payload = json.dumps({"schema": "agon.destination/1", "destination": "feeds"}).encode("utf-8")
    with pytest.raises(S.SealError, match="no destination"):
        _charter(destinations={"feeds": payload})


def test_relabelling_a_charter_as_a_report_cannot_skip_the_fact_gate():
    sealed = _charter()
    d = json.loads(sealed.manifest_bytes)
    d["kind"] = "fulltime"
    problems = S.manifest_shape_problems(d)
    assert any("factgate" in p for p in problems), problems
    data = S.canonical_utf8(d)
    check = S.verify_manifest(data, expected_sha256=S.sha256_hex(data))
    assert not check.ok and check.refusal_code(S.sha256_hex(data)) == "required_gate_missing"
    assert not S.gates_all_pass(d)

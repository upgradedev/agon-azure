"""Seal: canonical UTF-8 manifest, bundle hash pinned, verify_manifest recomputes from bytes."""

from __future__ import annotations

import json

import pytest

import govkit
from agon.governance import seal as S
from agon.synth.manifest import build_bundle

GREEK_LINE = "Γκολ στο 71ο λεπτό."
# Pinned once: if canonical form, key order, escaping or the schema drifts, this hash changes.
GOLDEN_BUNDLE_SHA256 = "122dc3c31b5ba639afc9ea61b12de5da5a76687536cc751b7900dd4e4960181e"


def _golden():
    return S.seal(
        kind="fulltime",
        match_id="s9-m0",
        seed_hash="a" * 64,
        event_log_hash="b" * 64,
        charter_hash="c" * 64,
        artifacts=[S.Artifact("report-el.txt", GREEK_LINE.encode("utf-8"), "text/plain", "edition", "el")],
        gate_verdicts={"factgate": "pass", "rights": "pass"},
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
        gate_verdicts={"factgate": "pass"},
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
        dict(attested=["nope.txt"]),
    ]
    for override in bad_cases:
        with pytest.raises(S.SealError):
            S.seal(**{**base, **override})


def test_gates_all_pass():
    assert S.gates_all_pass({"gate_verdicts": {"a": "pass"}})
    assert not S.gates_all_pass({"gate_verdicts": {"a": "pass", "rights": "refused"}})
    assert not S.gates_all_pass({"gate_verdicts": {}})
    assert not S.gates_all_pass({})


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
    ]
    for i, mutate in enumerate(broken):
        d = json.loads(json.dumps(m))
        mutate(d)
        assert S.manifest_shape_problems(d), i
        data = S.canonical_utf8(d)
        assert not S.verify_manifest(data).ok, i

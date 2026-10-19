"""Tests for the run files behind the Newsroom Wall, the stand-in card, the recap and Verify.

Why these exist: the one shipped run is a hand-written design preview, so its honesty rests on
checks, not on care. These tests prove that the sample regenerates byte for byte from the real
gates and the real seal, that the bundle hash a visitor recomputes is the one the run states,
that the exact design preview label is pinned everywhere it must appear, and that every check
fails once on a deliberately broken input.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

import readiness
import stamp_web
import web_check
from agon import webrun
from agon.governance.seal import canonical_utf8, sha256_hex, verify_manifest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
RUNS = WEB / "runs"
SAMPLE = RUNS / "sample-s1.json"
LABEL = "Design preview: sample run written by hand, not produced by the pipeline."
RUN_CASES = json.loads((ROOT / "tests" / "js" / "run_cases.json").read_text(encoding="utf-8"))


def _run() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _apply(run: dict, case: dict):
    if "replace" in case:
        return case["replace"]
    out = copy.deepcopy(run)
    for op in case["ops"]:
        target = out
        for key in op["path"][:-1]:
            target = target[key]
        if op.get("delete"):
            del target[op["path"][-1]]
        else:
            target[op["path"][-1]] = op["value"]
    return out


# ---------------------------------------------------------------- the committed sample


def test_sample_regenerates_byte_for_byte():
    """Fails when a gate, the seal, the generator or the hand-written text changes.
    Fix: PYTHONPATH=src python -m agon.webrun --write-sample web/runs"""
    run, files = webrun.build_sample_run()
    assert webrun.run_bytes(run) == SAMPLE.read_bytes()
    assert canonical_utf8(webrun.index_for([run])) + b"\n" == (RUNS / "index.json").read_bytes()
    for name, data in files.items():
        assert data == (RUNS / "sample-s1" / name).read_bytes(), name


def test_runs_folder_verifies_end_to_end():
    assert webrun.verify_run_dir(RUNS) == ["sample-s1"]


def test_bundle_hash_is_sha256_of_the_served_manifest_and_every_edition_matches():
    run = _run()
    manifest_bytes = (RUNS / "sample-s1" / "manifest.json").read_bytes()
    assert sha256_hex(manifest_bytes) == run["bundle"]["sha256"]
    artifacts = {p.name: p.read_bytes() for p in (RUNS / "sample-s1").iterdir() if p.name != "manifest.json"}
    check = verify_manifest(manifest_bytes, artifacts, expected_sha256=run["bundle"]["sha256"])
    assert check.ok, check.reasons
    assert check.manifest["attested_scope"] == {
        "attested": ["edition-en.json"],
        "gate_cleared": ["edition-easy.json", "edition-el.json", "edition-es.json", "edition-fr.json"],
    }


def test_sample_is_labelled_unapproved_and_awaiting():
    run = _run()
    assert run["provenance"] == "design_preview"
    assert run["label"] == LABEL
    assert run["state"] == "AWAITING_APPROVAL"
    assert run["approval"] == {"status": "awaiting", "approved_at": None, "approver_role": None, "channel": None}
    assert [s["state"] for s in run["states"]] == ["DRAFT", "CHECKED", "SEALED", "AWAITING_APPROVAL"]


def test_hand_written_sentences_are_tagged_hand_never_model():
    for path in sorted((RUNS / "sample-s1").glob("edition-*.json")):
        edition = json.loads(path.read_text(encoding="utf-8"))
        assert {s["author"] for s in edition["sentences"]} == {"hand"}, path.name


def test_planted_draft_is_struck_by_the_real_fact_gate():
    gates = {g["gate"]: g for g in _run()["gates"]}
    assert gates["factgate_draft"]["verdict"] == "mismatch"
    assert "claimed 2, log says 4" in gates["factgate_draft"]["evidence"]
    assert {gates[g]["verdict"] for g in ("factgate", "factgate_lang", "rights")} == {"pass"}


def test_confirm_rows_follow_the_routing_table():
    run = _run()
    rows = {r["sentence_id"]: r for r in run["confirm_rows"]}
    assert rows["en-04"]["label"] == "the referee sentence"
    assert rows["en-05"]["label"] == "the injury sentence"
    assert len(rows) == 2


def test_recap_text_never_shows_a_raw_id():
    import re

    for path in sorted((RUNS / "sample-s1").glob("edition-*.json")):
        for sentence in json.loads(path.read_text(encoding="utf-8"))["sentences"]:
            assert not re.search(r"\bC\d+(P\d+)?\b", sentence["text"]), sentence["text"]


def test_run_files_have_no_cr_bytes():
    for path in sorted(RUNS.rglob("*")):
        if path.is_file():
            assert b"\r" not in path.read_bytes(), path


def test_readme_documents_the_format():
    text = (RUNS / "README.txt").read_text(encoding="ascii")
    for needle in ("agon.web-run/1", "agon.web-runs/1", LABEL, "python -m agon.webrun --write-sample web/runs",
                   "sentence_types", "confirm_rows", "edition-<id>.json"):
        assert needle in text, needle


# ---------------------------------------------------------------- shared rule cases


@pytest.mark.parametrize("case", RUN_CASES, ids=[c["case"] for c in RUN_CASES])
def test_run_cases_shared_with_the_browser(case):
    problems = webrun.validate_run(_apply(_run(), case))
    assert (problems == []) is case["valid"], problems
    if "problem" in case:
        assert any(case["problem"] in p for p in problems), problems


def test_run_cases_cover_the_honesty_rules():
    pinned = {c.get("problem") for c in RUN_CASES}
    assert "a design preview run must carry the exact design preview label" in pinned
    assert "a design preview run can never be approved" in pinned
    assert "every injury, referee, quote and transfer sentence needs a Confirm row" in pinned
    assert sum(1 for c in RUN_CASES if not c["valid"]) >= 20


@pytest.mark.parametrize("index, problem", [
    ({"format": "agon.web-runs/1", "runs": []}, "at least one run"),
    ({"format": "x", "runs": [{"run_id": "a", "file": "a.json", "provenance": "pipeline"}]}, "format must be"),
    ({"format": "agon.web-runs/1", "runs": [{"run_id": "a", "file": "b.json", "provenance": "pipeline"}]}, "file must be a.json"),
    ({"format": "agon.web-runs/1", "runs": [{"run_id": "A/B", "file": "a.json", "provenance": "pipeline"}]}, "lowercase"),
    ({"format": "agon.web-runs/1", "runs": [{"run_id": "a", "file": "a.json", "provenance": "live"}]}, "provenance"),
])
def test_index_rules_fail(index, problem):
    assert any(problem in p for p in webrun.validate_index(index))


# ---------------------------------------------------------------- the label is pinned everywhere


def test_label_is_pinned_in_every_place_that_enforces_it():
    assert webrun.DESIGN_PREVIEW_LABEL == LABEL
    assert web_check.DESIGN_PREVIEW_LABEL == LABEL
    assert f'var DESIGN_PREVIEW_LABEL = "{LABEL}";' in (WEB / "agon-runs.js").read_text(encoding="utf-8")
    assert LABEL in (WEB / "index.html").read_text(encoding="utf-8")
    targets = {t.name: t for t in readiness.load_config(readiness.DEFAULT_CONFIG)[0]}
    assert LABEL in targets["demo"].markers
    assert LABEL in targets["demo-run"].markers


def test_standin_header_is_on_the_page_verbatim():
    header = "Stand-in for the Microsoft Teams card. It calls the same decision endpoint. The real Teams card is in the video."
    assert header in (WEB / "index.html").read_text(encoding="utf-8")
    assert header in (WEB / "agon-runs.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- negative proofs


def _copy_runs(tmp_path: Path) -> Path:
    target = tmp_path / "runs"
    shutil.copytree(RUNS, target)
    return target


def test_flipped_edition_byte_fails_the_bundle_check(tmp_path):
    runs = _copy_runs(tmp_path)
    path = runs / "sample-s1" / "edition-el.json"
    data = bytearray(path.read_bytes())
    data[20] = ord("Z") if data[20] != ord("Z") else ord("Y")
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="edition-el.json sha256 does not match"):
        webrun.verify_run_dir(runs)


def test_rewritten_manifest_fails_against_the_stated_bundle_hash(tmp_path):
    runs = _copy_runs(tmp_path)
    path = runs / "sample-s1" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["gate_verdicts"]["rights"] = "pass "
    path.write_bytes(canonical_utf8(manifest))
    with pytest.raises(ValueError, match="does not equal the expected"):
        webrun.verify_run_dir(runs)


def test_stray_file_in_runs_fails(tmp_path):
    runs = _copy_runs(tmp_path)
    (runs / "extra.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="not listed in the runs index: extra.json"):
        webrun.verify_run_dir(runs)


def test_extra_file_in_the_bundle_fails(tmp_path):
    runs = _copy_runs(tmp_path)
    (runs / "sample-s1" / "edition-de.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="edition-de.json is present but not listed"):
        webrun.verify_run_dir(runs)


def test_crlf_run_file_fails(tmp_path):
    runs = _copy_runs(tmp_path)
    path = runs / "sample-s1.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="CR bytes"):
        webrun.verify_run_dir(runs)


def test_unlabelled_run_fails_the_checker(tmp_path):
    runs = _copy_runs(tmp_path)
    run = _run()
    run["label"] = "Sample run."
    (runs / "sample-s1.json").write_bytes(webrun.run_bytes(run))
    with pytest.raises(ValueError, match="exact design preview label"):
        webrun.verify_run_dir(runs)


def test_sample_builder_refuses_sentences_a_gate_would_refuse(monkeypatch):
    original = webrun._sample_texts

    def wrong(n):
        texts = original(n)
        texts["fr"][2] = texts["fr"][2].replace(str(n["sot"]), "9")
        return texts

    monkeypatch.setattr(webrun, "_sample_texts", wrong)
    with pytest.raises(ValueError, match="fail a gate"):
        webrun.build_sample_run()


def test_cli_check_and_failure(tmp_path, capsys):
    assert webrun.main(["--check", str(RUNS)]) == 0
    assert "verified 1 runs: sample-s1" in capsys.readouterr().out
    runs = _copy_runs(tmp_path)
    (runs / "index.json").unlink()
    assert webrun.main(["--check", str(runs)]) == 1
    assert "FAIL" in capsys.readouterr().out


# ---------------------------------------------------------------- web_check rules


def _copy_web(tmp_path: Path) -> Path:
    target = tmp_path / "web"
    shutil.copytree(WEB, target)
    return target


def test_web_check_passes_on_the_shipped_tree():
    count, violations = web_check.scan(WEB)
    assert count > 20 and violations == []


def test_web_check_refuses_a_design_preview_run_without_its_label(tmp_path):
    web = _copy_web(tmp_path)
    run = _run()
    run["label"] = "Sample run."
    (web / "runs" / "sample-s1.json").write_bytes(webrun.run_bytes(run))
    rules = {v.rule for v in web_check.scan(web)[1]}
    assert "run-label" in rules


def test_web_check_refuses_a_page_without_the_label(tmp_path):
    web = _copy_web(tmp_path)
    index = web / "index.html"
    index.write_bytes(index.read_bytes().replace(LABEL.encode("utf-8"), b"Sample run."))
    rules = {v.rule for v in web_check.scan(web)[1]}
    assert "run-label-page" in rules


def test_web_check_refuses_a_fallback_without_the_runs_globs(tmp_path):
    web = _copy_web(tmp_path)
    config = web / "staticwebapp.config.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    data["navigationFallback"]["exclude"] = [p for p in data["navigationFallback"]["exclude"] if not p.startswith("/runs/")]
    config.write_text(json.dumps(data), encoding="utf-8")
    found = [v for v in web_check.scan(web)[1] if v.rule == "spa-fallback-dirs"]
    assert found and "/runs/" in found[0].message and "/runs/*/" in found[0].message


# ---------------------------------------------------------------- deploy stamp


def test_stamp_web_verifies_runs_and_fails_on_a_broken_bundle(tmp_path, capsys):
    web = _copy_web(tmp_path)
    sha = "0123456789abcdef0123456789abcdef01234567"
    assert stamp_web.main([str(web), "--sha", sha]) == 0
    assert "verified 1 runs" in capsys.readouterr().out
    web2 = _copy_web(tmp_path / "second")
    (web2 / "runs" / "sample-s1" / "edition-fr.json").write_bytes(b"{}\n")
    assert stamp_web.main([str(web2), "--sha", sha]) == 1
    assert "runs:" in capsys.readouterr().out


# ---------------------------------------------------------------- live gate rules over shipped files


def test_every_shipped_web_file_passes_the_live_gate_rules():
    """The live gate scans the page and its scripts; JSON fetched by scripts it cannot see, so all of it is scanned here."""
    rules = readiness.build_rules()
    scanned = 0
    for path in sorted(WEB.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".js", ".json", ".css", ".txt"):
            hits = readiness.forbidden_hits(path.read_text(encoding="utf-8"), rules)
            assert hits == [], f"{path.relative_to(ROOT)}: {hits}"
            scanned += 1
    assert scanned >= 25

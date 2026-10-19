"""Tests for the run files behind the Newsroom Wall, the stand-in card, the recap and Verify.

Why these exist: the one shipped run is a hand-written design preview, so its honesty rests on
checks, not on care. These tests prove that the real gates still agree with the committed
sentences (rerun, not compared byte for byte), that the bundle hash a visitor recomputes is the one
the run states, that the exact design preview label is pinned everywhere it must appear, and that
every check fails once on a deliberately broken input.

Coupling, on purpose and not: verify_run_dir holds the committed bytes to the committed hash, which
is the coupling worth keeping. The sample is NOT compared byte for byte with a fresh build, because
its bytes embed other lanes' output (Fact Gate reason wording, the Sentence dict shape, the seal
manifest layout); a wording change there must not turn this lane red. The semantic test below
reruns the gates over the committed sentences instead.
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
from agon.governance.claims import Claim, Sentence
from agon.governance.factgate import check_translation, run_factgate
from agon.governance.rights import run_rights
from agon.governance.seal import canonical_utf8, sha256_hex, verify_manifest
from sitekit import served

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
RUNS = WEB / "runs"
SAMPLE = RUNS / "sample-s1.json"
LABEL = "Design preview: sample run written by hand, not produced by the pipeline."
RUN_CASES = json.loads((ROOT / "tests" / "js" / "run_cases.json").read_text(encoding="utf-8"))
CONSOLE_RUNS = ROOT / "console" / "src" / "lib" / "runs.ts"
RECORDED = ROOT / "tests" / "fixtures" / "recorded-run" / "runs"
STANDIN = "Stand-in for the Microsoft Teams card. It calls the same decision endpoint. The real Teams card is in the video."
LABEL_PREFIXES = ('id="wall-label">', 'id="approve-label">', 'id="recap-label">')


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
        elif "from" in op:
            # copy a value already in the run (the bundle hash), so no case pins a hash
            source = out
            for key in op["from"]:
                source = source[key]
            target[op["path"][-1]] = copy.deepcopy(source)
        else:
            target[op["path"][-1]] = op["value"]
    return out


# ---------------------------------------------------------------- the committed sample


def _committed_sentences(runs: Path, edition_file: str) -> list[Sentence]:
    """Rebuild the committed sentences through the constructors, not from_dict, so a stricter dict
    parser in another lane cannot break this test. The file says author "hand"; the gates need a
    pipeline tag, and the author plays no part in any verdict."""
    edition = json.loads((runs / "sample-s1" / edition_file).read_text(encoding="utf-8"))
    return [
        Sentence(
            sentence_id=s["sentence_id"], text=s["text"], language=s["language"], author="template",
            claims=tuple(
                Claim(type=c["type"], value=c["value"], event_ids=tuple(c.get("event_ids", ())),
                      stat=c.get("stat"), subject=c.get("subject"))
                for c in s["claims"]
            ),
        )
        for s in edition["sentences"]
    ]


def _claim_key(c: Claim) -> tuple:
    return (c.type, json.dumps(c.value, sort_keys=True), c.subject, tuple(c.event_ids))


def test_real_gates_still_agree_with_the_committed_sample():
    """Reruns the Fact Gate, the translation check and the Rights Guard over the committed sentences
    against seed 1. It fails when the sample no longer tells the truth about seed 1, never on a
    change of reason wording or dict shape. Fix: PYTHONPATH=src python -m agon.webrun --write-sample web/runs"""
    _assert_sample_agrees_with_seed(RUNS)


def _assert_sample_agrees_with_seed(runs: Path) -> None:
    run = json.loads((runs / "sample-s1.json").read_text(encoding="utf-8"))
    assert run["seed"] == webrun.SAMPLE_SEED == 1
    f = webrun._sample_facts(run["seed"])
    facts, n = f["facts"], f["names"]

    # the match the run names is still seed 1's match, with the same clubs and score
    assert run["match_id"] == f["result"].meta["match_id"]
    assert (run["home"]["club_id"], run["away"]["club_id"]) == (facts.home_id, facts.away_id)
    assert (run["home"]["name"], run["away"]["name"]) == (n["home"], n["away"])
    assert run["score"] == {"home": n["fh"], "away": n["fa"]}

    editions = {e["edition"]: _committed_sentences(runs, e["file"]) for e in run["editions"]}
    en = editions["en"]

    # every name and minute claim still matches the log of seed 1, sentence by sentence
    expected = webrun._sample_claims(f)
    assert len(en) == len(expected)
    for sentence, claims in zip(en, expected):
        for kind in ("name", "minute"):
            got = sorted(_claim_key(c) for c in sentence.claims if c.type == kind)
            want = sorted(_claim_key(c) for c in claims if c.type == kind)
            assert got == want, (sentence.sentence_id, kind)

    # the planted draft is still struck, and the committed run still says so
    draft = run_factgate([webrun._planted_draft(f)], facts)
    gates = {g["gate"]: g for g in run["gates"]}
    assert draft.status == "mismatch" == gates["factgate_draft"]["verdict"]

    # every final sentence passes every gate, and the committed verdicts agree with the rerun
    en_report = run_factgate(en, facts)
    assert en_report.status == "pass" == gates["factgate"]["verdict"], [v.reasons for v in en_report.verdicts]
    for v in en_report.verdicts:
        assert run["sentence_gates"][v.sentence_id]["factgate"] == v.status == "pass"
    for edition, sentences in editions.items():
        if edition == "en":
            continue
        assert len(sentences) == len(en), edition
        for source, translated in zip(en, sentences):
            verdict = check_translation(source, translated, facts)
            assert verdict.status == "pass", (translated.sentence_id, verdict.reasons)
            assert run["sentence_gates"][translated.sentence_id]["factgate_lang"] == "pass"
    assert gates["factgate_lang"]["verdict"] == "pass"
    every = [s for sentences in editions.values() for s in sentences]
    rights = run_rights(facts.roster, every)
    assert rights.status == "pass" == gates["rights"]["verdict"], rights.refusals
    assert rights.refusals == ()


def _edit_sentence(runs: Path, edition_file: str, index: int, edit) -> None:
    path = runs / "sample-s1" / edition_file
    edition = json.loads(path.read_text(encoding="utf-8"))
    edit(edition["sentences"][index])
    path.write_bytes(canonical_utf8(edition) + b"\n")


def test_semantic_check_fails_on_a_wrong_number_in_a_translation(tmp_path):
    runs = _copy_runs(tmp_path)
    sot = str(webrun._sample_facts(1)["names"]["sot"])

    def wrong(sentence):
        assert sot in sentence["text"]
        sentence["text"] = sentence["text"].replace(sot, "9")

    _edit_sentence(runs, "edition-fr.json", 2, wrong)
    with pytest.raises(AssertionError):
        _assert_sample_agrees_with_seed(runs)


def test_semantic_check_fails_on_a_minute_that_drifted_from_the_log(tmp_path):
    runs = _copy_runs(tmp_path)

    def drift(sentence):
        for claim in sentence["claims"]:
            if claim["type"] == "minute":
                claim["value"] = str(int(claim["value"]) + 1)

    _edit_sentence(runs, "edition-en.json", 1, drift)
    with pytest.raises(AssertionError):
        _assert_sample_agrees_with_seed(runs)


def test_semantic_check_ignores_reason_wording_and_extra_dict_fields(tmp_path):
    """The coupling the byte test had and this one must not: other lanes' wording and dict shape."""
    runs = _copy_runs(tmp_path)
    path = runs / "sample-s1.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    run["gates"][0]["evidence"] = "reworded by another lane"
    path.write_bytes(webrun.run_bytes(run))
    _edit_sentence(runs, "edition-en.json", 0, lambda sentence: sentence.update(new_field_from_lane_c=True))
    _assert_sample_agrees_with_seed(runs)


def test_sample_still_builds_from_the_real_gates():
    """--write-sample stays a working convenience: the builder runs the real gates and the seal and
    refuses to write a sample they would refuse. Bytes are not compared with the committed files."""
    run, files = webrun.build_sample_run()
    assert webrun.validate_run(run) == []
    assert sorted(files) == sorted(p.name for p in (RUNS / "sample-s1").iterdir())


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
    assert run["approval"] == {
        "status": "awaiting", "approved_at": None, "approver_role": None, "channel": None, "approved_bundle_sha256": None,
    }
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
    assert "an approved run must name the approved bundle: approved_bundle_sha256 must equal bundle.sha256" in pinned
    assert "a run that is not approved has no approved_bundle_sha256" in pinned
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
    # the served console renders it from one constant; the retired vanilla page keeps its copy until web/ goes
    assert f'export const DESIGN_PREVIEW_LABEL = "{LABEL}";' in CONSOLE_RUNS.read_text(encoding="utf-8")
    assert f'var DESIGN_PREVIEW_LABEL = "{LABEL}";' in (WEB / "agon-runs.js").read_text(encoding="utf-8")
    anchored = [marker for _, marker in web_check.PAGE_LABEL_MARKERS]
    assert anchored[0] == 'id="wall-label">' + LABEL
    assert all(marker.endswith(LABEL) for marker in anchored)
    targets = {t.name: t for t in readiness.load_config(readiness.DEFAULT_CONFIG)[0]}
    assert LABEL in targets["demo-run"].markers


def test_the_served_page_carries_every_marker_of_the_live_gate():
    page = (served() / "index.html").read_text(encoding="utf-8")
    targets = {t.name: t for t in readiness.load_config(readiness.DEFAULT_CONFIG)[0]}
    labels = [m for m in targets["demo"].markers if m.startswith(LABEL_PREFIXES)]
    assert len(labels) == 3
    assert [m for m in targets["demo"].markers if m not in page] == []


def test_standin_header_is_on_the_page_verbatim():
    assert f'export const STANDIN_HEADER =\n  "{STANDIN}";' in CONSOLE_RUNS.read_text(encoding="utf-8")
    assert STANDIN in (WEB / "agon-runs.js").read_text(encoding="utf-8")
    assert STANDIN in (served() / "index.html").read_text(encoding="utf-8")


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


def _copy_served(tmp_path: Path) -> Path:
    target = tmp_path / "site"
    shutil.copytree(served(), target)
    return target


def test_web_check_passes_on_the_shipped_tree():
    count, violations = web_check.scan(served())
    assert count > 20 and violations == [], violations


def test_web_check_passes_on_the_retired_vanilla_tree_it_still_holds_as_a_fixture():
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
    web = _copy_served(tmp_path)
    index = web / "index.html"
    index.write_bytes(index.read_bytes().replace(LABEL.encode("utf-8"), b"Sample run."))
    rules = {v.rule for v in web_check.scan(web)[1]}
    assert "run-label-page" in rules


@pytest.mark.parametrize("element", ["wall-label", "approve-label", "recap-label"])
def test_web_check_refuses_a_page_missing_one_screen_copy_of_the_label(tmp_path, element):
    """Only one copy goes; the others stay. A bare substring check passed this."""
    web = _copy_served(tmp_path)
    index = web / "index.html"
    page = index.read_text(encoding="utf-8")
    start = page.index(f'id="{element}">')
    end = page.index("</p>", start)
    stripped = page[:start] + f'id="{element}">Sample run.' + page[end:]
    assert stripped.count(LABEL) == page.count(LABEL) - 1 >= 2
    index.write_bytes(stripped.encode("utf-8"))
    found = [v for v in web_check.scan(web)[1] if v.rule == "run-label-page"]
    assert len(found) == 1 and element in found[0].message, found
    assert web_check.main([str(web)]) == 1


def test_web_check_refuses_a_fallback_without_the_runs_globs(tmp_path):
    web = _copy_web(tmp_path)
    config = web / "staticwebapp.config.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    data["navigationFallback"]["exclude"] = [p for p in data["navigationFallback"]["exclude"] if not p.startswith("/runs/")]
    config.write_text(json.dumps(data), encoding="utf-8")
    found = [v for v in web_check.scan(web)[1] if v.rule == "spa-fallback-dirs"]
    assert found and "/runs/" in found[0].message and "/runs/*/" in found[0].message


def _with_recorded_default(tmp_path: Path) -> tuple[Path, dict]:
    """The vanilla tree with the committed recorded-run fixture listed first, as the exporter lists it."""
    web = _copy_web(tmp_path)
    index = json.loads((RECORDED / "index.json").read_text(encoding="utf-8"))
    row = index["runs"][0]
    shutil.copy(RECORDED / row["file"], web / "runs" / row["file"])
    shutil.copytree(RECORDED / row["run_id"], web / "runs" / row["run_id"])
    runs_index = json.loads((web / "runs" / "index.json").read_text(encoding="utf-8"))
    runs_index["runs"].insert(0, row)
    (web / "runs" / "index.json").write_bytes(canonical_utf8(runs_index) + b"\n")
    return web, json.loads((web / "runs" / row["file"]).read_text(encoding="utf-8"))


def test_web_check_requires_the_recorded_runs_own_labels_once_it_is_the_default(tmp_path):
    web, run = _with_recorded_default(tmp_path)
    found = [v for v in web_check.scan(web)[1] if v.rule == "run-label-page"]
    assert len(found) == 3, found
    assert all("recorded run shown by default" in v.message for v in found)
    assert webrun.verify_run_dir(web / "runs") == [run["run_id"], "sample-s1"]


def test_web_check_passes_a_page_that_states_the_recorded_runs_labels(tmp_path):
    web, run = _with_recorded_default(tmp_path)
    markers = [m for _, m in web_check.page_label_markers(run)]
    assert markers[2].startswith('id="recap-label">AI-drafted. Numbers and names checked by code. Headline report and flagged sentences approved')
    index = web / "index.html"
    page = index.read_text(encoding="utf-8")
    index.write_text(page.replace("</main>", "".join(f"<p {m}</p>" for m in markers) + "</main>"), encoding="utf-8")
    assert [v for v in web_check.scan(web)[1] if v.rule == "run-label-page"] == []


def test_the_approved_label_needs_the_approval_to_name_the_bundle_hash():
    row = json.loads((RECORDED / "index.json").read_text(encoding="utf-8"))["runs"][0]
    run = json.loads((RECORDED / row["file"]).read_text(encoding="utf-8"))
    assert web_check.edition_label(run).startswith(web_check.APPROVED_LABEL_PREFIX)
    other = copy.deepcopy(run)
    other["approval"]["approved_bundle_sha256"] = "0" * 64
    assert web_check.edition_label(other) == (
        "AI-drafted. Numbers and names checked by code. Not approved: the approval on file does not name this bundle hash."
    )


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


def test_every_served_file_passes_the_live_gate_rules():
    """The same scan over the built console: the page, its bundle and every run file it fetches."""
    rules = readiness.build_rules()
    site = served()
    scanned = 0
    for path in sorted(site.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".js", ".json", ".css", ".txt"):
            hits = readiness.forbidden_hits(path.read_text(encoding="utf-8"), rules)
            assert hits == [], f"{path.relative_to(site)}: {hits}"
            scanned += 1
    assert scanned >= 20


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

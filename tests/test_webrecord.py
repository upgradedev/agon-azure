"""agon.webrecord: a published bundle becomes the recorded run the console shows by default.

The bundle comes from tests/recorded_kit.py: sealed by agon.governance.seal, approved on the Teams
card by agon.governance.channels.teams with four of six destinations ticked, and published by the
real Publisher into an in-memory container written to disk. The export of that bundle is held byte
for byte to the committed fixture tests/fixtures/recorded-run/runs, which the console workflow also
renders and the vitest and node suites validate with the browser's own run rules.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import readiness
import recorded_kit
import web_check
from agon import webrecord, webrun
from agon.governance.seal import canonical_utf8
from sitekit import vanilla_fixture

ROOT = Path(__file__).resolve().parent.parent
RECORDED = ROOT / "tests" / "fixtures" / "recorded-run" / "runs"
SAMPLE_RUNS = ROOT / "web" / "runs"
CONFIG = ROOT / "ci" / "readiness.json"
LABEL_PREFIXES = ('id="wall-label">', 'id="approve-label">', 'id="recap-label">')
EXPORT_ARGS = ["--commit", recorded_kit.COMMIT, "--approver-role", "approver", "--channel", "teams"]


@pytest.fixture(scope="module")
def container(tmp_path_factory) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp("container")
    sha = recorded_kit.write_container(root)
    return root, sha


def _copy_container(container: tuple[Path, str], tmp_path: Path) -> Path:
    target = tmp_path / "container"
    shutil.copytree(container[0], target)
    return target


def _export(feed: Path, sha: str, out: Path, *extra: str) -> int:
    return webrecord.main(["--local", str(feed), "--bundle", sha, *EXPORT_ARGS, "--out", str(out), *extra])


def _fixture_run() -> dict:
    row = json.loads((RECORDED / "index.json").read_text(encoding="utf-8"))["runs"][0]
    return json.loads((RECORDED / row["file"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the round trip


def test_the_export_is_the_committed_fixture_byte_for_byte(container, tmp_path, capsys):
    root, sha = container
    out = tmp_path / "runs"
    assert _export(root, sha, out) == 0
    assert "verifies 1 runs: run-afc16bdd87a7" in capsys.readouterr().out
    written = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    committed = sorted(p.relative_to(RECORDED).as_posix() for p in RECORDED.rglob("*") if p.is_file())
    assert written == committed
    for rel in committed:
        assert (out / rel).read_bytes() == (RECORDED / rel).read_bytes(), rel


def test_the_kit_bundle_hash_is_stable():
    """When the kit, the seal or the renderers change this hash, regenerate the fixture with the
    commands in the tests/recorded_kit.py docstring."""
    assert _fixture_run()["bundle"]["sha256"] == "afc16bdd87a7e79a9b759d05cc0c6b296739466ef32e08c95bd53089a28c196b"


def test_the_fixture_verifies_with_the_python_run_rules():
    assert webrun.verify_run_dir(RECORDED) == ["run-afc16bdd87a7"]
    assert webrun.validate_run(_fixture_run()) == []


def test_the_recorded_run_says_what_it_is():
    run = _fixture_run()
    sha = run["bundle"]["sha256"]
    assert run["provenance"] == "pipeline"
    assert run["label"] == "Recorded run, 2026-10-21, commit 0123456"
    assert run["approval"] == {
        "approved_at": "2026-10-21T18:05:00Z",
        "approved_bundle_sha256": sha,
        "approver_role": "approver",
        "channel": "teams",
        "status": "approved",
    }
    assert run["state"] == "PUBLISHED"
    assert [e["file"] for e in run["editions"]] == ["report-en.json", "report-el.json", "report-easy.json"]
    assert run["desks"][0]["lines"][0]["text"] == "This trace is rebuilt from the published bundle, not taken from the live run log."


def test_destination_statuses_follow_the_ticks_and_the_published_files():
    statuses = {d["id"]: d["status"] for d in _fixture_run()["destinations"]}
    assert statuses == {
        "club_site": "delivered",
        "feeds": "delivered",
        "instagram": "preview_not_posted",
        "youtube": "not_selected",
        "newsroom_teams": "preview_not_posted",
        "journalist_email": "not_selected",
    }


def test_unticked_payloads_are_withheld_and_named_by_the_manifest():
    run = _fixture_run()
    manifest = json.loads((RECORDED / run["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert webrun.withheld_payloads(run) == {"destination-youtube.json", "destination-journalist_email.json"}
    for name in webrun.withheld_payloads(run):
        assert name in manifest["artifacts"] and not (RECORDED / run["run_id"] / name).exists()


def test_export_lists_the_recorded_run_first_and_keeps_the_design_preview(container, tmp_path):
    root, sha = container
    runs = vanilla_fixture(tmp_path / "web") / "runs"
    assert _export(root, sha, runs) == 0
    assert webrun.verify_run_dir(runs) == ["run-afc16bdd87a7", "sample-s1"]
    index = json.loads((runs / "index.json").read_text(encoding="utf-8"))
    assert [r["provenance"] for r in index["runs"]] == ["pipeline", "design_preview"]


def test_a_recorded_run_is_never_overwritten(container, tmp_path, capsys):
    root, sha = container
    out = tmp_path / "runs"
    assert _export(root, sha, out) == 0
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    capsys.readouterr()
    assert _export(root, sha, out) == 1
    assert "never overwritten" in capsys.readouterr().out
    assert before == {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}


# ---------------------------------------------------------------- refusals


def _refusal(feed: Path, sha: str, tmp_path: Path, capsys, *extra: str) -> str:
    out = tmp_path / "out"
    capsys.readouterr()
    assert _export(feed, sha, out, *extra) == 1
    assert not out.exists(), "nothing is written when the export is refused"
    return capsys.readouterr().out


def test_a_bundle_that_was_never_published_is_refused(container, tmp_path, capsys):
    root, _ = container
    assert "never published there" in _refusal(root, "0" * 64, tmp_path, capsys)


def test_a_tampered_artifact_is_refused(container, tmp_path, capsys):
    root, sha = container
    feed = _copy_container(container, tmp_path)
    path = feed / sha / "report-el.json"
    data = bytearray(path.read_bytes())
    data[40] = ord("Z") if data[40] != ord("Z") else ord("Y")
    path.write_bytes(bytes(data))
    assert "report-el.json does not match its sha256 or length" in _refusal(feed, sha, tmp_path, capsys)


def test_a_missing_artifact_that_is_not_an_unticked_payload_is_refused(container, tmp_path, capsys):
    root, sha = container
    feed = _copy_container(container, tmp_path)
    (feed / sha / "card.json").unlink()
    assert "card.json is listed in the manifest but not published" in _refusal(feed, sha, tmp_path, capsys)


def test_a_rewritten_manifest_is_refused(container, tmp_path, capsys):
    root, sha = container
    feed = _copy_container(container, tmp_path)
    path = feed / sha / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["gate_verdicts"]["rights"] = "fail"
    path.write_bytes(canonical_utf8(manifest))
    assert "the published manifest does not verify" in _refusal(feed, sha, tmp_path, capsys)


def test_published_files_that_disagree_on_the_approval_time_are_refused(container, tmp_path, capsys):
    root, sha = container
    feed = _copy_container(container, tmp_path)
    path = feed / "outbox" / "newsroom_teams" / sha / "message.json"
    message = json.loads(path.read_text(encoding="utf-8"))
    message["approved_at"] = "2026-10-21T18:06:00Z"
    path.write_bytes(canonical_utf8(message))
    assert "disagree on the approval time" in _refusal(feed, sha, tmp_path, capsys)


def test_an_approval_time_argument_for_the_same_moment_in_another_zone_agrees(container, tmp_path):
    root, sha = container
    out = tmp_path / "runs"
    assert _export(root, sha, out, "--approved-at", "2026-10-21T19:05:00+01:00") == 0
    assert (out / "run-afc16bdd87a7.json").read_bytes() == (RECORDED / "run-afc16bdd87a7.json").read_bytes()


def test_an_approval_time_argument_that_names_another_moment_is_refused(container, tmp_path, capsys):
    root, sha = container
    assert "disagrees with 2026-10-21T18:05:00Z" in _refusal(root, sha, tmp_path, capsys, "--approved-at", "2026-10-21T18:10:00Z")


def test_without_a_public_approval_time_the_argument_is_required(container, tmp_path, capsys):
    root, sha = container
    feed = _copy_container(container, tmp_path)
    shutil.rmtree(feed / "outbox")
    shutil.rmtree(feed / "feeds")
    assert "pass --approved-at" in _refusal(feed, sha, tmp_path, capsys)


@pytest.mark.parametrize(
    ("flag", "value", "problem"),
    [
        ("--commit", "0123456", "full 40-character"),
        ("--channel", "email", "invalid choice"),
        ("--bundle", "ABC", "64 lowercase hex"),
    ],
)
def test_bad_arguments_are_refused(container, tmp_path, capsys, flag, value, problem):
    root, sha = container
    args = ["--local", str(root), "--bundle", sha, *EXPORT_ARGS, "--out", str(tmp_path / "out")]
    args[args.index(flag) + 1] = value
    if flag == "--channel":
        with pytest.raises(SystemExit):
            webrecord.main(args)
        assert problem in capsys.readouterr().err
        return
    assert webrecord.main(args) == 1
    assert problem in capsys.readouterr().out


def test_missing_arguments_are_named(container, tmp_path, capsys):
    root, sha = container
    assert webrecord.main(["--local", str(root), "--bundle", sha, "--out", str(tmp_path / "out")]) == 1
    assert "missing --commit, --approver-role, --channel" in capsys.readouterr().out


def test_the_feed_url_must_be_https():
    with pytest.raises(webrecord.ExportError, match="https"):
        webrecord.HttpFeed("http://agon.example/feed/")


def test_the_local_feed_never_leaves_its_folder(tmp_path):
    feed = webrecord.LocalFeed(tmp_path)
    with pytest.raises(webrecord.ExportError, match="refusing the container path"):
        feed.get("../secret.json")


def test_text_the_site_cannot_serve_is_named_by_file_and_rule():
    dash = chr(0x2013)  # an en dash, built so this file holds none
    files = {
        "run-x/report-en.json": f'{{"text":"1{dash}0"}}'.encode("utf-8"),
        "run-x/destination-feeds.json": b'{"text":"Rated 9/10 by the desk."}',
        "run-x/card.json": b"{}",
    }
    problems = webrecord.text_problems(files)
    assert any(p.startswith("run-x/report-en.json: ") for p in problems), problems
    assert any(p.startswith("run-x/destination-feeds.json: ") for p in problems), problems
    assert not any(p.startswith("run-x/card.json") for p in problems)


# ---------------------------------------------------------------- the live gate follows the default run


def test_readiness_rewrite_points_the_live_gate_at_the_recorded_run(tmp_path):
    config = tmp_path / "readiness.json"
    shutil.copy(CONFIG, config)
    before = {t.name: t for t in readiness.load_config(config)[0]}
    run = _fixture_run()
    labels = webrecord.rewrite_readiness(config, run)
    assert labels == [m for _, m in web_check.page_label_markers(run)]
    assert labels[0] == 'id="wall-label">Recorded run, 2026-10-21, commit 0123456'
    assert labels[2] == (
        'id="recap-label">AI-drafted. Numbers and names checked by code. Headline report and flagged sentences '
        "approved by the club media office at 18:05 UTC on 2026-10-21."
    )
    after = {t.name: t for t in readiness.load_config(config)[0]}
    names = list(after)
    assert names.index("demo-recorded") == names.index("demo-bundle") + 1
    assert names.index("demo-recorded-bundle") == names.index("demo-recorded") + 1
    demo_kept = [m for m in before["demo"].markers if not m.startswith(LABEL_PREFIXES)]
    assert after["demo"].markers == demo_kept + labels
    # Every target the rewrite does not own stays as it was. The committed config may already
    # point the two recorded targets at the live recorded run; the rewrite replaces them in place.
    owned = {"demo", "demo-recorded", "demo-recorded-bundle"}
    for name, target in before.items():
        if name not in owned:
            assert after[name].markers == target.markers and after[name].url == target.url
    sha = run["bundle"]["sha256"]
    assert after["demo-recorded"].markers == [
        '"run_id":"run-afc16bdd87a7"',
        '"provenance":"pipeline"',
        "Recorded run, 2026-10-21, commit 0123456",
        f'"approved_bundle_sha256":"{sha}"',
    ]
    body = (RECORDED / "run-afc16bdd87a7.json").read_text(encoding="utf-8")
    assert all(marker in body for marker in after["demo-recorded"].markers)
    manifest = (RECORDED / "run-afc16bdd87a7" / "manifest.json").read_text(encoding="utf-8")
    assert all(marker in manifest for marker in after["demo-recorded-bundle"].markers)
    text = config.read_text(encoding="utf-8")
    webrecord.rewrite_readiness(config, run)
    assert config.read_text(encoding="utf-8") == text, "a second rewrite changes nothing"


def test_the_committed_config_is_what_the_rewrite_writes_for_the_run_shown_by_default(tmp_path):
    """Rewriting for the first run in web/runs/index.json reproduces ci/readiness.json byte for byte:
    the design preview before a recorded run is committed, that run after."""
    config = tmp_path / "readiness.json"
    shutil.copy(CONFIG, config)
    shown = web_check.default_run(SAMPLE_RUNS.parent)
    assert shown is not None
    webrecord.rewrite_readiness(config, shown[1])
    assert config.read_bytes() == CONFIG.read_bytes()


def test_rewriting_for_the_design_preview_again_drops_the_recorded_targets(tmp_path):
    config = tmp_path / "readiness.json"
    shutil.copy(CONFIG, config)
    webrecord.rewrite_readiness(config, _fixture_run())
    sample = json.loads((SAMPLE_RUNS / "sample-s1.json").read_text(encoding="utf-8"))
    assert webrecord.rewrite_readiness(config, sample) == [m for _, m in web_check.PAGE_LABEL_MARKERS]
    targets = {t.name: t for t in readiness.load_config(config)[0]}
    assert "demo-recorded" not in targets and "demo-recorded-bundle" not in targets
    assert [m for m in targets["demo"].markers if m.startswith(LABEL_PREFIXES)] == [m for _, m in web_check.PAGE_LABEL_MARKERS]


def test_readiness_only_rewrites_from_a_run_file(tmp_path, capsys):
    config = tmp_path / "readiness.json"
    shutil.copy(CONFIG, config)
    run_file = RECORDED / "run-afc16bdd87a7.json"
    assert webrecord.main(["--readiness-only", str(run_file), "--readiness", str(config)]) == 0
    assert "readiness markers now" in capsys.readouterr().out
    assert "demo-recorded" in {t.name for t in readiness.load_config(config)[0]}
    assert webrecord.main(["--readiness-only", str(run_file)]) == 1
    assert "--readiness-only needs --readiness" in capsys.readouterr().out


def test_readiness_rewrite_refuses_a_pipeline_run_not_approved_for_its_own_hash(tmp_path):
    config = tmp_path / "readiness.json"
    shutil.copy(CONFIG, config)
    run = _fixture_run()
    run["approval"]["approved_bundle_sha256"] = "0" * 64
    with pytest.raises(webrecord.ExportError, match="not approved for its own bundle hash"):
        webrecord.rewrite_readiness(config, run)
    assert config.read_bytes() == CONFIG.read_bytes()


# ---------------------------------------------------------------- flags, under the step that raised them


def _committed_pipeline_runs() -> list[tuple[Path, dict]]:
    out = []
    for folder in (SAMPLE_RUNS, RECORDED):
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        for row in index["runs"]:
            if row["provenance"] == "pipeline":
                out.append((folder, json.loads((folder / row["file"]).read_text(encoding="utf-8"))))
    return out


@pytest.mark.parametrize(
    "folder, run", _committed_pipeline_runs(), ids=lambda v: v["run_id"] if isinstance(v, dict) else v.parent.name
)
def test_committed_runs_show_each_flag_under_the_step_that_raised_it(folder, run):
    """The flag fields and the Desk Editor and Fact Gate lines of every committed recorded run are
    what the exporter computes from that run's own sealed card.json, so no other step's flag is
    ever shown as a Desk Editor flag."""
    bundle = folder / run["bundle"]["dir"]
    card = json.loads((bundle / "card.json").read_bytes().decode("utf-8"))
    verdicts = json.loads((bundle / "manifest.json").read_bytes().decode("utf-8"))["gate_verdicts"]
    ids = {s["sentence_id"] for e in run["editions"] for s in json.loads((bundle / e["file"]).read_bytes())["sentences"]}
    desk, checks = webrecord.flags_by_source(card, ids, verdicts)
    assert run["desk_editor_flags"] == desk
    assert run["check_flags"] == checks
    lines = {d["desk"]: d["lines"] for d in run["desks"]}
    assert lines["desk_editor"] == [{"step": 4, "text": webrecord.desk_editor_line(len(desk), len(run["confirm_rows"]))}]
    assert lines["factgate"][1:-1] == webrecord.factgate_retry_lines(card)
    assert [g["evidence"] for g in run["gates"]] == [webrecord.gate_evidence(card, g["gate"], g["verdict"]) for g in run["gates"]]
    own = [f for f in card["flags"] if f.get("by") == "desk_editor" and f.get("sentence_id") in ids]
    assert len(run["desk_editor_flags"]) == len(own)


def test_the_default_run_shows_its_template_line_under_the_fact_gate():
    run = json.loads((SAMPLE_RUNS / "run-0e16414b71d7.json").read_text(encoding="utf-8"))
    assert run["desk_editor_flags"] == []
    assert run["check_flags"] == [{
        "sentence_id": "s7",
        "by": "factgate",
        "name": "Fact Gate",
        "text": "The Writer's sentence failed the Fact Gate: claim value 4 (1x) is not written in the prose. "
                "Code replaced it with the template line shown, which passed the Fact Gate.",
    }]
    report = json.loads((SAMPLE_RUNS / "run-0e16414b71d7" / "report-en.json").read_bytes())
    assert [s["author"] for s in report["sentences"] if s["sentence_id"] == "s7"] == ["template"]
    lines = {d["desk"]: [line["text"] for line in d["lines"]] for d in run["desks"]}
    assert lines["desk_editor"] == ["Flagged no sentence for a person; 1 Confirm row on the card."]
    assert lines["factgate"][1] == ("Sent sentences s4 and s7 back to the Writer once; "
                                    "sentence s7 still failed, so code replaced it with a template line.")
    # The gate's own output names the Writer's s7, which the template line replaced.
    assert run["gates"][0]["gate"] == "factgate" and run["gates"][0]["evidence"] == (
        "s7 (the Writer's sentence, replaced by a template line that passed): claim value 4 (1x) is not written in the prose")


def test_a_run_with_one_sentence_sent_back_says_so_in_the_singular():
    run = json.loads((SAMPLE_RUNS / "run-6bb5b655464f.json").read_text(encoding="utf-8"))
    lines = {d["desk"]: [line["text"] for line in d["lines"]] for d in run["desks"]}
    assert lines["factgate"][1] == "Sent sentence s4 back to the Writer once; every sentence passed the second check."


def test_flags_split_by_who_raised_them():
    card = {"flags": [
        {"code": "factgate_retry", "detail": "sent back to the Writer once: s2: x", "by": "factgate"},
        {"code": "factgate_template_line", "detail": "claim value 4 (1x) is not written in the prose", "by": "factgate", "sentence_id": "s2"},
        {"code": "desk_unsupported", "detail": "the log cannot show this", "by": "desk_editor", "sentence_id": "s1"},
        {"code": "translation_template_line", "detail": "number 3 missing", "by": "factgate_lang", "sentence_id": "s1-el"},
        {"code": "easy_read_template_line", "detail": "21 words, the cap is 15", "by": "easy_read", "sentence_id": "s1-easy"},
        {"code": "desk_unsupported", "detail": "a sentence this bundle does not hold", "by": "desk_editor", "sentence_id": "s9"},
        {"code": "desk_editor_not_run", "detail": "Desk Editor is switched off", "by": "desk_editor"},
    ]}
    verdicts = {"factgate": "pass", "factgate_lang": "mismatch"}
    desk, checks = webrecord.flags_by_source(card, {"s1", "s2", "s1-el", "s1-easy"}, verdicts)
    assert desk == [{"sentence_id": "s1", "text": "the log cannot show this"}]
    assert [(c["sentence_id"], c["by"], c["name"]) for c in checks] == [
        ("s2", "factgate", "Fact Gate"),
        ("s1-el", "factgate_lang", "Fact Gate, other editions"),
        ("s1-easy", "easy_read", "Easy-read rules"),
    ]
    assert checks[0]["text"] == ("The Writer's sentence failed the Fact Gate: claim value 4 (1x) is not written in the prose. "
                                 "Code replaced it with the template line shown, which passed the Fact Gate.")
    # A gate that did not pass is never said to have passed the replacement.
    assert checks[1]["text"] == "The translated sentence failed the Fact Gate: number 3 missing. Code replaced it with the template line shown."
    assert checks[2]["text"] == ("The English sentence broke the easy-read rules: 21 words, the cap is 15. "
                                 "Code replaced it with the template line shown.")
    assert webrecord.desk_editor_line(len(desk), 2) == "Flagged 1 sentence for a person; 2 Confirm rows on the card."
    assert webrecord.desk_editor_line(0, 1) == "Flagged no sentence for a person; 1 Confirm row on the card."


RETRY_S4 = "sent back to the Writer once: s4: claim value 1 (2x) is not written in the prose"
RETRY_S4_S7 = RETRY_S4 + "; s7: claim value 4 (1x) is not written in the prose / name 'Ilse Varrow' is claimed but not written in the sentence"


@pytest.mark.parametrize("flags, line", [
    ([], None),
    ([{"code": "factgate_retry", "by": "factgate", "detail": RETRY_S4}],
     "Sent sentence s4 back to the Writer once; every sentence passed the second check."),
    ([{"code": "factgate_retry", "by": "factgate", "detail": RETRY_S4_S7}],
     "Sent sentences s4 and s7 back to the Writer once; every sentence passed the second check."),
    ([{"code": "factgate_retry", "by": "factgate", "detail": RETRY_S4_S7},
      {"code": "factgate_template_line", "by": "factgate", "detail": "d", "sentence_id": "s7"}],
     "Sent sentences s4 and s7 back to the Writer once; sentence s7 still failed, so code replaced it with a template line."),
    ([{"code": "factgate_retry", "by": "factgate", "detail": RETRY_S4_S7},
      {"code": "factgate_template_line", "by": "factgate", "detail": "d", "sentence_id": "s4"},
      {"code": "factgate_template_line", "by": "factgate", "detail": "d", "sentence_id": "s7"}],
     "Sent sentences s4 and s7 back to the Writer once; sentences s4 and s7 still failed, so code replaced them with template lines."),
    # A sentence the retry did not name never "still" failed.
    ([{"code": "factgate_retry", "by": "factgate", "detail": RETRY_S4},
      {"code": "factgate_template_line", "by": "factgate", "detail": "d", "sentence_id": "s7"}],
     "Sent sentence s4 back to the Writer once; sentence s7 failed the second check, so code replaced it with a template line."),
    # A retry whose detail names no sentence is told without a count.
    ([{"code": "factgate_retry", "by": "factgate", "detail": "d"}],
     "Sent every failing sentence back to the Writer once; every sentence passed the second check."),
    ([{"code": "factgate_template_line", "by": "factgate", "detail": "d", "sentence_id": "s7"}],
     "Sentence s7 failed, so code replaced it with a template line."),
    ([{"code": "factgate_retry", "by": "writer", "detail": "not the Fact Gate"}], None),
])
def test_the_fact_gate_line_tells_its_retry_and_its_template_lines(flags, line):
    assert webrecord.factgate_retry_lines({"flags": flags}) == ([] if line is None else [{"step": 3, "text": line}])


def test_the_fact_gate_output_names_the_sentence_a_template_line_replaced_only_when_the_gate_passed():
    card = {
        "flags": [{"code": "factgate_template_line", "by": "factgate", "detail": "x", "sentence_id": "s7"}],
        "gate_reasons": {"factgate": ["s2: claim value 1 (2x) is not written in the prose",
                                      "s7: claim value 4 (1x) is not written in the prose"],
                         "rights": ["refusal at s3: x"]},
    }
    assert webrecord.gate_evidence(card, "factgate", "pass") == (
        "s2: claim value 1 (2x) is not written in the prose; "
        "s7 (the Writer's sentence, replaced by a template line that passed): claim value 4 (1x) is not written in the prose")
    # On a mismatch the reasons also hold the template line's own failure, so they stay as written.
    assert webrecord.gate_evidence(card, "factgate", "mismatch") == (
        "s2: claim value 1 (2x) is not written in the prose; s7: claim value 4 (1x) is not written in the prose")
    assert webrecord.gate_evidence(card, "rights", "refused") == "refusal at s3: x"
    assert webrecord.gate_evidence(card, "factgate_lang", "pass") == "manifest gate_verdicts.factgate_lang = pass"


@pytest.mark.parametrize("mutate, ok", [
    (lambda r: r.pop("check_flags"), True),
    (lambda r: r.__setitem__("check_flags", []), True),
    (lambda r: r["check_flags"][0].__setitem__("by", "desk_editor"), False),
    (lambda r: r["check_flags"][0].pop("name"), False),
    (lambda r: r["check_flags"][0].__setitem__("text", " "), False),
    (lambda r: r["check_flags"][0].__setitem__("by", "Fact Gate"), False),
    (lambda r: r.__setitem__("check_flags", {"s7": "x"}), False),
])
def test_the_run_rules_hold_the_check_flags(mutate, ok):
    run = json.loads((SAMPLE_RUNS / "run-0e16414b71d7.json").read_text(encoding="utf-8"))
    assert webrun.validate_run(run) == []
    mutate(run)
    problems = webrun.validate_run(run)
    assert (problems == []) is ok, problems
    if not ok:
        assert problems == ["check_flags must be a list of {sentence_id, by, name, text}, none by the Desk Editor"]


def test_a_check_flag_on_a_sentence_no_edition_holds_fails_the_folder_check(tmp_path):
    runs = tmp_path / "runs"
    shutil.copytree(SAMPLE_RUNS, runs)
    path = runs / "run-0e16414b71d7.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    run["check_flags"][0]["sentence_id"] = "s99"
    path.write_bytes(webrun.run_bytes(run))
    with pytest.raises(ValueError, match="names sentences no edition holds: s99"):
        webrun.verify_run_dir(runs)

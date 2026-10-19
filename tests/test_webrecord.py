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

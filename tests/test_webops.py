"""agon.webops: the recorded runs' job execution evidence, copied into the site only for a shipped run.

The fixture tests/fixtures/evidence/run-c8a19020c98a.executions.json is the evidence file the build
session read from Azure for the recorded run (four Container Apps job executions); the Media office
view draws it beside the approval time in the run file.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

import stamp_web
from agon import webops
from agon.webdata import write_web_data

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "tests" / "fixtures" / "evidence"
RUN_ID = "run-c8a19020c98a"
SHA = "89abcdef0123456789abcdef0123456789abcdef"


def _site(tmp_path: Path) -> Path:
    web = tmp_path / "site"
    (web / "runs").mkdir(parents=True)
    shutil.copytree(ROOT / "web" / "runs", web / "runs", dirs_exist_ok=True)
    return web


def _evidence(tmp_path: Path, mutate=None) -> Path:
    folder = tmp_path / "evidence"
    folder.mkdir()
    body = json.loads((EVIDENCE / f"{RUN_ID}.executions.json").read_text(encoding="utf-8"))
    if mutate:
        mutate(body)
    (folder / f"{RUN_ID}.executions.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    return folder


def test_the_evidence_ships_beside_the_run_it_describes(tmp_path):
    web = _site(tmp_path)
    manifest = webops.write_ops(str(web), str(EVIDENCE))
    assert [f["run_id"] for f in manifest["files"]] == [RUN_ID]
    assert webops.verify_ops_dir(str(web)) == 1
    assert (web / "ops" / f"{RUN_ID}.executions.json").read_bytes() == (EVIDENCE / f"{RUN_ID}.executions.json").read_bytes()


def test_the_fixture_is_the_four_executions_of_the_recorded_run():
    body = json.loads((EVIDENCE / f"{RUN_ID}.executions.json").read_text(encoding="utf-8"))
    assert webops.validate_executions(body) == []
    assert [e["job"] for e in body["executions"]] == ["job-agon-fleet", "job-agon-intake", "job-agon-fleet", "job-agon-publisher"]
    run = json.loads((ROOT / "web" / "runs" / f"{RUN_ID}.json").read_text(encoding="utf-8"))
    assert body["bundle_sha256"] == run["bundle"]["sha256"]


def test_without_evidence_the_manifest_lists_none(tmp_path):
    web = _site(tmp_path)
    assert webops.write_ops(str(web), str(tmp_path / "missing")) == {"files": [], "schema": "agon.ops-manifest/1"}
    assert webops.verify_ops_dir(str(web)) == 0


def test_evidence_for_a_run_the_site_does_not_list_is_not_shipped(tmp_path):
    web = tmp_path / "bare"
    web.mkdir()
    assert webops.write_ops(str(web), str(EVIDENCE))["files"] == []


def test_evidence_naming_another_bundle_stops_the_stamp(tmp_path):
    web = _site(tmp_path)
    folder = _evidence(tmp_path, lambda b: b.update(bundle_sha256="0" * 64))
    with pytest.raises(webops.OpsError, match="names bundle 000000000000"):
        webops.write_ops(str(web), str(folder))


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (lambda b: b["executions"][0].update(end="2026-10-19T21:00:00+00:00"), "ends before it starts"),
        (lambda b: b["executions"][1].update(start="2026-10-19T21:24:36"), "with a zone"),
        (lambda b: b.pop("source_command"), "must hold exactly"),
        (lambda b: b.update(executions=[]), "non-empty list"),
        (lambda b: b.update(schema="agon.run-executions/2"), "schema must be"),
    ],
)
def test_broken_evidence_stops_the_stamp(tmp_path, mutate, problem):
    web = _site(tmp_path)
    folder = _evidence(tmp_path, mutate)
    with pytest.raises(webops.OpsError, match=problem):
        webops.write_ops(str(web), str(folder))


def test_a_tampered_or_stray_ops_file_fails_verification(tmp_path):
    web = _site(tmp_path)
    webops.write_ops(str(web), str(EVIDENCE))
    path = web / "ops" / f"{RUN_ID}.executions.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(webops.OpsError, match="does not hash"):
        webops.verify_ops_dir(str(web))
    webops.write_ops(str(web), str(EVIDENCE))
    (web / "ops" / "extra.json").write_bytes(b"{}")
    with pytest.raises(webops.OpsError, match="not listed"):
        webops.verify_ops_dir(str(web))


def test_the_deploy_stamp_writes_the_ops_folder(tmp_path, capsys):
    web = _site(tmp_path)
    (web / "index.html").write_bytes(b'<!doctype html><html><body data-commit="__AGON_COMMIT__"></body></html>')
    data = web / "data"
    write_web_data(str(data), [1], max_minute=5)
    code = stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(data), "--seeds", "1", "--evidence-dir", str(EVIDENCE)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "wrote and verified 1 execution evidence files under" in out
    manifest = json.loads((web / "ops" / "manifest.json").read_text(encoding="ascii"))
    assert manifest["files"][0]["run_id"] == RUN_ID


def test_the_cli(tmp_path, capsys):
    web = _site(tmp_path)
    assert webops.main(["--evidence", str(EVIDENCE), "--web", str(web)]) == 0
    assert "wrote and verified 1 execution files" in capsys.readouterr().out
    broken = _evidence(tmp_path, lambda b: b.update(bundle_sha256="0" * 64))
    assert webops.main(["--evidence", str(broken), "--web", str(web)]) == 1
    assert "webops: FAIL" in capsys.readouterr().out


def test_validate_refuses_a_non_object():
    assert webops.validate_executions([]) != []
    assert webops.validate_executions(copy.deepcopy({})) != []

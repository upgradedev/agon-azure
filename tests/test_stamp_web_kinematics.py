"""The deploy stamp writes and verifies the kinematics files, and fails when they cannot be trusted.

Kept apart from tests/test_stamp_web.py so the kinematics hunk in ci/stamp_web.py is tested on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import stamp_web
from agon import webkinematics
from agon.webdata import write_web_data

SHA = "fedcba9876543210fedcba9876543210fedcba98"
SHELL = '<!doctype html><html><body data-commit="__AGON_COMMIT__"><p>Agon</p></body></html>'


def make_web(tmp_path: Path) -> tuple[Path, Path]:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_bytes(SHELL.encode("utf-8"))
    data = web / "data"
    write_web_data(str(data), [1], max_minute=5)
    return web, data


def test_deploy_path_writes_and_verifies_the_kinematics(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    web, data = make_web(tmp_path)
    code = stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(data), "--seeds", "1"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "wrote and verified 2 kinematics files under" in out
    manifest = json.loads((web / "kinematics" / "manifest.json").read_text(encoding="ascii"))
    shipped = json.loads((data / "manifest.json").read_text(encoding="ascii"))["files"][0]["sha256"]
    assert manifest["files"][0]["events_sha256"] == shipped
    assert stamp_web.verify_data(data, [1]) == 2  # nothing was added under data/


def test_a_cap_violation_stops_the_deploy(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch):
    monkeypatch.setattr(webkinematics.kin, "check", lambda record: ["planted: speed over the cap"])
    web, data = make_web(tmp_path)
    code = stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(data), "--seeds", "1"])
    out = capsys.readouterr().out
    assert code == 1
    assert "stamp_web: FAIL: kinematics: seed 1: refusing an invalid kinematics file" in out
    assert "metric M9 requires 0" in out


def test_without_a_data_dir_no_kinematics_are_written(tmp_path: Path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_bytes(SHELL.encode("utf-8"))
    assert stamp_web.main([str(web), "--sha", SHA]) == 0
    assert not (web / "kinematics").exists()

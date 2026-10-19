"""The deploy stamp writes and verifies the season files, and fails when they cannot be trusted.

Kept apart from tests/test_stamp_web.py so the season hunk in ci/stamp_web.py is tested on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import stamp_web
from agon import webseason
from agon.webdata import write_web_data

SHA = "89abcdef0123456789abcdef0123456789abcdef"
SHELL = '<!doctype html><html><body data-commit="__AGON_COMMIT__"><p>Agon</p></body></html>'


def make_web(tmp_path: Path) -> tuple[Path, Path]:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_bytes(SHELL.encode("utf-8"))
    data = web / "data"
    write_web_data(str(data), [1], max_minute=5)
    return web, data


def test_deploy_path_writes_and_verifies_the_season(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    web, data = make_web(tmp_path)
    code = stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(data), "--seeds", "1"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "wrote and verified 7 season files under" in out
    assert webseason.verify_season_dir(web / "season") == 7
    manifest = json.loads((web / "season" / "manifest.json").read_text(encoding="ascii"))
    assert manifest["season"] == 1
    # The season folder sits beside data/, never inside it: data/ allows no unlisted file.
    assert stamp_web.verify_data(data, [1]) == 2


def test_a_stale_bands_record_stops_the_deploy(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch):
    record = webseason.load_bands_record()
    record["generator"] = "synth-v0.1"
    stale = tmp_path / "bands_heldout.json"
    stale.write_text(json.dumps(record), encoding="ascii")
    monkeypatch.setattr(webseason, "BANDS_RECORD", str(stale))
    web, data = make_web(tmp_path)
    code = stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(data), "--seeds", "1"])
    out = capsys.readouterr().out
    assert code == 1
    assert "stamp_web: FAIL: season: bands record refused" in out and "re-measure" in out
    assert not (web / "season" / "manifest.json").exists()


def test_without_a_data_dir_no_season_is_written(tmp_path: Path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_bytes(SHELL.encode("utf-8"))
    assert stamp_web.main([str(web), "--sha", SHA]) == 0
    assert not (web / "season").exists()

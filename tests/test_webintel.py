"""The intel web data contract: written from the served data, re-checked, and refused when it cannot be trusted."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from agon import webintel
from agon.interpret.advanced import METRICS, PLAYER_KEYS, SERIES_KEYS, TEAM_KEYS
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.webdata import write_web_data

README = Path(webintel.__file__).with_name("webintel_README.txt")
DASHES = (chr(0x2013), chr(0x2014))


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("intel")
    data, intel = root / "data", root / "intel"
    write_web_data(str(data), [1])
    webintel.write_intel_web(str(intel), str(data), [1])
    return data, intel


def _copy(site, tmp_path: Path) -> tuple[Path, Path]:
    data, intel = site
    shutil.copytree(data, tmp_path / "data")
    shutil.copytree(intel, tmp_path / "intel")
    return tmp_path / "data", tmp_path / "intel"


def _load(intel: Path, name: str) -> dict:
    return json.loads((intel / name).read_bytes().decode("ascii"))


def test_written_folder_verifies_and_lists_every_file(site):
    data, intel = site
    assert webintel.verify_intel_dir(intel, data) == 2
    manifest = _load(intel, "manifest.json")
    assert [f["name"] for f in manifest["files"]] == ["metrics-1.json", "milestones-season-1.json"]
    served = sha256_hex((data / "1.ndjson").read_bytes())
    assert manifest["files"][0]["events_sha256"] == served == _load(intel, "metrics-1.json")["events_sha256"]


def test_files_are_canonical_with_a_final_lf(site):
    _, intel = site
    for path in sorted(intel.iterdir()):
        raw = path.read_bytes()
        assert raw.endswith(b"\n") and canonical_bytes(json.loads(raw)) + b"\n" == raw, path.name


def test_metrics_file_carries_what_the_console_draws(site):
    _, intel = site
    body = _load(intel, "metrics-1.json")
    assert body["label"].startswith("Synthetic match") and "real football" in body["label"]
    assert [d["key"] for d in body["definitions"]] == list(METRICS)
    home, away = body["home"], body["away"]
    assert set(body["teams"][home]) == set(TEAM_KEYS)
    assert body["series"]["bins"] == ["0-15", "15-30", "30-45+", "45-60", "60-75", "75-90+"]
    assert body["series"]["keys"] == list(SERIES_KEYS)
    assert all(set(r) == {"player_id", "name", "shirt", "position", "club_id", *PLAYER_KEYS} for r in body["players"])
    assert body["passes"]["frame"] == "home_attacks_high_x_both_periods"
    assert len(body["passes"]["rows"]) == body["teams"][home]["passes"] + body["teams"][away]["passes"]
    changes = body["rhythm"]["control_vs_chaos"]["changes"] + body["rhythm"]["tempo"]["changes"]
    assert changes and all(c["sentence"].endswith(".") for c in changes)
    assert {m["kind"] for m in body["milestones"]} >= {"first_goal", "table"} and body["milestones_note"] is None


def test_pass_rows_are_in_the_fixed_frame(site):
    data, intel = site
    body = _load(intel, "metrics-1.json")
    cols = body["passes"]["columns"]
    events = {e["event_id"]: e for e in (json.loads(line) for line in (data / "1.ndjson").read_text(encoding="ascii").splitlines())}
    for row in body["passes"]["rows"][:200]:
        r = dict(zip(cols, row))
        e = events[r["event_id"]]
        if e["team"] == body["home"]:
            assert (r["x_dm"], r["y_dm"]) == (e["x_dm"], e["y_dm"])
        else:
            assert (r["x_dm"], r["y_dm"]) == (1050 - e["x_dm"], 680 - e["y_dm"])


def test_a_tampered_file_fails(site, tmp_path):
    data, intel = _copy(site, tmp_path)
    path = intel / "metrics-1.json"
    path.write_bytes(path.read_bytes().replace(b'"pack":"advanced-v1"', b'"pack":"advanced-v2"'))
    with pytest.raises(ValueError, match="sha256"):
        webintel.verify_intel_dir(intel, data)


def test_a_stray_file_fails(site, tmp_path):
    data, intel = _copy(site, tmp_path)
    (intel / "extra.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="not listed"):
        webintel.verify_intel_dir(intel, data)


def test_a_file_for_another_event_log_fails(site, tmp_path):
    data, intel = _copy(site, tmp_path)
    log = data / "1.ndjson"
    log.write_bytes(log.read_bytes().replace(b'"seq":3,', b'"seq":3, ', 1))
    with pytest.raises(ValueError, match="served event log"):
        webintel.verify_intel_dir(intel, data)


def test_a_truncated_replay_gets_metrics_and_no_milestones_with_a_reason(tmp_path):
    data = tmp_path / "data"
    write_web_data(str(data), [1], max_minute=5)
    body = webintel.metrics_payload(1, str(data))
    assert body["milestones"] == [] and body["milestones_note"] == webintel.TRUNCATED_NOTE
    assert webintel.validate_payload("metrics-1.json", body) == []
    lying = dict(body, milestones_note=None)
    assert webintel.validate_payload("metrics-1.json", lying) == []  # null note with no facts is allowed
    lying = dict(body, milestones=[{"kind": "x"}])
    assert webintel.validate_payload("metrics-1.json", lying)


def test_a_log_that_is_not_the_season_match_is_refused(tmp_path):
    data = tmp_path / "data"
    write_web_data(str(data), [1])
    log = data / "1.ndjson"
    lines = log.read_bytes().splitlines(keepends=True)
    log.write_bytes(b"".join(lines[:3] + lines[4:]))  # one event missing from the middle
    with pytest.raises(ValueError, match="is not season 1 match 0"):
        webintel.metrics_payload(1, str(data))


def test_validators_refuse_broken_shapes(site):
    _, intel = site
    good = _load(intel, "metrics-1.json")
    assert webintel.validate_payload("metrics-1.json", good) == []
    assert webintel.validate_payload("metrics-2.json", good)  # wrong seed for the name
    missing = {k: v for k, v in good.items() if k != "rhythm"}
    assert webintel.validate_payload("metrics-1.json", missing)
    neg = json.loads(json.dumps(good))
    neg["teams"][good["home"]]["progressive_passes"] = -1
    assert any("progressive_passes" in p for p in webintel.validate_payload("metrics-1.json", neg))
    cols = json.loads(json.dumps(good))
    cols["passes"]["columns"] = cols["passes"]["columns"][:-1]
    assert webintel.validate_payload("metrics-1.json", cols)
    cell = json.loads(json.dumps(good))
    cell["passes"]["rows"][0][7] = "far"
    assert webintel.validate_payload("metrics-1.json", cell)
    series = json.loads(json.dumps(good))
    series["series"][good["home"]]["xg_ppm"] = [0, 0, 0]
    assert webintel.validate_payload("metrics-1.json", series)
    cite = json.loads(json.dumps(good))
    cite["citable"]["stats"].append({"stat": "xg_ppm", "unit": "ppm", "subject": "club"})
    assert any("does not know" in p for p in webintel.validate_payload("metrics-1.json", cite))
    sentence = json.loads(json.dumps(good))
    sentence["rhythm"]["tempo"]["changes"][0]["sentence"] = ""
    assert webintel.validate_payload("metrics-1.json", sentence)
    milestone = json.loads(json.dumps(good))
    milestone["milestones"][0]["claims"][0]["type"] = "stat"
    assert webintel.validate_payload("metrics-1.json", milestone)
    assert webintel.validate_payload("other.json", good) == ["other.json is not an intel file"]


def test_manifest_validator_refuses_bad_entries(site):
    _, intel = site
    manifest = _load(intel, "manifest.json")
    assert webintel.validate_manifest(manifest) == []
    bad = json.loads(json.dumps(manifest))
    bad["files"][0]["name"] = "../metrics-1.json"
    assert webintel.validate_manifest(bad)
    bad = json.loads(json.dumps(manifest))
    bad["files"][1]["seed"] = 1
    assert webintel.validate_manifest(bad)
    bad = json.loads(json.dumps(manifest))
    bad["files"][0]["kind"] = "rumours"
    assert webintel.validate_manifest(bad)


def test_window_text_names_added_time():
    assert webintel.window_text(1, 45) == "first-half added time"
    assert webintel.window_text(2, 90) == "second-half added time"
    assert webintel.window_text(2, 60) == "minutes 60 to 65"


def test_ordinals():
    assert [webintel.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st"]


def test_readme_names_every_key_and_column():
    text = README.read_text(encoding="ascii")
    assert not any(d in text for d in DASHES)
    words = set(re.findall(r"[a-z0-9_]+", text))
    expected = set(TEAM_KEYS) | set(PLAYER_KEYS) | set(SERIES_KEYS) | set(webintel.PASS_COLUMNS) | set(webintel.METRICS_KEYS)
    expected |= set(webintel.MANIFEST_KEYS) | set(webintel.MANIFEST_FILE_KEYS) | {c["stat"] for c in webintel.CITABLE}
    missing = sorted(k for k in expected if k not in words)
    assert not missing, f"webintel_README.txt does not mention: {missing}"


def test_cli_writes_and_verifies(site, tmp_path, capsys):
    data, _ = site
    out = tmp_path / "intel"
    assert webintel.main(["--data", str(data), "--seeds", "1", "--out", str(out)]) == 0
    assert "intel files 2" in capsys.readouterr().out
    assert sorted(os.listdir(out)) == ["manifest.json", "metrics-1.json", "milestones-season-1.json"]

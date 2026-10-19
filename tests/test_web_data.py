"""Tests for the web data contract and the committed fixture in web/data-fixture/.

Why these exist: the browser trusts nothing it cannot recompute, so the files
it reads must be exactly what the generator produces and exactly the shape the
deploy step writes. The fixture is regenerated here byte for byte, every listed
hash is recomputed, every line is checked against the event schema, and the
manifest rules are held to a case list that the JavaScript side
(tests/js/core.test.js) checks against the same expectations.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agon.synth.engine import GENERATOR_VERSION
from agon.synth.manifest import build_bundle
from agon.synth.schema import PITCH_X_DM, PITCH_Y_DM, sha256_hex, to_fixed_frame, validate_event
from agon.webdata import (
    FILE_KEYS,
    MANIFEST_KEYS,
    build_web_data,
    main,
    manifest_bytes,
    validate_manifest,
    write_web_data,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "web" / "data-fixture"
JS_TESTS = ROOT / "tests" / "js"
FIXTURE_SEEDS = [1]
FIXTURE_MAX_MINUTE = 10


def _manifest() -> dict:
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="ascii"))


def test_fixture_manifest_matches_the_contract():
    manifest = _manifest()
    assert validate_manifest(manifest) == []
    assert set(manifest) == MANIFEST_KEYS
    assert all(set(entry) == FILE_KEYS for entry in manifest["files"])
    assert manifest["generator"] == GENERATOR_VERSION
    assert [e["seed"] for e in manifest["files"]] == FIXTURE_SEEDS
    assert [e["name"] for e in manifest["files"]] == ["1.ndjson"]


def test_fixture_hashes_and_counts_recompute():
    for entry in _manifest()["files"]:
        data = (FIXTURE / entry["name"]).read_bytes()
        assert sha256_hex(data) == entry["sha256"], f"{entry['name']}: sha256 differs from the manifest"
        assert b"\r" not in data, "CRLF in the fixture would change its hash on checkout"
        data.decode("ascii")
        assert data.endswith(b"\n")
        assert data.count(b"\n") == entry["events"]


def test_fixture_events_are_valid_and_inside_the_first_ten_minutes():
    for entry in _manifest()["files"]:
        lines = (FIXTURE / entry["name"]).read_text(encoding="ascii").splitlines()
        events = [json.loads(line) for line in lines]
        assert [e["seq"] for e in events] == list(range(len(events)))
        for event in events:
            assert validate_event(event) == [], event["event_id"]
            assert event["period"] == 1 and event["minute"] < FIXTURE_MAX_MINUTE
            assert event["match_id"].startswith(f"s{entry['seed']}-")


def test_fixture_is_reproducible_from_the_generator():
    """Fails when the generator changes. Fix: PYTHONPATH=src python -m agon.webdata --seeds 1 --max-minute 10 --out web/data-fixture"""
    manifest, payloads = build_web_data(FIXTURE_SEEDS, max_minute=FIXTURE_MAX_MINUTE)
    assert manifest_bytes(manifest) == (FIXTURE / "manifest.json").read_bytes()
    for name, data in payloads.items():
        assert data == (FIXTURE / name).read_bytes(), f"{name} differs from a fresh generator run"


def test_fixture_folder_holds_only_contract_files_and_the_readme():
    names = sorted(p.name for p in FIXTURE.iterdir())
    assert names == sorted(["README.txt", "manifest.json"] + [e["name"] for e in _manifest()["files"]])


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_home_club_is_the_team_of_the_first_event(seed):
    bundle = build_bundle(seed)
    first = bundle.events[0]
    assert first["seq"] == 0 and first["period"] == 1
    assert first["type"] == "possession_change" and first["outcome"] == "kick_off"
    assert first["team"] == bundle.match["home"]


def test_full_match_write_round_trip(tmp_path, capsys):
    assert main(["--seeds", "1", "--out", str(tmp_path)]) == 0
    assert "1.ndjson events" in capsys.readouterr().out
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="ascii"))
    assert validate_manifest(manifest) == []
    entry = manifest["files"][0]
    data = (tmp_path / "1.ndjson").read_bytes()
    assert sha256_hex(data) == entry["sha256"]
    assert entry["events"] == len(build_bundle(1).events) == data.count(b"\n")


def test_truncation_keeps_only_the_opening_minutes(tmp_path):
    manifest = write_web_data(str(tmp_path), [2], max_minute=3)
    events = [json.loads(line) for line in (tmp_path / "2.ndjson").read_text(encoding="ascii").splitlines()]
    assert manifest["files"][0]["events"] == len(events) > 0
    assert all(e["period"] == 1 and e["minute"] < 3 for e in events)


def test_writer_refuses_an_invalid_manifest(tmp_path, monkeypatch):
    import agon.webdata as webdata

    monkeypatch.setattr(webdata, "GENERATOR_VERSION", "")
    with pytest.raises(ValueError, match="invalid manifest"):
        webdata.write_web_data(str(tmp_path), [1], max_minute=1)
    assert not (tmp_path / "manifest.json").exists()


CASES = json.loads((JS_TESTS / "manifest_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_manifest_cases_shared_with_the_browser(case):
    problems = validate_manifest(case["manifest"])
    assert (problems == []) is case["valid"], problems


def test_manifest_cases_cover_both_outcomes():
    assert {c["valid"] for c in CASES} == {True, False}
    assert sum(1 for c in CASES if not c["valid"]) >= 10


def test_fixed_frame_golden_matches_python():
    """The JS port is held to this golden; the golden is held to Python here."""
    golden = json.loads((JS_TESTS / "fixed_frame_golden.json").read_text(encoding="utf-8"))
    lines = (ROOT / golden["source"]).read_text(encoding="ascii").splitlines()
    events = [json.loads(line) for line in lines]
    home = events[0]["team"]
    assert golden["home"] == home
    assert golden["points"] == [list(to_fixed_frame(e, home)) for e in events]
    away = [e for e in events if e["team"] != home]
    assert away, "the golden must include away events or it cannot test the rotation"
    for event, point in zip(events, golden["points"]):
        if event["team"] != home:
            assert point[0] == PITCH_X_DM - event["x_dm"] and point[1] == PITCH_Y_DM - event["y_dm"]


def test_readme_documents_the_contract():
    text = (FIXTURE / "README.txt").read_text(encoding="ascii")
    for needle in (
        '{"generator": str,',
        '"files": [{"name": str, "sha256": str, "seed": int, "events": int}]}',
        "python -m agon.webdata --seeds 1 --max-minute 10 --out web/data-fixture",
        "to_fixed_frame",
        "1050 - x AND y to 680 - y",
        "data/manifest.json",
    ):
        assert needle in text, needle


def test_fixture_copy_with_a_flipped_byte_fails_the_hash(tmp_path):
    """The hash check is only evidence if it can fail."""
    target = tmp_path / "fixture"
    shutil.copytree(FIXTURE, target)
    path = target / "1.ndjson"
    data = bytearray(path.read_bytes())
    data[10] = ord("Z") if data[10] != ord("Z") else ord("Y")
    path.write_bytes(bytes(data))
    entry = _manifest()["files"][0]
    assert sha256_hex(path.read_bytes()) != entry["sha256"]

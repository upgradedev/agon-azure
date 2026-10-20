"""The kinematics web contract: files derived from the shipped event bytes, re-hashed and shape-checked."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

import pytest

import kinkit

from agon import webkinematics as wk
from agon.synth import kinematics as kin
from agon.synth.manifest import build_bundle
from agon.synth.schema import sha256_hex
from agon.webdata import write_web_data


@lru_cache(maxsize=None)
def built() -> tuple[dict, dict]:
    return wk.build_from_seeds([1])


def body() -> dict:
    return json.loads(built()[1]["1.kinematics.json"].decode("ascii"))


def short_data(tmp_path: Path, seeds=(1,)) -> Path:
    data = tmp_path / "data"
    write_web_data(str(data), list(seeds), max_minute=5)
    return data


def test_full_match_file_names_its_source_and_matches_the_synth_record():
    b = body()
    assert b["events_sha256"] == sha256_hex(build_bundle(1).events_ndjson())
    assert b["source"] == "1.ndjson" and b["match_id"] == "s1-m0"
    assert b["periods"] == kinkit.record(1)["periods"]
    assert b["checks"]["cap_violations"] == 0 and b["checks"]["anchors"] > 1500
    assert "not measured" in b["note"]
    assert wk.validate_payload(b) == []


def test_player_triggers_come_from_the_published_samples():
    b = body()
    full = [p for p in b["players"] if p["distance_dm"] > 60_000]
    assert full, "full-match players run more than 6 km"
    for p in b["players"]:
        assert p["top_speed_kmh_x10"] <= kin.CHECK_VMAX_DM_S * 36 // 10
        assert all(s["period"] in (1, 2) and s["start_s"] <= s["end_s"] for s in p["sprints"])
        kms = [m["km"] for m in p["distance_milestones"]]
        assert kms == list(range(2, 2 * len(kms) + 1, 2))
        assert len(kms) == p["distance_dm"] // 20_000


def test_written_folder_verifies_against_the_shipped_data(tmp_path: Path):
    data = short_data(tmp_path, (1, 2))
    out = tmp_path / "kinematics"
    manifest = wk.write_kinematics_web(out, data)
    assert wk.verify_kinematics_dir(out, data) == 3
    shipped = {e["seed"]: e["sha256"] for e in json.loads((data / "manifest.json").read_text())["files"]}
    assert {e["seed"]: e["events_sha256"] for e in manifest["files"]} == shipped
    readme = (out / "README.txt").read_bytes()
    assert readme == wk.README_TEXT.encode("ascii")
    assert chr(0x2013).encode() not in readme and chr(0x2014).encode() not in readme
    for name in ("1.kinematics.json", "manifest.json"):
        raw = (out / name).read_bytes()
        assert raw.endswith(b"\n") and b"\r" not in raw and all(c < 128 for c in raw)


def test_readme_states_units_frame_rate_and_joins():
    for phrase in ("decimetres", "home_attacks_high_x_both_periods", "1 sample per second", "tenths of km/h",
                   "event_id", "player_id", "events_sha256", "SYNTHETIC", "not measured"):
        assert phrase in wk.README_TEXT, phrase


def test_a_file_derived_from_other_events_is_refused(tmp_path: Path):
    data = short_data(tmp_path)
    out = tmp_path / "kinematics"
    wk.write_kinematics_web(out, data)
    other = tmp_path / "other"
    write_web_data(str(other), [1], max_minute=6)
    with pytest.raises(ValueError, match="derived from events other than the shipped 1.ndjson"):
        wk.verify_kinematics_dir(out, other)


def test_tampered_stray_and_missing_files_are_refused(tmp_path: Path):
    data = short_data(tmp_path)
    out = tmp_path / "kinematics"
    wk.write_kinematics_web(out, data)
    (out / "extra.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="not listed in the manifest: extra.json"):
        wk.verify_kinematics_dir(out, data)
    (out / "extra.json").unlink()
    raw = bytearray((out / "1.kinematics.json").read_bytes())
    raw[30] = ord("X") if raw[30] != ord("X") else ord("Y")
    (out / "1.kinematics.json").write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="kinematics/1.kinematics.json: sha256"):
        wk.verify_kinematics_dir(out, data)
    wk.write_kinematics_web(out, data)
    (out / "README.txt").unlink()
    with pytest.raises(ValueError, match="README.txt does not exist"):
        wk.verify_kinematics_dir(out, data)


def test_payload_rules_refuse_cap_violations_and_a_missing_label():
    b = copy.deepcopy(body())
    b["checks"]["cap_violations"] = 1
    assert any("metric M9 requires 0" in p for p in wk.validate_payload(b))
    b = copy.deepcopy(body())
    b["note"] = "tracking"
    assert any("not measured" in p for p in wk.validate_payload(b))
    b = copy.deepcopy(body())
    b["periods"][0]["ball"].append(1)
    assert any("pairs" in p or "one pair per second" in p for p in wk.validate_payload(b))
    b = copy.deepcopy(body())
    del b["players"][0]["sprints"]
    assert any("players[0] keys" in p for p in wk.validate_payload(b))


def test_manifest_rules():
    m = copy.deepcopy(built()[0])
    assert wk.validate_manifest(m) == []
    m["files"][0]["name"] = "2.kinematics.json"
    assert any("<seed>.kinematics.json" in p for p in wk.validate_manifest(m))
    m = copy.deepcopy(built()[0])
    m["readme"] = {"name": "README.md", "sha256": "0" * 64}
    assert any("README.txt" in p for p in wk.validate_manifest(m))


def test_events_from_another_seed_are_refused():
    events = [json.loads(line) for line in build_bundle(2).events_ndjson().decode("ascii").splitlines()[:40]]
    with pytest.raises(ValueError, match="events belong to s2-m0"):
        wk.kinematics_payload(1, events, "0" * 64, "1.ndjson")

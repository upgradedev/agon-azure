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
    assert webintel.verify_intel_dir(intel, data) == 3
    manifest = _load(intel, "manifest.json")
    assert [f["name"] for f in manifest["files"]] == ["metrics-1.json", "whatif-1.json", "milestones-season-1.json"]
    served = sha256_hex((data / "1.ndjson").read_bytes())
    assert manifest["files"][0]["events_sha256"] == served == _load(intel, "metrics-1.json")["events_sha256"]
    assert manifest["files"][1]["events_sha256"] == served == _load(intel, "whatif-1.json")["events_sha256"]


def test_the_whatif_file_is_the_committed_set_byte_for_byte(site):
    _, intel = site
    from agon.whatif.scenarios import SET_DIR

    assert (intel / "whatif-1.json").read_bytes() == Path(SET_DIR, "whatif-1.json").read_bytes()


def test_the_whatif_helper_names_why_a_set_is_stale(site, tmp_path):
    from agon.whatif.scenarios import SET_DIR

    with pytest.raises(ValueError, match="does not match the served event log"):
        webintel.committed_whatif(1, "0" * 64)
    with pytest.raises(ValueError, match="no committed What-If set for seed 99"):
        webintel.committed_whatif(99, "0" * 64)
    body = json.loads(Path(SET_DIR, "whatif-1.json").read_bytes())
    served = body["events_sha256"]
    Path(tmp_path, "whatif-1.json").write_bytes(canonical_bytes(dict(body, generator="synth-v9")) + b"\n")
    with pytest.raises(ValueError, match="generator version"):
        webintel.committed_whatif(1, served, str(tmp_path))
    Path(tmp_path, "whatif-1.json").write_bytes(json.dumps(body, indent=1).encode("ascii"))
    with pytest.raises(ValueError, match="not canonical"):
        webintel.committed_whatif(1, served, str(tmp_path))


def test_a_stale_whatif_set_is_withheld_and_the_deploy_goes_on(site, tmp_path):
    from agon.whatif.scenarios import SET_DIR

    data, _ = site
    stale = tmp_path / "set"
    stale.mkdir()
    body = json.loads(Path(SET_DIR, "whatif-1.json").read_bytes())
    (stale / "whatif-1.json").write_bytes(canonical_bytes(dict(body, model_sha256="0" * 64)) + b"\n")
    out = tmp_path / "intel"
    manifest = webintel.write_intel_web(str(out), str(data), [1], set_dir=str(stale))
    assert [f["name"] for f in manifest["files"]] == ["metrics-1.json", "milestones-season-1.json"]
    [held] = manifest["withheld"]
    assert held["name"] == "whatif-1.json" and held["seed"] == 1 and "interpret model" in held["detail"]
    assert held["reason"].startswith("The What-If Lab is not available for this match")
    assert webintel.verify_intel_dir(out, data) == 2
    (out / "whatif-1.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="listed as withheld but is present"):
        webintel.verify_intel_dir(out, data)


def test_a_missing_or_invalid_whatif_file_is_withheld_too(site, tmp_path):
    from agon.whatif.scenarios import SET_DIR

    data, _ = site
    empty = tmp_path / "empty"
    empty.mkdir()
    manifest, files = webintel.build_intel_web(str(data), [1], set_dir=str(empty))
    assert "whatif-1.json" not in files and manifest["withheld"][0]["detail"].startswith("no committed What-If set")
    broken = tmp_path / "broken"
    broken.mkdir()
    body = json.loads(Path(SET_DIR, "whatif-1.json").read_bytes())
    body["scenarios"][0]["prefix"]["differing_events"] = 3
    (broken / "whatif-1.json").write_bytes(canonical_bytes(body) + b"\n")
    manifest, files = webintel.build_intel_web(str(data), [1], set_dir=str(broken))
    assert "whatif-1.json" not in files and "What-If contract" in manifest["withheld"][0]["detail"]


@pytest.mark.parametrize("payload", [b"[]\n", b'"text"\n', b"null\n", b"{}\n"])
def test_a_malformed_whatif_file_is_withheld_not_a_stopped_deploy(site, tmp_path, payload):
    data, _ = site
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "whatif-1.json").write_bytes(payload)
    manifest, files = webintel.build_intel_web(str(data), [1], set_dir=str(bad))
    assert "whatif-1.json" not in files and [w["name"] for w in manifest["withheld"]] == ["whatif-1.json"]


@pytest.mark.parametrize(("field", "value", "cause"), [
    ("code_sha256", "0" * 64, "the code of python -m agon.whatif"),
    ("interpret", "interpret-v0", "the interpret version"),
])
def test_a_whatif_file_for_other_code_is_withheld(site, tmp_path, field, value, cause):
    from agon.whatif.scenarios import SET_DIR

    data, _ = site
    other = tmp_path / "other"
    other.mkdir()
    body = json.loads(Path(SET_DIR, "whatif-1.json").read_bytes())
    (other / "whatif-1.json").write_bytes(canonical_bytes(dict(body, **{field: value})) + b"\n")
    manifest, files = webintel.build_intel_web(str(data), [1], set_dir=str(other))
    assert "whatif-1.json" not in files and cause in manifest["withheld"][0]["detail"]


def test_the_code_fingerprint_ignores_line_endings_and_sees_a_change(tmp_path):
    from agon.whatif.scenarios import AGON_DIR, CODE_FILES, code_sha256

    for rel in CODE_FILES:
        target = tmp_path.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(AGON_DIR, *rel.split("/")).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert code_sha256(str(tmp_path)) == code_sha256()
    metrics = tmp_path / "interpret" / "metrics.py"
    metrics.write_bytes(metrics.read_bytes() + b"# a changed definition\r\n")
    assert code_sha256(str(tmp_path)) != code_sha256()


def test_manifest_refuses_a_file_both_shipped_and_withheld(site):
    _, intel = site
    manifest = _load(intel, "manifest.json")
    assert manifest["withheld"] == []
    both = json.loads(json.dumps(manifest))
    both["withheld"] = [{"name": "whatif-1.json", "kind": "whatif", "seed": 1, "reason": "r", "detail": "d"}]
    assert any("both shipped and withheld" in p for p in webintel.validate_manifest(both))
    odd = json.loads(json.dumps(manifest))
    odd["withheld"] = [{"name": "metrics-1.json", "kind": "metrics", "seed": 1, "reason": "r", "detail": "d"}]
    assert webintel.validate_manifest(odd)


def test_a_truncated_replay_has_no_whatif_file(tmp_path):
    data = tmp_path / "data"
    write_web_data(str(data), [1], max_minute=5)
    manifest, files = webintel.build_intel_web(str(data), [1])
    assert sorted(files) == ["metrics-1.json", "milestones-season-1.json"]


def test_whatif_validator_refuses_a_changed_past_and_broken_numbers():
    from agon.whatif.scenarios import SET_DIR

    good = json.loads(Path(SET_DIR, "whatif-1.json").read_bytes())
    assert webintel.validate_payload("whatif-1.json", good) == []
    assert webintel.validate_payload("whatif-2.json", good)
    past = json.loads(json.dumps(good))
    past["scenarios"][0]["prefix"]["differing_events"] = 1
    assert any("published match" in p for p in webintel.validate_payload("whatif-1.json", past))
    label = dict(good, label="A forecast.")
    assert webintel.validate_payload("whatif-1.json", label)
    counts = json.loads(json.dumps(good))
    counts["scenarios"][0]["outcomes"]["baseline"]["win"]["count"] += 1
    assert any("add up to k" in p for p in webintel.validate_payload("whatif-1.json", counts))
    order = json.loads(json.dumps(good))
    d = order["scenarios"][0]["deltas"][0]
    d["delta_lo"] = d["delta_mean"] + 1
    assert webintel.validate_payload("whatif-1.json", order)
    timeline = json.loads(json.dumps(good))
    timeline["scenarios"][0]["xg_timeline"]["scenario"]["for"].pop()
    assert webintel.validate_payload("whatif-1.json", timeline)
    dash = json.loads(json.dumps(good))
    dash["scenarios"][0]["summary"] += chr(0x2014)
    assert webintel.validate_payload("whatif-1.json", dash)
    for field, value in (("title", 7), ("question", None), ("engine_note", ["x"]), ("summary", {}), ("scenario_id", 1)):
        typed = json.loads(json.dumps(good))
        typed["scenarios"][0][field] = value
        assert any(field in p for p in webintel.validate_payload("whatif-1.json", typed)), field
    deltas = json.loads(json.dumps(good))
    deltas["scenarios"][0]["deltas"] = "none"
    assert webintel.validate_payload("whatif-1.json", deltas)
    for field, value in (("clubs", [{"club_id": 1}]), ("method", "one sentence"), ("schema", "other/1")):
        top = json.loads(json.dumps(good))
        top[field] = value
        assert any(field in p for p in webintel.validate_payload("whatif-1.json", top)), field


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
    bad["files"][-1]["seed"] = 1  # the milestones file has no seed
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


CLUB_ID = re.compile(r"^C[0-9]+$")


def _keys_written(value, found: set[str]) -> set[str]:
    """Every dict key in a written file, at any depth; club ids used as keys are data, not keys."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not CLUB_ID.match(k):
                found.add(k)
            _keys_written(v, found)
    elif isinstance(value, list):
        for v in value:
            _keys_written(v, found)
    return found


def test_readme_names_every_key_the_code_writes(site):
    # One direction, as the README says: every key written (sub-keys included) is named in it.
    _, intel = site
    text = README.read_text(encoding="ascii")
    assert not any(d in text for d in DASHES)
    words = set(re.findall(r"[a-z0-9_]+", text))
    written: set[str] = set()
    for name in ("manifest.json", "metrics-1.json", "milestones-season-1.json"):
        _keys_written(_load(intel, name), written)
    written |= set(webintel.PASS_COLUMNS) | {c["stat"] for c in webintel.CITABLE}
    missing = sorted(k for k in written if k not in words)
    assert not missing, f"webintel_README.txt does not mention: {missing}"
    assert len(written) > 120


def test_cli_writes_and_verifies(site, tmp_path, capsys):
    data, _ = site
    out = tmp_path / "intel"
    assert webintel.main(["--data", str(data), "--seeds", "1", "--out", str(out)]) == 0
    assert "intel files 3" in capsys.readouterr().out
    assert sorted(os.listdir(out)) == ["manifest.json", "metrics-1.json", "milestones-season-1.json", "whatif-1.json"]

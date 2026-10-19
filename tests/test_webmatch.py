"""agon.webmatch: the match analysis files the Match Centre draws, computed by agon.interpret.

The committed fixture tests/fixtures/match/1.json is the analysis of the full seed 1 match exactly
as the deploy publishes it; this file holds it byte for byte, and the console's unit tests read the
same bytes, so the browser is tested on real output and never computes a football number itself.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

import stamp_web
from agon import webmatch
from agon.interpret import INTERPRET_VERSION, model_sha256
from agon.webdata import write_web_data

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "match" / "1.json"
SHA = "89abcdef0123456789abcdef0123456789abcdef"
SHELL = '<!doctype html><html><body data-commit="__AGON_COMMIT__"><p>Agon</p></body></html>'


@pytest.fixture(scope="module")
def served(tmp_path_factory) -> Path:
    data = tmp_path_factory.mktemp("served") / "data"
    write_web_data(str(data), [1, 2])
    return data


@pytest.fixture(scope="module")
def built(served, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("built") / "match"
    webmatch.write_match_web(str(out), str(served))
    return out


def _copy(src: Path, tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(src, target)
    return target


def test_the_seed_1_analysis_is_the_committed_fixture_byte_for_byte(built):
    assert (built / "1.json").read_bytes() == FIXTURE.read_bytes()


def test_every_file_verifies_against_the_served_event_logs(built, served):
    assert webmatch.verify_match_dir(built, served) == 2
    manifest = json.loads((built / "manifest.json").read_text(encoding="ascii"))
    assert manifest["interpret"] == INTERPRET_VERSION and manifest["model_sha256"] == model_sha256()
    data = json.loads((served / "manifest.json").read_text(encoding="ascii"))
    assert [f["events_sha256"] for f in manifest["files"]] == [f["sha256"] for f in data["files"]]


def test_the_fixture_matches_the_published_match_and_its_rules():
    body = json.loads(FIXTURE.read_text(encoding="ascii"))
    assert webmatch.validate_match(body) == []
    assert body["score"] == {"home": 1, "away": 1}
    assert [g["score_after"] for g in body["goals"]] == [{"home": 0, "away": 1}, {"home": 1, "away": 1}]
    assert body["clubs"]["home"]["name"] == "Pellmoor Harriers" and body["clubs"]["away"]["name"] == "Zennmark Town"
    stats = {r["key"]: r for r in body["team_stats"]}
    assert stats["possession"]["home"] + stats["possession"]["away"] == 1000
    assert stats["shots"]["home"] == sum(1 for s in body["shots"] if s["side"] == "home")
    for side in ("home", "away"):
        assert stats["xg"][side] == max(s["cum_xg_ppm"] for s in body["shots"] if s["side"] == side)
        assert stats["xg"][side] == body["live"][-1][f"{side}_xg_ppm"]
    assert body["live"][-1]["home_possession_permille"] == stats["possession"]["home"]
    assert set(body["rules"]) == {"possession", "big_chance", "star", "speed", "distance"}
    assert all(p["distance_dm"] is None for p in body["players"])
    assert all(t["at_s"] <= body["axis"]["end_s"] for t in body["timeline"])


def test_the_star_player_follows_the_stated_rule():
    body = json.loads(FIXTURE.read_text(encoding="ascii"))
    involved = [p for p in body["players"] if p["goals"] + p["assists"]]
    best = sorted(involved, key=lambda p: (-(p["goals"] + p["assists"]), -p["xg_ppm"], -p["xt_added_ppm"], p["shirt"]))[0]
    assert body["star"] == {"player_id": best["player_id"], "side": best["side"], "name": best["name"]}


def test_big_chances_use_the_interpret_threshold():
    body = json.loads(FIXTURE.read_text(encoding="ascii"))
    assert all(s["big_chance"] == (s["xg_ppm"] >= 200_000) for s in body["shots"])
    shots = {s["event_id"]: s for s in body["shots"]}
    assert all(t["big_chance"] == shots[t["event_id"]]["big_chance"] for t in body["timeline"] if t["event_id"] in shots)


def test_territory_counts_every_on_ball_action_once(served):
    body = json.loads(FIXTURE.read_text(encoding="ascii"))
    events = [json.loads(line) for line in (served / "1.ndjson").read_text(encoding="utf-8").splitlines()]
    on_ball = sum(1 for e in events if e["type"] in ("pass", "carry", "shot"))
    assert sum(body["territory"]["home"]) + sum(body["territory"]["away"]) == on_ball


# ---------------------------------------------------------------- refusals


def test_a_tampered_match_file_is_refused(built, served, tmp_path):
    out = _copy(built, tmp_path, "match")
    path = out / "1.json"
    body = json.loads(path.read_text(encoding="ascii"))
    body["score"]["home"] = 4
    path.write_bytes(webmatch.file_bytes(body))
    with pytest.raises(webmatch.MatchWebError, match="does not hash"):
        webmatch.verify_match_dir(out, served)


def test_a_match_file_for_another_event_log_is_refused(built, served, tmp_path):
    out = _copy(built, tmp_path, "match")
    manifest = json.loads((out / "manifest.json").read_text(encoding="ascii"))
    path = out / "1.json"
    body = json.loads(path.read_text(encoding="ascii"))
    body["events_sha256"] = "0" * 64
    data = webmatch.file_bytes(body)
    path.write_bytes(data)
    manifest["files"][0]["sha256"] = webmatch.sha256_hex(data)
    (out / "manifest.json").write_bytes(webmatch.file_bytes(manifest))
    with pytest.raises(webmatch.MatchWebError, match="another event log"):
        webmatch.verify_match_dir(out, served)


def test_a_stray_file_is_refused(built, served, tmp_path):
    out = _copy(built, tmp_path, "match")
    (out / "9.json").write_bytes(b"{}\n")
    with pytest.raises(webmatch.MatchWebError, match="not listed"):
        webmatch.verify_match_dir(out, served)


def test_a_data_file_that_does_not_hash_to_its_manifest_is_refused(served, tmp_path):
    data = _copy(served, tmp_path, "data")
    path = data / "1.ndjson"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(webmatch.MatchWebError, match="does not hash"):
        webmatch.build_match_web(str(data))


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (lambda b: b.pop("shots"), "keys differ"),
        (lambda b: b["win_probability"][0].update(home_ppm=1), "sum to 1000000"),
        (lambda b: b["timeline"][0].update(kind="offside"), "kind is unknown"),
        (lambda b: b["team_stats"][0].update(unit="percent_float"), "unit is unknown"),
        (lambda b: b["territory"]["home"].pop(), "cols x rows"),
        (lambda b: b["shots"][0].update(xg_ppm=0.5), "must be nat"),
        (lambda b: b["goals"][0].update(side="neutral"), "must be side"),
    ],
)
def test_the_validator_refuses_a_broken_shape(mutate, problem):
    body = json.loads(FIXTURE.read_text(encoding="ascii"))
    broken = copy.deepcopy(body)
    mutate(broken)
    assert any(problem in p for p in webmatch.validate_match(broken)), webmatch.validate_match(broken)


# ---------------------------------------------------------------- the deploy stamp


def test_the_deploy_stamp_writes_and_verifies_the_match_files(tmp_path, capsys):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_bytes(SHELL.encode("utf-8"))
    data = web / "data"
    write_web_data(str(data), [1], max_minute=5)
    assert stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(data), "--seeds", "1"]) == 0
    out = capsys.readouterr().out
    assert "wrote and verified 1 match files under" in out
    assert webmatch.verify_match_dir(web / "match", data) == 1
    # the match folder sits beside data/, never inside it: data/ allows no unlisted file
    assert stamp_web.verify_data(data, [1]) == 2


def test_the_cli_writes_and_reports(served, tmp_path, capsys):
    assert webmatch.main(["--data", str(served), "--out", str(tmp_path / "match")]) == 0
    assert "wrote and verified 2 match files for seeds 1, 2" in capsys.readouterr().out
    assert webmatch.main(["--data", str(tmp_path / "missing"), "--out", str(tmp_path / "m2")]) == 1
    assert "webmatch: FAIL" in capsys.readouterr().out

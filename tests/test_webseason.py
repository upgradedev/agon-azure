"""The season web data contract: what the console reads, re-hashed and shape-checked.

The good case writes the real season 1 files with the real storyline index; every negative case
asserts the reason. Expected values marked "hand-derived" come from the season 1 results that
`python -m agon.synth.season --season 1 --print-hash` prints, not from the code under test.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

import pytest

import seasonkit
from agon import webseason
from agon.governance import recompute
from agon.referee.storyline_index import storyline_index
from agon.synth.bands import FROZEN_BANDS_SHA256, load_bands
from agon.synth.manifest import build_bundle
from agon.synth.schema import sha256_hex


@lru_cache(maxsize=None)
def index() -> dict:
    return storyline_index(seeds=(2001, 2002, 2003), per_category=1)


@lru_cache(maxsize=None)
def built() -> tuple[dict, dict]:
    return webseason.build_season_web(1, index(), season=seasonkit.season(1))


def body(name: str) -> dict:
    return json.loads(built()[1][name].decode("ascii"))


def write(tmp_path: Path) -> Path:
    out = tmp_path / "season"
    webseason.write_season_web(out, index(), season=seasonkit.season(1))
    return out


# ---------------------------------------------------------------- good case and content


def test_written_folder_verifies(tmp_path: Path):
    out = write(tmp_path)
    assert webseason.verify_season_dir(out) == 7
    manifest = json.loads((out / "manifest.json").read_text(encoding="ascii"))
    assert manifest["season_sha256"] == seasonkit.season(1).sha256()
    assert [f["name"] for f in manifest["files"]] == list(webseason.FILE_ORDER)
    for name in webseason.FILE_ORDER:
        raw = (out / name).read_bytes()
        assert raw.endswith(b"\n") and b"\r" not in raw
        assert all(b < 128 for b in raw), f"{name} must be ASCII"
        assert chr(0x2013).encode() not in raw and chr(0x2014).encode() not in raw


def test_fixtures_point_at_the_published_replay_by_content_hash():
    fixtures = body("fixtures.json")
    matches = [m for day in fixtures["matchdays"] for m in day["matches"]]
    assert len(matches) == 30 and len(fixtures["matchdays"]) == 10
    m0 = next(m for m in matches if m["match_index"] == 0)
    # The deploy publishes seed 1, match index 0 as data/1.ndjson; its manifest sha256 is this.
    assert m0["events_sha256"] == sha256_hex(build_bundle(1, 0).events_ndjson())
    assert fixtures["calendar"] == "fictional calendar: matchdays only, no dates"


def test_tables_and_form_carry_the_hand_derived_matchday_6_values():
    tables = body("tables.json")
    after6 = next(d for d in tables["matchdays"] if d["matchday"] == 6)["rows"]
    assert [(r["club_id"], r["points"]) for r in after6] == [
        ("C4", 13), ("C5", 9), ("C1", 8), ("C6", 8), ("C3", 7), ("C2", 5),
    ]  # hand-derived
    form = body("form.json")
    c4 = next(c for c in next(d for d in form["matchdays"] if d["matchday"] == 6)["clubs"] if c["club_id"] == "C4")
    assert c4["form_string"] == "DLWWW" and c4["streaks"]["win"] == 3  # hand-derived
    assert tables["tiebreak_rule"] == recompute.TIEBREAK_RULE


def test_players_totals_add_up_and_the_speed_is_labelled_a_proxy():
    players = body("players.json")
    rows = players["players"]
    assert len(rows) == 6 * 18
    season = seasonkit.season(1)
    goals = sum(m.meta["score"]["home"] + m.meta["score"]["away"] for m in season.matches)
    assert sum(r["goals"] for r in rows) == goals
    assert "not running speed" in players["speed"]["note"]
    assert players["leaders"]["goals"][0]["value"] == max(r["goals"] for r in rows)


def test_head_to_head_lists_every_pair_with_both_meetings():
    pairs = body("h2h.json")["pairs"]
    assert len(pairs) == 15
    assert all(p["played"] == 2 for p in pairs)


def test_datalab_shows_the_committed_held_out_bands_and_the_commands():
    lab = body("datalab.json")
    bands = lab["bands"]
    assert bands == webseason.load_bands_record()
    assert bands["command"] == "python -m agon.synth.bands --seeds 1001-1200"
    assert bands["bands_sha256"] == FROZEN_BANDS_SHA256
    assert lab["dataset_card"]["path"] == "DATASET_CARD.md"
    assert any(c["command"] == "python -m agon.synth.season --season 1 --print-hash" for c in lab["regenerate"])


def test_committed_bands_record_never_widens_a_band():
    """Every lo and hi in the record is the frozen band, formatted the same way."""
    record = webseason.load_bands_record()
    frozen = {b.id: b for b in load_bands()}
    for row in record["rows"]:
        band = frozen[row["id"]]
        assert row["lo"] == f"{float(band.lo):.4f}" and row["hi"] == f"{float(band.hi):.4f}", row["id"]
    assert record["passed"] == sum(1 for r in record["rows"] if r["status"] == "PASS")


def test_every_published_name_passes_the_rights_name_screen():
    from agon.governance.rights import screen_name_field

    names = [body("fixtures.json")["league"]]
    names += [c["name"] for c in body("fixtures.json")["clubs"]]
    names += [p["name"] for p in body("players.json")["players"]]
    for cat in body("storylines.json")["index"]["categories"]:
        for e in cat["entries"]:
            names += [e["league"], e["home"]["name"], e["away"]["name"]]
    refusals = [r for i, name in enumerate(names) for r in screen_name_field(name, f"name {i}")]
    assert refusals == []
    assert len(names) > 100


def test_build_is_deterministic():
    again = webseason.build_season_web(1, index(), season=seasonkit.season(1))
    assert again == built()


# ---------------------------------------------------------------- refusals


def test_tampered_file_fails(tmp_path: Path):
    out = write(tmp_path)
    raw = bytearray((out / "tables.json").read_bytes())
    raw[20] = ord("X") if raw[20] != ord("X") else ord("Y")
    (out / "tables.json").write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="season/tables.json: sha256"):
        webseason.verify_season_dir(out)


def test_stray_file_fails(tmp_path: Path):
    out = write(tmp_path)
    (out / "extra.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="not listed in the manifest: extra.json"):
        webseason.verify_season_dir(out)


def test_missing_file_fails(tmp_path: Path):
    out = write(tmp_path)
    (out / "h2h.json").unlink()
    with pytest.raises(ValueError, match="lists h2h.json, which does not exist"):
        webseason.verify_season_dir(out)


def test_missing_manifest_fails(tmp_path: Path):
    with pytest.raises(ValueError, match="manifest.json does not exist"):
        webseason.verify_season_dir(tmp_path)


def test_file_with_a_matching_hash_but_a_bad_shape_fails(tmp_path: Path):
    out = write(tmp_path)
    tables = json.loads((out / "tables.json").read_text(encoding="ascii"))
    tables["matchdays"][0]["rows"][0]["position"] = 2
    data = json.dumps(tables, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    (out / "tables.json").write_bytes(data)
    manifest = json.loads((out / "manifest.json").read_text(encoding="ascii"))
    for entry in manifest["files"]:
        if entry["name"] == "tables.json":
            entry["sha256"] = sha256_hex(data)
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="ascii")
    with pytest.raises(ValueError, match="positions must run 1..6 in order"):
        webseason.verify_season_dir(out)


def test_manifest_order_and_names_are_fixed():
    manifest = copy.deepcopy(built()[0])
    manifest["files"].reverse()
    assert any("in that order" in p for p in webseason.validate_manifest(manifest))


def test_stale_or_unfrozen_bands_record_is_refused():
    record = copy.deepcopy(webseason.load_bands_record())
    record["generator"] = "synth-v0.1"
    assert any("re-measure" in p for p in webseason.validate_bands_record(record))
    record = copy.deepcopy(webseason.load_bands_record())
    record["bands_sha256"] = "0" * 64
    assert any("not the frozen bands" in p for p in webseason.validate_bands_record(record))
    record = copy.deepcopy(webseason.load_bands_record())
    record["passed"] += 1
    assert any("the rows pass" in p for p in webseason.validate_bands_record(record))
    with pytest.raises(ValueError, match="bands record refused"):
        webseason.build_season_web(1, index(), bands=record, season=seasonkit.season(1))


def test_speed_without_its_proxy_label_is_refused():
    players = body("players.json")
    players["speed"]["note"] = "top speed"
    assert "players.speed.note must say the speed is a proxy and not running speed" in webseason.validate_payload("players.json", players)


def test_storyline_index_shape_is_enforced():
    bad = copy.deepcopy(index())
    bad["categories"][0]["entries"] = []
    bad["categories"][0]["reason"] = ""
    assert any("must give its reason" in p for p in webseason.validate_storyline_index(bad))
    bad = copy.deepcopy(index())
    bad["categories"][1]["entries"][0]["command"] = "curl http://example.invalid"
    assert any("storyline regenerate command" in p for p in webseason.validate_storyline_index(bad))
    with pytest.raises(ValueError, match="refusing invalid season files"):
        webseason.build_season_web(1, {"schema": "x"}, season=seasonkit.season(1))


def test_unknown_or_mislabelled_payloads_are_refused():
    assert webseason.validate_payload("other.json", {}) == ["other.json is not a season file"]
    assert webseason.validate_payload("form.json", {"kind": "tables", "season": 1}) == ["form.json.kind must be form"]
    assert webseason.validate_payload("form.json", {"kind": "form", "season": 0}) == ["form.json.season must be a positive integer"]

"""The fictional season: schedule shape, calendar, hashes, the matchday cut and the CLI."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

import seasonkit
from agon.synth import season as season_mod
from agon.synth.manifest import build_bundle
from agon.synth.schema import sha256_hex

# Printed on origin/main 50a8a06 by `python -m agon.synth.manifest --seeds 1 --print-hash`, before
# the season existed (tests/test_synth_additive.py pins the same value).
M1_SEED_1_SHA256 = "976215472f5879d54cf5545896331dbe807b39e9ef3604279309e3752aefcb66"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def s1():
    return seasonkit.season(1)


def test_double_round_robin_shape(s1):
    assert len(s1.matches) == 30
    assert s1.matchdays_total == 10
    clubs = {c.club_id for c in s1.league.clubs}
    assert len(clubs) == 6
    by_day: dict[int, list] = {}
    for m in s1.matches:
        by_day.setdefault(m.matchday, []).append(m)
    assert sorted(by_day) == list(range(1, 11))
    for day, games in by_day.items():
        playing = [m.meta["home"] for m in games] + [m.meta["away"] for m in games]
        assert sorted(playing) == sorted(clubs), f"matchday {day}: every club plays exactly once"
    pairs = Counter((m.meta["home"], m.meta["away"]) for m in s1.matches)
    assert len(pairs) == 30 and set(pairs.values()) == {1}
    assert all(h != a for h, a in pairs)
    assert {(h, a) for h in clubs for a in clubs if h != a} == set(pairs)


def test_return_leg_mirrors_the_first_leg(s1):
    for day in range(1, 6):
        leg1 = [(m.meta["home"], m.meta["away"]) for m in s1.matches if m.matchday == day]
        leg2 = [(m.meta["away"], m.meta["home"]) for m in s1.matches if m.matchday == day + 5]
        assert len(leg1) == 3 and leg1 == leg2, day
    record = s1.record()
    assert [d["leg"] for d in record["matchdays"]] == [1] * 5 + [2] * 5


def test_match_zero_is_the_published_m1_match_byte_for_byte(s1):
    m0 = s1.match(0)
    assert m0.meta["match_id"] == "s1-m0"
    assert m0.sha256 == M1_SEED_1_SHA256
    assert m0.events_ndjson() == build_bundle(1, 0).events_ndjson()


@pytest.mark.parametrize("index", [17, 29])
def test_every_season_match_is_the_m1_bundle_of_its_index(s1, index):
    assert s1.match(index).sha256 == build_bundle(1, index).sha256()


def test_record_is_dateless_and_commits_to_every_match(s1):
    record = s1.record()
    text = json.dumps(record)
    assert record["calendar"] == season_mod.CALENDAR_NOTE
    assert not re.search(r"\b(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}\b", text), "no calendar dates"
    months = ("January", "February", "March", "April", "June", "July", "August", "September", "October", "November", "December")
    assert not any(month in text for month in months)
    assert [m["sha256"] for m in record["matches"]] == [m.sha256 for m in s1.matches]
    for m in record["matches"]:
        assert len(m["lineups"]["home"]) == 11 and len(m["lineups"]["away"]) == 11
        assert all(pid.startswith(m["home"] + "P") for pid in m["lineups"]["home"])
        assert all(pid.startswith(m["away"] + "P") for pid in m["lineups"]["away"])
    assert s1.sha256() == sha256_hex(s1.record_bytes()[:-1])


def test_hash_lines_name_every_match_and_end_with_the_season_hash(s1):
    lines = season_mod.hash_lines(s1)
    assert lines[0] == "season 1 seed 1 generator synth-v0.2 package 0.1.0 matchdays 10 of 10 matches 30"
    assert len(lines) == 32
    assert lines[1].startswith("match 0 matchday 1 ") and lines[1].endswith(f"sha256 {M1_SEED_1_SHA256}")
    last = lines[-1].split(" ")
    assert last[:2] == ["season", "sha256"] and HEX64.match(last[2]) and last[2] == s1.sha256()


def test_the_matchday_cut_changes_no_match(s1):
    cut = season_mod.build_season(1, matchdays=1)
    assert [m.match_index for m in cut.matches] == [0, 1, 2]
    assert [m.sha256 for m in cut.matches] == [m.sha256 for m in s1.matches[:3]]
    assert cut.sha256() != s1.sha256()


def test_bad_arguments_are_refused():
    with pytest.raises(ValueError):
        season_mod.season_seed(0)
    with pytest.raises(ValueError):
        season_mod.build_season(1, matchdays=11)
    with pytest.raises(ValueError):
        season_mod.build_season(1, matchdays=0)


def test_cli_writes_binary_lf_hash_lines_and_the_record(tmp_path: Path):
    hash_out = tmp_path / "season.txt"
    out = tmp_path / "out"
    code = season_mod.main(["--season", "1", "--matchdays", "1", "--hash-out", str(hash_out), "--out", str(out)])
    assert code == 0
    raw = hash_out.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    cut = season_mod.build_season(1, matchdays=1)
    assert raw.decode("ascii").splitlines() == season_mod.hash_lines(cut)
    assert (out / "season.json").read_bytes() == cut.record_bytes()

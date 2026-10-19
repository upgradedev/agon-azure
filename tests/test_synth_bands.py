"""Realism bands (M2): frozen file, pinned threshold, exact observations and a CLI with teeth."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from fractions import Fraction

import pytest

from agon.synth import bands as B
from agon.synth.engine import simulate_match
from agon.synth.world import build_world

# Pinned literals. Changing the bands means changing bands.json, bands.lock AND this file.
FROZEN_BANDS_SHA256 = "0e3b59af44b836a230996e9b6951cd738f1ef410087e690d6cf8013418db8325"
M2_THRESHOLD_FIXTURE = 20

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_bands_file_matches_its_lock_and_the_pinned_hash() -> None:
    assert _sha(B.BANDS_FILE) == B.read_lock(B.LOCK_FILE) == B.FROZEN_BANDS_SHA256 == FROZEN_BANDS_SHA256


def test_threshold_is_pinned_in_code_and_fixture() -> None:
    assert B.M2_PASS_THRESHOLD == M2_THRESHOLD_FIXTURE
    assert B.verdict_exit_code(M2_THRESHOLD_FIXTURE - 1) == 1
    assert B.verdict_exit_code(M2_THRESHOLD_FIXTURE) == 0
    assert B.verdict_exit_code(22) == 0


def test_twenty_two_bands_with_a_header_that_disclaims_real_leagues() -> None:
    bands = B.load_bands()
    assert len(bands) == 22 == B.BAND_COUNT
    assert len({b.id for b in bands}) == 22
    assert sorted(b.id for b in bands if b.measurable_from == "tracking") == [
        "distance_per_outfield_player_km",
        "sprints_per_outfield_player",
    ]
    with open(B.BANDS_FILE, encoding="utf-8") as fh:
        header = json.load(fh)["header"]
    assert "not claims about, any real league" in header
    assert "never widened" in header


def test_edited_bands_without_a_new_lock_are_refused(tmp_path) -> None:
    copy = tmp_path / "bands.json"
    shutil.copy(B.BANDS_FILE, copy)
    shutil.copy(B.LOCK_FILE, tmp_path / "bands.lock")
    doc = json.loads(copy.read_text(encoding="utf-8"))
    doc["bands"][0]["hi"] = "9.99"  # a widened band
    copy.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(B.BandsError, match="does not match its lock"):
        B.load_bands(str(copy))
    assert B.main(["--seeds", "1201", "--bands", str(copy)]) == 2


def test_missing_lock_is_refused(tmp_path) -> None:
    copy = tmp_path / "bands.json"
    shutil.copy(B.BANDS_FILE, copy)
    with pytest.raises(B.BandsError, match="no lock file"):
        B.load_bands(str(copy))


def _relocked_copy(tmp_path, edit) -> str:
    """A bands file edited by `edit` and given a fresh, correct bands.lock of its own."""
    doc = json.loads(open(B.BANDS_FILE, encoding="utf-8").read())
    edit(doc)
    data = json.dumps(doc).encode("utf-8")
    (tmp_path / "bands.json").write_bytes(data)
    (tmp_path / "bands.lock").write_bytes(f"sha256 {hashlib.sha256(data).hexdigest()}  bands.json\n".encode("ascii"))
    return str(tmp_path / "bands.json")


def test_widened_and_relocked_bands_never_produce_an_m2_line(tmp_path, capsys) -> None:
    """The attack: widen a band and write a matching lock next to it. The CLI must refuse (exit 2)."""

    def widen(doc):
        for band in doc["bands"]:
            band["lo"], band["hi"] = "0", "900000"

    path = _relocked_copy(tmp_path, widen)
    assert B.load_bands(path)  # its own lock is consistent, so the lock check alone would accept it
    assert B.main(["--seeds", "1201", "--bands", path]) == 2
    captured = capsys.readouterr()
    assert "NOT THE FROZEN BANDS: not an M2 result" in captured.err
    assert "M2 passed" not in captured.out
    assert "PASS" not in captured.out


def test_report_lines_refuse_a_non_frozen_hash() -> None:
    with pytest.raises(B.BandsError, match="NOT THE FROZEN BANDS"):
        B.report_lines([], "1", B.BANDS_FILE, "0" * 64, 0)


def test_impossible_bands_fail_m2_through_load_and_evaluate(tmp_path) -> None:
    """Negative control on the scoring path: a band set nothing can meet scores 0 and exits 1.

    It runs through load_bands, observe and evaluate, not the CLI: the CLI refuses any non-frozen
    file with exit 2 before scoring, so a non-frozen file can never reach exit 0 there.
    """

    def impossible(doc):
        for band in doc["bands"]:
            band["lo"], band["hi"] = "900000", "900001"

    path = _relocked_copy(tmp_path, impossible)
    rows = B.evaluate(B.load_bands(path), B.observe(B.collect_stats([1201, 1202])))
    assert B.passed_count(rows) == 0
    assert B.verdict_exit_code(B.passed_count(rows)) == 1
    assert B.main(["--seeds", "1201-1202", "--bands", path]) == 2


def test_spec_command_resolves_bands_json_from_the_repo_root(monkeypatch, capsys) -> None:
    monkeypatch.chdir(REPO_ROOT)
    assert not os.path.exists(os.path.join(REPO_ROOT, "bands.json"))
    code = B.main(["--seeds", "1201-1202", "--bands", "bands.json"])
    out = capsys.readouterr().out
    assert code in (0, 1)
    assert "bands agon/synth/bands.json (packaged) sha256 " + FROZEN_BANDS_SHA256 in out
    rows = [line for line in out.splitlines() if line.endswith(("PASS", "FAIL", "NOT MEASURABLE"))]
    assert len(rows) == 22
    assert sum(1 for r in rows if r.endswith("NOT MEASURABLE")) == 2


def test_unknown_bands_path_is_refused() -> None:
    assert B.main(["--seeds", "1201", "--bands", "no/such/bands.json"]) == 2


def test_not_measurable_never_counts_as_a_pass() -> None:
    bands = B.load_bands()
    observed = {b.id: (b.lo if b.measurable_from == "events" else None) for b in bands}
    rows = B.evaluate(bands, observed)
    assert B.passed_count(rows) == 20
    assert [r.status for r in rows if r.band.measurable_from == "tracking"] == [B.NOT_MEASURABLE] * 2


def test_band_edges_are_inclusive_and_exact() -> None:
    bands = B.load_bands()
    band = next(b for b in bands if b.id == "pass_completion")
    just_out = band.hi + Fraction(1, 10**9)
    observed = {b.id: b.lo for b in bands}
    observed["pass_completion"] = just_out
    status = {r.band.id: r.status for r in B.evaluate(bands, observed)}
    assert status["pass_completion"] == B.FAIL
    assert status["goals_per_match"] == B.PASS


def _ev(seq, etype, team, *, outcome=None, qualifier=None, chain=1, period=1, minute=10):
    return {
        "seq": seq, "type": etype, "team": team, "outcome": outcome, "qualifier": qualifier,
        "possession_chain_id": chain, "period": period, "minute": minute,
    }


def test_match_stats_on_a_hand_built_log() -> None:
    meta = {"home": "C1", "away": "C2"}
    events = [
        _ev(0, "set_piece", "C2", outcome="taken", qualifier="corner", chain=4),
        _ev(1, "shot", "C2", outcome="goal", chain=4, period=1, minute=20),
        _ev(2, "pass", "C1", outcome="complete", chain=5),
        _ev(3, "pass", "C1", outcome="incomplete", chain=5),
        _ev(4, "pass", "C2", outcome="out", chain=6),
        _ev(5, "foul", "C2", outcome="committed", chain=7),
        _ev(6, "card", "C2", outcome="second_yellow", chain=7),
        _ev(7, "shot", "C1", outcome="goal", chain=8, period=2, minute=80),
        _ev(8, "shot", "C1", outcome="saved", chain=9, period=2, minute=81),
        _ev(9, "set_piece", "C1", outcome="taken", qualifier="penalty", chain=10, period=2, minute=88),
        _ev(10, "shot", "C1", outcome="goal", qualifier="penalty", chain=10, period=2, minute=88),
    ]
    s = B.match_stats(meta, events)
    assert (s["home_goals"], s["away_goals"], s["goals"], s["shots"], s["on_target"]) == (2, 1, 3, 4, 4)
    assert (s["home_passes"], s["away_passes"], s["complete_passes"]) == (2, 1, 1)
    assert (s["fouls"], s["yellows"], s["reds"], s["corners"], s["penalties"]) == (1, 0, 1, 1, 1)
    assert (s["set_piece_goals"], s["second_half_goals"], s["late_goals"]) == (2, 2, 2)
    assert s["first_goal_team"] == 2
    assert B._comeback(s) == 1  # home conceded first and won


def test_observations_at_a_seed_offset_unlike_the_cli_default() -> None:
    """Fixture starts at 3013, not the CLI default 1001: recount from raw events, independently."""
    seeds = (3013, 3014, 3015)
    goals = shots = passes = complete = home_goals = 0
    for seed in seeds:
        result = simulate_match(build_world(seed), 0)
        for ev in result.events:
            if ev.type == "shot":
                shots += 1
                if ev.outcome == "goal":
                    goals += 1
                    home_goals += ev.team == result.meta["home"]
            if ev.type == "pass":
                passes += 1
                complete += ev.outcome == "complete"
    obs = B.observe(B.collect_stats(seeds))
    assert obs["goals_per_match"] == Fraction(goals, 3)
    assert obs["shots_per_team"] == Fraction(shots, 6)
    assert obs["pass_completion"] == Fraction(complete, passes)
    if goals:
        assert obs["home_goal_share"] == Fraction(home_goals, goals)


def test_formatting_is_exact_half_up() -> None:
    assert B._fmt(Fraction(1, 3)) == "0.3333"
    assert B._fmt(Fraction(2, 3)) == "0.6667"
    assert B._fmt(Fraction(5, 1)) == "5.0000"
    assert B._fmt(None) == "n/a"

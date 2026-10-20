"""What-If Lab: the replay is the engine, the fork shares the past, the pairs share their randomness.

The interval arithmetic is checked against values worked out by hand (comments show the steps), and
the committed scenario set is checked against the served match and the web contract. The full set is
regenerated and compared across ubuntu and windows by the determinism workflow, not here.
"""

from __future__ import annotations

import json
import os

import pytest

from agon import webintel
from agon.synth.engine import simulate_match
from agon.synth.interventions import Intervention
from agon.synth.manifest import build_bundle
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.synth.world import build_world
from agon.webdata import ndjson_bytes
from agon.whatif import __main__ as cli
from agon.whatif.lab import LABEL, Lab, Scenario
from agon.whatif.replay import Change, full_replay, play_out, replay_to
from agon.whatif.scenarios import DEFAULT_K, SCENARIO_IDS, scenario_set, whatif_payload
from agon.whatif.stats import fixed, paired_delta, round_div, wilson_ppm

SET_DIR = os.path.join(os.path.dirname(cli.__file__), "scenario_set")


def ids(result) -> list[str]:
    return [e.event_id for e in result.events]


# --------------------------------------------------------------------------- the replay is the engine


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_resumable_replay_reproduces_the_published_match(seed):
    league = build_world(seed)
    plain, replay = simulate_match(league, 0), full_replay(league, 0)
    assert ids(plain) == ids(replay) and plain.meta == replay.meta


def test_resumable_replay_reproduces_another_fixture():
    league = build_world(1)
    assert ids(simulate_match(league, 17)) == ids(full_replay(league, 17))


@pytest.mark.parametrize(("iv", "until"), [
    (Intervention("red_card", "home", 1, 30), (1, 1800)),
    (Intervention("goalkeeper_heroics", "away", 2, 60, 500), (2, 3600)),
])
def test_fork_at_replicate_0_is_the_engines_own_intervention_path(iv, until):
    league = build_world(1)
    paused, over = replay_to(league, 0, until)
    assert not over
    assert ids(play_out(paused.fork(Change(), 0))) == ids(simulate_match(league, 0))
    assert ids(play_out(paused.fork(Change(interventions=(iv,)), 0))) == ids(simulate_match(league, 0, [iv]))


# --------------------------------------------------------------------------- the past is shared, the future is paired


def test_every_replicate_shares_the_published_past_and_differs_after():
    league = build_world(1)
    published = simulate_match(league, 0)
    paused, _ = replay_to(league, 0, (2, 3600))
    n = len(paused.events)
    runs = [play_out(paused.fork(Change(), r)) for r in (1, 2, 3)]
    for run in runs:
        assert ids(run)[:n] == ids(published)[:n]
    assert ids(runs[0])[n:] != ids(runs[1])[n:]  # replicate keying gives each replay its own future


def test_pairs_share_random_numbers_until_the_change_fires():
    # The collapse needs a home goal after 60:00. In a replay without one, the change never fires,
    # so with common random numbers the scenario replay is the baseline replay, event for event.
    league = build_world(1)
    paused, _ = replay_to(league, 0, (2, 3600))
    change = Change(collapse_side="home", collapse_magnitude=2000)
    fired = quiet = 0
    for r in range(1, 9):
        base = play_out(paused.fork(Change(), r))
        twin_sim = paused.fork(change, r)
        twin = play_out(twin_sim)
        if twin_sim.collapse_at is None:
            assert ids(twin) == ids(base)
            quiet += 1
        else:
            fired += 1
            goal = next(i for i, e in enumerate(twin.events) if i >= len(paused.events) and e.type == "shot"
                        and e.outcome == "goal" and e.team == twin.meta["home"])
            assert ids(twin)[:goal + 1] == ids(base)[:goal + 1]  # identical up to the trigger goal
    assert fired and quiet


def test_press_knob_moves_pressing_in_the_stated_direction():
    league = build_world(1)
    paused, _ = replay_to(league, 0, (2, 3600))
    n, home = len(paused.events), paused.home.club_id

    def presses(change: Change) -> int:
        total = 0
        for r in range(1, 13):
            run = play_out(paused.fork(change, r))
            total += sum(1 for e in run.events[n:] if e.type == "pressure" and e.team == home)
        return total

    base = presses(Change())
    assert presses(Change(press_side="home", press_shift=300)) > base > presses(Change(press_side="home", press_shift=-300))


def test_lab_counts_zero_differing_pre_scenario_events():
    lab = Lab(1, 3)
    sc = Scenario("t", "home", 1, 30, Change(interventions=(Intervention("red_card", "home", 1, 30),)))
    out = lab.run(sc)
    assert out["prefix"] == {"events_before_scenario": out["prefix"]["events_before_scenario"], "replays_checked": 6, "differing_events": 0}
    assert out["prefix"]["events_before_scenario"] > 0
    assert out["k"] == 3 and len(out["xg_timeline"]["marks"]) == len(out["xg_timeline"]["baseline"]["for"]) == 20
    # The baseline arm is cached per scenario minute and reused.
    assert lab.baseline((1, 1800)) is lab.baseline((1, 1800))


def test_a_minute_after_full_time_is_refused():
    with pytest.raises(ValueError):
        Scenario("t", "home", 1, 50)


# --------------------------------------------------------------------------- integer intervals, by hand


def test_wilson_interval_by_hand():
    # x = 8, n = 30, z = 1.96: centre = (8 + 1.9208) / 33.8416 = 0.293154;
    # half = 1.96 * sqrt(8 * 22 / 30 + 0.9604) / 33.8416 = 1.96 * 2.612876 / 33.8416 = 0.151330;
    # interval 0.141824 to 0.444484 (rounded outward to whole ppm).
    w = wilson_ppm(8, 30)
    assert w == {"count": 8, "ppm": 266667, "lo_ppm": 141824, "hi_ppm": 444484}
    assert wilson_ppm(0, 10)["lo_ppm"] == 0 and wilson_ppm(10, 10)["hi_ppm"] == 1_000_000
    with pytest.raises(ValueError):
        wilson_ppm(3, 2)


def test_paired_and_unpaired_half_widths_by_hand():
    # Points in 4 replays: baseline 1, 0, 3, 1 and scenario 0, 0, 1, 1, so d = -1, 0, -2, 0.
    # Mean -0.75. n sum(d^2) - (sum d)^2 = 4 * 5 - 9 = 11; s_d^2 = 11 / 12; half = 1.96 * sqrt(11 / 12) / 2
    # = 0.93827, rounded up to 0.939. Unpaired: spreads 4 * 11 - 25 = 19 and 4 * 2 - 4 = 4, so
    # half = 1.96 * sqrt(23 / 12) / 2 = 1.35677, rounded up to 1.357.
    d = paired_delta([1, 0, 3, 1], [0, 0, 1, 1], 1000)
    assert d == {"pairs": 4, "baseline_mean": 1250, "scenario_mean": 500, "delta_mean": -750,
                 "delta_lo": -1689, "delta_hi": 189, "unpaired_half": 1357}
    assert paired_delta([1], [2], 1000)["delta_mean"] is None
    with pytest.raises(ValueError):
        paired_delta([1, 2], [1], 1000)


def test_integer_formatting_helpers():
    assert round_div(5, 2) == 3 and round_div(-5, 2) == -3 and round_div(4, 3) == 1
    assert fixed(-415, 1000, 2) == "-0.42" and fixed(1234, 1000, 2) == "1.23" and fixed(5, 1000, 2) == "0.01"


# --------------------------------------------------------------------------- the payload and the CLI


def test_payload_is_canonical_deterministic_and_labelled():
    a = whatif_payload(1, 2, ("red_home_30",))
    assert canonical_bytes(a) == canonical_bytes(whatif_payload(1, 2, ("red_home_30",)))
    assert a["label"] == LABEL == "Synthetic engine, paired replays, not a prediction about real football."
    assert a["events_sha256"] == sha256_hex(ndjson_bytes(build_bundle(1).events))
    assert [s["scenario_id"] for s in a["scenarios"]] == ["red_home_30"]
    assert webintel.validate_payload("whatif-1.json", a) == []


def test_fixed_set_names_seven_scenarios_with_notes():
    scs = scenario_set("Home FC", "Away FC")
    assert [s.scenario_id for s in scs] == list(SCENARIO_IDS) and len(scs) == 7
    assert all(s.title and s.question.startswith("What if") and s.engine_note for s in scs)
    assert all("central" not in s.engine_note for s in scs)  # the engine cannot pick a position


def test_cli_reproduces_a_scenario_from_parameters(tmp_path, capsysbinary):
    assert cli.main(["--seed", "1", "--side", "home", "--minute", "60", "--press-shift", "300", "--k", "2"]) == 0
    captured = capsysbinary.readouterr()
    out = json.loads(captured.out)
    assert out["params"]["press_shift"] == 300 and out["k"] == 2
    assert b"wall time" in captured.err and b"/s" not in captured.err  # a total, never a rate
    with pytest.raises(SystemExit):
        cli.main(["--seed", "1", "--side", "home", "--minute", "60", "--k", "2"])


def test_cli_set_writes_files_and_hash_lines(tmp_path, capsysbinary):
    out = tmp_path / "set"
    assert cli.main(["--set", "--seeds", "1", "--k", "2", "--out", str(out), "--print-hash",
                     "--hash-out", str(tmp_path / "hash.txt")]) == 0
    lines = capsysbinary.readouterr().out.decode("ascii").splitlines()
    data = (out / "whatif-1.json").read_bytes()
    assert lines[0] == f"whatif file whatif-1.json k 2 sha256 {sha256_hex(data)}"
    assert lines[-1].startswith("whatif set sha256 ") and len(lines[-1].split()[-1]) == 64
    assert (tmp_path / "hash.txt").read_bytes() == ("\n".join(lines) + "\n").encode("ascii")


# --------------------------------------------------------------------------- the committed set


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_committed_scenario_set_matches_the_served_match_and_the_contract(seed):
    path = os.path.join(SET_DIR, f"whatif-{seed}.json")
    raw = open(path, "rb").read()
    body = json.loads(raw)
    assert raw == canonical_bytes(body) + b"\n"
    assert webintel.validate_payload(f"whatif-{seed}.json", body) == []
    assert body["k"] == DEFAULT_K and [s["scenario_id"] for s in body["scenarios"]] == list(SCENARIO_IDS)
    assert body["events_sha256"] == sha256_hex(ndjson_bytes(build_bundle(seed).events))
    assert all(s["prefix"]["differing_events"] == 0 for s in body["scenarios"])


def test_committed_hash_file_lists_the_committed_files():
    lines = open(os.path.join(SET_DIR, "hash.txt"), "rb").read().decode("ascii").splitlines()
    files = [(f"whatif-{s}.json", open(os.path.join(SET_DIR, f"whatif-{s}.json"), "rb").read()) for s in (1, 2, 3)]
    assert lines == cli.set_hash_lines(files, DEFAULT_K)

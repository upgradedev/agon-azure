"""Synthetic kinematics: caps (metric M9), planted errors, ball flights, shape knobs, anchors, tracks."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import kinkit
from agon.synth import engine
from agon.synth import kinematics as kin
from agon.synth.schema import canonical_bytes, to_fixed_frame
from agon.synth.world import build_world

RED_CARD_SEED = 9  # match 0 of seed 9 has a sending-off (C6P09)


# ---------------------------------------------------------------- M9 and planted errors


@pytest.mark.parametrize("seed", [1, RED_CARD_SEED])
def test_published_tracks_have_no_cap_violations(seed):
    assert kin.check(kinkit.record(seed)) == []


def test_cli_reports_zero_and_fails_on_a_planted_teleport(capsys):
    assert kin.main(["--seeds", "2"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"kinematics {kin.KINEMATICS_VERSION} generator ")
    assert " violations 0 " in out[1] and out[-1] == "M9 cap violations 0 (target 0)"
    assert kin.main(["--seeds", "2", "--mutate"]) == 1
    assert capsys.readouterr().out.splitlines()[-1].startswith("M9 cap violations ")


def test_a_teleport_breaks_the_speed_and_acceleration_caps():
    problems = kin.check(kin.mutate(kinkit.record()))
    assert any("speed" in p and "over the cap" in p for p in problems), problems
    assert any("acceleration" in p for p in problems), problems


def test_a_change_of_pace_within_the_speed_cap_still_breaks_the_acceleration_cap():
    rec = kinkit.record()
    t = rec["periods"][0]["players"][5]
    k = len(t["xy"]) // 4 * 2
    # A 6 m jump in one second, then the original path: under the speed cap, over the acceleration cap.
    t["xy"][k] = t["xy"][k - 2] + 60 if t["xy"][k - 2] < 900 else t["xy"][k - 2] - 60
    t["xy"][k + 1] = t["xy"][k - 1]
    problems = kin.check(rec)
    assert any("acceleration" in p for p in problems), problems


def test_positions_outside_the_box_are_refused():
    rec = kinkit.record()
    rec["periods"][1]["players"][0]["xy"][1] = -101
    assert any("outside the box" in p for p in kin.check(rec))


def test_ball_speeds_out_of_bounds_and_late_flights_are_refused():
    rec = kinkit.record()
    passes = [f for f in rec["ball_flights"] if f["type"] == "pass" and f["flight_ms"]]
    shots = [f for f in rec["ball_flights"] if f["type"] == "shot" and f["flight_ms"]]
    passes[0]["speed_kmh_x10"] = kin.PASS_BOUNDS_KMH_X10[1] + 1
    shots[0]["speed_kmh_x10"] = kin.SHOT_BOUNDS_KMH_X10[0] - 1
    passes[1]["flight_ms"] = passes[1]["gap_ms"] + 1
    problems = kin.check(rec)
    assert sum("ball speed" in p for p in problems) == 2
    assert any("longer than the gap" in p for p in problems)


# ---------------------------------------------------------------- determinism and additivity


def test_same_inputs_same_bytes_and_events_untouched():
    league = build_world(3)
    result = engine.simulate_match(league, 0)
    events = [e.to_dict() for e in result.events]
    before = canonical_bytes(events)
    a = kin.simulate_tracking(league, events, result.meta["home"], result.meta["away"], 3, 0)
    b = kin.simulate_tracking(league, events, result.meta["home"], result.meta["away"], 3, 0)
    assert canonical_bytes(a) == canonical_bytes(b)
    assert canonical_bytes(events) == before
    assert kin.record_sha256(a) == kin.record_sha256(kinkit.record(3))


def test_the_engine_never_draws_the_kinematics_stream():
    assert "KINEMATICS" not in inspect.getsource(engine)
    assert kin.StreamId.KINEMATICS not in {v for k, v in vars(kin.StreamId).items() if k != "KINEMATICS" and k.isupper()}


# ---------------------------------------------------------------- ball


def test_every_pass_and_shot_has_a_consistent_flight():
    rec = kinkit.record()
    evs = kinkit.events()
    assert len(rec["ball_flights"]) == sum(1 for e in evs if e["type"] in ("pass", "shot"))
    for f in rec["ball_flights"]:
        lo, hi = kin.PASS_BOUNDS_KMH_X10 if f["type"] == "pass" else kin.SHOT_BOUNDS_KMH_X10
        assert f["flight_ms"] <= f["gap_ms"]
        if f["flight_ms"]:
            assert f["speed_kmh_x10"] == f["dist_dm"] * 100 * 36 // f["flight_ms"]
            assert lo <= f["speed_kmh_x10"] <= hi


def _pass(seq: int, dist_dm: int, under_pressure: bool, qualifier=None, etype="pass", player="C1P10") -> dict:
    return {
        "event_id": f"e{seq}", "seq": seq, "type": etype, "outcome": "complete" if etype == "pass" else "saved",
        "qualifier": qualifier, "player": player, "period": 1, "minute": 10, "second": 0,
        "x_dm": 100, "y_dm": 300, "end_x_dm": 100 + dist_dm, "end_y_dm": 300, "under_pressure": under_pressure,
    }


def test_ball_speed_reads_distance_pressure_and_shot_type():
    def speed(e, finishing=60):
        return kin.drawn_speed_kmh_x10(e, e["end_x_dm"] - e["x_dm"], finishing, 1, 0)

    assert speed(_pass(7, 400, False)) - speed(_pass(7, 100, False)) == kin.PASS_PER_M_KMH_X10 * 30
    assert speed(_pass(7, 200, True)) - speed(_pass(7, 200, False)) == kin.PASS_PRESSURE_KMH_X10
    header = speed(_pass(8, 100, False, "header", "shot"))
    penalty = speed(_pass(8, 100, False, "penalty", "shot"))
    assert penalty - header == kin.SHOT_BASE_KMH_X10["penalty"] - kin.SHOT_BASE_KMH_X10["header"]
    assert speed(_pass(8, 100, False, "open_play", "shot"), 80) - speed(_pass(8, 100, False, "open_play", "shot"), 60) == 20 * kin.SHOT_PER_FINISHING_KMH_X10
    assert speed(_pass(8, 100, True, "open_play", "shot")) - speed(_pass(8, 100, False, "open_play", "shot")) == kin.SHOT_PRESSURE_KMH_X10
    league = build_world(1)
    nxt = {"period": 1, "minute": 10, "second": 5, "type": "carry"}
    f = kin.ball_flights(league, [_pass(7, 400, False), nxt], 1, 0)[0]
    assert abs(f["speed_kmh_x10"] - speed(_pass(7, 400, False))) <= 1  # the published average, rounded


def test_a_flight_never_outlasts_the_gap_to_the_next_event():
    league = build_world(1)
    slow = _pass(9, 800, False)  # 80 m at a drawn 30 to 110 km/h cannot land within 1 s
    nxt = {"period": 1, "minute": 10, "second": 1, "type": "carry"}
    f = kin.ball_flights(league, [slow, nxt], 1, 0)[0]
    assert f["gap_ms"] == 1000 and f["flight_ms"] == 1000
    assert f["speed_kmh_x10"] == 800 * 100 * 36 // 1000


# ---------------------------------------------------------------- shape knobs


def _mover(slot: int, offset=(0, 0)) -> kin._Mover:
    return kin._Mover("C1P05", "C1", slot, 9000, offset)


def test_the_block_pushes_up_and_widens_in_possession():
    ball = (60000, 34000)
    m = _mover(1)
    attack = kin._shape_mm(m, True, ball, True)
    defend = kin._shape_mm(m, True, ball, False)
    assert attack[0] - defend[0] == kin.POSSESSION_PUSH_MM + kin.DEFENCE_DROP_MM
    assert abs(attack[1] - 34000) > abs(defend[1] - 34000)


def test_the_block_follows_the_ball_and_the_away_club_mirrors_it():
    m = _mover(6)
    deep = kin._shape_mm(m, True, (30000, 34000), True)
    high = kin._shape_mm(m, True, (80000, 34000), True)
    assert high[0] - deep[0] == 50000 * kin.BLOCK_FOLLOW_X_PCT // 100
    left = kin._shape_mm(m, True, (52500, 10000), True)
    assert left[1] < 34000
    home_spot = kin._shape_target(m, True, (52500, 34000), True)
    away_spot = kin._shape_target(m, False, (52500, 34000), True)
    assert away_spot == (kin.X_MM * 10 - home_spot[0], kin.Y_MM * 10 - home_spot[1])


def test_the_keeper_stays_in_his_box_and_tracks_the_ball():
    k = _mover(0)
    near = kin._shape_mm(k, True, (5000, 34000), False)
    far = kin._shape_mm(k, True, (100000, 60000), False)
    assert near[0] < far[0] <= 16500
    assert far[1] > 34000


def test_personal_offsets_and_pace_move_players():
    assert kin._shape_mm(_mover(3, (2000, -1500)), True, (52500, 34000), True) != kin._shape_mm(_mover(3), True, (52500, 34000), True)
    assert kin.player_vmax(50) == kin.VMAX_BASE_MM_S + 50 * kin.VMAX_PER_PACE_MM_S
    assert kin.player_vmax(99) == kin.PLAYER_VMAX_MM_S
    assert kin.player_vmax(30) < kin.player_vmax(70)


# ---------------------------------------------------------------- anchors and tracks


def test_most_actors_are_exactly_at_their_event_and_every_miss_is_named():
    rec, evs = kinkit.record(), kinkit.events()
    misses = kin.anchor_misses(rec, evs)
    anchors = [e for e in evs if e["player"] and e["type"] not in ("sub", "possession_change")]
    # Regression floor on seed 1 (99 misses of 1647 when this was written): at least 92% exact.
    assert len(misses) * 100 <= len(anchors) * 8, (len(misses), len(anchors))
    ids = {e["event_id"] for e in evs}
    assert all(m["event_id"] in ids for m in misses)
    exact = [e for e in anchors if e["event_id"] not in {m["event_id"] for m in misses}]
    e = exact[len(exact) // 2]
    x, y, _, _ = to_fixed_frame(e, rec["home"])
    t = kinkit.track(rec, e["period"], e["player"])
    k = e["minute"] * 60 + e["second"] - t["start_s"]
    assert (t["xy"][2 * k], t["xy"][2 * k + 1]) == (x, y)


def test_misses_are_split_by_cause():
    misses = kin.anchor_misses(kinkit.record(), kinkit.events())
    summary = kin.miss_summary(misses)
    assert summary["misses"] == len(misses) == summary["under_1m"] + summary["beyond_cap"] + summary["model_limit"]
    assert all("need_mm_s" in m for m in misses)
    planted = [
        {"event_id": "a", "player": "p", "miss_dm": 50, "need_mm_s": kin.PLAYER_VMAX_MM_S + 1},
        {"event_id": "b", "player": "p", "miss_dm": 5, "need_mm_s": kin.PLAYER_VMAX_MM_S + 1},
        {"event_id": "c", "player": "p", "miss_dm": 50, "need_mm_s": 3000},
        {"event_id": "d", "player": "p", "miss_dm": 12, "need_mm_s": None},
    ]
    assert kin.miss_summary(planted) == {"misses": 4, "under_1m": 1, "beyond_cap": 1, "model_limit": 2, "max_miss_dm": 50}


def test_a_miss_next_to_an_unreachable_action_is_filed_beyond_the_cap():
    # Seed 1: a corner taker is pulled off the flag by a press 40 m away 3 s later (looking forward).
    misses = {m["event_id"]: m for m in kin.anchor_misses(kinkit.record(), kinkit.events())}
    corner = misses["b9d1b782ee765d99"]
    assert corner["miss_dm"] >= 10 and corner["need_next_mm_s"] > kin.PLAYER_VMAX_MM_S
    assert corner["need_mm_s"] is not None and corner["need_mm_s"] <= kin.PLAYER_VMAX_MM_S
    only_forward = [{"event_id": "x", "player": "p", "miss_dm": 40, "need_mm_s": 2000, "need_next_mm_s": kin.PLAYER_VMAX_MM_S + 1}]
    assert kin.miss_summary(only_forward)["beyond_cap"] == 1


def test_receiver_arrivals_are_checked_and_a_planted_miss_is_counted():
    rec, evs = kinkit.record(), kinkit.events()
    arrivals = kin.receiver_arrivals(rec, evs)
    assert len(arrivals) == sum(1 for e in evs if e["type"] == "pass" and e["outcome"] == "complete" and e["related_player"])
    before = kin.receiver_summary(arrivals)
    assert before["misses"] * 100 <= before["arrivals"] * 4  # regression floor: 14 of 731 when written
    hit = next(a for a in arrivals if a["miss_dm"] == 0)
    e = next(x for x in evs if x["event_id"] == hit["event_id"])
    t = kinkit.track(rec, e["period"], hit["player"])
    k = hit["arrive_s"] - t["start_s"]
    t["xy"][2 * k] += 30
    after = kin.receiver_summary(kin.receiver_arrivals(rec, evs))
    assert after["misses"] == before["misses"] + 1


def test_tracks_cover_each_period_at_one_hertz_with_subs_and_a_sending_off():
    rec, evs = kinkit.record(RED_CARD_SEED), kinkit.events(RED_CARD_SEED)
    for per in rec["periods"]:
        seconds = per["end_s"] - per["start_s"] + 1
        assert len(per["ball"]) == 2 * seconds
        for t in per["players"]:
            assert t["start_s"] + len(t["xy"]) // 2 - 1 <= per["end_s"]
    subs = [e for e in evs if e["type"] == "sub"]
    assert subs
    for e in subs:
        newcomer = kinkit.track(rec, e["period"], e["related_player"])
        assert newcomer["start_s"] == e["minute"] * 60 + e["second"]
    card = next(e for e in evs if e["type"] == "card" and e["outcome"] in ("red", "second_yellow"))
    gone = kinkit.track(rec, card["period"], card["player"])
    assert gone["start_s"] + len(gone["xy"]) // 2 - 1 == card["minute"] * 60 + card["second"]
    later = [per for per in rec["periods"] if per["period"] > card["period"]]
    assert all(card["player"] not in {t["player_id"] for t in per["players"]} for per in later)


def test_hash_out_is_binary_lf(tmp_path: Path):
    out = tmp_path / "k" / "hash.txt"
    assert kin.main(["--seeds", "3", "--hash-out", str(out)]) == 0
    raw = out.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    assert f"sha256 {kin.record_sha256(kinkit.record(3))}" in raw.decode("ascii")

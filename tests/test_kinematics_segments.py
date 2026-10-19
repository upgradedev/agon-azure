"""triggers_from_segments: the brief's thresholds over one stream per period, never across half time."""

from __future__ import annotations

from agon.interpret import kinematics


def run(speed_dm_s: int, seconds: int, t0_ms: int = 0, x0: int = 0) -> list[tuple[int, int, int]]:
    return [(t0_ms + i * 1000, x0 + i * speed_dm_s, 0) for i in range(seconds + 1)]


def test_one_segment_matches_triggers_from_samples():
    samples = run(30, 5) + [(6000 + i * 1000, 150 + i * 80, 0) for i in range(1, 5)]
    one = kinematics.triggers_from_samples("p", samples)
    seg = kinematics.triggers_from_segments("p", [(1, samples)])
    assert seg["distance_dm"] == one["distance_dm"] and seg["top_speed_kmh_x10"] == one["top_speed_kmh_x10"]
    assert [(s["start_ms"], s["end_ms"]) for s in seg["sprints"]] == [(s["start_ms"], s["end_ms"]) for s in one["sprints"]]
    assert [t["t_ms"] for t in seg["top_speed_triggers"]] == one["top_speed_triggers_ms"]
    assert all(s["segment"] == 1 for s in seg["sprints"])


def test_no_step_bridges_two_segments():
    first = run(20, 10)  # 7.2 km/h
    second = run(20, 10, t0_ms=2_700_000, x0=900)  # starts 70 m away; a bridged step would be a sprint
    r = kinematics.triggers_from_segments("p", [(1, first), (2, second)])
    assert r["distance_dm"] == 400
    assert r["sprints"] == [] and r["top_speed_triggers"] == []


def test_milestones_count_across_periods_and_sprints_close_with_their_period():
    # 1.2 km in period 1 at 6 m/s, then 1.2 km in period 2: the 2 km mark falls in period 2.
    first = run(60, 200)
    second = run(60, 200, t0_ms=2_700_000)
    r = kinematics.triggers_from_segments("p", [(1, first), (2, second)])
    assert [(m["km"], m["segment"]) for m in r["distance_milestones"]] == [(2, 2)]
    fast = run(80, 3)  # 28.8 km/h to the end of the stream
    r2 = kinematics.triggers_from_segments("p", [(1, fast), (2, run(10, 3, t0_ms=2_700_000))])
    assert r2["sprints"] == [{"segment": 1, "start_ms": 0, "end_ms": 3000}]

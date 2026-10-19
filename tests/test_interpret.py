"""interpret: frozen model, determinism, isolation, and each metric on hand-built and seeded events."""

from __future__ import annotations

import ast
import math
import os
import shutil
from decimal import Decimal

import pytest

from agon.interpret import INTERPRET_VERSION, interpret_match, kinematics, metrics, wp
from agon.interpret._fixed import normalised_entropy_permille, outcome_ppm, poisson_pmf, ratio_round, sigmoid_ppm
from agon.interpret.features import pass_difficulty, xg_ppm, xt_added_ppm
from agon.interpret.fit import fit_logistic, fit_model, model_bytes, terciles
from agon.interpret.frame import GRID_X, GRID_Y, MatchView, bin15, window5, zone_of
from agon.interpret.model import LOCK_FILE, MODEL_FILE, ModelError, file_sha256, load_model, load_model_file, read_lock
from agon.synth import engine
from agon.synth.manifest import build_bundle
from agon.synth.schema import PITCH_X_DM, PITCH_Y_DM, canonical, canonical_bytes, sha256_hex

# Pinned literals. A refit changes both; a generator bump must come with a refit
# (python -m agon.interpret.fit --seeds 1-500) and new values here.
MODEL_SHA256 = "7ab5e12ab86e08263a6e827412b94ab2ef7b53d0d25de29d2485da2c711b0ba7"
SEED1_INTERPRET_SHA256 = "4e14900d0148e717f7f58e4cf1ecfee309d923369cf03a219b91c7c14e915ac0"

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
INTERPRET_DIR = os.path.join(SRC, "agon", "interpret")


@pytest.fixture(scope="module")
def seed1() -> MatchView:
    b = build_bundle(1)
    return MatchView.from_bundle(b.match, b.events)


def ev(seq: int, etype: str, team: str, **kw) -> dict:
    """A minimal event dict with the fields interpret reads."""
    base = {
        "event_id": f"e{seq:015d}",
        "match_id": "t",
        "seq": seq,
        "period": 1,
        "minute": 10,
        "second": 0,
        "type": etype,
        "team": team,
        "player": "p1",
        "x_dm": 500,
        "y_dm": 340,
        "end_x_dm": None,
        "end_y_dm": None,
        "possession_chain_id": 1,
        "under_pressure": False,
        "outcome": None,
        "qualifier": None,
        "related_player": None,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------- model


def test_model_matches_lock_and_pinned_hash():
    assert file_sha256(MODEL_FILE) == read_lock(LOCK_FILE) == MODEL_SHA256
    model = load_model()
    assert model["schema"] == "agon.interpret.model/1"
    assert model["provenance"]["seeds"] == "1-500"
    assert model["provenance"]["command"] == "python -m agon.interpret.fit --seeds 1-500"


def test_model_was_fitted_on_the_current_generator():
    # A generator bump changes the training data; refit before this passes again.
    assert load_model()["provenance"]["generator"] == engine.GENERATOR_VERSION


def test_tampered_model_is_refused(tmp_path):
    bad = tmp_path / "model.json"
    lock = tmp_path / "model.lock"
    shutil.copy(MODEL_FILE, bad)
    shutil.copy(LOCK_FILE, lock)
    data = bad.read_bytes().replace(b'"irls_steps":', b'"irls_steps" :', 1)
    bad.write_bytes(data)
    with pytest.raises(ModelError, match="does not match the lock"):
        load_model_file(str(bad), str(lock))


def test_model_is_canonical_integer_json():
    model = load_model()
    with open(MODEL_FILE, "rb") as fh:
        assert fh.read() == model_bytes(model)


# --------------------------------------------------------------------------- isolation


def _agon_imports(path: str) -> set[str]:
    with open(path, "rb") as fh:
        tree = ast.parse(fh.read().decode("utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("agon"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agon"):
            found.add(node.module)
    return found


def test_inference_modules_read_only_the_event_contract():
    """Interpret must not see engine constants, interventions or the referee (M6 would be circular)."""
    allowed = {"agon.synth.schema", "agon.governance.recompute"}
    scanned = 0
    for name in sorted(os.listdir(INTERPRET_DIR)):
        if not name.endswith(".py") or name == "fit.py":
            continue
        scanned += 1
        for mod in _agon_imports(os.path.join(INTERPRET_DIR, name)):
            if mod == "agon.interpret" or mod.startswith("agon.interpret."):
                continue
            assert mod in allowed, f"{name} imports {mod}"
    assert scanned >= 7


def test_zone_grid_matches_the_generator():
    for x in range(0, PITCH_X_DM + 1, 7):
        for y in range(0, PITCH_Y_DM + 1, 11):
            assert zone_of(x, y) == engine.zone_of(x, y)
    assert (GRID_X, GRID_Y) == (engine.GRID_X, engine.GRID_Y)


# --------------------------------------------------------------------------- fixed point


def test_sigmoid_and_entropy_agree_with_math_within_one_quantum():
    for z in (-6_000_000, -1_234_567, 0, 250_000, 3_999_999):
        assert abs(sigmoid_ppm(z) - 1e6 / (1 + math.exp(-z / 1e6))) <= 1
    counts = [5, 3, 1, 1, 7]
    n = sum(counts)
    h = -sum(c / n * math.log(c / n) for c in counts) / math.log(n)
    assert abs(normalised_entropy_permille(counts) - 1000 * h) <= 1
    assert normalised_entropy_permille([9]) == 0
    assert normalised_entropy_permille([1, 1, 1, 1]) == 1000
    assert normalised_entropy_permille([]) == 0


def test_poisson_outcome_sums_and_lead():
    h, d, a = outcome_ppm(1_300_000_000, 1_000_000_000, 0)
    assert h + d + a == 1_000_000 and h > a
    h2, d2, a2 = outcome_ppm(0, 0, 1)
    assert (h2, d2, a2) == (1_000_000, 0, 0)
    pmf = poisson_pmf(2_000_000_000, 6)
    assert abs(float(pmf[2]) - math.exp(-2) * 2) < 1e-12
    assert isinstance(pmf[0], Decimal)
    assert ratio_round(1, 3, 1000) == 333 and ratio_round(2, 3, 1000) == 667 and ratio_round(1, 0, 10) is None


# --------------------------------------------------------------------------- time keys


def test_first_half_added_time_and_second_half_never_share_a_window():
    late_first = ev(1, "pass", "H", period=1, minute=46)
    early_second = ev(2, "pass", "H", period=2, minute=46)
    assert window5(late_first) != window5(early_second)
    assert bin15(late_first) == 2 and bin15(early_second) == 3
    assert bin15(ev(3, "pass", "H", period=2, minute=93)) == 5
    assert bin15(ev(4, "pass", "H", period=1, minute=0)) == 0


# --------------------------------------------------------------------------- xG, passes, xT


def test_xg_orders_shots_by_quality():
    close = ev(1, "shot", "H", x_dm=980, y_dm=340, outcome="saved", qualifier="open_play")
    far = ev(2, "shot", "H", x_dm=780, y_dm=340, outcome="saved", qualifier="open_play")
    wide = ev(3, "shot", "H", x_dm=980, y_dm=120, outcome="saved", qualifier="open_play")
    header = dict(close, qualifier="header")
    pen = ev(4, "shot", "H", x_dm=940, y_dm=340, outcome="goal", qualifier="penalty")
    pressed = dict(close, under_pressure=True)
    vals = {k: xg_ppm(e, "H") for k, e in [("close", close), ("far", far), ("wide", wide), ("header", header), ("pen", pen), ("pressed", pressed)]}
    assert vals["close"] > vals["far"]
    assert vals["close"] > vals["wide"]
    assert vals["header"] < vals["close"]
    assert vals["pressed"] < vals["close"]
    assert vals["pen"] > 600_000
    assert all(0 < v < 1_000_000 for v in vals.values())


def test_pass_difficulty_rises_with_length_and_pressure():
    short = ev(1, "pass", "H", x_dm=400, y_dm=340, end_x_dm=480, end_y_dm=340, outcome="complete")
    long = ev(2, "pass", "H", x_dm=400, y_dm=340, end_x_dm=850, end_y_dm=340, outcome="complete")
    pressed = dict(short, under_pressure=True)
    d_short, d_long, d_pressed = (pass_difficulty(p, "H") for p in (short, long, pressed))
    assert 0 <= d_short < d_long <= 100
    assert d_pressed > d_short


def test_xt_grid_rewards_moving_towards_goal():
    grid = load_model()["xt"]["grid"]
    assert len(grid) == GRID_X and all(len(col) == GRID_Y for col in grid)
    assert max(grid[15]) > max(grid[3])
    fwd = ev(1, "carry", "H", x_dm=700, y_dm=340, end_x_dm=950, end_y_dm=340, outcome="complete")
    back = ev(2, "pass", "H", x_dm=950, y_dm=340, end_x_dm=300, end_y_dm=340, outcome="complete")
    assert xt_added_ppm(fwd) > 0 > xt_added_ppm(back)
    assert xt_added_ppm(dict(back, outcome="incomplete")) is None
    assert xt_added_ppm(ev(3, "shot", "H", outcome="goal")) is None


# --------------------------------------------------------------------------- PPDA


def test_ppda_counts_opponent_build_up_passes_against_defensive_actions():
    events = [
        # Away build-up passes in their own 60% (x < 630 in the away frame): 4 count, 1 does not.
        *[ev(i, "pass", "A", x_dm=300, end_x_dm=350, end_y_dm=340, outcome="complete") for i in range(4)],
        ev(4, "pass", "A", x_dm=800, end_x_dm=850, end_y_dm=340, outcome="complete"),
        # Home defensive actions in the same area (x >= 420 in the home frame): 2 count, 1 does not.
        ev(5, "tackle", "H", x_dm=700, outcome="won"),
        ev(6, "possession_change", "H", x_dm=500, outcome="interception", player=None),
        ev(7, "foul", "H", x_dm=200, outcome="committed"),
    ]
    view = MatchView.build(events, "H", "A", "t")
    res = metrics.ppda(view)
    assert res["H"]["opponent_passes"] == 4 and res["H"]["defensive_actions"] == 2
    assert res["H"]["ppda_centi"] == 200
    assert res["A"]["ppda_centi"] is None
    assert [b["bin"] for b in res["H"]["by_15"]] == ["0-15", "15-30", "30-45+", "45-60", "60-75", "75-90+"]
    assert res["H"]["by_15"][0]["ppda_centi"] == 200


# --------------------------------------------------------------------------- rhythm


def test_control_vs_chaos_reads_one_long_chain_as_control():
    calm = [ev(i, "pass", "H", minute=2, possession_chain_id=1, end_x_dm=520, end_y_dm=340, outcome="complete") for i in range(20)]
    wild = []
    for i in range(20):
        wild.append(ev(100 + 2 * i, "pass", "H" if i % 2 else "A", minute=7, possession_chain_id=10 + i, end_x_dm=520, end_y_dm=340, outcome="incomplete"))
        wild.append(ev(101 + 2 * i, "possession_change", "A" if i % 2 else "H", minute=7, possession_chain_id=11 + i, outcome="interception", player=None))
    view = MatchView.build(calm + wild, "H", "A", "t")
    rows = metrics.control_vs_chaos(view)
    assert rows[0]["window"] == "p1 0-5" and rows[0]["chain_entropy_permille"] == 0 and rows[0]["band"] == "control"
    assert rows[1]["window"] == "p1 5-10" and rows[1]["chain_entropy_permille"] == 1000 and rows[1]["band"] == "chaos"


def test_pressure_regimes_merge_windows_and_list_changes():
    th = load_model()["bands"]["pressure"]
    high = th[1] + 1
    events = []
    seq = 0
    for w, n in ((0, 0), (1, high), (2, high), (3, 0)):
        for _ in range(max(n, 1)):
            etype = "pressure" if n else "pass"
            events.append(ev(seq, etype, "H", minute=w * 5 + 1, outcome="applied" if n else "complete", end_x_dm=600, end_y_dm=340))
            seq += 1
    res = metrics.pressure_regimes(MatchView.build(events, "H", "A", "t"))
    bands = [r["band"] for r in res["H"]["regimes"]]
    assert bands == ["low", "high", "low"]
    assert res["H"]["regimes"][1]["windows"] == ["p1 5-10", "p1 10-15"]
    assert [c["to"] for c in res["H"]["changes"]] == ["high", "low"]


def test_tempo_bands_use_frozen_terciles():
    th = load_model()["bands"]["tempo"]
    assert th[0] <= th[1]
    events = [ev(i, "pass", "H", minute=1, end_x_dm=520, end_y_dm=340, outcome="complete") for i in range(th[1] + 2)]
    assert metrics.tempo_bands(MatchView.build(events, "H", "A", "t"))[0]["band"] == "fast"
    assert terciles([1, 2, 3, 4, 5, 6]) == [3, 5]


# --------------------------------------------------------------------------- win probability


def test_win_probability_responds_to_score_time_and_numbers():
    kick = wp.win_probability(wp.GameState(1, 0, 0, 0))
    assert sum(kick.values()) == 1_000_000
    assert kick["home_ppm"] > kick["away_ppm"]  # home advantage learnt from training outcomes
    late_lead = wp.win_probability(wp.GameState(2, 88 * 60, 1, 0))
    assert late_lead["home_ppm"] > 850_000
    ten_men = wp.win_probability(wp.GameState(1, 1200, 0, 0, home_players=10))
    eleven = wp.win_probability(wp.GameState(1, 1200, 0, 0))
    assert ten_men["home_ppm"] < eleven["home_ppm"]


def test_swing_on_seeded_goals_and_saves(seed1):
    goals = [e for e in seed1.events if e["type"] == "shot" and e["outcome"] == "goal"]
    assert goals
    for g in goals:
        s = wp.swing(seed1, g["event_id"])
        assert s["delta_team_ppm"] > 0
        assert s["after"]["home_ppm"] + s["after"]["draw_ppm"] + s["after"]["away_ppm"] == 1_000_000
    saved = next(e for e in seed1.events if e["type"] == "shot" and e["outcome"] == "saved")
    s = wp.swing(seed1, saved["event_id"])
    assert s["delta_team_ppm"] <= 0 < s["expected_swing_ppm"]
    with pytest.raises(KeyError):
        wp.swing(seed1, "0" * 16)
    cands = wp.candidate_moments(seed1)
    assert {g["event_id"] for g in goals} <= {c["event_id"] for c in cands}
    weights = [c["weight_ppm"] for c in cands]
    assert weights == sorted(weights, reverse=True)


# --------------------------------------------------------------------------- speed and distance


def test_triggers_from_samples_find_sprints_top_speed_and_milestones():
    # 100 ms steps: 7 dm per step is 25.2 km/h (a sprint), 9 dm is 32.4 km/h (top speed).
    pts = [(0, 0, 0)]
    t, x = 0, 0
    for step in [3] * 10 + [7] * 5 + [9] * 3 + [3] * 10:
        t += 100
        x += step
        pts.append((t, x, 0))
    res = kinematics.triggers_from_samples("p1", pts)
    assert res["top_speed_kmh_x10"] == 324
    assert len(res["sprints"]) == 1 and res["sprints"][0] == {"start_ms": 1000, "end_ms": 1800}
    assert res["top_speed_triggers_ms"] == [1600, 1700, 1800]
    long_run = [(i * 1000, i * 50, 0) for i in range(900)]  # 5 m/s for 899 s: 4.495 km
    assert [m["km"] for m in kinematics.triggers_from_samples("p2", long_run)["distance_milestones"]] == [2, 4]


def test_events_only_proxy_says_what_it_is(seed1):
    res = kinematics.carry_proxy(seed1)
    assert res["source"] == "events_carry_proxy"
    assert res["distance_milestones"] is None
    assert "not derivable from events" in res["distance_milestones_reason"]
    assert "not running speed" in res["note"]
    assert res["players"] and all(p["carries"] > 0 for p in res["players"])


# --------------------------------------------------------------------------- whole match


def test_interpret_match_is_canonical_and_deterministic(seed1):
    a = interpret_match(seed1)
    b = interpret_match(seed1)
    assert canonical(a) == canonical(b)  # canonical() also refuses floats
    assert a["interpret"] == INTERPRET_VERSION and a["model_sha256"] == MODEL_SHA256
    assert a["win_probability"][-1]["minute"] == "FT"


def test_interpret_output_hash_is_pinned(seed1):
    """Same bytes on every OS: CI (ubuntu) recomputes the hash pinned here on Windows."""
    assert sha256_hex(canonical_bytes(interpret_match(seed1))) == SEED1_INTERPRET_SHA256


# --------------------------------------------------------------------------- fitter


def test_fitter_is_deterministic_on_a_small_seed_set():
    a = model_bytes(fit_model([1, 2, 3], "1-3"))
    b = model_bytes(fit_model([1, 2, 3], "1-3"))
    assert a == b  # model_bytes goes through canonical_bytes, which refuses any float


def test_logistic_fit_refuses_one_sided_data():
    with pytest.raises(ValueError):
        fit_logistic({(1,): [5, 0]}, 1)

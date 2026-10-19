"""Fit and freeze the interpret model on training seeds (default 1 to 500).

Usage:
    python -m agon.interpret.fit --seeds 1-500            # write model.json and model.lock
    python -m agon.interpret.fit --seeds 1-500 --check    # refit and compare with the committed bytes
    python -m agon.interpret.fit --seeds 1-5 --out-dir build/fit   # write elsewhere (tests)

What is fitted, all from the generated event logs and match meta, never from engine constants:
- xG: logistic regression (IRLS with a small ridge) on XG_FEATURES, label = goal.
- pass completion: logistic regression on PASS_FEATURES, label = completed.
- xT: shot, move and goal shares per zone of the 16 by 12 grid, transition counts of completed
  passes and carries, then value iteration to convergence.
- win probability: goals per second per 15-minute bin for home and away; a sending-off factor for
  the side with more and with fewer players (observed over expected goals in those spells); mean
  added time per half; training outcome base rates (used only as a baseline in M6).
- band thresholds: terciles of the training per-window values for control vs chaos, pressure and
  tempo.

Determinism: the fitter uses floats, but its transcendental calls (exp, ln) go through `decimal`
(correctly rounded) rather than libm, every float sum of more than two terms is `math.fsum`
(exactly rounded; the builtin `sum` over floats changed algorithm in Python 3.12), and single IEEE
+ - * / are the same on every platform in CPython. Coefficients are then rounded to integer
micro-units. The design goal is the same bytes on every OS and Python version; mcp.yml checks it on
ubuntu and windows with Python 3.12. The training-set identity is the combined sha256 of the generated bundles.
Wall time goes to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from math import fsum
from decimal import Decimal, localcontext
from typing import Any, Iterable, Sequence

from agon.interpret._fixed import _CTX, COEF_SCALE, PPM, RATE_SCALE
from agon.interpret.features import PASS_FEATURES, XG_FEATURES, pass_features, shot_features
from agon.interpret.frame import GRID_X, GRID_Y, HALF_SECONDS, MatchView, bin15, clock_seconds, zone_of
from agon.interpret.metrics import pressure_counts, window_raw
from agon.interpret.model import LOCK_FILE, MODEL_FILE, MODEL_SCHEMA
from agon.interpret.wp import FULL_SIDE, SENDING_OFF, remaining_seconds_by_bin
from agon.synth.engine import GENERATOR_VERSION
from agon.synth.manifest import build_bundle, combined_hash, parse_seeds
from agon.synth.schema import canonical_bytes, sha256_hex

DEFAULT_SEEDS = "1-500"
RIDGE = 0.1
MAX_IRLS = 100
XT_MAX_ITER = 500
TOL = 1e-10
MAX_HALVINGS = 40
PRIOR_GOALS = 5  # pseudo-goals for the sending-off factor


# --------------------------------------------------------------------------- logistic regression


def _exp(z: float) -> float:
    with localcontext(_CTX):
        return float(Decimal(z).exp())


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (ties keep the lower row index)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: (abs(m[r][col]), -r))
        m[col], m[piv] = m[piv], m[col]
        if m[col][col] == 0.0:
            raise ArithmeticError("singular system in the logistic fit")
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f != 0.0:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - fsum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def _softplus(z: float) -> float:
    """log(1 + e^z), computed through `decimal` so it is the same on every OS."""
    with localcontext(_CTX):
        d = Decimal(z)
        return float(max(d, Decimal(0)) + (Decimal(1) + (-abs(d)).exp()).ln())


def _objective(groups: dict[tuple[int, ...], list[int]], keys: list[tuple[int, ...]], beta: list[float]) -> float:
    """Penalised binomial log-likelihood: sum(y z - n log(1 + e^z)) - ridge / 2 x sum(slope^2)."""
    total = 0.0
    for k in keys:
        n, y = groups[k]
        z = fsum([beta[0], *(b * xi for b, xi in zip(beta[1:], k))])
        total += y * z - n * _softplus(z)
    return total - RIDGE / 2 * fsum(b * b for b in beta[1:])


def fit_logistic(groups: dict[tuple[int, ...], list[int]], dim: int) -> tuple[list[float], int]:
    """Binomial Newton-Raphson (IRLS) on aggregated rows {features: [trials, successes]}, ridge on slopes.

    Each Newton step is halved until the penalised log-likelihood does not fall, so a near-separated
    indicator (penalties score most of the time) cannot throw the iteration off. Returns
    (coefficients with the intercept first, steps used). Rows are visited in sorted key order so
    float sums are always taken in one fixed order.
    """
    keys = sorted(groups)
    beta = [0.0] * (dim + 1)
    total = sum(groups[k][0] for k in keys)
    succ = sum(groups[k][1] for k in keys)
    if total == 0 or succ in (0, total):
        raise ValueError("logistic fit needs both outcomes in the training rows")
    beta[0] = _log_ratio(succ, total - succ)
    current = _objective(groups, keys, beta)
    for it in range(1, MAX_IRLS + 1):
        h = [[0.0] * (dim + 1) for _ in range(dim + 1)]
        g = [0.0] * (dim + 1)
        for k in keys:
            n, y = groups[k]
            x = (1,) + k
            z = fsum(b * xi for b, xi in zip(beta, x))
            p = 1.0 / (1.0 + _exp(-z))
            w = n * p * (1.0 - p)
            r = y - n * p
            for i in range(dim + 1):
                xi = x[i]
                if xi == 0:
                    continue
                g[i] += r * xi
                wi = w * xi
                row = h[i]
                for j in range(dim + 1):
                    if x[j]:
                        row[j] += wi * x[j]
        for i in range(1, dim + 1):
            h[i][i] += RIDGE
            g[i] -= RIDGE * beta[i]
        step = _solve(h, g)
        t = 1.0
        for _ in range(MAX_HALVINGS):
            trial = [b + t * s for b, s in zip(beta, step)]
            value = _objective(groups, keys, trial)
            if value >= current:
                break
            t /= 2.0
        else:
            return beta, it
        moved = max(abs(t * s) for s in step)
        beta, current = trial, value
        if moved < TOL:
            return beta, it
    return beta, MAX_IRLS


def _log_ratio(a: int, b: int) -> float:
    with localcontext(_CTX):
        return float((Decimal(a) / Decimal(b)).ln())


def to_micro(beta: Sequence[float]) -> list[int]:
    return [int(round(b * COEF_SCALE)) for b in beta]


# --------------------------------------------------------------------------- xT


def fit_xt(views: Iterable[MatchView]) -> tuple[list[list[int]], int]:
    cells = GRID_X * GRID_Y
    shots = [0] * cells
    goals = [0] * cells
    moves = [0] * cells
    trans: list[dict[int, int]] = [dict() for _ in range(cells)]

    def idx(x: int, y: int) -> int:
        zx, zy = zone_of(x, y)
        return zx * GRID_Y + zy

    for v in views:
        for e in v.events:
            if e["type"] == "shot":
                z = idx(e["x_dm"], e["y_dm"])
                shots[z] += 1
                goals[z] += e["outcome"] == "goal"
            elif e["type"] in ("pass", "carry"):
                z = idx(e["x_dm"], e["y_dm"])
                moves[z] += 1
                if e["outcome"] == "complete":
                    t = idx(e["end_x_dm"], e["end_y_dm"])
                    trans[z][t] = trans[z].get(t, 0) + 1
    xt = [0.0] * cells
    used = XT_MAX_ITER
    for it in range(1, XT_MAX_ITER + 1):
        new = [0.0] * cells
        for z in range(cells):
            acts = shots[z] + moves[z]
            if acts == 0:
                continue
            val = goals[z] / acts  # s x g = (shots / acts) x (goals / shots)
            if moves[z]:
                val += fsum(c * xt[t] for t, c in sorted(trans[z].items())) / acts
            new[z] = val
        delta = max(abs(a - b) for a, b in zip(new, xt))
        xt = new
        if delta < TOL:
            used = it
            break
    grid = [[int(round(xt[zx * GRID_Y + zy] * PPM)) for zy in range(GRID_Y)] for zx in range(GRID_X)]
    return grid, used


# --------------------------------------------------------------------------- win probability


def _bin_lengths(added: tuple[int, int]) -> list[int]:
    return remaining_seconds_by_bin(1, 0, added)


def fit_wp(bundles: Sequence[tuple[dict[str, Any], MatchView]]) -> dict[str, Any]:
    exposure = [0] * 6
    goals = {"home": [0] * 6, "away": [0] * 6}
    added_sum = [0, 0]
    outcomes = {"home": 0, "draw": 0, "away": 0}
    for meta, v in bundles:
        added = (meta["added_seconds"]["first_half"], meta["added_seconds"]["second_half"])
        added_sum[0] += added[0]
        added_sum[1] += added[1]
        for b, s in enumerate(_bin_lengths(added)):
            exposure[b] += s
        for e in v.events:
            if e["type"] == "shot" and e["outcome"] == "goal":
                goals["home" if e["team"] == v.home_id else "away"][bin15(e)] += 1
        sh, sa = meta["score"]["home"], meta["score"]["away"]
        outcomes["home" if sh > sa else "away" if sa > sh else "draw"] += 1
    n = len(bundles)
    rate = {side: [(g * RATE_SCALE * 2 + exposure[b]) // (2 * exposure[b]) for b, g in enumerate(goals[side])] for side in ("home", "away")}

    # Sending-off factor: observed over expected goals during spells with unequal numbers.
    obs = {"up": 0, "down": 0}
    exp_nano = {"up": 0, "down": 0}
    for meta, v in bundles:
        added = (meta["added_seconds"]["first_half"], meta["added_seconds"]["second_half"])
        hp = ap = FULL_SIDE
        pos = (1, 0)
        spells: list[tuple[tuple[int, int], tuple[int, int], int, int]] = []
        for e in v.events:
            if e["type"] == "shot" and e["outcome"] == "goal" and hp != ap:
                scorer_up = (e["team"] == v.home_id) == (hp > ap)
                obs["up" if scorer_up else "down"] += 1
            elif e["type"] == "card" and e["outcome"] in SENDING_OFF:
                at = (e["period"], clock_seconds(e))
                spells.append((pos, at, hp, ap))
                pos = at
                if e["team"] == v.home_id:
                    hp -= 1
                else:
                    ap -= 1
        spells.append((pos, (2, 2 * HALF_SECONDS + added[1]), hp, ap))
        for start, end, h_n, a_n in spells:
            if h_n == a_n:
                continue
            r0 = remaining_seconds_by_bin(start[0], start[1], added)
            r1 = remaining_seconds_by_bin(end[0], end[1], added)
            secs = [max(0, a - b) for a, b in zip(r0, r1)]
            up_side, down_side = ("home", "away") if h_n > a_n else ("away", "home")
            exp_nano["up"] += sum(r * s for r, s in zip(rate[up_side], secs))
            exp_nano["down"] += sum(r * s for r, s in zip(rate[down_side], secs))
    # Shrunk towards 1000 by PRIOR_GOALS pseudo-goals on both sides of the ratio, because spells
    # with unequal numbers are rare and a raw ratio of 0 would make a side unable to score.
    prior = PRIOR_GOALS * RATE_SCALE
    factor = {k: ((obs[k] * RATE_SCALE + prior) * 1000 * 2 + exp_nano[k] + prior) // (2 * (exp_nano[k] + prior)) for k in ("up", "down")}
    return {
        "rate_nano": rate,
        "bin_exposure_seconds": exposure,
        "bin_goals": goals,
        "man_advantage_permille": factor,
        "man_advantage_evidence": {"observed_goals": obs, "expected_nano_goals": exp_nano, "prior_goals": PRIOR_GOALS},
        "expected_added_seconds": [(added_sum[0] * 2 + n) // (2 * n), (added_sum[1] * 2 + n) // (2 * n)],
        "outcomes_ppm": {k: (c * PPM * 2 + n) // (2 * n) for k, c in outcomes.items()},
        "matches": n,
    }


# --------------------------------------------------------------------------- bands


def terciles(values: Sequence[int]) -> list[int]:
    v = sorted(values)
    if not v:
        raise ValueError("no values for terciles")
    return [v[len(v) // 3], v[2 * len(v) // 3]]


def fit_bands(views: Sequence[MatchView]) -> dict[str, list[int]]:
    chaos: list[int] = []
    press: list[int] = []
    tempo: list[int] = []
    for v in views:
        groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for e in v.events:
            groups.setdefault((e["period"], e["minute"] // 5), []).append(e)
        for key in sorted(groups):
            raw = window_raw(groups[key])
            chaos.append(raw["index_permille"])
            tempo.append(raw["on_ball_actions"])
        for c in pressure_counts(v).values():
            press.extend(c[k] for k in sorted(c))
    return {"control_chaos": terciles(chaos), "pressure": terciles(press), "tempo": terciles(tempo)}


# --------------------------------------------------------------------------- driver


def fit_model(seeds: Sequence[int], seeds_text: str, log=lambda s: None) -> dict[str, Any]:
    bundles = []
    pairs = []
    for s in seeds:
        b = build_bundle(s)
        pairs.append((s, b.sha256()))
        bundles.append((b.match, MatchView.from_bundle(b.match, b.events)))
    log(f"generated {len(bundles)} matches")
    views = [v for _, v in bundles]

    xg_rows: dict[tuple[int, ...], list[int]] = {}
    pass_rows: dict[tuple[int, ...], list[int]] = {}
    for v in views:
        for e in v.events:
            if e["type"] == "shot":
                row = xg_rows.setdefault(tuple(shot_features(e, v.home_id)), [0, 0])
                row[0] += 1
                row[1] += e["outcome"] == "goal"
            elif e["type"] == "pass":
                row = pass_rows.setdefault(tuple(pass_features(e, v.home_id)), [0, 0])
                row[0] += 1
                row[1] += e["outcome"] == "complete"
    xg_beta, xg_it = fit_logistic(xg_rows, len(XG_FEATURES))
    log(f"xG fitted in {xg_it} IRLS steps over {len(xg_rows)} distinct rows")
    pass_beta, pass_it = fit_logistic(pass_rows, len(PASS_FEATURES))
    log(f"pass model fitted in {pass_it} IRLS steps over {len(pass_rows)} distinct rows")
    grid, xt_it = fit_xt(views)
    log(f"xT converged in {xt_it} iterations")
    wp = fit_wp(bundles)
    bands = fit_bands(views)
    return {
        "schema": MODEL_SCHEMA,
        "provenance": {
            "command": f"python -m agon.interpret.fit --seeds {seeds_text}",
            "seeds": seeds_text,
            "match_index": 0,
            "generator": GENERATOR_VERSION,
            "training_combined_sha256": combined_hash(pairs),
            "ridge_micro": int(round(RIDGE * COEF_SCALE)),
        },
        "xg": {
            "features": list(XG_FEATURES),
            "coef_micro": to_micro(xg_beta),
            "training_shots": sum(r[0] for r in xg_rows.values()),
            "training_goals": sum(r[1] for r in xg_rows.values()),
            "irls_steps": xg_it,
        },
        "pass": {
            "features": list(PASS_FEATURES),
            "coef_micro": to_micro(pass_beta),
            "training_passes": sum(r[0] for r in pass_rows.values()),
            "training_completed": sum(r[1] for r in pass_rows.values()),
            "irls_steps": pass_it,
        },
        "xt": {"grid": grid, "grid_shape": [GRID_X, GRID_Y], "iterations": xt_it},
        "wp": wp,
        "bands": bands,
    }


def model_bytes(model: dict[str, Any]) -> bytes:
    return canonical_bytes(model) + b"\n"


def _write(path: str, data: bytes) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.interpret.fit", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    parser.add_argument("--check", action="store_true", help="refit and compare with the committed model bytes")
    parser.add_argument("--out-dir", help="write model.json and model.lock here instead of the package")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    seeds = parse_seeds(args.seeds)
    model = fit_model(seeds, args.seeds, log=lambda s: sys.stderr.write(s + "\n"))
    data = model_bytes(model)
    digest = sha256_hex(data)
    sys.stderr.write(f"wall time {int((time.perf_counter() - started) * 1000)} ms for {len(seeds)} seed(s)\n")
    if args.check:
        with open(MODEL_FILE, "rb") as fh:
            committed = fh.read()
        if committed != data:
            print(f"interpret refit MISMATCH: refit sha256 {digest}, committed {sha256_hex(committed)}")
            return 1
        print(f"interpret refit OK: sha256 {digest} seeds {args.seeds} generator {GENERATOR_VERSION}")
        return 0
    model_path = os.path.join(args.out_dir, "model.json") if args.out_dir else MODEL_FILE
    lock_path = os.path.join(args.out_dir, "model.lock") if args.out_dir else LOCK_FILE
    _write(model_path, data)
    _write(lock_path, (digest + "\n").encode("ascii"))
    print(f"interpret model sha256 {digest} seeds {args.seeds} generator {GENERATOR_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

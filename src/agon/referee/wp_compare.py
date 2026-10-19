"""M6: interpret win probability against what actually happened, on held-out seeds.

Usage:
    python -m agon.referee.wp_compare --seeds 3001-3020

What it compares, stated plainly: synth-v0.2 emits no true win probabilities (no oracle, no
snapshot or rollout API), so interpret's win probability is scored against the FINAL RESULT of
each held-out match, not against a generator probability. At every 5th minute of regular time
(kick-off, 5', ..., 85' in each half's clock) the state is rebuilt from the events before that
moment and the three-way probability (home, draw, away) is compared with the one-hot final result.

Printed: the three-way Brier score (sum over the three outcomes of (p - o)^2, mean over points) and
the mean absolute difference (mean over points and the three outcomes of |p - o|), for interpret
and for a constant baseline (the training outcome shares stored in the model). Points inside one
match are correlated, so no interval is printed. Self-measured; 20 matches is a small sample.

Lives in the referee package because it reads generated outcomes for evaluation; it imports
interpret, never the other way round.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Sequence

from agon.interpret.frame import HALF_SECONDS, MatchView, clock_seconds
from agon.interpret.model import load_model, model_sha256
from agon.interpret.wp import GameState, apply_event, win_probability
from agon.synth.engine import GENERATOR_VERSION
from agon.synth.manifest import build_bundle, parse_seeds

DEFAULT_SEEDS = "3001-3020"
STEP_SECONDS = 300
PPM = 1_000_000


def checkpoints() -> list[tuple[int, int]]:
    """(period, clock) every 5 minutes of regular time: 0..2400 in period 1, 2700..5100 in period 2."""
    first = [(1, c) for c in range(0, HALF_SECONDS, STEP_SECONDS)]
    second = [(2, HALF_SECONDS + c) for c in range(0, HALF_SECONDS, STEP_SECONDS)]
    return first + second


def states_at_checkpoints(view: MatchView) -> list[GameState]:
    out = []
    for period, clock in checkpoints():
        s = GameState(1, 0, 0, 0)
        for e in view.events:
            if (e["period"], clock_seconds(e)) >= (period, clock):
                break
            s = apply_event(s, e, view.home_id)
        out.append(GameState(period, clock, s.home_goals, s.away_goals, s.home_players, s.away_players))
    return out


def outcome_vector(score: dict[str, int]) -> tuple[int, int, int]:
    h, a = score["home"], score["away"]
    return (PPM, 0, 0) if h > a else (0, 0, PPM) if a > h else (0, PPM, 0)


def score_points(preds: Sequence[tuple[int, int, int]], outcomes: Sequence[tuple[int, int, int]]) -> dict[str, int]:
    """Integer sums: squared error in ppm^2 and absolute error in ppm, over points and outcomes."""
    sq = ab = 0
    for p, o in zip(preds, outcomes):
        for pk, ok in zip(p, o):
            sq += (pk - ok) ** 2
            ab += abs(pk - ok)
    return {"points": len(preds), "sq_ppm2": sq, "abs_ppm": ab}


def fixed4(num: int, den: int) -> str:
    """num / den printed with four decimals, rounded half up, integers only."""
    q = (2 * num * 10_000 + den) // (2 * den)
    return f"{q // 10_000}.{q % 10_000:04d}"


def compare(seeds: Sequence[int]) -> dict[str, Any]:
    model = load_model()
    base = model["wp"]["outcomes_ppm"]
    base_vec = (base["home"], base["draw"], base["away"])
    preds: list[tuple[int, int, int]] = []
    basel: list[tuple[int, int, int]] = []
    outs: list[tuple[int, int, int]] = []
    results = []
    for seed in seeds:
        b = build_bundle(seed)
        view = MatchView.from_bundle(b.match, b.events)
        o = outcome_vector(b.match["score"])
        results.append(o)
        for st in states_at_checkpoints(view):
            w = win_probability(st, model)
            preds.append((w["home_ppm"], w["draw_ppm"], w["away_ppm"]))
            basel.append(base_vec)
            outs.append(o)
    return {
        "matches": len(seeds),
        "interpret": score_points(preds, outs),
        "baseline": score_points(basel, outs),
        "results": {"home": sum(1 for r in results if r[0]), "draw": sum(1 for r in results if r[1]), "away": sum(1 for r in results if r[2])},
    }


def report_lines(seeds_text: str, res: dict[str, Any]) -> list[str]:
    def line(name: str, s: dict[str, int]) -> str:
        brier = fixed4(s["sq_ppm2"], s["points"] * PPM * PPM)
        mad = fixed4(s["abs_ppm"], s["points"] * 3 * PPM)
        return f"{name}: brier {brier} mean_abs_diff {mad}"

    r = res["results"]
    return [
        "M6 wp_compare: interpret win probability vs FINAL RESULTS (synth-v0.2 emits no true probabilities; no oracle comparison)",
        f"command: python -m agon.referee.wp_compare --seeds {seeds_text}",
        f"generator {GENERATOR_VERSION} model sha256 {model_sha256()} matches {res['matches']} points {res['interpret']['points']} (every 5 regular-time minutes)",
        f"held-out results: home {r['home']} draw {r['draw']} away {r['away']}",
        line("interpret", res["interpret"]),
        line("baseline (training outcome shares, constant)", res["baseline"]),
        "self-measured; points within a match are correlated, so no interval is printed",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.referee.wp_compare", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    args = parser.parse_args(argv)
    seeds = parse_seeds(args.seeds)
    res = compare(seeds)
    sys.stdout.write("\n".join(report_lines(args.seeds, res)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Realism bands (metric M2): check generated matches against 22 frozen sanity ranges.

Usage (from the repository root):
    python -m agon.synth.bands --seeds 1001-1200 --bands bands.json

The bands in `bands.json` are ranges we chose as sanity checks for a synthetic generator. They are
not measurements of, and not claims about, any real league. They were frozen and hashed into
`bands.lock` before any engine constant was calibrated, and the CLI refuses to run when the file
no longer matches its lock. A band is never widened to make the generator pass: change the engine,
or report the miss.

Two bands (sprints and distance per outfield player) need tracking data, which the generator does
not produce yet. They are reported as NOT MEASURABLE and counted as not passed. They are never
skipped and never counted as a pass.

Exit codes: 0 when at least `M2_PASS_THRESHOLD` bands pass; 1 below it; 2 when the bands file is
missing, malformed or does not match its lock.

Every comparison uses exact fractions, so the verdict is the same on every OS. Wall time goes to
stderr so stdout stays byte-comparable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable, Sequence

from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.manifest import parse_seeds
from agon.synth.world import build_world

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BANDS_FILE = os.path.join(PACKAGE_DIR, "bands.json")
LOCK_FILE = os.path.join(PACKAGE_DIR, "bands.lock")
BAND_COUNT = 22
# M2 target from the build spec (section 5.3): at least 20 of 22. Pinned here AND as a literal in
# tests/test_synth_bands.py; change both or neither.
M2_PASS_THRESHOLD = 20
DEFAULT_SEEDS = "1001-1200"
DEFAULT_MATCH_INDEX = 0

PASS = "PASS"
FAIL = "FAIL"
NOT_MEASURABLE = "NOT MEASURABLE"

CORNER_FREE_KICK_PENALTY = frozenset({"corner", "free_kick", "penalty"})
LATE_MINUTE = 75


class BandsError(ValueError):
    """The bands file is missing, malformed or does not match its lock."""


@dataclass(frozen=True)
class Band:
    id: str
    statistic: str
    unit: str
    lo: Fraction
    hi: Fraction
    measurable_from: str


@dataclass(frozen=True)
class Row:
    band: Band
    observed: Fraction | None
    status: str


def file_sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_lock(lock_path: str) -> str:
    """Return the sha256 recorded in a lock file of the form 'sha256 <hex>  bands.json'."""
    with open(lock_path, "rb") as fh:
        text = fh.read().decode("ascii")
    parts = text.split()
    if len(parts) != 3 or parts[0] != "sha256" or len(parts[1]) != 64:
        raise BandsError(f"malformed lock file {lock_path}")
    return parts[1]


def resolve_bands_path(path: str | None) -> str:
    """The spec command passes `--bands bands.json` from the repo root.

    A path that exists is used as given. A bare `bands.json` that does not exist in the working
    directory resolves to the packaged file. The resolved path is always printed, so this is
    never a silent substitution.
    """
    if path is None:
        return BANDS_FILE
    if os.path.exists(path):
        return os.path.abspath(path)
    if os.path.basename(path) == path == os.path.basename(BANDS_FILE):
        return BANDS_FILE
    raise BandsError(f"bands file not found: {path}")


def load_bands(path: str = BANDS_FILE, lock_path: str | None = None) -> list[Band]:
    """Load and validate the bands, refusing a file whose sha256 differs from its lock."""
    if lock_path is None:
        lock_path = os.path.join(os.path.dirname(path), "bands.lock")
    if not os.path.exists(lock_path):
        raise BandsError(f"no lock file next to {path}; bands must be frozen before use")
    expected = read_lock(lock_path)
    actual = file_sha256(path)
    if actual != expected:
        raise BandsError(f"bands file sha256 {actual} does not match its lock {expected}")
    with open(path, "rb") as fh:
        doc = json.loads(fh.read().decode("utf-8"))
    raw = doc.get("bands")
    if not isinstance(raw, list) or len(raw) != BAND_COUNT:
        raise BandsError(f"expected {BAND_COUNT} bands")
    bands: list[Band] = []
    seen: set[str] = set()
    for item in raw:
        lo, hi = Fraction(item["lo"]), Fraction(item["hi"])
        if lo > hi:
            raise BandsError(f"band {item['id']} has lo > hi")
        if item["id"] in seen:
            raise BandsError(f"duplicate band {item['id']}")
        if item["measurable_from"] not in ("events", "tracking"):
            raise BandsError(f"band {item['id']} has unknown source {item['measurable_from']}")
        seen.add(item["id"])
        bands.append(Band(item["id"], item["statistic"], item["unit"], lo, hi, item["measurable_from"]))
    return bands


# ----------------------------------------------------------------- per-match counts


def match_stats(meta: dict[str, Any], events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Integer counts for one match, read from the event log only (no engine internals)."""
    home, away = meta["home"], meta["away"]
    s = {
        "home_goals": 0, "away_goals": 0, "shots": 0, "on_target": 0, "goals": 0,
        "home_passes": 0, "away_passes": 0, "complete_passes": 0, "fouls": 0, "yellows": 0,
        "reds": 0, "corners": 0, "penalties": 0, "set_piece_goals": 0, "second_half_goals": 0,
        "late_goals": 0, "first_goal_team": 0,
    }
    chain_opening: dict[int, str] = {}
    first_scorer: str | None = None
    for ev in events:
        etype, outcome, team = ev["type"], ev["outcome"], ev["team"]
        if etype == "set_piece":
            chain_opening.setdefault(ev["possession_chain_id"], ev["qualifier"])
            if ev["qualifier"] == "corner":
                s["corners"] += 1
            elif ev["qualifier"] == "penalty":
                s["penalties"] += 1
        elif etype == "pass":
            s["home_passes" if team == home else "away_passes"] += 1
            if outcome == "complete":
                s["complete_passes"] += 1
        elif etype == "foul":
            s["fouls"] += 1
        elif etype == "card":
            if outcome == "yellow":
                s["yellows"] += 1
            else:
                s["reds"] += 1
        elif etype == "shot":
            s["shots"] += 1
            if outcome in ("goal", "saved"):
                s["on_target"] += 1
            if outcome == "goal":
                s["goals"] += 1
                s["home_goals" if team == home else "away_goals"] += 1
                if first_scorer is None:
                    first_scorer = team
                if chain_opening.get(ev["possession_chain_id"]) in CORNER_FREE_KICK_PENALTY:
                    s["set_piece_goals"] += 1
                if ev["period"] == 2:
                    s["second_half_goals"] += 1
                if ev["minute"] >= LATE_MINUTE:
                    s["late_goals"] += 1
    # 1 = home scored first, 2 = away scored first, 0 = no goal.
    s["first_goal_team"] = 0 if first_scorer is None else (1 if first_scorer == home else 2)
    if first_scorer is not None and first_scorer not in (home, away):
        raise ValueError(f"goal by unknown team {first_scorer}")
    return s


def _comeback(s: dict[str, int]) -> int:
    """1 when the club that conceded first did not lose."""
    h, a = s["home_goals"], s["away_goals"]
    if s["first_goal_team"] == 1:
        return 1 if a >= h else 0
    if s["first_goal_team"] == 2:
        return 1 if h >= a else 0
    return 0


def observe(stats: Sequence[dict[str, int]]) -> dict[str, Fraction | None]:
    """Observed value of every band id. Tracking bands are None (not measurable)."""
    n = len(stats)
    if n == 0:
        raise ValueError("no matches to observe")

    def total(key: str) -> int:
        return sum(s[key] for s in stats)

    def ratio(num: int, den: int) -> Fraction | None:
        return Fraction(num, den) if den else None

    goals = total("goals")
    shots = total("shots")
    passes = total("home_passes") + total("away_passes")
    with_goal = sum(1 for s in stats if s["goals"] > 0)
    dominant = sum(
        (Fraction(max(s["home_passes"], s["away_passes"]), s["home_passes"] + s["away_passes"])
         for s in stats if s["home_passes"] + s["away_passes"] > 0),
        Fraction(0),
    )
    return {
        "goals_per_match": Fraction(goals, n),
        "home_win_rate": Fraction(sum(1 for s in stats if s["home_goals"] > s["away_goals"]), n),
        "draw_rate": Fraction(sum(1 for s in stats if s["home_goals"] == s["away_goals"]), n),
        "zero_zero_rate": Fraction(sum(1 for s in stats if s["goals"] == 0), n),
        "home_goal_share": ratio(total("home_goals"), goals),
        "comeback_rate": ratio(sum(_comeback(s) for s in stats), with_goal),
        "shots_per_team": Fraction(shots, 2 * n),
        "shots_on_target_ratio": ratio(total("on_target"), shots),
        "goals_per_shot": ratio(goals, shots),
        "passes_per_team": Fraction(passes, 2 * n),
        "pass_completion": ratio(total("complete_passes"), passes),
        "possession_dominant_share": dominant / n,
        "fouls_per_match": Fraction(total("fouls"), n),
        "yellow_cards_per_match": Fraction(total("yellows"), n),
        "red_cards_per_match": Fraction(total("reds"), n),
        "corners_per_match": Fraction(total("corners"), n),
        "penalties_per_match": Fraction(total("penalties"), n),
        "set_piece_goal_share": ratio(total("set_piece_goals"), goals),
        "second_half_goal_share": ratio(total("second_half_goals"), goals),
        "late_goal_share": ratio(total("late_goals"), goals),
        # The generator produces no tracking data yet; these stay None and count as not passed.
        "sprints_per_outfield_player": None,
        "distance_per_outfield_player_km": None,
    }


def evaluate(bands: Sequence[Band], observed: dict[str, Fraction | None]) -> list[Row]:
    rows: list[Row] = []
    for band in bands:
        if band.id not in observed:
            raise BandsError(f"no observation for band {band.id}")
        value = observed[band.id]
        if value is None:
            rows.append(Row(band, None, NOT_MEASURABLE))
        elif band.lo <= value <= band.hi:
            rows.append(Row(band, value, PASS))
        else:
            rows.append(Row(band, value, FAIL))
    return rows


def passed_count(rows: Sequence[Row]) -> int:
    return sum(1 for r in rows if r.status == PASS)


def verdict_exit_code(passed: int, threshold: int = M2_PASS_THRESHOLD) -> int:
    return 0 if passed >= threshold else 1


def collect_stats(seeds: Sequence[int], match_index: int = DEFAULT_MATCH_INDEX) -> list[dict[str, int]]:
    out: list[dict[str, int]] = []
    for seed in seeds:
        result = simulate_match(build_world(seed), match_index)
        out.append(match_stats(result.meta, (e.to_dict() for e in result.events)))
    return out


def _fmt(value: Fraction | None) -> str:
    if value is None:
        return "n/a"
    # Exact rational to 4 decimals with half-up rounding, no float involved.
    scaled = value * 10_000
    q = (scaled.numerator * 2 + scaled.denominator) // (2 * scaled.denominator)
    sign = "-" if q < 0 else ""
    q = abs(q)
    return f"{sign}{q // 10_000}.{q % 10_000:04d}"


def report_lines(rows: Sequence[Row], seeds_text: str, bands_path: str, bands_sha: str, match_index: int) -> list[str]:
    shown = "agon/synth/bands.json (packaged)" if os.path.abspath(bands_path) == BANDS_FILE else bands_path
    lines = [
        f"realism bands M2, generator {GENERATOR_VERSION}, seeds {seeds_text}, match_index {match_index}",
        f"bands {shown} sha256 {bands_sha} (matches bands.lock)",
        f"{'band':34} {'lo':>8} {'hi':>8} {'observed':>10}  status",
    ]
    for r in rows:
        lines.append(f"{r.band.id:34} {_fmt(r.band.lo):>8} {_fmt(r.band.hi):>8} {_fmt(r.observed):>10}  {r.status}")
    passed = passed_count(rows)
    lines.append(f"M2 passed {passed} of {len(rows)} (threshold {M2_PASS_THRESHOLD})")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.synth.bands", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=DEFAULT_SEEDS, help="e.g. 1001-1200")
    parser.add_argument("--bands", default=None, help="bands file; its bands.lock must sit next to it")
    parser.add_argument("--match-index", type=int, default=DEFAULT_MATCH_INDEX)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    try:
        path = resolve_bands_path(args.bands)
        bands = load_bands(path)
        sha = file_sha256(path)
    except (BandsError, OSError, KeyError, ValueError) as exc:
        print(f"bands refused: {exc}", file=sys.stderr)
        return 2
    seeds = parse_seeds(args.seeds)
    rows = evaluate(bands, observe(collect_stats(seeds, args.match_index)))
    sys.stdout.write("\n".join(report_lines(rows, args.seeds, path, sha, args.match_index)) + "\n")
    sys.stdout.flush()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    sys.stderr.write(f"wall time {elapsed_ms} ms total for {len(seeds)} seed(s)\n")
    return verdict_exit_code(passed_count(rows))


if __name__ == "__main__":
    raise SystemExit(main())

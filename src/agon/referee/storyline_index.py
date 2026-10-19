"""Storyline index for the console's storyline picker, built from verified referee labels.

Usage:
    python -m agon.referee.storyline_index --print

Why it lives in the referee: it reads ground-truth labels, and nothing under src/agon outside this
package may import the referee (ci/check_referee_isolation.py). CI (ci/stamp_web.py) builds the
index here and hands it to `agon.webseason` as plain data. No agent and no MCP tool reads it.

Each entry points at one storyline match: the plain fixture `match_index` 0 of a storyline seed,
re-run with exactly one planned intervention (`storylines.plan_storyline(seed, kind)`). That is a
different match from the plain seed match the site replays, so an entry names its (seed, kind) run
and the command that regenerates it, and its anchor event ids exist only in that run.

Categories, each a written rule over a label whose two code paths agree:
- red_card_comeback: a red_card label where the side shown the red card was behind at some moment
  from the card on and did not lose the match;
- goalkeeper_heroics: a goalkeeper_heroics label with turning_point true (3 or more saves after it);
- late_collapse: a late_collapse label with turning_point true (a goal conceded after it);
- tactical_shift: a tactical_shift label with turning_point true (the press rate rose against the
  opponent's on-ball rate).
Seeds are scanned in order from the spec's storyline set (2001 and up); a category takes the first
`per_category` seeds that qualify. A category with no qualifying seed in the scan is listed with an
empty entry list and its reason, never dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable, Sequence

from agon.referee.labels import labels_from_ledger, labels_from_log
from agon.referee.storylines import run_storyline
from agon.synth.engine import MatchResult
from agon.synth.world import build_world

INDEX_SCHEMA = "agon.storylines/1"
DEFAULT_SEEDS: tuple[int, ...] = tuple(range(2001, 2031))
DEFAULT_PER_CATEGORY = 2


def _red_card_comeback(label: dict[str, Any], result: MatchResult) -> bool:
    """Behind at some moment from the red card on, and not beaten at full time."""
    if label["anchor_event_id"] is None:
        return False
    team = label["team"]
    own = other = 0
    trailed = False
    seen_card = False
    for e in result.events:
        if e.type == "shot" and e.outcome == "goal":
            if e.team == team:
                own += 1
            else:
                other += 1
        if e.event_id == label["anchor_event_id"]:
            seen_card = True
        if seen_card and own < other:
            trailed = True
    return seen_card and trailed and own >= other


def _turning_point(label: dict[str, Any], result: MatchResult) -> bool:
    return bool(label["turning_point"])


CATEGORIES: tuple[tuple[str, str, str, Callable[[dict[str, Any], MatchResult], bool]], ...] = (
    ("red_card_comeback", "red_card",
     "the side shown the red card was behind at some moment from the card on and did not lose", _red_card_comeback),
    ("goalkeeper_heroics", "goalkeeper_heroics",
     "turning point: the keeper made 3 or more saves after the storyline started", _turning_point),
    ("late_collapse", "late_collapse",
     "turning point: the side conceded at least once after the storyline started", _turning_point),
    ("tactical_shift", "tactical_shift",
     "turning point: the press rate rose against the opponent's on-ball rate", _turning_point),
)


def verified_storyline(seed: int, kind: str) -> tuple[dict[str, Any], MatchResult]:
    """The label for (seed, kind), after both label code paths agree, plus the match it labels."""
    plan, result = run_storyline(seed, kind)
    one = labels_from_ledger(seed, plan, result)
    two = labels_from_log(seed, plan, result.meta, [e.to_dict() for e in result.events])
    if one != two:
        raise AssertionError(f"label paths disagree for seed {seed} {kind}")
    return one, result


def _entry(category: str, label: dict[str, Any], result: MatchResult) -> dict[str, Any]:
    league = build_world(label["seed"])
    meta = result.meta
    return {
        "category": category,
        "kind": label["kind"],
        "seed": label["seed"],
        "match_index": meta["match_index"],
        "match_id": meta["match_id"],
        "run": f"storyline match: seed {label['seed']}, match index {meta['match_index']}, one planned {label['kind']}",
        "command": f"python -m agon.referee.storylines --seeds {label['seed']}",
        "league": league.name,
        "home": {"club_id": meta["home"], "name": league.club(meta["home"]).name},
        "away": {"club_id": meta["away"], "name": league.club(meta["away"]).name},
        "score": dict(meta["score"]),
        "label": label,
    }


def storyline_index(seeds: Sequence[int] = DEFAULT_SEEDS, per_category: int = DEFAULT_PER_CATEGORY) -> dict[str, Any]:
    """Scan `seeds` in order and return the index (pure function of its arguments)."""
    if per_category < 1:
        raise ValueError("per_category must be at least 1")
    categories = []
    for category, kind, rule, accept in CATEGORIES:
        entries: list[dict[str, Any]] = []
        for seed in seeds:
            if len(entries) >= per_category:
                break
            label, result = verified_storyline(seed, kind)
            if accept(label, result):
                entries.append(_entry(category, label, result))
        categories.append({
            "category": category,
            "kind": kind,
            "rule": rule,
            "entries": entries,
            "reason": "" if entries else f"no seed in {seeds[0]}..{seeds[-1]} met the rule",
        })
    return {
        "schema": INDEX_SCHEMA,
        "seeds_scanned": [seeds[0], seeds[-1]] if seeds else [],
        "per_category": per_category,
        "categories": categories,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.referee.storyline_index", description=__doc__.splitlines()[0])
    parser.add_argument("--print", action="store_true", help="print the index as JSON")
    parser.add_argument("--per-category", type=int, default=DEFAULT_PER_CATEGORY)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    index = storyline_index(per_category=args.per_category)
    if args.print:
        sys.stdout.write(json.dumps(index, indent=1, sort_keys=True) + "\n")
    for cat in index["categories"]:
        seeds = ", ".join(str(e["seed"]) for e in cat["entries"]) or "none"
        sys.stderr.write(f"{cat['category']}: seeds {seeds}\n")
    sys.stderr.write(f"wall time {int((time.perf_counter() - started) * 1000)} ms total\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

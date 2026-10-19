"""A full fictional season: the double round robin of one seeded league, every match from the engine.

Usage:
    python -m agon.synth.season --season 1 --print-hash
    python -m agon.synth.season --season 1 --hash-out build/season-1.txt --out build/season-1

What a season is. Season S is the league `build_world(S)` and its 30 fixtures from
`world.fixture_list()`: 10 matchdays of 3 matches, every club at home once and away once against
every other club. Each match is simulated by the unchanged engine as `simulate_match(league, i)`.
Nothing new is drawn: every random value still comes from the existing counter-based streams keyed by
(seed S, match_index + 1, stream, possession, draw), so season S match 0 is byte-identical to the M1
match for seed S (`manifest.build_bundle(S, 0)`). A test pins that for season 1, whose match 0 is the
match the live site replays.

Calendar. Fictional and dateless: a season is matchdays 1 to 10 in two legs. Leg 1 is matchdays 1
to 5; leg 2 (matchdays 6 to 10) repeats leg 1 in the same order with home and away swapped. There
are no calendar dates anywhere, so nothing shown on a page can expire.

Lineups. The engine starts every club in its fixed 4-3-3 (`Club.starting_eleven`). Each match record
lists both starting elevens, because they are not in the event log and minutes played need them.

Hashes. A match is identified by its M1 bundle sha256 for (seed, match_index): sha256 over the
canonical JSON of {"world", "match", "events"} (`manifest.SeedBundle.sha256`). The season sha256 is
sha256 over the canonical JSON of the season record, which lists every match with that sha256, its
score, its lineups and its matchday. Hash lines are ASCII, written in binary with LF endings, and
contain no paths or timings; wall time goes to stderr, so stdout stays byte-comparable across OS.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Sequence

from agon import __version__
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.manifest import SeedBundle
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.synth.world import League, build_world, fixture_list

SEASON_SCHEMA = "agon.season/1"
CALENDAR_NOTE = "fictional calendar: matchdays only, no dates"
DEFAULT_SEASON = 1


def season_seed(season: int) -> int:
    """The world seed of a season. Season S is seed S, so season 1 is the league of published seed 1."""
    if season < 1:
        raise ValueError("season must be 1 or more")
    return season


def leg_of(matchday: int, matchdays_total: int) -> int:
    """1 for the first half of the double round robin, 2 for the return leg."""
    return 1 if matchday <= matchdays_total // 2 else 2


@dataclass(frozen=True)
class SeasonMatch:
    """One simulated season match: the engine's meta and events plus the starting elevens."""

    match_index: int
    meta: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    lineups: dict[str, tuple[str, ...]]  # club_id -> starting eleven player ids
    sha256: str

    @property
    def matchday(self) -> int:
        return int(self.meta["matchday"])

    def events_ndjson(self) -> bytes:
        return b"".join(canonical_bytes(e) + b"\n" for e in self.events)


@dataclass(frozen=True)
class Season:
    season: int
    seed: int
    league: League
    world: dict[str, Any]
    matchdays_total: int
    matches: tuple[SeasonMatch, ...]

    def record(self) -> dict[str, Any]:
        """The canonical season record: everything the season sha256 commits to."""
        days: dict[int, list[int]] = {}
        for m in self.matches:
            days.setdefault(m.matchday, []).append(m.match_index)
        return {
            "schema": SEASON_SCHEMA,
            "generator": GENERATOR_VERSION,
            "season": self.season,
            "seed": self.seed,
            "league": self.league.name,
            "calendar": CALENDAR_NOTE,
            "matchdays_total": self.matchdays_total,
            "clubs": [{"club_id": c.club_id, "name": c.name, "short": c.short} for c in self.league.clubs],
            "matchdays": [
                {"matchday": md, "leg": leg_of(md, self.matchdays_total), "match_indices": sorted(idx)}
                for md, idx in sorted(days.items())
            ],
            "matches": [
                {
                    "match_index": m.match_index,
                    "match_id": m.meta["match_id"],
                    "matchday": m.matchday,
                    "leg": leg_of(m.matchday, self.matchdays_total),
                    "home": m.meta["home"],
                    "away": m.meta["away"],
                    "score": dict(m.meta["score"]),
                    "events": len(m.events),
                    "sha256": m.sha256,
                    "lineups": {
                        "home": list(m.lineups[m.meta["home"]]),
                        "away": list(m.lineups[m.meta["away"]]),
                    },
                }
                for m in self.matches
            ],
        }

    def record_bytes(self) -> bytes:
        return canonical_bytes(self.record()) + b"\n"

    def sha256(self) -> str:
        return sha256_hex(canonical_bytes(self.record()))

    def match(self, match_index: int) -> SeasonMatch:
        for m in self.matches:
            if m.match_index == match_index:
                return m
        raise KeyError(match_index)


def build_season(season: int = DEFAULT_SEASON, matchdays: int | None = None) -> Season:
    """Simulate a season, or only its first `matchdays` matchdays (the cut the owner allows).

    Pure function of (season, matchdays). Matches come back in fixture order, which is matchday order.
    """
    seed = season_seed(season)
    league = build_world(seed)
    world = league.to_dict()
    fixtures = fixture_list(len(league.clubs))
    total = max(md for md, _, _ in fixtures)
    if matchdays is not None and not 1 <= matchdays <= total:
        raise ValueError(f"matchdays must be in 1..{total}")
    last = total if matchdays is None else matchdays
    matches: list[SeasonMatch] = []
    for index, (md, home_id, away_id) in enumerate(fixtures):
        if md > last:
            continue
        result = simulate_match(league, index)
        events = [e.to_dict() for e in result.events]
        bundle = SeedBundle(seed=seed, world=world, match=result.meta, events=events)
        lineups = {
            cid: tuple(p.player_id for p in league.club(cid).starting_eleven()) for cid in (home_id, away_id)
        }
        matches.append(
            SeasonMatch(
                match_index=index,
                meta=result.meta,
                events=tuple(events),
                lineups=lineups,
                sha256=bundle.sha256(),
            )
        )
    return Season(season=season, seed=seed, league=league, world=world, matchdays_total=total, matches=tuple(matches))


def hash_lines(season: Season) -> list[str]:
    """ASCII lines that must be identical on every OS: one per match, then the season sha256."""
    lines = [
        f"season {season.season} seed {season.seed} generator {GENERATOR_VERSION} package {__version__}"
        f" matchdays {len({m.matchday for m in season.matches})} of {season.matchdays_total}"
        f" matches {len(season.matches)}"
    ]
    for m in season.matches:
        score = m.meta["score"]
        lines.append(
            f"match {m.match_index} matchday {m.matchday} {m.meta['home']}-{m.meta['away']}"
            f" score {score['home']}-{score['away']} events {len(m.events)} sha256 {m.sha256}"
        )
    lines.append(f"season sha256 {season.sha256()}")
    return lines


def _write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.synth.season", description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON, help="season number; season S uses world seed S")
    parser.add_argument("--matchdays", type=int, default=None, help="only the first N matchdays (default: all)")
    parser.add_argument("--print-hash", action="store_true", help="print one line per match and the season sha256")
    parser.add_argument("--hash-out", help="also write the hash lines to this file (binary, LF)")
    parser.add_argument("--out", help="write season.json (the canonical season record) under this directory")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    season = build_season(args.season, args.matchdays)
    lines = hash_lines(season)
    text = ("\n".join(lines) + "\n").encode("ascii")
    if args.print_hash:
        sys.stdout.buffer.write(text)
        sys.stdout.buffer.flush()
    if args.hash_out:
        _write_bytes(args.hash_out, text)
    if args.out:
        _write_bytes(os.path.join(args.out, "season.json"), season.record_bytes())
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    sys.stderr.write(f"wall time {elapsed_ms} ms total for {len(season.matches)} match(es)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Write and validate the season files the console reads (the season web data contract).

Usage:
    python -m agon.referee.storyline_index --out build/storylines.json
    python -m agon.webseason --season 1 --storylines build/storylines.json --out build/web-season

At deploy, ci/stamp_web.py does the same in one step: it builds the storyline index with the referee
and calls `write_season_web`, then `verify_season_dir`. The storyline index is never built here,
because it reads referee labels and no module under src/agon outside the referee may import it
(ci/check_referee_isolation.py); it arrives as plain data and is shape-checked like every file.

Contract. One folder (web/season/), every file canonical JSON (sorted keys, ASCII, integers and
strings only, no floats) plus a final LF:

    manifest.json    {"schema", "generator", "season", "seed", "season_sha256",
                      "files": [{"name", "sha256"}]}, one entry per file below, in this order
    fixtures.json    the fictional calendar (matchdays, no dates), the clubs and every match with its
                     score, its M1 bundle sha256 and `events_sha256`, the sha256 of its NDJSON event
                     log; the web data manifest lists the same hash for a match the site replays
    tables.json      the table after every matchday, with the tie-break rule
    form.json        per matchday and club: the last five results (oldest first) and current streaks
    players.json     every squad player's season totals, the carry speed proxy, and leaders
    h2h.json         head to head for every pair of clubs
    storylines.json  the storyline picker index (agon.referee.storyline_index), as passed in
    datalab.json     the 22 realism bands on the held-out seeds (M2, self-measured, from the committed
                     `agon/synth/bands_heldout.json`), the dataset card path and regenerate commands

Every season number comes from `agon.governance.recompute` (the definitions the Fact Gate checks)
over `agon.synth.season`. The speed figure is `agon.interpret.kinematics.carry_proxy`: an on-ball
carry displacement proxy, not running speed, and the file says so beside the number.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from itertools import combinations
from typing import Any, Callable, Mapping, Sequence

from agon.governance import recompute
from agon.interpret.frame import MatchView
from agon.interpret.kinematics import EVENTS_ONLY_NOTE, carry_proxy
from agon.synth.bands import BAND_COUNT, FROZEN_BANDS_SHA256, M2_PASS_THRESHOLD, PACKAGE_DIR
from agon.synth.engine import GENERATOR_VERSION
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.synth.season import CALENDAR_NOTE, DEFAULT_SEASON, Season, build_season, leg_of
from agon.webdata import KIT_HEX

SEASON_DIR = "season"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "agon.webseason/1"
STORYLINE_SCHEMA = "agon.storylines/1"
BANDS_RECORD = os.path.join(PACKAGE_DIR, "bands_heldout.json")
DATASET_CARD = {"path": "DATASET_CARD.md", "url": "https://github.com/upgradedev/agon-azure/blob/main/DATASET_CARD.md"}
LEADERS = 5
FILE_ORDER: tuple[str, ...] = (
    "fixtures.json", "tables.json", "form.json", "players.json", "h2h.json", "storylines.json", "datalab.json",
)
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def regenerate_commands(season: int) -> list[dict[str, str]]:
    return [
        {"what": "season matches and season sha256", "command": f"python -m agon.synth.season --season {season} --print-hash"},
        {"what": "storyline index", "command": "python -m agon.referee.storyline_index --out build/storylines.json"},
        {"what": "season web files", "command": f"python -m agon.webseason --season {season} --storylines build/storylines.json --out build/web-season"},
        {"what": "realism bands on the held-out seeds (M2)", "command": "python -m agon.synth.bands --seeds 1001-1200"},
        {"what": "published demo matches (M1)", "command": "python -m agon.synth.manifest --seeds 1,2,3 --print-hash"},
        {"what": "storyline labels", "command": "python -m agon.referee.storylines --seeds 1,2,3 --print-hash"},
        {"what": "label re-derivation and mutate proof (M3)", "command": "python -m agon.referee.verify_labels --seeds 1,2,3 --mutate"},
        {"what": "planted-error fixtures", "command": "python -m agon.synth.fixtures --seeds 1,2,3 --print-hash"},
    ]


# ----------------------------------------------------------------- building


def season_facts_of(season: Season) -> recompute.SeasonFacts:
    return recompute.build_season_facts(
        season.season, [c.club_id for c in season.league.clubs], ((m.meta, m.events, m.lineups) for m in season.matches)
    )


def load_bands_record(path: str | None = None) -> dict[str, Any]:
    """The committed M2 record; refused unless it is for the frozen bands and this generator."""
    with open(path or BANDS_RECORD, "rb") as fh:
        record = json.loads(fh.read().decode("ascii"))
    problems = validate_bands_record(record)
    if problems:
        raise ValueError("bands record refused: " + "; ".join(problems))
    return record


def _fixtures(season: Season) -> dict[str, Any]:
    days: dict[int, list[dict[str, Any]]] = {}
    for m in season.matches:
        days.setdefault(m.matchday, []).append({
            "match_index": m.match_index,
            "match_id": m.meta["match_id"],
            "home": m.meta["home"],
            "away": m.meta["away"],
            "score": dict(m.meta["score"]),
            "events": len(m.events),
            "sha256": m.sha256,
            "events_sha256": sha256_hex(m.events_ndjson()),
        })
    return {
        "kind": "fixtures",
        "season": season.season,
        "seed": season.seed,
        "generator": GENERATOR_VERSION,
        "league": season.league.name,
        "calendar": CALENDAR_NOTE,
        "matchdays_total": season.matchdays_total,
        "clubs": [
            {"club_id": c.club_id, "name": c.name, "short": c.short,
             "kit_primary_hex": KIT_HEX[c.kit_primary], "kit_secondary_hex": KIT_HEX[c.kit_secondary]}
            for c in season.league.clubs
        ],
        "matchdays": [
            {"matchday": md, "leg": leg_of(md, season.matchdays_total), "matches": games}
            for md, games in sorted(days.items())
        ],
    }


def _matchdays(season: Season) -> list[int]:
    return sorted({m.matchday for m in season.matches})


def _tables(season: Season, facts: recompute.SeasonFacts) -> dict[str, Any]:
    return {
        "kind": "tables",
        "season": season.season,
        "tiebreak_rule": recompute.TIEBREAK_RULE,
        "points": {"win": recompute.POINTS_WIN, "draw": recompute.POINTS_DRAW, "loss": 0},
        "matchdays": [{"matchday": md, "rows": recompute.league_table(facts, md)} for md in _matchdays(season)],
    }


def _form(season: Season, facts: recompute.SeasonFacts) -> dict[str, Any]:
    out = []
    for md in _matchdays(season):
        clubs = []
        for club in facts.clubs:
            rows = recompute.form(facts, club, md)
            clubs.append({
                "club_id": club,
                "form": rows,
                "form_string": "".join(r["result"] for r in rows),
                "streaks": recompute.current_streaks(facts, club, md),
            })
        out.append({"matchday": md, "clubs": clubs})
    return {"kind": "form", "season": season.season, "form_length": recompute.FORM_LENGTH, "order": "oldest first", "matchdays": out}


def _players(season: Season, facts: recompute.SeasonFacts) -> dict[str, Any]:
    totals = recompute.player_season_totals(facts)
    top: dict[str, int] = {}
    for m in season.matches:
        view = MatchView.from_bundle(m.meta, m.events)
        for row in carry_proxy(view)["players"]:
            top[row["player"]] = max(top.get(row["player"], 0), row["top_proxy_kmh_x10"])
    players = []
    for club in season.league.clubs:
        for p in club.players:
            t = totals.get(p.player_id, {k: 0 for k in recompute.PLAYER_SEASON_STATS})
            players.append({
                "player_id": p.player_id,
                "name": p.name,
                "club_id": club.club_id,
                "position": p.position,
                "shirt": p.shirt,
                **{k: int(t[k]) for k in recompute.PLAYER_SEASON_STATS},
                "top_proxy_kmh_x10": top.get(p.player_id, 0),
            })

    def leaders(stat: str) -> list[dict[str, Any]]:
        ranked = sorted((p for p in players if p[stat] > 0), key=lambda p: (-p[stat], p["player_id"]))
        return [{"player_id": p["player_id"], "value": p[stat]} for p in ranked[:LEADERS]]

    return {
        "kind": "players",
        "season": season.season,
        "through_matchday": max(_matchdays(season)),
        "speed": {"field": "top_proxy_kmh_x10", "unit": "tenths of km/h", "source": "events_carry_proxy", "note": EVENTS_ONLY_NOTE},
        "players": players,
        "leaders": {stat: leaders(stat) for stat in ("goals", "assists", "minutes", "yellow_cards")},
    }


def _h2h(season: Season, facts: recompute.SeasonFacts) -> dict[str, Any]:
    pairs = [recompute.head_to_head(facts, a, b) for a, b in combinations(sorted(facts.clubs), 2)]
    return {"kind": "h2h", "season": season.season, "pairs": pairs}


def _datalab(season: Season, bands: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "datalab",
        "season": season.season,
        "label": "self-measured",
        "bands": dict(bands),
        "dataset_card": dict(DATASET_CARD),
        "regenerate": regenerate_commands(season.season),
    }


def file_bytes(payload: Any) -> bytes:
    return canonical_bytes(payload) + b"\n"


def build_season_web(
    season_number: int,
    storylines: Mapping[str, Any],
    bands: Mapping[str, Any] | None = None,
    season: Season | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return (manifest, {file name: bytes}) without touching the disk. Refuses invalid content."""
    if bands is None:
        bands = load_bands_record()
    elif validate_bands_record(bands):
        raise ValueError("bands record refused: " + "; ".join(validate_bands_record(bands)))
    season = season or build_season(season_number)
    if season.season != season_number:
        raise ValueError(f"season object is season {season.season}, asked for {season_number}")
    facts = season_facts_of(season)
    payloads = {
        "fixtures.json": _fixtures(season),
        "tables.json": _tables(season, facts),
        "form.json": _form(season, facts),
        "players.json": _players(season, facts),
        "h2h.json": _h2h(season, facts),
        "storylines.json": {"kind": "storylines", "season": season.season, "index": dict(storylines)},
        "datalab.json": _datalab(season, bands),
    }
    problems = [p for name, body in payloads.items() for p in validate_payload(name, body)]
    if problems:
        raise ValueError("refusing invalid season files: " + "; ".join(problems))
    data = {name: file_bytes(body) for name, body in payloads.items()}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generator": GENERATOR_VERSION,
        "season": season.season,
        "seed": season.seed,
        "season_sha256": season.sha256(),
        "files": [{"name": name, "sha256": sha256_hex(data[name])} for name in FILE_ORDER],
    }
    return manifest, data


def write_season_web(
    out_dir: str | os.PathLike[str],
    storylines: Mapping[str, Any],
    season_number: int = DEFAULT_SEASON,
    bands: Mapping[str, Any] | None = None,
    season: Season | None = None,
) -> dict[str, Any]:
    """Write every season file and manifest.json under out_dir, binary, LF only. Returns the manifest."""
    manifest, data = build_season_web(season_number, storylines, bands, season)
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("refusing an invalid season manifest: " + "; ".join(problems))
    os.makedirs(out_dir, exist_ok=True)
    for name, body in data.items():
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(body)
    with open(os.path.join(out_dir, MANIFEST_NAME), "wb") as fh:
        fh.write(file_bytes(manifest))
    return manifest


# ----------------------------------------------------------------- validation


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _keys(obj: Any, expected: frozenset[str], where: str) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{where} must be an object"]
    if set(obj) != expected:
        return [f"{where} keys must be exactly {', '.join(sorted(expected))}"]
    return []


def _ints(obj: Mapping[str, Any], keys: Sequence[str], where: str, allow_negative: Sequence[str] = ()) -> list[str]:
    out = []
    for k in keys:
        if not _is_int(obj[k]) or (obj[k] < 0 and k not in allow_negative):
            out.append(f"{where}.{k} must be a non-negative integer")
    return out


MANIFEST_KEYS = frozenset({"schema", "generator", "season", "seed", "season_sha256", "files"})


def validate_manifest(manifest: Any) -> list[str]:
    problems = _keys(manifest, MANIFEST_KEYS, "manifest")
    if problems:
        return problems
    if manifest["schema"] != MANIFEST_SCHEMA:
        problems.append(f"manifest.schema must be {MANIFEST_SCHEMA}")
    if not isinstance(manifest["generator"], str) or not manifest["generator"]:
        problems.append("manifest.generator must be a non-empty string")
    problems += _ints(manifest, ("season", "seed"), "manifest")
    if not isinstance(manifest["season_sha256"], str) or not SHA256_HEX.match(manifest["season_sha256"]):
        problems.append("manifest.season_sha256 must be 64 lowercase hex characters")
    files = manifest["files"]
    if not isinstance(files, list) or [f.get("name") if isinstance(f, dict) else None for f in files] != list(FILE_ORDER):
        problems.append("manifest.files must list exactly " + ", ".join(FILE_ORDER) + " in that order")
        return problems
    for i, entry in enumerate(files):
        problems += _keys(entry, frozenset({"name", "sha256"}), f"files[{i}]")
        if isinstance(entry, dict) and (not isinstance(entry.get("sha256"), str) or not SHA256_HEX.match(entry["sha256"])):
            problems.append(f"files[{i}].sha256 must be 64 lowercase hex characters")
    return problems


TABLE_ROW_KEYS = frozenset({
    "position", "club_id", "played", "won", "drawn", "lost", "goals_for", "goals_against", "goal_difference", "points",
})
FORM_ROW_KEYS = frozenset({"matchday", "match_id", "opponent", "venue", "result", "goals_for", "goals_against"})
PLAYER_KEYS = frozenset({"player_id", "name", "club_id", "position", "shirt", "top_proxy_kmh_x10", *recompute.PLAYER_SEASON_STATS})
FIXTURE_MATCH_KEYS = frozenset({"match_index", "match_id", "home", "away", "score", "events", "sha256", "events_sha256"})
STORY_CATEGORY_KEYS = frozenset({"category", "kind", "rule", "entries", "reason"})
STORY_ENTRY_KEYS = frozenset({"category", "kind", "seed", "match_index", "match_id", "run", "command", "league", "home", "away", "score", "label"})
STORY_LABEL_KEYS = frozenset({
    "label_id", "seed", "match_id", "kind", "side", "team", "period", "minute", "at_clock", "anchor_event_id", "effect", "turning_point",
})
BANDS_KEYS = frozenset({"metric", "label", "command", "generator", "seeds", "match_index", "bands_sha256", "threshold", "passed", "of", "rows"})
BAND_ROW_KEYS = frozenset({"id", "statistic", "unit", "lo", "hi", "observed", "status"})
BAND_STATUSES = frozenset({"PASS", "FAIL", "NOT MEASURABLE"})
DECIMAL = re.compile(r"^-?[0-9]+\.[0-9]{4}$")


def _validate_fixtures(body: Mapping[str, Any]) -> list[str]:
    p = _keys(body, frozenset({"kind", "season", "seed", "generator", "league", "calendar", "matchdays_total", "clubs", "matchdays"}), "fixtures")
    if p:
        return p
    club_ids = set()
    for i, club in enumerate(body["clubs"]):
        p += _keys(club, frozenset({"club_id", "name", "short", "kit_primary_hex", "kit_secondary_hex"}), f"fixtures.clubs[{i}]")
        if isinstance(club, dict):
            club_ids.add(club.get("club_id"))
    seen: set[int] = set()
    for d, day in enumerate(body["matchdays"]):
        p += _keys(day, frozenset({"matchday", "leg", "matches"}), f"fixtures.matchdays[{d}]")
        if p:
            return p
        for j, m in enumerate(day["matches"]):
            where = f"fixtures.matchdays[{d}].matches[{j}]"
            p += _keys(m, FIXTURE_MATCH_KEYS, where)
            if p:
                return p
            if m["home"] not in club_ids or m["away"] not in club_ids or m["home"] == m["away"]:
                p.append(f"{where}: home and away must be two listed clubs")
            if m["match_index"] in seen:
                p.append(f"{where}: match_index {m['match_index']} is listed twice")
            seen.add(m["match_index"])
            for k in ("sha256", "events_sha256"):
                if not isinstance(m[k], str) or not SHA256_HEX.match(m[k]):
                    p.append(f"{where}.{k} must be 64 lowercase hex characters")
    return p


def _validate_tables(body: Mapping[str, Any]) -> list[str]:
    p = _keys(body, frozenset({"kind", "season", "tiebreak_rule", "points", "matchdays"}), "tables")
    if p:
        return p
    for d, day in enumerate(body["matchdays"]):
        p += _keys(day, frozenset({"matchday", "rows"}), f"tables.matchdays[{d}]")
        if p:
            return p
        positions = []
        for i, row in enumerate(day["rows"]):
            where = f"tables.matchdays[{d}].rows[{i}]"
            p += _keys(row, TABLE_ROW_KEYS, where)
            if p:
                return p
            p += _ints(row, sorted(TABLE_ROW_KEYS - {"club_id"}), where, allow_negative=("goal_difference",))
            positions.append(row["position"])
        if positions != list(range(1, len(positions) + 1)):
            p.append(f"tables.matchdays[{d}]: positions must run 1..{len(positions)} in order")
    return p


def _validate_form(body: Mapping[str, Any]) -> list[str]:
    p = _keys(body, frozenset({"kind", "season", "form_length", "order", "matchdays"}), "form")
    if p:
        return p
    for d, day in enumerate(body["matchdays"]):
        p += _keys(day, frozenset({"matchday", "clubs"}), f"form.matchdays[{d}]")
        if p:
            return p
        for c, club in enumerate(day["clubs"]):
            where = f"form.matchdays[{d}].clubs[{c}]"
            p += _keys(club, frozenset({"club_id", "form", "form_string", "streaks"}), where)
            if p:
                return p
            if len(club["form"]) > body["form_length"]:
                p.append(f"{where}.form is longer than form_length")
            for r in club["form"]:
                p += _keys(r, FORM_ROW_KEYS, f"{where}.form[]")
            if club["form_string"] != "".join(r.get("result", "?") for r in club["form"] if isinstance(r, dict)):
                p.append(f"{where}.form_string does not match form")
            if set(club["streaks"]) != set(recompute.SEASON_STREAK_STATS):
                p.append(f"{where}.streaks keys must be exactly " + ", ".join(recompute.SEASON_STREAK_STATS))
    return p


def _validate_players(body: Mapping[str, Any]) -> list[str]:
    p = _keys(body, frozenset({"kind", "season", "through_matchday", "speed", "players", "leaders"}), "players")
    if p:
        return p
    ids = set()
    for i, row in enumerate(body["players"]):
        where = f"players.players[{i}]"
        p += _keys(row, PLAYER_KEYS, where)
        if p:
            return p
        p += _ints(row, ("shirt", "top_proxy_kmh_x10", *recompute.PLAYER_SEASON_STATS), where)
        if row["player_id"] in ids:
            p.append(f"{where}: player_id {row['player_id']} is listed twice")
        ids.add(row["player_id"])
    for stat, rows in body["leaders"].items():
        for r in rows:
            if not isinstance(r, dict) or r.get("player_id") not in ids:
                p.append(f"players.leaders.{stat} names a player who is not listed")
    if "proxy" not in body["speed"].get("note", "") or "not running speed" not in body["speed"].get("note", ""):
        p.append("players.speed.note must say the speed is a proxy and not running speed")
    return p


def _validate_h2h(body: Mapping[str, Any]) -> list[str]:
    p = _keys(body, frozenset({"kind", "season", "pairs"}), "h2h")
    if p:
        return p
    for i, pair in enumerate(body["pairs"]):
        p += _keys(pair, frozenset({"clubs", "played", "wins", "draws", "goals", "meetings"}), f"h2h.pairs[{i}]")
    return p


def validate_storyline_index(index: Any) -> list[str]:
    p = _keys(index, frozenset({"schema", "seeds_scanned", "per_category", "categories"}), "storylines.index")
    if p:
        return p
    if index["schema"] != STORYLINE_SCHEMA:
        p.append(f"storylines.index.schema must be {STORYLINE_SCHEMA}")
    for c, cat in enumerate(index["categories"]):
        where = f"storylines.index.categories[{c}]"
        p += _keys(cat, STORY_CATEGORY_KEYS, where)
        if p:
            return p
        if not cat["entries"] and not cat["reason"]:
            p.append(f"{where}: an empty category must give its reason")
        for e, entry in enumerate(cat["entries"]):
            ewhere = f"{where}.entries[{e}]"
            p += _keys(entry, STORY_ENTRY_KEYS, ewhere)
            if p:
                return p
            p += _keys(entry["label"], STORY_LABEL_KEYS, f"{ewhere}.label")
            if entry["category"] != cat["category"] or entry["kind"] != cat["kind"]:
                p.append(f"{ewhere}: category or kind differs from its group")
            if not str(entry["command"]).startswith("python -m agon.referee.storylines --seeds "):
                p.append(f"{ewhere}.command must be the storyline regenerate command")
    return p


def _validate_storylines(body: Mapping[str, Any]) -> list[str]:
    p = _keys(body, frozenset({"kind", "season", "index"}), "storylines")
    return p or validate_storyline_index(body["index"])


def validate_bands_record(record: Any) -> list[str]:
    p = _keys(record, BANDS_KEYS, "bands")
    if p:
        return p
    if record["bands_sha256"] != FROZEN_BANDS_SHA256:
        p.append("bands.bands_sha256 is not the frozen bands: not an M2 result")
    if record["generator"] != GENERATOR_VERSION:
        p.append(f"bands.generator {record['generator']} is not the current generator {GENERATOR_VERSION}: re-measure")
    if record["label"] != "self-measured":
        p.append("bands.label must be self-measured")
    if record["threshold"] != M2_PASS_THRESHOLD or record["of"] != BAND_COUNT or len(record["rows"]) != BAND_COUNT:
        p.append(f"bands must hold {BAND_COUNT} rows against the threshold {M2_PASS_THRESHOLD}")
    passed = 0
    for i, row in enumerate(record["rows"]):
        where = f"bands.rows[{i}]"
        p += _keys(row, BAND_ROW_KEYS, where)
        if p:
            return p
        if row["status"] not in BAND_STATUSES:
            p.append(f"{where}.status must be one of {', '.join(sorted(BAND_STATUSES))}")
        if not DECIMAL.match(str(row["lo"])) or not DECIMAL.match(str(row["hi"])):
            p.append(f"{where}: lo and hi must be decimal strings")
        if (row["observed"] is None) != (row["status"] == "NOT MEASURABLE"):
            p.append(f"{where}: observed is null exactly when the band is not measurable")
        passed += row["status"] == "PASS"
    if passed != record["passed"]:
        p.append(f"bands.passed says {record['passed']}, the rows pass {passed}")
    return p


def _validate_datalab(body: Mapping[str, Any]) -> list[str]:
    p = _keys(body, frozenset({"kind", "season", "label", "bands", "dataset_card", "regenerate"}), "datalab")
    if p:
        return p
    p += validate_bands_record(body["bands"])
    if body["dataset_card"] != DATASET_CARD:
        p.append("datalab.dataset_card must name DATASET_CARD.md")
    for i, cmd in enumerate(body["regenerate"]):
        p += _keys(cmd, frozenset({"what", "command"}), f"datalab.regenerate[{i}]")
        if isinstance(cmd, dict) and not str(cmd.get("command", "")).startswith("python -m agon."):
            p.append(f"datalab.regenerate[{i}].command must be a python -m agon command")
    return p


VALIDATORS: dict[str, Callable[[Mapping[str, Any]], list[str]]] = {
    "fixtures.json": _validate_fixtures,
    "tables.json": _validate_tables,
    "form.json": _validate_form,
    "players.json": _validate_players,
    "h2h.json": _validate_h2h,
    "storylines.json": _validate_storylines,
    "datalab.json": _validate_datalab,
}


def validate_payload(name: str, body: Any) -> list[str]:
    """Problems with one season file's content; empty means it matches the contract."""
    if name not in VALIDATORS:
        return [f"{name} is not a season file"]
    if not isinstance(body, dict):
        return [f"{name} must hold a JSON object"]
    kind = name.removesuffix(".json")
    if body.get("kind") != kind:
        return [f"{name}.kind must be {kind}"]
    if not _is_int(body.get("season")) or body["season"] < 1:
        return [f"{name}.season must be a positive integer"]
    try:
        return VALIDATORS[name](body)
    except (AttributeError, KeyError, TypeError) as exc:
        return [f"{name}: malformed ({type(exc).__name__}: {exc})"]


def verify_season_dir(season_dir: str | os.PathLike[str]) -> int:
    """Re-hash and shape-check a written season folder; unlisted files fail. Returns files verified.

    Raises ValueError naming the first broken rule.
    """
    root = os.fspath(season_dir)
    manifest_path = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise ValueError(f"{manifest_path} does not exist")
    with open(manifest_path, "rb") as fh:
        manifest = json.loads(fh.read().decode("ascii"))
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("season manifest breaks the contract: " + "; ".join(problems))
    for entry in manifest["files"]:
        path = os.path.join(root, entry["name"])
        if not os.path.isfile(path):
            raise ValueError(f"season manifest lists {entry['name']}, which does not exist")
        with open(path, "rb") as fh:
            data = fh.read()
        if sha256_hex(data) != entry["sha256"]:
            raise ValueError(f"season/{entry['name']}: sha256 {sha256_hex(data)}, manifest says {entry['sha256']}")
        body = json.loads(data.decode("ascii"))
        problems = validate_payload(entry["name"], body)
        if problems:
            raise ValueError(f"season/{entry['name']} breaks the contract: " + "; ".join(problems))
        if body["season"] != manifest["season"]:
            raise ValueError(f"season/{entry['name']} is for season {body['season']}, manifest says {manifest['season']}")
    present = sorted(
        os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/") for d, _, names in os.walk(root) for n in names
    )
    stray = [rel for rel in present if rel != MANIFEST_NAME and rel not in FILE_ORDER]
    if stray:
        raise ValueError(f"season files not listed in the manifest: {', '.join(stray)}")
    return len(FILE_ORDER)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webseason", description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--storylines", required=True, help="JSON from python -m agon.referee.storyline_index --out <file>")
    parser.add_argument("--out", required=True, help="the season folder, for example web/season")
    args = parser.parse_args(argv)
    with open(args.storylines, "rb") as fh:
        storylines = json.loads(fh.read().decode("utf-8"))
    manifest = write_season_web(args.out, storylines, args.season)
    count = verify_season_dir(args.out)
    for entry in manifest["files"]:
        sys.stdout.write(f"{entry['name']} sha256 {entry['sha256']}\n")
    sys.stdout.write(f"season {manifest['season']} sha256 {manifest['season_sha256']} files {count}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Write and validate the intelligence files the console draws (the intel web data contract).

Usage:
    python -m agon.webdata --seeds 1,2,3 --out build/data
    python -m agon.webintel --data build/data --seeds 1,2,3 --out build/intel

At deploy, ci/stamp_web.py calls `write_intel_web` on the data folder it has just verified, then
`verify_intel_dir`. The format is documented for the console in webintel_README.txt beside this
module; the validators below are the contract and the README must agree with them.

Source. The exact event log and names file the site serves (data/<seed>.ndjson and
data/<seed>.world.json), so every number describes the bytes a visitor replays; each match file
carries `events_sha256`, the sha256 of that event log. The numbers come from
`agon.interpret.advanced` (the metrics pack), `agon.interpret.metrics` (control vs chaos, pressure
regimes, tempo) and `agon.governance.recompute` (season milestones, the definitions the Fact Gate
checks). The season context of a published seed is its own season (`agon.synth.season`, season S
is seed S, the published match is its match 0). The served log must equal that season match byte for
byte; a log that is only its first part (a truncated replay, such as the development fixture) gets
the metrics of what it holds and no milestones, with `milestones_note` saying why; any other log is
refused and nothing is written.

Files (folder intel/, canonical JSON: sorted keys, ASCII, integers, strings, booleans and null only,
no floats, plus a final LF):

    manifest.json              {"schema", "generator", "interpret", "pack", "model_sha256",
                                "files": [{"name", "kind", "seed", "sha256", "events_sha256"}]}
    metrics-<seed>.json        one published match: definitions, per club, per 15 minutes, per
                               player, every pass, rhythm changes with sentences, milestones
    milestones-season-<n>.json every matchday of season n: citable milestone facts per match
    whatif-<seed>.json         the What-If Lab scenario set for one published match, copied from
                               the committed set (agon/whatif/scenario_set, regenerated and compared
                               on ubuntu and windows by the determinism workflow); it must describe
                               the served log, this generator and this model, or the deploy stops
    brief-<seed>.json          only when a brief is committed under agon/whatif/briefs (drafted by the
                               main-only workflow whatif-brief.yml): the accepted analyst briefs, every
                               number re-checked here against the What-If file; a mismatch stops the deploy

Every milestone carries a sentence and the claims that make it citable (existing Fact Gate claim
types: player_season_total, season_streak, table_position); tests run each one through the gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from functools import lru_cache
from typing import Any, Mapping, Sequence

from agon.governance import recompute
from agon.interpret import INTERPRET_VERSION, load_model, metrics, model_sha256
from agon.interpret.advanced import METRICS, PACK_VERSION, PLAYER_KEYS, SERIES_KEYS, TEAM_KEYS, pack
from agon.interpret.frame import MatchView
from agon.synth.engine import GENERATOR_VERSION
from agon.synth.manifest import DEFAULT_MATCH_INDEX, parse_seeds
from agon.synth.schema import FIXED_FRAME, canonical_bytes, sha256_hex, to_fixed_frame
from agon.synth.season import Season, build_season, season_seed
from agon.synth.world import fixture_list

INTEL_DIR = "intel"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "agon.webintel-manifest/1"
METRICS_SCHEMA = "agon.webintel.metrics/1"
MILESTONES_SCHEMA = "agon.webintel.milestones/1"
MILESTONE_SEASON = 1
LABEL = (
    "Synthetic match between fictional clubs. The metrics, passes and rhythm are computed from this "
    "match's event log by agon.interpret with the Fact Gate's definitions; nothing here describes real "
    "football."
)
MILESTONES_PROVENANCE = (
    "Season facts recomputed by agon.governance.recompute (the Fact Gate's definitions) over every match "
    "of the simulated season up to this matchday (agon.synth.season), not over this match's log alone: "
    "the table also needs the other fixtures of each matchday."
)
SEASON_MILESTONES_LABEL = (
    "Synthetic season of fictional clubs. Every milestone is recomputed by agon.governance.recompute (the "
    "Fact Gate's definitions) over all matches of the simulated season up to its matchday."
)
METRICS_NAME = re.compile(r"^metrics-([0-9]+)\.json$")
WHATIF_NAME = re.compile(r"^whatif-([0-9]+)\.json$")
BRIEF_NAME = re.compile(r"^brief-([0-9]+)\.json$")
BRIEF_SCHEMA = "agon.webintel.brief/1"
MILESTONES_NAME = re.compile(r"^milestones-season-([0-9]+)\.json$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
STREAK_MIN = 3

PASS_COLUMNS: tuple[str, ...] = (
    "event_id", "club_id", "player_id", "receiver_id", "period", "minute", "second",
    "x_dm", "y_dm", "end_x_dm", "end_y_dm", "outcome", "under_pressure",
    "distance_m", "completion_ppm", "difficulty",
    "progressive", "into_box", "cross_into_box", "deep_completion", "key_pass", "xa_ppm",
)
# The stats a writer may cite as `stat` claims for these metrics (recompute.STAT_KEYS), with units.
CITABLE: tuple[dict[str, str], ...] = (
    {"stat": "progressive_passes", "unit": "count", "subject": "club or player"},
    {"stat": "progressive_carries", "unit": "count", "subject": "club or player"},
    {"stat": "passes_into_box", "unit": "count", "subject": "club or player"},
    {"stat": "crosses_into_box", "unit": "count", "subject": "club or player"},
    {"stat": "carries_into_box", "unit": "count", "subject": "club or player"},
    {"stat": "box_entries", "unit": "count", "subject": "club or player"},
    {"stat": "deep_completions", "unit": "count", "subject": "club or player"},
    {"stat": "key_passes", "unit": "count", "subject": "club or player"},
    {"stat": "pass_accuracy_pct", "unit": "percent", "subject": "club or player"},
    {"stat": "field_tilt_pct", "unit": "percent", "subject": "club"},
    {"stat": "counter_press_recovery_pct", "unit": "percent", "subject": "club"},
)
NOT_CITABLE_NOTE = (
    "xG, xA, xT and PPDA are decimals in prose; the Fact Gate reads whole numbers only, so they are "
    "shown, never cited."
)


# ----------------------------------------------------------------- reading the served data


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def load_served(data_dir: str, seed: int) -> tuple[bytes, list[dict[str, Any]], dict[str, Any]]:
    """(event log bytes, events, world) for one published seed, read from the served data folder."""
    raw = _read_bytes(os.path.join(data_dir, f"{seed}.ndjson"))
    events = [json.loads(line) for line in raw.decode("ascii").splitlines() if line]
    world = json.loads(_read_bytes(os.path.join(data_dir, f"{seed}.world.json")).decode("utf-8"))
    return raw, events, world


def _names(world: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    clubs = {c["club_id"]: {"name": c["name"], "short": c["short"]} for c in world["clubs"]}
    players = {
        p["player_id"]: {"name": p["name"], "shirt": p["shirt"], "position": p["position"], "club_id": c["club_id"]}
        for c in world["clubs"] for p in c["players"]
    }
    return clubs, players


# ----------------------------------------------------------------- rhythm, in plain words


def window_text(period: int, start_minute: int) -> str:
    """Readable name of a 5-minute window: 'minutes 60 to 65', or the added time of a half."""
    if period == 1 and start_minute >= 45:
        return "first-half added time"
    if period == 2 and start_minute >= 90:
        return "second-half added time"
    return f"minutes {start_minute} to {start_minute + 5}"


def _window_key(label: str) -> tuple[int, int]:
    period, span = label.split(" ")
    return int(period[1:]), int(span.split("-")[0])


def _chaos_sentence(when: str, frm: str, to: str, a: int, b: int) -> str:
    if b > a:
        return (f"In {when} the game turned from {frm} to {to}: possessions got shorter and more of them "
                f"ended in open-play turnovers (index {a} to {b} per mille).")
    return (f"In {when} the game settled from {frm} to {to}: possessions got longer and fewer ended in "
            f"open-play turnovers (index {a} to {b} per mille).")


def _pressure_sentence(club: str, when: str, frm: str, to: str, n: int) -> str:
    verb = "raised" if ("low", "medium", "high").index(to) > ("low", "medium", "high").index(frm) else "lowered"
    events = "pressure event" if n == 1 else "pressure events"
    return f"{club} {verb} their pressing from {frm} to {to} in {when}, with {n} {events} in that window."


def _tempo_sentence(when: str, frm: str, to: str, n: int, before: int) -> str:
    return f"The tempo went from {frm} to {to} in {when}: {n} on-ball actions in five minutes, against {before} in the window before."


def rhythm(view: MatchView, clubs: Mapping[str, Mapping[str, Any]], model: Mapping[str, Any]) -> dict[str, Any]:
    """Control vs chaos, pressure regimes and tempo per 5-minute window, each change with one sentence."""
    chaos = metrics.control_vs_chaos(view, model)
    chaos_changes = []
    for prev, cur in zip(chaos, chaos[1:]):
        if prev["band"] != cur["band"]:
            when = window_text(cur["period"], cur["start_minute"])
            chaos_changes.append({
                "window": cur["window"], "from": prev["band"], "to": cur["band"],
                "index_from_permille": prev["index_permille"], "index_to_permille": cur["index_permille"],
                "sentence": _chaos_sentence(when, prev["band"], cur["band"], prev["index_permille"], cur["index_permille"]),
            })
    counts = metrics.pressure_counts(view)
    regimes = metrics.pressure_regimes(view, model)
    th = model["bands"]["pressure"]
    pressure: dict[str, Any] = {}
    for team in (view.home_id, view.away_id):
        windows = [
            {"window": f"p{key[0]} {key[1] * 5}-{key[1] * 5 + 5}", "pressures": c[team],
             "band": metrics.band_of(c[team], th, metrics.BANDS["pressure"])}
            for key, c in counts.items()
        ]
        by_label = {w["window"]: w for w in windows}
        changes = []
        for ch in regimes[team]["changes"]:
            period, start = _window_key(ch["at"])
            changes.append({
                "window": ch["at"], "from": ch["from"], "to": ch["to"], "pressures": by_label[ch["at"]]["pressures"],
                "sentence": _pressure_sentence(clubs[team]["name"], window_text(period, start), ch["from"], ch["to"],
                                               by_label[ch["at"]]["pressures"]),
            })
        pressure[team] = {"windows": windows, "changes": changes}
    tempo = metrics.tempo_bands(view, model)
    tempo_changes = []
    for prev, cur in zip(tempo, tempo[1:]):
        if prev["band"] != cur["band"]:
            period, start = _window_key(cur["window"])
            tempo_changes.append({
                "window": cur["window"], "from": prev["band"], "to": cur["band"],
                "on_ball_actions": cur["on_ball_actions"], "on_ball_actions_before": prev["on_ball_actions"],
                "sentence": _tempo_sentence(window_text(period, start), prev["band"], cur["band"],
                                            cur["on_ball_actions"], prev["on_ball_actions"]),
            })
    return {
        "explain": {
            "control_vs_chaos": "Low index: one side keeps the ball in long possessions (control). High: short possessions and open-play turnovers (chaos).",
            "pressure": "Pressure events a club made in each 5-minute window, banded low, medium or high by frozen training terciles.",
            "tempo": "On-ball actions by both clubs in each 5-minute window, banded slow, normal or fast by frozen training terciles.",
        },
        "control_vs_chaos": {
            "windows": [{"window": w["window"], "period": w["period"], "start_minute": w["start_minute"],
                         "index_permille": w["index_permille"], "band": w["band"]} for w in chaos],
            "changes": chaos_changes,
        },
        "pressure": pressure,
        "tempo": {"windows": [dict(w) for w in tempo], "changes": tempo_changes},
    }


# ----------------------------------------------------------------- milestones (season facts)


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th, 11th, 12th, 13th, 21st: digits plus an English suffix."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _claim(ctype: str, stat: str | None, subject: str, value: int) -> dict[str, Any]:
    out: dict[str, Any] = {"type": ctype, "value": value, "event_ids": [], "subject": subject}
    if stat is not None:
        out["stat"] = stat
    return out


def season_facts(season: Season) -> recompute.SeasonFacts:
    return recompute.build_season_facts(
        season.season, [c.club_id for c in season.league.clubs], ((m.meta, m.events, m.lineups) for m in season.matches)
    )


def milestones_for_match(
    facts: recompute.SeasonFacts, match_id: str, club_names: Mapping[str, str], player_names: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Citable milestone facts for one season match, each with a sentence and its Fact Gate claims.

    Player facts (from that match's players): goals and assists to date when the player scored or
    assisted in it. Appearances and minutes are left out on purpose: the engine starts the same
    eleven every match, so they would list every starter. Club facts: the table position and points after the matchday, and every current streak of 3 or more
    (unbeaten and winless only when longer than the win or loss run). Values are recomputed by
    agon.governance.recompute "as of" the matchday, exactly as the Fact Gate checks them.
    """
    record = facts.match_by_id(match_id)
    if record is None:
        raise ValueError(f"{match_id} is not in season {facts.season}")
    md = record.matchday
    totals = recompute.player_season_totals(facts, md)
    out: list[dict[str, Any]] = []

    def fact(kind: str, subject: str, sentence: str, claims: list[dict[str, Any]]) -> None:
        out.append({"kind": kind, "subject": subject, "matchday": md, "match_id": match_id, "sentence": sentence, "claims": claims})

    for line in sorted(record.players, key=lambda p: (p.club_id != record.home_id, p.player_id)):
        pid, name, t = line.player_id, player_names[line.player_id], totals[line.player_id]
        if line.goals:
            n = t["goals"]
            text = f"{name} scored a 1st goal of the season." if n == 1 else f"{name} has {n} goals this season."
            fact("first_goal" if n == 1 else "season_goals", pid, text, [_claim("player_season_total", "goals", pid, n)])
        if line.assists:
            n = t["assists"]
            text = f"{name} made a 1st assist of the season." if n == 1 else f"{name} has {n} assists this season."
            fact("first_assist" if n == 1 else "season_assists", pid, text, [_claim("player_season_total", "assists", pid, n)])
    table = {row["club_id"]: row for row in recompute.league_table(facts, md)}
    for club in (record.home_id, record.away_id):
        name, row = club_names[club], table[club]
        points = f"{row['points']} point" + ("" if row["points"] == 1 else "s")
        fact("table", club, f"{name} are {ordinal(row['position'])} in the table on {points}.",
             [_claim("table_position", "position", club, row["position"]), _claim("table_position", "points", club, row["points"])])
        runs = recompute.current_streaks(facts, club, md)
        streak_text = {
            "win": f"{name} have won {runs['win']} in a row.",
            "unbeaten": f"{name} are unbeaten in {runs['unbeaten']}.",
            "loss": f"{name} have lost {runs['loss']} in a row.",
            "winless": f"{name} are without a win in {runs['winless']}.",
            "scored": f"{name} have scored in {runs['scored']} straight matches.",
            "clean_sheet": f"{name} have kept {runs['clean_sheet']} clean sheets in a row.",
        }
        for stat in ("win", "unbeaten", "loss", "winless", "scored", "clean_sheet"):
            n = runs[stat]
            if n < STREAK_MIN:
                continue
            if (stat == "unbeaten" and n == runs["win"]) or (stat == "winless" and n == runs["loss"]):
                continue
            fact("club_streak", club, streak_text[stat], [_claim("season_streak", stat, club, n)])
    return out


def _season_names(season: Season) -> tuple[dict[str, str], dict[str, str]]:
    clubs = {c.club_id: c.name for c in season.league.clubs}
    players = {p.player_id: p.name for c in season.league.clubs for p in c.players}
    return clubs, players


def milestones_payload(season: Season) -> dict[str, Any]:
    facts = season_facts(season)
    clubs, players = _season_names(season)
    days: dict[int, list[dict[str, Any]]] = {}
    for m in season.matches:
        days.setdefault(m.matchday, []).append({
            "match_id": m.meta["match_id"], "home": m.meta["home"], "away": m.meta["away"],
            "score": dict(m.meta["score"]),
            "facts": milestones_for_match(facts, m.meta["match_id"], clubs, players),
        })
    return {
        "kind": "milestones",
        "schema": MILESTONES_SCHEMA,
        "season": season.season,
        "generator": GENERATOR_VERSION,
        "label": SEASON_MILESTONES_LABEL,
        "rules": milestone_rules(),
        "matchdays": [{"matchday": md, "matches": games} for md, games in sorted(days.items())],
    }


def milestone_rules() -> list[str]:
    return [
        "Season totals are counted through the matchday of the match (agon.governance.recompute, the Fact Gate's definitions).",
        "Goals and assists to date are listed for players who scored or assisted in the match (appearances and minutes are not: the engine starts the same eleven every match).",
        f"Club streaks of {STREAK_MIN} or more; unbeaten and winless only when longer than the win or loss run.",
        "Each fact carries its claims (player_season_total, season_streak or table_position), so a writer can cite it and the gate re-checks it.",
    ]


# ----------------------------------------------------------------- one published match


TRUNCATED_NOTE = (
    "The served event log is the first part of the season match only (a truncated replay), so no "
    "season milestones are written for it."
)


@lru_cache(maxsize=8)
def _season(number: int, matchdays: int | None = None) -> Season:
    """A season (or its first matchdays), simulated once per process: deploy and tests stamp repeatedly."""
    return build_season(number, matchdays=matchdays)


def _published_season_match(seed: int, served: bytes) -> tuple[Season, str, bool]:
    """(season to the published match's matchday, match id, whether the served log is the whole match).

    Refuses a log that is neither the season match nor its first part.
    """
    matchday = fixture_list()[DEFAULT_MATCH_INDEX][0]
    season = _season(season_seed(seed), matchday)
    m = season.match(DEFAULT_MATCH_INDEX)
    full = m.events_ndjson()
    if served == full:
        return season, m.meta["match_id"], True
    if served and full.startswith(served) and served.endswith(b"\n"):
        return season, m.meta["match_id"], False
    raise ValueError(f"seed {seed}: the served event log is not season {season.season} match {DEFAULT_MATCH_INDEX}")


def metrics_payload(seed: int, data_dir: str) -> dict[str, Any]:
    raw, events, world = load_served(data_dir, seed)
    home, away = world["home"], world["away"]
    view = MatchView.build(events, home, away, world["match_id"])
    model = load_model()
    clubs, players = _names(world)
    p = pack(view, model)
    out_players = []
    for row in p["players"]:
        who = players[row["player_id"]]
        out_players.append({"player_id": row["player_id"], "name": who["name"], "shirt": who["shirt"],
                            "position": who["position"], **{k: row[k] for k in ("club_id", *PLAYER_KEYS)}})
    rows = []
    for r in p["passes"]:
        x, y, ex, ey = to_fixed_frame(r, home)
        rows.append([r["event_id"], r["team"], r["player"], r["receiver"], r["period"], r["minute"], r["second"],
                     x, y, ex, ey, r["outcome"], r["under_pressure"], r["distance_m"], r["completion_ppm"],
                     r["difficulty"], r["progressive"], r["into_box"], r["cross_into_box"], r["deep_completion"],
                     r["key_pass"], r["xa_ppm"]])
    season, match_id, whole = _published_season_match(seed, raw)
    s_clubs, s_players = _season_names(season)
    return {
        "kind": "metrics",
        "schema": METRICS_SCHEMA,
        "seed": seed,
        "match_id": match_id,
        "generator": GENERATOR_VERSION,
        "interpret": INTERPRET_VERSION,
        "pack": PACK_VERSION,
        "model_sha256": model_sha256(),
        "events_sha256": sha256_hex(raw),
        "label": LABEL,
        "home": home,
        "away": away,
        "clubs": [{"club_id": cid, **clubs[cid]} for cid in (home, away)],
        "definitions": [{"key": k, **METRICS[k]} for k in METRICS],
        "teams": {t: {k: p["teams"][t][k] for k in TEAM_KEYS} for t in (home, away)},
        "series": {"bins": list(p["bins"]), "keys": list(SERIES_KEYS), **{t: p["series"][t] for t in (home, away)}},
        "players": out_players,
        "passes": {"frame": FIXED_FRAME, "columns": list(PASS_COLUMNS), "rows": rows},
        "rhythm": rhythm(view, clubs, model),
        "milestones": milestones_for_match(season_facts(season), match_id, s_clubs, s_players) if whole else [],
        "milestones_note": None if whole else TRUNCATED_NOTE,
        "milestones_provenance": MILESTONES_PROVENANCE,
        "citable": {"claim_type": "stat", "stats": [dict(c) for c in CITABLE], "note": NOT_CITABLE_NOTE},
    }


# ----------------------------------------------------------------- writing


def file_bytes(payload: Any) -> bytes:
    return canonical_bytes(payload) + b"\n"


def committed_whatif(seed: int, served_sha256: str, set_dir: str | None = None) -> tuple[dict[str, Any], bytes]:
    """The committed What-If file of a published seed, refused unless it describes this log, generator and model."""
    from agon.whatif.scenarios import REGENERATE, SET_DIR

    path = os.path.join(set_dir or SET_DIR, f"whatif-{seed}.json")
    if not os.path.isfile(path):
        raise ValueError(f"no committed What-If set for seed {seed}: {REGENERATE}")
    raw = _read_bytes(path)
    body = json.loads(raw.decode("ascii"))
    stale = []
    if body.get("events_sha256") != served_sha256:
        stale.append("the served event log")
    if body.get("generator") != GENERATOR_VERSION:
        stale.append("the generator version")
    if body.get("model_sha256") != model_sha256():
        stale.append("the interpret model")
    if stale:
        raise ValueError(f"the committed What-If set for seed {seed} does not match {', '.join(stale)}: {REGENERATE}")
    if raw != file_bytes(body):
        raise ValueError(f"the committed What-If set for seed {seed} is not canonical JSON with a final LF")
    return body, raw


def committed_brief(seed: int, whatif_raw: bytes, brief_dir: str | None = None) -> dict[str, Any] | None:
    """The web brief of a published seed, or None when no brief is committed for it.

    A committed brief must name the sha256 of the What-If file it was drafted from, and every accepted
    text must pass agon.whatif.brief.check_brief again against that file; otherwise the deploy stops.
    Refused drafts stay in the repo as evidence and are never published.
    """
    from agon.whatif.brief import BRIEF_LABEL, check_brief
    from agon.whatif.scenarios import BRIEF_DIR

    path = os.path.join(brief_dir or BRIEF_DIR, f"brief-{seed}.json")
    if not os.path.isfile(path):
        return None
    committed = json.loads(_read_bytes(path).decode("ascii"))
    if committed.get("whatif_sha256") != sha256_hex(whatif_raw):
        raise ValueError(f"brief-{seed}.json was drafted from another What-If file; draft it again with whatif-brief.yml")
    whatif = json.loads(whatif_raw.decode("ascii"))
    scenarios = {sc["scenario_id"]: sc for sc in whatif["scenarios"]}
    briefs = []
    for row in committed.get("briefs", []):
        if row.get("status") != "accepted":
            continue
        sc = scenarios.get(row.get("scenario_id"))
        if sc is None:
            raise ValueError(f"brief-{seed}.json names a scenario the What-If file does not hold: {row.get('scenario_id')}")
        reasons = check_brief(row.get("text") or "", sc)
        if reasons:
            raise ValueError(f"brief-{seed}.json {row['scenario_id']}: " + "; ".join(reasons))
        briefs.append({"scenario_id": row["scenario_id"], "text": row["text"], "model": row.get("model", ""),
                       "prompt_sha256": row.get("prompt_sha256", "")})
    return {
        "kind": "brief",
        "schema": BRIEF_SCHEMA,
        "seed": seed,
        "events_sha256": whatif["events_sha256"],
        "whatif_sha256": sha256_hex(whatif_raw),
        "deployment": committed.get("deployment", ""),
        "label": BRIEF_LABEL,
        "briefs": briefs,
    }


def build_intel_web(data_dir: str, seeds: Sequence[int], season_number: int = MILESTONE_SEASON) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return (manifest, {file name: bytes}) without touching the disk. Refuses invalid content.

    A What-If file is included for every seed whose served log is the whole published match; a
    truncated replay (the development fixture) has none, because the replays describe the whole match.
    """
    payloads: dict[str, dict[str, Any]] = {}
    raw_files: dict[str, bytes] = {}
    for seed in sorted(set(seeds)):
        metrics_body = metrics_payload(seed, data_dir)
        payloads[f"metrics-{seed}.json"] = metrics_body
        if metrics_body["milestones_note"] is None:
            body, raw = committed_whatif(seed, metrics_body["events_sha256"])
            payloads[f"whatif-{seed}.json"] = body
            raw_files[f"whatif-{seed}.json"] = raw
            brief = committed_brief(seed, raw)
            if brief is not None:
                payloads[f"brief-{seed}.json"] = brief
    payloads[f"milestones-season-{season_number}.json"] = milestones_payload(_season(season_number))
    problems = [p for name, body in payloads.items() for p in validate_payload(name, body)]
    if problems:
        raise ValueError("refusing invalid intel files: " + "; ".join(problems))
    data = {name: raw_files.get(name) or file_bytes(body) for name, body in payloads.items()}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generator": GENERATOR_VERSION,
        "interpret": INTERPRET_VERSION,
        "pack": PACK_VERSION,
        "model_sha256": model_sha256(),
        "files": [
            {"name": name, "kind": body["kind"], "seed": body.get("seed"), "sha256": sha256_hex(data[name]),
             "events_sha256": body.get("events_sha256")}
            for name, body in payloads.items()
        ],
    }
    return manifest, data


def write_intel_web(out_dir: str, data_dir: str, seeds: Sequence[int]) -> dict[str, Any]:
    """Write every intel file and manifest.json under out_dir, binary, LF only. Returns the manifest."""
    manifest, data = build_intel_web(data_dir, seeds)
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("refusing an invalid intel manifest: " + "; ".join(problems))
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


def _int_or_none(value: Any) -> bool:
    return value is None or _is_int(value)


def _keys(obj: Any, expected: frozenset[str] | set[str], where: str) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{where} must be an object"]
    if set(obj) != set(expected):
        return [f"{where} keys must be exactly {', '.join(sorted(expected))}"]
    return []


MANIFEST_KEYS = frozenset({"schema", "generator", "interpret", "pack", "model_sha256", "files"})
MANIFEST_FILE_KEYS = frozenset({"name", "kind", "seed", "sha256", "events_sha256"})
FILE_KINDS = frozenset({"metrics", "milestones", "whatif", "brief"})


def validate_manifest(manifest: Any) -> list[str]:
    problems = _keys(manifest, MANIFEST_KEYS, "manifest")
    if problems:
        return problems
    if manifest["schema"] != MANIFEST_SCHEMA:
        problems.append(f"manifest.schema must be {MANIFEST_SCHEMA}")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        return problems + ["manifest.files must be a non-empty list"]
    seen = set()
    for i, entry in enumerate(manifest["files"]):
        where = f"manifest.files[{i}]"
        bad = _keys(entry, MANIFEST_FILE_KEYS, where)
        if bad:
            problems.extend(bad)
            continue
        name = entry["name"]
        if not isinstance(name, str) or "/" in name or "\\" in name or name in seen:
            problems.append(f"{where}.name must be a bare, unique file name")
            continue
        seen.add(name)
        if entry["kind"] not in FILE_KINDS:
            problems.append(f"{where}.kind must be one of {', '.join(sorted(FILE_KINDS))}")
        if not isinstance(entry["sha256"], str) or not SHA256_HEX.match(entry["sha256"]):
            problems.append(f"{where}.sha256 must be 64 lowercase hex characters")
        if entry["kind"] == "milestones":
            if entry["seed"] is not None or entry["events_sha256"] is not None or not MILESTONES_NAME.match(name):
                problems.append(f"{where}: a milestones file is milestones-season-<n>.json with no seed and no events_sha256")
        else:
            seed_ok = _is_int(entry["seed"]) and entry["seed"] >= 0
            if not seed_ok or not isinstance(entry["events_sha256"], str) or not SHA256_HEX.match(entry["events_sha256"]):
                problems.append(f"{where}: a match file needs an integer seed and an events_sha256")
            elif name != f"{entry['kind']}-{entry['seed']}.json":
                problems.append(f"{where}.name must be {entry['kind']}-{entry['seed']}.json")
    return problems


TEAM_KEYS_SET = frozenset(TEAM_KEYS)
PLAYER_ROW_KEYS = frozenset({"player_id", "name", "shirt", "position", "club_id", *PLAYER_KEYS})
DEFINITION_KEYS = frozenset({"key", "label", "unit", "scope", "better", "description", "rule"})
METRICS_KEYS = frozenset({
    "kind", "schema", "seed", "match_id", "generator", "interpret", "pack", "model_sha256", "events_sha256",
    "label", "home", "away", "clubs", "definitions", "teams", "series", "players", "passes", "rhythm",
    "milestones", "milestones_note", "milestones_provenance", "citable",
})
MILESTONE_KEYS = frozenset({"kind", "subject", "matchday", "match_id", "sentence", "claims"})
CLAIM_KEYS = frozenset({"type", "value", "event_ids", "subject", "stat"})
MILESTONE_CLAIM_TYPES = frozenset({"player_season_total", "season_streak", "table_position"})
# Values that may be None: a ratio with nothing to divide by, or a mean of nothing.
NULLABLE_TEAM_KEYS = frozenset({
    "field_tilt_permille", "ppda_centi", "defensive_action_height_dm", "counter_press_rate_permille",
    "pass_accuracy_permille", "mean_pass_distance_dm", "mean_pass_difficulty_centi",
})
SIGNED_KEYS = frozenset({"xt_added_ppm"})
PASS_COLUMN_TYPES: dict[str, str] = {
    "event_id": "str", "club_id": "str", "player_id": "str", "receiver_id": "str?", "period": "int",
    "minute": "int", "second": "int", "x_dm": "int", "y_dm": "int", "end_x_dm": "int", "end_y_dm": "int",
    "outcome": "str", "under_pressure": "bool", "distance_m": "int", "completion_ppm": "int",
    "difficulty": "int", "progressive": "bool", "into_box": "bool", "cross_into_box": "bool",
    "deep_completion": "bool", "key_pass": "bool", "xa_ppm": "int",
}


def _check_numbers(row: Mapping[str, Any], keys: Sequence[str], where: str) -> list[str]:
    out = []
    for k in keys:
        v = row[k]
        if v is None and k in NULLABLE_TEAM_KEYS:
            continue
        if not _is_int(v) or (v < 0 and k not in SIGNED_KEYS):
            out.append(f"{where}.{k} must be {'an integer' if k in SIGNED_KEYS else 'a non-negative integer'}")
    return out


def _cell_ok(kind: str, v: Any) -> bool:
    if kind == "str":
        return isinstance(v, str)
    if kind == "str?":
        return v is None or isinstance(v, str)
    if kind == "bool":
        return isinstance(v, bool)
    return _is_int(v)


def _validate_milestone(m: Any, where: str) -> list[str]:
    problems = _keys(m, MILESTONE_KEYS, where)
    if problems:
        return problems
    if not isinstance(m["sentence"], str) or not m["sentence"]:
        problems.append(f"{where}.sentence must be a non-empty string")
    if not isinstance(m["claims"], list) or not m["claims"]:
        return problems + [f"{where}.claims must be a non-empty list"]
    for j, c in enumerate(m["claims"]):
        cw = f"{where}.claims[{j}]"
        if not isinstance(c, dict) or not set(c) <= CLAIM_KEYS or not {"type", "value", "event_ids", "subject"} <= set(c):
            problems.append(f"{cw} must have type, value, event_ids, subject and optionally stat")
            continue
        if c["type"] not in MILESTONE_CLAIM_TYPES or not _is_int(c["value"]):
            problems.append(f"{cw} must be a {', '.join(sorted(MILESTONE_CLAIM_TYPES))} claim with an integer value")
    return problems


def _validate_metrics(body: Mapping[str, Any]) -> list[str]:
    problems = _keys(body, METRICS_KEYS, "metrics")
    if problems:
        return problems
    if body["schema"] != METRICS_SCHEMA:
        problems.append(f"metrics.schema must be {METRICS_SCHEMA}")
    if not isinstance(body["events_sha256"], str) or not SHA256_HEX.match(body["events_sha256"]):
        problems.append("metrics.events_sha256 must be 64 lowercase hex characters")
    clubs = [body["home"], body["away"]]
    if [c.get("club_id") for c in body["clubs"]] != clubs:
        problems.append("metrics.clubs must list home then away")
    defs = body["definitions"]
    if not isinstance(defs, list) or not defs:
        problems.append("metrics.definitions must be a non-empty list")
    else:
        for i, d in enumerate(defs):
            problems.extend(_keys(d, DEFINITION_KEYS, f"metrics.definitions[{i}]"))
    if sorted(body["teams"]) != sorted(clubs):
        problems.append("metrics.teams must hold exactly the two clubs")
    else:
        for team in clubs:
            bad = _keys(body["teams"][team], TEAM_KEYS_SET, f"metrics.teams.{team}")
            problems.extend(bad or _check_numbers(body["teams"][team], TEAM_KEYS, f"metrics.teams.{team}"))
    series = body["series"]
    if set(series) != {"bins", "keys", *clubs}:
        problems.append("metrics.series keys must be bins, keys and the two clubs")
    else:
        width = len(series["bins"])
        for team in clubs:
            if set(series[team]) != set(series["keys"]):
                problems.append(f"metrics.series.{team} must hold every key in series.keys")
                continue
            for k in series["keys"]:
                vals = series[team][k]
                if not isinstance(vals, list) or len(vals) != width or not all(_int_or_none(v) for v in vals):
                    problems.append(f"metrics.series.{team}.{k} must be {width} integers or nulls")
    for i, row in enumerate(body["players"]):
        where = f"metrics.players[{i}]"
        bad = _keys(row, PLAYER_ROW_KEYS, where)
        if bad:
            problems.extend(bad)
            continue
        if row["club_id"] not in clubs:
            problems.append(f"{where}.club_id must be one of the two clubs")
        problems.extend(_check_numbers(row, PLAYER_KEYS, where))
    passes = body["passes"]
    if _keys(passes, {"frame", "columns", "rows"}, "metrics.passes") or passes["frame"] != FIXED_FRAME:
        problems.append(f"metrics.passes must hold frame {FIXED_FRAME}, columns and rows")
    elif passes["columns"] != list(PASS_COLUMNS):
        problems.append("metrics.passes.columns must be the documented column list")
    else:
        kinds = [PASS_COLUMN_TYPES[c] for c in PASS_COLUMNS]
        for i, r in enumerate(passes["rows"]):
            if not isinstance(r, list) or len(r) != len(kinds) or not all(_cell_ok(k, v) for k, v in zip(kinds, r)):
                problems.append(f"metrics.passes.rows[{i}] does not match the column types")
                break
    rh = body["rhythm"]
    if _keys(rh, {"explain", "control_vs_chaos", "pressure", "tempo"}, "metrics.rhythm"):
        problems.append("metrics.rhythm must hold explain, control_vs_chaos, pressure and tempo")
    else:
        changes = list(rh["control_vs_chaos"]["changes"]) + list(rh["tempo"]["changes"])
        changes += [c for team in clubs for c in rh["pressure"][team]["changes"]]
        if any(not isinstance(c.get("sentence"), str) or not c["sentence"] for c in changes):
            problems.append("metrics.rhythm: every change needs a sentence")
    for i, m in enumerate(body["milestones"]):
        problems.extend(_validate_milestone(m, f"metrics.milestones[{i}]"))
    note = body["milestones_note"]
    if note is not None and (not isinstance(note, str) or body["milestones"]):
        problems.append("metrics.milestones_note is null, or a reason with no milestones")
    stats = [c.get("stat") for c in body["citable"].get("stats", [])]
    unknown = [s for s in stats if s not in recompute.STAT_KEYS]
    if unknown:
        problems.append(f"metrics.citable names statistics the Fact Gate does not know: {', '.join(unknown)}")
    return problems


def _validate_milestones(body: Mapping[str, Any]) -> list[str]:
    problems = _keys(body, {"kind", "schema", "season", "generator", "label", "rules", "matchdays"}, "milestones")
    if problems:
        return problems
    if body["schema"] != MILESTONES_SCHEMA:
        problems.append(f"milestones.schema must be {MILESTONES_SCHEMA}")
    if not _is_int(body["season"]) or body["season"] < 1:
        problems.append("milestones.season must be a positive integer")
    for d in body["matchdays"]:
        for g in d["matches"]:
            for i, m in enumerate(g["facts"]):
                problems.extend(_validate_milestone(m, f"milestones.{g['match_id']}.facts[{i}]"))
    return problems


WHATIF_KEYS = frozenset({
    "kind", "schema", "seed", "match_id", "generator", "interpret", "model_sha256", "events_sha256", "label",
    "method", "k", "home", "away", "clubs", "actual_score", "timeline_marks", "scenarios",
})
SCENARIO_KEYS = frozenset({
    "scenario_id", "title", "question", "engine_note", "side", "club_id", "params", "k", "prefix", "triggered",
    "outcomes", "deltas", "xg_timeline", "summary", "command",
})
SHARE_KEYS = frozenset({"count", "ppm", "lo_ppm", "hi_ppm"})
DELTA_KEYS = frozenset({
    "metric", "side", "unit", "scale", "dropped", "pairs", "baseline_mean", "scenario_mean", "delta_mean",
    "delta_lo", "delta_hi", "unpaired_half",
})
WHATIF_LABEL = "Synthetic engine, paired replays, not a prediction about real football."
_DASHES = (chr(0x2013), chr(0x2014))


def _validate_scenario(sc: Any, k: int, marks: list[str], where: str) -> list[str]:
    problems = _keys(sc, SCENARIO_KEYS, where)
    if problems:
        return problems
    if sc["k"] != k:
        problems.append(f"{where}.k must equal the file's k")
    prefix = sc["prefix"]
    if not isinstance(prefix, dict) or prefix.get("differing_events") != 0:
        problems.append(f"{where}.prefix.differing_events must be 0: the pre-scenario play must be the published match")
    for arm in ("baseline", "scenario"):
        shares = sc["outcomes"][arm]
        if set(shares) != {"win", "draw", "loss"}:
            problems.append(f"{where}.outcomes.{arm} must hold win, draw and loss")
            continue
        for kind, sh in shares.items():
            ok = not _keys(sh, SHARE_KEYS, "share") and all(_is_int(sh[x]) for x in SHARE_KEYS)
            if not ok or not 0 <= sh["lo_ppm"] <= sh["ppm"] <= sh["hi_ppm"] <= 1_000_000:
                problems.append(f"{where}.outcomes.{arm}.{kind} must be integers with lo <= share <= hi in ppm")
        if sum(sh.get("count", 0) for sh in shares.values()) != k:
            problems.append(f"{where}.outcomes.{arm} counts must add up to k")
    for i, d in enumerate(sc["deltas"]):
        dw = f"{where}.deltas[{i}]"
        if _keys(d, DELTA_KEYS, dw):
            problems.append(f"{dw} keys must be exactly {', '.join(sorted(DELTA_KEYS))}")
            continue
        values = [d[x] for x in ("baseline_mean", "scenario_mean", "delta_mean", "delta_lo", "delta_hi", "unpaired_half")]
        if not all(_int_or_none(v) for v in values) or not _is_int(d["pairs"]) or d["pairs"] + d["dropped"] != k:
            problems.append(f"{dw} must hold integers or nulls, and pairs plus dropped must equal k")
        elif d["delta_mean"] is not None and not d["delta_lo"] <= d["delta_mean"] <= d["delta_hi"]:
            problems.append(f"{dw} must have delta_lo <= delta_mean <= delta_hi")
    tl = sc["xg_timeline"]
    if tl.get("marks") != marks:
        problems.append(f"{where}.xg_timeline.marks must equal timeline_marks")
    for arm in ("baseline", "scenario"):
        for side in ("for", "against"):
            vals = tl.get(arm, {}).get(side)
            if not isinstance(vals, list) or len(vals) != len(marks) or not all(_is_int(v) and v >= 0 for v in vals):
                problems.append(f"{where}.xg_timeline.{arm}.{side} must be {len(marks)} non-negative integers")
    text = " ".join(str(sc[x]) for x in ("title", "question", "engine_note", "summary"))
    if not sc["summary"] or any(ch in text for ch in _DASHES):
        problems.append(f"{where}: summary must be a sentence and the texts must not use long dashes")
    return problems


def _validate_whatif(body: Mapping[str, Any]) -> list[str]:
    problems = _keys(body, WHATIF_KEYS, "whatif")
    if problems:
        return problems
    if body["label"] != WHATIF_LABEL:
        problems.append(f"whatif.label must be {WHATIF_LABEL!r}")
    if not isinstance(body["events_sha256"], str) or not SHA256_HEX.match(body["events_sha256"]):
        problems.append("whatif.events_sha256 must be 64 lowercase hex characters")
    if not _is_int(body["k"]) or body["k"] < 2:
        return problems + ["whatif.k must be an integer of at least 2"]
    if not isinstance(body["scenarios"], list) or not body["scenarios"]:
        return problems + ["whatif.scenarios must be a non-empty list"]
    marks = body["timeline_marks"]
    for i, sc in enumerate(body["scenarios"]):
        problems.extend(_validate_scenario(sc, body["k"], marks, f"whatif.scenarios[{i}]"))
    return problems


BRIEF_KEYS = frozenset({"kind", "schema", "seed", "events_sha256", "whatif_sha256", "deployment", "label", "briefs"})
BRIEF_ROW_KEYS = frozenset({"scenario_id", "text", "model", "prompt_sha256"})


def _validate_brief(body: Mapping[str, Any]) -> list[str]:
    from agon.whatif.brief import BRIEF_LABEL

    problems = _keys(body, BRIEF_KEYS, "brief")
    if problems:
        return problems
    if body["schema"] != BRIEF_SCHEMA or body["label"] != BRIEF_LABEL:
        problems.append(f"brief must be {BRIEF_SCHEMA} with the label {BRIEF_LABEL!r}")
    for i, row in enumerate(body["briefs"]):
        if _keys(row, BRIEF_ROW_KEYS, "row") or not isinstance(row["text"], str) or not row["text"]:
            problems.append(f"brief.briefs[{i}] must hold scenario_id, text, model and prompt_sha256")
    return problems


def validate_payload(name: str, body: Any) -> list[str]:
    """Problems with one intel file's content; empty means it matches the contract."""
    if not isinstance(body, dict):
        return [f"{name} must hold a JSON object"]
    try:
        if METRICS_NAME.match(name):
            if body.get("kind") != "metrics" or str(body.get("seed")) != METRICS_NAME.match(name).group(1):
                return [f"{name} must be the metrics file of seed {METRICS_NAME.match(name).group(1)}"]
            return _validate_metrics(body)
        if BRIEF_NAME.match(name):
            if body.get("kind") != "brief" or str(body.get("seed")) != BRIEF_NAME.match(name).group(1):
                return [f"{name} must be the brief file of seed {BRIEF_NAME.match(name).group(1)}"]
            return _validate_brief(body)
        if WHATIF_NAME.match(name):
            if body.get("kind") != "whatif" or str(body.get("seed")) != WHATIF_NAME.match(name).group(1):
                return [f"{name} must be the What-If file of seed {WHATIF_NAME.match(name).group(1)}"]
            return _validate_whatif(body)
        if MILESTONES_NAME.match(name):
            if body.get("kind") != "milestones" or str(body.get("season")) != MILESTONES_NAME.match(name).group(1):
                return [f"{name} must be the milestones file of season {MILESTONES_NAME.match(name).group(1)}"]
            return _validate_milestones(body)
    except (AttributeError, KeyError, TypeError) as exc:
        return [f"{name}: malformed ({type(exc).__name__}: {exc})"]
    return [f"{name} is not an intel file"]


def verify_intel_dir(intel_dir: str | os.PathLike[str], data_dir: str | os.PathLike[str]) -> int:
    """Re-hash and shape-check a written intel folder against the served data; unlisted files fail.

    Raises ValueError naming the first broken rule. Returns the number of files verified.
    """
    root, data = os.fspath(intel_dir), os.fspath(data_dir)
    manifest_path = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise ValueError(f"{manifest_path} does not exist")
    manifest = json.loads(_read_bytes(manifest_path).decode("ascii"))
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("intel manifest breaks the contract: " + "; ".join(problems))
    for entry in manifest["files"]:
        path = os.path.join(root, entry["name"])
        if not os.path.isfile(path):
            raise ValueError(f"intel manifest lists {entry['name']}, which does not exist")
        raw = _read_bytes(path)
        if sha256_hex(raw) != entry["sha256"]:
            raise ValueError(f"intel/{entry['name']}: sha256 {sha256_hex(raw)}, manifest says {entry['sha256']}")
        body = json.loads(raw.decode("ascii"))
        problems = validate_payload(entry["name"], body)
        if problems:
            raise ValueError(f"intel/{entry['name']} breaks the contract: " + "; ".join(problems))
        if entry["events_sha256"] is not None:
            served = os.path.join(data, f"{entry['seed']}.ndjson")
            if not os.path.isfile(served):
                raise ValueError(f"intel/{entry['name']} describes seed {entry['seed']}, which the data folder does not serve")
            if sha256_hex(_read_bytes(served)) != entry["events_sha256"] or body.get("events_sha256") != entry["events_sha256"]:
                raise ValueError(f"intel/{entry['name']} does not describe the served event log of seed {entry['seed']}")
    listed = {entry["name"] for entry in manifest["files"]}
    present = sorted(
        os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/") for d, _, names in os.walk(root) for n in names
    )
    stray = [rel for rel in present if rel != MANIFEST_NAME and rel not in listed]
    if stray:
        raise ValueError(f"intel files not listed in the manifest: {', '.join(stray)}")
    return len(listed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webintel", description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, help="the served data folder (python -m agon.webdata --out)")
    parser.add_argument("--seeds", required=True, help="published seeds, for example 1,2,3")
    parser.add_argument("--out", required=True, help="the intel folder, for example web/intel")
    args = parser.parse_args(argv)
    manifest = write_intel_web(args.out, args.data, parse_seeds(args.seeds))
    count = verify_intel_dir(args.out, args.data)
    for entry in manifest["files"]:
        sys.stdout.write(f"{entry['name']} sha256 {entry['sha256']}\n")
    sys.stdout.write(f"intel files {count}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

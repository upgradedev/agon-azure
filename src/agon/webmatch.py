"""Write and validate the match analysis files the console draws (the match web data contract).

Usage:
    python -m agon.webmatch --data web/data --out build/match

At deploy, ci/stamp_web.py calls `write_match_web` on the data folder it has just verified, then
`verify_match_dir`. Every number the Match Centre draws beyond the replay itself comes from here:
the browser picks values and draws them, it never computes a football number.

Source. The exact event log and names file the site serves (data/<seed>.ndjson and
data/<seed>.world.json), so the analysis always describes the bytes a visitor replays; each match
file carries `events_sha256` and `world_sha256`, and the browser refuses a match file whose
`events_sha256` differs from the data manifest's entry for that seed. The starting elevens come
from the seed's world (`agon.synth.world`, the engine's fixed 4-3-3), which the event log does not
carry. The numbers are computed by `agon.interpret` (xG, xT, PPDA, control vs chaos, pressure,
tempo, momentum, win probability, the carry speed proxy) and by `agon.governance.recompute` (the
counts the Fact Gate checks: shots, shots on target, corners, fouls, cards, pass accuracy, player
minutes, goals and assists).

Contract. One folder (match/), canonical JSON (sorted keys, ASCII, integers, strings, booleans and
null only, no floats) plus a final LF:

    manifest.json   {"schema", "generator", "interpret", "model_sha256",
                     "files": [{"name", "seed", "sha256", "events_sha256"}]}, one entry per seed
    <seed>.json     one match: clubs, axis, score, goals, timeline, live (per minute), win
                    probability and turning points, xG race, momentum, 5-minute bands, shots,
                    territory, team stats, players, the star player and the rules that pick it

Rules stated in the file and on the page (`rules`): possession is each club's share of passes
attempted; a big chance is a shot whose modelled xG is at least 0.2 (agon.interpret's candidate
threshold); the star player is the most goals plus assists, then xG, then xT added, then the
lower shirt number, and there is none when nobody scored or assisted.

Units follow docs/INTERPRET.md: ppm for probabilities, xG and xT; per mille for shares and the
control vs chaos index; centi-units for PPDA; tenths of km/h for speed. The time axis is match
seconds, with the second half shifted to start where the first half ended (`axis.half_offset_s`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Mapping, Sequence

from agon.governance import recompute
from agon.interpret import INTERPRET_VERSION, MatchView, load_model, model_sha256
from agon.interpret import kinematics, metrics, wp
from agon.interpret._fixed import ratio_round
from agon.interpret.features import pass_difficulty, pass_distance_m, xg_ppm, xt_added_ppm
from agon.interpret.frame import ON_BALL_TYPES, minute_label, window5, window_label
from agon.synth.engine import GENERATOR_VERSION
from agon.synth.schema import Event, canonical_bytes, sha256_hex, to_fixed_frame
from agon.synth.world import build_world

MATCH_DIR = "match"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "agon.webmatch-manifest/1"
MATCH_SCHEMA = "agon.webmatch/1"
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
FILE_NAME = re.compile(r"^[0-9]{1,9}\.json$")
GRID_X, GRID_Y = 16, 12
PITCH_X_DM, PITCH_Y_DM = 1050, 680
TURNING_POINTS = 5
HALF_SECONDS = 45 * 60
SIDES = ("home", "away")
TIMELINE_KINDS = ("goal", "save", "block", "shot_off", "yellow", "red", "sub", "corner", "foul")

RULES: dict[str, str] = {
    "possession": "Possession is each club's share of the passes attempted.",
    "big_chance": "A big chance is a shot whose modelled xG is at least 0.2, the threshold agon.interpret uses for candidate moments.",
    "star": (
        "The star player has the most goals plus assists; a tie goes to the higher xG, then the higher xT added, "
        "then the lower shirt number. There is no star player when nobody scored or assisted."
    ),
    "speed": (
        "Carry speed and sprint carries are an on-ball proxy (carry displacement over the gap to the next event), not running "
        "speed: the match engine produces events only. Running speed and distance come only from the separate synthetic tracking "
        "layer, modelled from the event log within speed and acceleration caps, not measured."
    ),
    "distance": (
        "Distance covered is not in the event log. The separate synthetic tracking layer models it within speed and acceleration "
        "caps; it is not measured."
    ),
}


class MatchWebError(ValueError):
    """The match files cannot be written or trusted; the message says why."""


# ----------------------------------------------------------------- reading the served data


def _json_file(path: str) -> Any:
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        return data, json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MatchWebError(f"{path} is not UTF-8 JSON: {exc}") from exc


def read_served(data_dir: str, entry: Mapping[str, Any]) -> tuple[bytes, list[dict[str, Any]], bytes, dict[str, Any]]:
    """The NDJSON bytes and events, and the world bytes and file, of one data manifest entry."""
    with open(os.path.join(data_dir, str(entry["name"])), "rb") as fh:
        ndjson = fh.read()
    if sha256_hex(ndjson) != entry["sha256"]:
        raise MatchWebError(f"{entry['name']} does not hash to its data manifest entry")
    try:
        events = [json.loads(line) for line in ndjson.decode("utf-8").splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MatchWebError(f"{entry['name']} is not NDJSON: {exc}") from exc
    world_bytes, world = _json_file(os.path.join(data_dir, str(entry["world"])))
    if sha256_hex(world_bytes) != entry["world_sha256"]:
        raise MatchWebError(f"{entry['world']} does not hash to its data manifest entry")
    return ndjson, events, world_bytes, world


# ----------------------------------------------------------------- building one match


def _side(view: MatchView, team: str) -> str:
    return "home" if team == view.home_id else "away"


def _axis(events: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Match seconds for drawing: the second half starts where the first half's last minute ends."""
    first = [e for e in events if e["period"] == 1]
    second = [e for e in events if e["period"] == 2]
    h1_end = (max(e["minute"] for e in first) + 1) * 60 if first else HALF_SECONDS
    h1_end = max(h1_end, HALF_SECONDS)
    offset = h1_end - HALF_SECONDS
    end = ((max(e["minute"] for e in second) + 1) * 60 + offset) if second else h1_end
    return {"half_offset_s": offset, "half_time_s": h1_end, "end_s": max(end, h1_end)}


def _at(axis: Mapping[str, int], period: int, clock: int) -> int:
    return clock + (axis["half_offset_s"] if period == 2 else 0)


def _event_at(axis: Mapping[str, int], e: Mapping[str, Any]) -> int:
    return _at(axis, e["period"], e["minute"] * 60 + e["second"])


def _club(world: Mapping[str, Any], club_id: str) -> dict[str, Any]:
    club = next((c for c in world["clubs"] if c["club_id"] == club_id), None)
    if club is None:
        raise MatchWebError(f"the names file has no club {club_id}")
    return club


def _names(world: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["player_id"]: p for c in world["clubs"] for p in c["players"]}


def _name(names: Mapping[str, Mapping[str, Any]], player: str | None) -> str | None:
    if player is None:
        return None
    row = names.get(player)
    if row is None:
        raise MatchWebError(f"the names file does not name player {player}")
    return str(row["name"])


def _timeline(view: MatchView, axis: Mapping[str, int], names: Mapping[str, Mapping[str, Any]], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in view.events:
        kind = None
        big = False
        if e["type"] == "shot":
            kind = {"goal": "goal", "saved": "save", "blocked": "block", "off_target": "shot_off"}[e["outcome"]]
            big = xg_ppm(e, view.home_id, model) >= wp.CANDIDATE_XG_PPM
        elif e["type"] == "card":
            kind = "yellow" if e["outcome"] == "yellow" else "red"
        elif e["type"] == "sub":
            kind = "sub"
        elif e["type"] == "set_piece" and e["qualifier"] == "corner":
            kind = "corner"
        elif e["type"] == "foul":
            kind = "foul"
        if kind is None:
            continue
        row: dict[str, Any] = {
            "event_id": e["event_id"],
            "seq": e["seq"],
            "kind": kind,
            "side": _side(view, e["team"]),
            "minute": minute_label(e),
            "at_s": _event_at(axis, e),
            "player": e["player"],
            "name": _name(names, e["player"]),
            "big_chance": big,
            "penalty": e["type"] == "shot" and e["qualifier"] == "penalty",
            "second_yellow": e["type"] == "card" and e["outcome"] == "second_yellow",
            "related_name": _name(names, e["related_player"]) if e["type"] == "sub" else None,
        }
        out.append(row)
    return out


def _goals(view: MatchView, axis: Mapping[str, int], names: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    score = {"home": 0, "away": 0}
    for e in view.events:
        if not (e["type"] == "shot" and e["outcome"] == "goal"):
            continue
        side = _side(view, e["team"])
        score[side] += 1
        out.append({
            "event_id": e["event_id"],
            "seq": e["seq"],
            "side": side,
            "minute": minute_label(e),
            "at_s": _event_at(axis, e),
            "player": e["player"],
            "name": _name(names, e["player"]),
            "penalty": e["qualifier"] == "penalty",
            "score_after": dict(score),
        })
    return out


def _live(view: MatchView, axis: Mapping[str, int], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per engine minute with events: the last sequence number, cumulative possession and xG."""
    out: list[dict[str, Any]] = []
    passes = {"home": 0, "away": 0}
    xg = {"home": 0, "away": 0}
    shots = {"home": 0, "away": 0}
    events = view.events
    for i, e in enumerate(events):
        side = _side(view, e["team"])
        if e["type"] == "pass":
            passes[side] += 1
        if e["type"] == "shot":
            xg[side] += xg_ppm(e, view.home_id, model)
            shots[side] += 1
        last = i + 1 == len(events) or (events[i + 1]["period"], events[i + 1]["minute"]) != (e["period"], e["minute"])
        if not last:
            continue
        total = passes["home"] + passes["away"]
        out.append({
            "seq": e["seq"],
            "period": e["period"],
            "minute": minute_label(e),
            "at_s": _at(axis, e["period"], (e["minute"] + 1) * 60),
            "home_possession_permille": (1000 * passes["home"] * 2 + total) // (2 * total) if total else 500,
            "home_xg_ppm": xg["home"],
            "away_xg_ppm": xg["away"],
            "home_shots": shots["home"],
            "away_shots": shots["away"],
        })
    return out


def _win_probability(view: MatchView, axis: Mapping[str, int], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in wp.series(view, model):
        at = axis["end_s"] if row["clock"] is None else _at(axis, row["period"], row["clock"])
        out.append({
            "period": row["period"],
            "minute": row["minute"],
            "at_s": at,
            "home_ppm": row["home_ppm"],
            "draw_ppm": row["draw_ppm"],
            "away_ppm": row["away_ppm"],
        })
    return out


def _turning_points(view: MatchView, axis: Mapping[str, int], names: Mapping[str, Mapping[str, Any]], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in wp.candidate_moments(view, limit=TURNING_POINTS, model=model):
        e = view.get(c["event_id"])
        out.append({
            "event_id": c["event_id"],
            "seq": e["seq"],
            "reason": c["reason"],
            "side": _side(view, e["team"]),
            "minute": minute_label(e),
            "at_s": _event_at(axis, e),
            "name": _name(names, e["player"]),
            "delta_home_ppm": c["delta_home_ppm"],
            "weight_ppm": c["weight_ppm"],
        })
    return sorted(out, key=lambda r: r["seq"])


def _shots(view: MatchView, axis: Mapping[str, int], names: Mapping[str, Mapping[str, Any]], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    cum = {"home": 0, "away": 0}
    for e in view.events:
        if e["type"] != "shot":
            continue
        side = _side(view, e["team"])
        value = xg_ppm(e, view.home_id, model)
        cum[side] += value
        x, y, _, _ = to_fixed_frame(e, view.home_id)
        out.append({
            "event_id": e["event_id"],
            "seq": e["seq"],
            "side": side,
            "minute": minute_label(e),
            "at_s": _event_at(axis, e),
            "player": e["player"],
            "name": _name(names, e["player"]),
            "outcome": e["outcome"],
            "qualifier": e["qualifier"],
            "x_dm": x,
            "y_dm": y,
            "xg_ppm": value,
            "cum_xg_ppm": cum[side],
            "big_chance": value >= wp.CANDIDATE_XG_PPM,
        })
    return out


def _momentum(view: MatchView, axis: Mapping[str, int], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"period": r["period"], "minute": r["minute"], "at_s": _at(axis, r["period"], r["minute"] * 60), "momentum_ppm": r["momentum_ppm"]}
        for r in metrics.momentum_series(view, model)
    ]


def _bands(view: MatchView, axis: Mapping[str, int], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per 5-minute window: control vs chaos, both sides' pressure and its band, and the tempo band."""
    cvc = {r["window"]: r for r in metrics.control_vs_chaos(view, model)}
    tempo = {r["window"]: r for r in metrics.tempo_bands(view, model)}
    regimes = metrics.pressure_regimes(view, model)
    band_of_window: dict[str, dict[str, str]] = {}
    for team in (view.home_id, view.away_id):
        for regime in regimes[team]["regimes"]:
            for label in regime["windows"]:
                band_of_window.setdefault(label, {})[_side(view, team)] = regime["band"]
    out = []
    counts_by_window = metrics.pressure_counts(view)
    last_of_period = {period: key for key in counts_by_window for period in [key[0]]}
    for key, counts in counts_by_window.items():
        label = window_label(key)
        period, w = key
        period_end = axis["half_time_s"] if period == 1 else axis["end_s"]
        start = _at(axis, period, w * 5 * 60)
        end = period_end if key == last_of_period[period] else min(start + 5 * 60, period_end)
        out.append({
            "window": label,
            "period": period,
            "from_s": start,
            "to_s": max(end, start + 60),
            "control_permille": cvc[label]["index_permille"] if label in cvc else None,
            "control_band": cvc[label]["band"] if label in cvc else None,
            "home_pressures": counts[view.home_id],
            "away_pressures": counts[view.away_id],
            "home_pressure_band": band_of_window.get(label, {}).get("home"),
            "away_pressure_band": band_of_window.get(label, {}).get("away"),
            "tempo_band": tempo[label]["band"] if label in tempo else None,
        })
    return out


def _territory(view: MatchView) -> dict[str, Any]:
    """On-ball actions (passes, carries, shots) per zone of the 16 by 12 grid, by where they started,
    in the frame where the home club attacks to the right."""
    grids = {"home": [0] * (GRID_X * GRID_Y), "away": [0] * (GRID_X * GRID_Y)}
    for e in view.events:
        if e["type"] not in ON_BALL_TYPES:
            continue
        x, y, _, _ = to_fixed_frame(e, view.home_id)
        zx = min(max(x * GRID_X // (PITCH_X_DM + 1), 0), GRID_X - 1)
        zy = min(max(y * GRID_Y // (PITCH_Y_DM + 1), 0), GRID_Y - 1)
        grids[_side(view, e["team"])][zy * GRID_X + zx] += 1
    return {"cols": GRID_X, "rows": GRID_Y, "home": grids["home"], "away": grids["away"]}


def _facts(view: MatchView, names: Mapping[str, Mapping[str, Any]], world: Mapping[str, Any]) -> recompute.MatchFacts:
    roster = recompute.Roster(
        league_name=str(world["league"]),
        clubs={c["club_id"]: c["name"] for c in world["clubs"]},
        players={pid: row["name"] for pid, row in names.items()},
        player_club={p["player_id"]: c["club_id"] for c in world["clubs"] for p in c["players"]},
    )
    events = tuple(Event(**e) for e in view.events)
    return recompute.MatchFacts(events=events, home_id=view.home_id, away_id=view.away_id, roster=roster)


def _team_stats(view: MatchView, facts: recompute.MatchFacts, live: Sequence[Mapping[str, Any]], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    totals = metrics.totals(view, model)
    ppda = metrics.ppda(view)
    home_pos = live[-1]["home_possession_permille"] if live else 500

    def count(stat: str) -> dict[str, int]:
        return {"home": recompute.recompute_stat(facts, stat, view.home_id), "away": recompute.recompute_stat(facts, stat, view.away_id)}

    rows: list[dict[str, Any]] = [
        {"key": "possession", "label": "Possession", "unit": "permille", "better": "higher", "home": home_pos, "away": 1000 - home_pos},
        {"key": "shots", "label": "Shots", "unit": "count", "better": "higher", **count("shots")},
        {"key": "shots_on_target", "label": "On target", "unit": "count", "better": "higher", **count("shots_on_target")},
        {"key": "xg", "label": "Expected goals (xG)", "unit": "ppm", "better": "higher",
         "home": totals[view.home_id]["xg_ppm"], "away": totals[view.away_id]["xg_ppm"]},
        {"key": "passes", "label": "Passes", "unit": "count", "better": "higher", **count("passes")},
        {"key": "pass_accuracy", "label": "Pass accuracy", "unit": "percent", "better": "higher", **count("pass_accuracy_pct")},
        {"key": "ppda", "label": "PPDA (lower presses harder)", "unit": "centi", "better": "lower",
         "home": ppda[view.home_id]["ppda_centi"], "away": ppda[view.away_id]["ppda_centi"]},
        {"key": "corners", "label": "Corners", "unit": "count", "better": "higher", **count("corners")},
        {"key": "fouls", "label": "Fouls", "unit": "count", "better": "lower", **count("fouls")},
        {"key": "yellow_cards", "label": "Yellow cards", "unit": "count", "better": "lower", **count("yellow_cards")},
        {"key": "red_cards", "label": "Red cards", "unit": "count", "better": "lower", **count("red_cards")},
    ]
    return rows


def _ppda_by_15(view: MatchView) -> list[dict[str, Any]]:
    ppda = metrics.ppda(view)
    home, away = ppda[view.home_id]["by_15"], ppda[view.away_id]["by_15"]
    return [
        {"bin": h["bin"], "home_ppda_centi": h["ppda_centi"], "away_ppda_centi": a["ppda_centi"]}
        for h, a in zip(home, away)
    ]


def _players(view: MatchView, world: Mapping[str, Any], seed: int, model: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    league = build_world(seed)
    lineups = {cid: [p.player_id for p in league.club(cid).starting_eleven()] for cid in (view.home_id, view.away_id)}
    names = _names(world)
    for cid, ids in lineups.items():
        unknown = [pid for pid in ids if pid not in names]
        if unknown:
            raise MatchWebError(f"the names file does not name the starting eleven of {cid}: {', '.join(unknown)}")
    lines = recompute.player_lines(view.events, view.home_id, view.away_id, lineups)
    proxy = {row["player"]: row for row in kinematics.carry_proxy(view)["players"]}
    shots: dict[str, list[int]] = {}
    passes: dict[str, list[int]] = {}  # attempted, completed, distance in metres, difficulty
    xt: dict[str, int] = {}
    zones: dict[str, dict[int, int]] = {}
    for e in view.events:
        p = e["player"]
        if p is None:
            continue
        if e["type"] == "shot":
            shots.setdefault(p, []).append(xg_ppm(e, view.home_id, model))
        if e["type"] == "pass":
            row = passes.setdefault(p, [0, 0, 0, 0])
            row[0] += 1
            row[1] += 1 if e["outcome"] == "complete" else 0
            row[2] += pass_distance_m(e)
            row[3] += pass_difficulty(e, view.home_id, model)
        if e["type"] in ON_BALL_TYPES:
            x, y, _, _ = to_fixed_frame(e, view.home_id)
            cell = min(max(y * GRID_Y // (PITCH_Y_DM + 1), 0), GRID_Y - 1) * GRID_X + min(max(x * GRID_X // (PITCH_X_DM + 1), 0), GRID_X - 1)
            zones.setdefault(p, {})[cell] = zones.setdefault(p, {}).get(cell, 0) + 1
        v = xt_added_ppm(e, model)
        if v is not None:
            xt[p] = xt.get(p, 0) + v
    out = []
    for line in lines:
        info = names[line.player_id]
        carry = proxy.get(line.player_id)
        out.append({
            "player_id": line.player_id,
            "side": _side(view, line.club_id),
            "name": info["name"],
            "shirt": info["shirt"],
            "position": info["position"],
            "started": line.started,
            "minutes": line.minutes,
            "goals": line.goals,
            "assists": line.assists,
            "yellow_cards": line.yellow_cards,
            "red_cards": line.red_cards,
            "shots": len(shots.get(line.player_id, [])),
            "xg_ppm": sum(shots.get(line.player_id, [])),
            "xt_added_ppm": xt.get(line.player_id, 0),
            "passes": passes.get(line.player_id, [0, 0, 0, 0])[0],
            "passes_completed": passes.get(line.player_id, [0, 0, 0, 0])[1],
            "pass_accuracy_pct": ratio_round(*passes.get(line.player_id, [0, 0, 0, 0])[1::-1], 100),
            "mean_pass_distance_dm": ratio_round(passes.get(line.player_id, [0, 0, 0, 0])[2], passes.get(line.player_id, [0, 0, 0, 0])[0], 10),
            "mean_pass_difficulty_centi": ratio_round(passes.get(line.player_id, [0, 0, 0, 0])[3], passes.get(line.player_id, [0, 0, 0, 0])[0], 100),
            "zones": [[cell, n] for cell, n in sorted(zones.get(line.player_id, {}).items())],
            "top_carry_proxy_kmh_x10": carry["top_proxy_kmh_x10"] if carry else None,
            "sprint_carries": len(carry["sprint_proxy"]) if carry else 0,
            "distance_dm": None,
        })
    order = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}
    out.sort(key=lambda r: (SIDES.index(r["side"]), not r["started"], order[r["position"]], r["shirt"]))
    involved = [r for r in out if r["goals"] + r["assists"] > 0]
    star = None
    if involved:
        best = sorted(involved, key=lambda r: (-(r["goals"] + r["assists"]), -r["xg_ppm"], -r["xt_added_ppm"], r["shirt"]))[0]
        star = {"player_id": best["player_id"], "side": best["side"], "name": best["name"]}
    return out, star


def build_match(seed: int, ndjson: bytes, events: list[dict[str, Any]], world_bytes: bytes, world: Mapping[str, Any],
                model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The analysis file for one served match."""
    m = model or load_model()
    if not events:
        raise MatchWebError(f"seed {seed}: the event log is empty")
    view = MatchView.build(events, str(world["home"]), str(world["away"]), str(world["match_id"]))
    axis = _axis(view.events)
    names = _names(world)
    live = _live(view, axis, m)
    facts = _facts(view, names, world)
    players, star = _players(view, world, seed, m)
    clubs = {}
    for side, cid in (("home", view.home_id), ("away", view.away_id)):
        c = _club(world, cid)
        clubs[side] = {k: c[k] for k in ("club_id", "name", "short", "kit_primary_hex", "kit_secondary_hex")}
    final = recompute.score_after(facts, None)
    return {
        "schema": MATCH_SCHEMA,
        "generator": str(world["generator"]),
        "interpret": INTERPRET_VERSION,
        "model_sha256": model_sha256() if model is None else sha256_hex(canonical_bytes(dict(model)) + b"\n"),
        "seed": seed,
        "match_id": view.match_id,
        "events_sha256": sha256_hex(ndjson),
        "world_sha256": sha256_hex(world_bytes),
        "events": len(view.events),
        "last_seq": view.events[-1]["seq"],
        "clubs": clubs,
        "axis": axis,
        "score": {"home": int(final["home"]), "away": int(final["away"])},
        "goals": _goals(view, axis, names),
        "timeline": _timeline(view, axis, names, m),
        "live": live,
        "win_probability": _win_probability(view, axis, m),
        "turning_points": _turning_points(view, axis, names, m),
        "shots": _shots(view, axis, names, m),
        "momentum": _momentum(view, axis, m),
        "bands": _bands(view, axis, m),
        "territory": _territory(view),
        "team_stats": _team_stats(view, facts, live, m),
        "ppda_by_15": _ppda_by_15(view),
        "players": players,
        "star": star,
        "rules": dict(RULES),
    }


def file_bytes(payload: Any) -> bytes:
    return canonical_bytes(payload) + b"\n"


def build_match_web(data_dir: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    """(manifest, {file name: bytes}) for every seed the data manifest lists, without touching the disk."""
    _, manifest = _json_file(os.path.join(data_dir, "manifest.json"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise MatchWebError("data/manifest.json lists no files")
    model = load_model()
    files = []
    payloads: dict[str, bytes] = {}
    for entry in sorted(manifest["files"], key=lambda f: f["seed"]):
        seed = int(entry["seed"])
        ndjson, events, world_bytes, world = read_served(data_dir, entry)
        body = build_match(seed, ndjson, events, world_bytes, world, model)
        problems = validate_match(body)
        if problems:
            raise MatchWebError(f"seed {seed}: refusing an invalid match file: " + "; ".join(problems[:5]))
        data = file_bytes(body)
        name = f"{seed}.json"
        payloads[name] = data
        files.append({"name": name, "seed": seed, "sha256": sha256_hex(data), "events_sha256": body["events_sha256"]})
    out = {"schema": MANIFEST_SCHEMA, "generator": GENERATOR_VERSION, "interpret": INTERPRET_VERSION, "model_sha256": model_sha256(), "files": files}
    return out, payloads


def write_match_web(out_dir: str, data_dir: str) -> dict[str, Any]:
    manifest, payloads = build_match_web(data_dir)
    problems = validate_manifest(manifest)
    if problems:
        raise MatchWebError("refusing an invalid match manifest: " + "; ".join(problems))
    os.makedirs(out_dir, exist_ok=True)
    for name, data in payloads.items():
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(out_dir, MANIFEST_NAME), "wb") as fh:
        fh.write(file_bytes(manifest))
    return manifest


# ----------------------------------------------------------------- validating


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check(obj: Any, keys: Mapping[str, str], where: str) -> list[str]:
    """`keys` maps each key to its kind: int, str, bool, side, int?, str?, list, dict."""
    if not isinstance(obj, dict):
        return [f"{where} must be an object"]
    problems = []
    if set(obj) != set(keys):
        problems.append(f"{where} keys differ: missing {sorted(set(keys) - set(obj))}, unexpected {sorted(set(obj) - set(keys))}")
        return problems
    for key, kind in keys.items():
        v = obj[key]
        optional = kind.endswith("?")
        base = kind.rstrip("?")
        if optional and v is None:
            continue
        ok = {
            "int": _is_int(v),
            "nat": _is_int(v) and v >= 0,
            "str": isinstance(v, str) and v != "",
            "bool": isinstance(v, bool),
            "side": v in SIDES,
            "list": isinstance(v, list),
            "dict": isinstance(v, dict),
            "sha": isinstance(v, str) and bool(SHA256_HEX.match(v)),
        }[base]
        if not ok:
            problems.append(f"{where}.{key} must be {base}{' or null' if optional else ''}")
    return problems


MANIFEST_KEYS = {"schema": "str", "generator": "str", "interpret": "str", "model_sha256": "sha", "files": "list"}
MANIFEST_FILE_KEYS = {"name": "str", "seed": "nat", "sha256": "sha", "events_sha256": "sha"}
MATCH_KEYS = {
    "schema": "str", "generator": "str", "interpret": "str", "model_sha256": "sha", "seed": "nat", "match_id": "str",
    "events_sha256": "sha", "world_sha256": "sha", "events": "nat", "last_seq": "nat", "clubs": "dict", "axis": "dict",
    "score": "dict", "goals": "list", "timeline": "list", "live": "list", "win_probability": "list", "turning_points": "list",
    "shots": "list", "momentum": "list", "bands": "list", "territory": "dict", "team_stats": "list", "ppda_by_15": "list", "players": "list",
    "star": "dict?", "rules": "dict",
}
CLUB_KEYS = {"club_id": "str", "name": "str", "short": "str", "kit_primary_hex": "str", "kit_secondary_hex": "str"}
AXIS_KEYS = {"half_offset_s": "nat", "half_time_s": "nat", "end_s": "nat"}
GOAL_KEYS = {"event_id": "str", "seq": "nat", "side": "side", "minute": "str", "at_s": "nat", "player": "str", "name": "str",
             "penalty": "bool", "score_after": "dict"}
TIMELINE_KEYS = {"event_id": "str", "seq": "nat", "kind": "str", "side": "side", "minute": "str", "at_s": "nat", "player": "str?",
                 "name": "str?", "big_chance": "bool", "penalty": "bool", "second_yellow": "bool", "related_name": "str?"}
LIVE_KEYS = {"seq": "nat", "period": "nat", "minute": "str", "at_s": "nat", "home_possession_permille": "nat",
             "home_xg_ppm": "nat", "away_xg_ppm": "nat", "home_shots": "nat", "away_shots": "nat"}
WP_KEYS = {"period": "nat", "minute": "str", "at_s": "nat", "home_ppm": "nat", "draw_ppm": "nat", "away_ppm": "nat"}
TURNING_KEYS = {"event_id": "str", "seq": "nat", "reason": "str", "side": "side", "minute": "str", "at_s": "nat", "name": "str?",
                "delta_home_ppm": "int", "weight_ppm": "nat"}
SHOT_KEYS = {"event_id": "str", "seq": "nat", "side": "side", "minute": "str", "at_s": "nat", "player": "str", "name": "str",
             "outcome": "str", "qualifier": "str?", "x_dm": "nat", "y_dm": "nat", "xg_ppm": "nat", "cum_xg_ppm": "nat", "big_chance": "bool"}
MOMENTUM_KEYS = {"period": "nat", "minute": "nat", "at_s": "nat", "momentum_ppm": "int"}
BAND_KEYS = {"window": "str", "period": "nat", "from_s": "nat", "to_s": "nat", "control_permille": "nat?", "control_band": "str?",
             "home_pressures": "nat", "away_pressures": "nat", "home_pressure_band": "str?", "away_pressure_band": "str?", "tempo_band": "str?"}
TERRITORY_KEYS = {"cols": "nat", "rows": "nat", "home": "list", "away": "list"}
STAT_KEYS = {"key": "str", "label": "str", "unit": "str", "better": "str", "home": "nat?", "away": "nat?"}
PLAYER_KEYS = {"player_id": "str", "side": "side", "name": "str", "shirt": "nat", "position": "str", "started": "bool", "minutes": "nat",
               "goals": "nat", "assists": "nat", "yellow_cards": "nat", "red_cards": "nat", "shots": "nat", "xg_ppm": "nat",
               "xt_added_ppm": "int", "passes": "nat", "passes_completed": "nat", "pass_accuracy_pct": "nat?",
               "mean_pass_distance_dm": "nat?", "mean_pass_difficulty_centi": "nat?", "zones": "list", "top_carry_proxy_kmh_x10": "nat?",
               "sprint_carries": "nat", "distance_dm": "nat?"}
STAR_KEYS = {"player_id": "str", "side": "side", "name": "str"}
UNITS = frozenset({"count", "permille", "ppm", "percent", "centi"})


def validate_manifest(manifest: Any) -> list[str]:
    problems = _check(manifest, MANIFEST_KEYS, "manifest")
    if problems:
        return problems
    if manifest["schema"] != MANIFEST_SCHEMA:
        problems.append(f"manifest.schema must be {MANIFEST_SCHEMA}")
    seeds = []
    for i, entry in enumerate(manifest["files"]):
        problems += _check(entry, MANIFEST_FILE_KEYS, f"files[{i}]")
        if isinstance(entry, dict) and not problems:
            if not FILE_NAME.match(str(entry["name"])) or entry["name"] != f"{entry['seed']}.json":
                problems.append(f"files[{i}].name must be <seed>.json")
            seeds.append(entry["seed"])
    if seeds != sorted(set(seeds)):
        problems.append("files must list each seed once, in order")
    return problems


def _rows(rows: Any, keys: Mapping[str, str], where: str) -> list[str]:
    problems: list[str] = []
    for i, row in enumerate(rows):
        problems += _check(row, keys, f"{where}[{i}]")
    return problems


def validate_match(body: Any) -> list[str]:
    """Problems with one match file's shape; empty means it matches the contract."""
    problems = _check(body, MATCH_KEYS, "match")
    if problems:
        return problems
    if body["schema"] != MATCH_SCHEMA:
        problems.append(f"match.schema must be {MATCH_SCHEMA}")
    for side in SIDES:
        problems += _check(body["clubs"].get(side), CLUB_KEYS, f"clubs.{side}")
    if set(body["clubs"]) != set(SIDES):
        problems.append("clubs must hold home and away")
    problems += _check(body["axis"], AXIS_KEYS, "axis")
    problems += _check(body["score"], {"home": "nat", "away": "nat"}, "score")
    problems += _rows(body["goals"], GOAL_KEYS, "goals")
    problems += _rows(body["timeline"], TIMELINE_KEYS, "timeline")
    problems += [f"timeline[{i}].kind is unknown" for i, r in enumerate(body["timeline"]) if isinstance(r, dict) and r.get("kind") not in TIMELINE_KINDS]
    problems += _rows(body["live"], LIVE_KEYS, "live")
    problems += _rows(body["win_probability"], WP_KEYS, "win_probability")
    for i, r in enumerate(body["win_probability"]):
        if isinstance(r, dict) and all(_is_int(r.get(k)) for k in ("home_ppm", "draw_ppm", "away_ppm")):
            if r["home_ppm"] + r["draw_ppm"] + r["away_ppm"] != 1_000_000:
                problems.append(f"win_probability[{i}] must sum to 1000000 ppm")
    problems += _rows(body["turning_points"], TURNING_KEYS, "turning_points")
    problems += _rows(body["shots"], SHOT_KEYS, "shots")
    problems += _rows(body["momentum"], MOMENTUM_KEYS, "momentum")
    problems += _rows(body["bands"], BAND_KEYS, "bands")
    t = body["territory"]
    problems += _check(t, TERRITORY_KEYS, "territory")
    if not problems:
        for side in SIDES:
            cells = t[side]
            if len(cells) != t["cols"] * t["rows"] or not all(_is_int(c) and c >= 0 for c in cells):
                problems.append(f"territory.{side} must hold cols x rows counts")
    problems += _rows(body["team_stats"], STAT_KEYS, "team_stats")
    problems += [f"team_stats[{i}].unit is unknown" for i, r in enumerate(body["team_stats"]) if isinstance(r, dict) and r.get("unit") not in UNITS]
    problems += _rows(body["ppda_by_15"], {"bin": "str", "home_ppda_centi": "nat?", "away_ppda_centi": "nat?"}, "ppda_by_15")
    problems += _rows(body["players"], PLAYER_KEYS, "players")
    if body["star"] is not None:
        problems += _check(body["star"], STAR_KEYS, "star")
    problems += _check(body["rules"], {k: "str" for k in RULES}, "rules")
    return problems


def verify_match_dir(match_dir: str | os.PathLike[str], data_dir: str | os.PathLike[str]) -> int:
    """Re-hash every match file, check its shape and that it describes the served event log. Returns the file count."""
    match_dir, data_dir = os.fspath(match_dir), os.fspath(data_dir)
    _, manifest = _json_file(os.path.join(match_dir, MANIFEST_NAME))
    problems = validate_manifest(manifest)
    if problems:
        raise MatchWebError("match manifest: " + "; ".join(problems))
    _, data_manifest = _json_file(os.path.join(data_dir, "manifest.json"))
    served = {int(f["seed"]): f["sha256"] for f in data_manifest.get("files", [])}
    listed = {e["seed"] for e in manifest["files"]}
    if listed != set(served):
        raise MatchWebError(f"match files cover seeds {sorted(listed)}, the data manifest lists {sorted(served)}")
    for entry in manifest["files"]:
        data, body = _json_file(os.path.join(match_dir, entry["name"]))
        if sha256_hex(data) != entry["sha256"]:
            raise MatchWebError(f"{entry['name']} does not hash to its manifest entry")
        if data != file_bytes(body):
            raise MatchWebError(f"{entry['name']} is not canonical JSON with a final LF")
        problems = validate_match(body)
        if problems:
            raise MatchWebError(f"{entry['name']}: " + "; ".join(problems[:5]))
        if body["events_sha256"] != served[entry["seed"]] or entry["events_sha256"] != served[entry["seed"]]:
            raise MatchWebError(f"{entry['name']} describes another event log than data/{entry['seed']}.ndjson")
    present = sorted(n for n in os.listdir(match_dir) if n != MANIFEST_NAME)
    stray = sorted(set(present) - {e["name"] for e in manifest["files"]})
    if stray:
        raise MatchWebError(f"files not listed in the match manifest: {', '.join(stray)}")
    return len(manifest["files"])


# ----------------------------------------------------------------- command line


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webmatch", description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, help="the verified data folder (data/manifest.json and its files)")
    parser.add_argument("--out", required=True, help="the match folder to write")
    args = parser.parse_args(argv)
    try:
        manifest = write_match_web(args.out, args.data)
        count = verify_match_dir(args.out, args.data)
    except (MatchWebError, OSError, KeyError) as exc:
        sys.stdout.write(f"webmatch: FAIL: {exc}\n")
        return 1
    sys.stdout.write(f"webmatch: wrote and verified {count} match files for seeds {', '.join(str(f['seed']) for f in manifest['files'])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The live cue engine: timed, machine-readable overlay cues, only inside an approved Match-Day Charter.

During play, deterministic code (never a language model) fills the charter's templates from the
event log and stamps every cue with the approved charter hash (spec 3.3, live cue engine; 7.2).
With no approved charter, `generate` returns nothing at all, and the export refuses to write.

Each cue is one graphic for a broadcast or web overlay system. The console's keys come first
(console/src/lib/cues.ts on the console branch reads `schema`, `seed`, `charter_sha256` and, per
cue, `at_s`, `until_s`, `kind` and `text`); every other key is extra and never renames one of them:

* `at_s`, `until_s`: the on-screen window on the console's drawn time axis, in whole match
  seconds with the second half shifted to start where the first half's last minute ends
  (`axis.half_offset_s`, the same rule as the console's match analysis).
* `kind`: the charter template id.
* `clock`: when it happens on the match clock, `{period, ms, label}`. `ms` is the clock a
  broadcast shows, in milliseconds: the second half kicks off at 45:00 (2 700 000 ms) and
  first-half added time runs past 45:00 in period 1, so a stream is ordered by (period, ms).
* `window`: when the graphic is on screen, `{in_ms, out_ms}` on the same clock and period.
* `overlay`: `lower_third`, `player_tag` or `stat_card`, from the charter template.
* `text`: the rendered caption in every charter language (en, el, es, fr).
* `data`: the same facts as integers, for a graphics system that draws its own text.
* `source_event_ids`, `template_id` and `charter_sha256`: why the cue exists and under which
  approved rules. `charter_sha256` is the sha256 of the approved charter bundle manifest: the hash
  on the Teams card and the folder name of the published charter in the public feed.

What fires a cue (the charter's thresholds decide, not this module): a goal, a red card, a
substitution (an injury replacement is held, class injury_medical, and never cued), a big chance
(interpret xG), a notable pass (distance and agon.interpret pass difficulty), and a name tag for
the player at the centre of each. Speed and distance tags (sprint, top speed, every 2 km) need
tracking data in the `agon.kinematics-triggers/1` form (web/cues-format/README.txt); without it,
name tags carry no number and no speed or distance cue exists. The events-only carry proxy in
agon.interpret.kinematics is never shown as a running speed.

Export (the console's file, web/cues/<seed>.json with schema `agon.cues/1`, plus the console's
list web/cues/manifest.json, `agon.cues-manifest/1`, in the bytes the deploy's stamp step writes;
the folder holds nothing else. The JSON Schema and the README are in web/cues-format/):

    PYTHONPATH=src python -m agon.cues \\
        --feed-base-url https://<storage account>.blob.core.windows.net/feed/ \\
        --charter <approved charter bundle sha256> --approved-at <ISO 8601 time with a zone> \\
        [--kinematics FILE] --out web/cues
    PYTHONPATH=src python -m agon.cues --check web/cues

It reads the published charter by its hash (agon.webrecord.read_published: the manifest must hash
to it, every gate must pass, charter.json must match its sha256), refuses anything but a charter
bundle, re-runs charter_check against the fictional roster of the seed, regenerates the match from
the seed, and writes the cues. A charter publishes no destination, so no public file carries the
approval time: --approved-at is required, from the Published card in Teams.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from agon import charter as charter_mod
from agon.governance.recompute import display_minute
from agon.governance.seal import canonical_utf8, is_sha256_hex, sha256_hex
from agon.interpret.features import pass_difficulty, pass_distance_m, xg_ppm
from agon.interpret.model import load_model

FORMAT = "agon.cues/1"
SCHEMA_FILE = "agon.cues-1.schema.json"
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = "agon.cues-manifest/1"
HALF_SECONDS = 45 * 60
OVERLAY_RANK: dict[str, int] = {"player_tag": 0, "stat_card": 1, "lower_third": 2}
KINEMATICS_FORMAT = "agon.kinematics-triggers/1"
KINEMATICS_TYPES: dict[str, str] = {"sprint": "sprint", "top_speed": "top_speed", "distance": "distance"}
LANGUAGES = charter_mod.LANGUAGES
OVERLAYS = charter_mod.OVERLAYS
TEMPLATE_IDS = charter_mod.TEMPLATE_IDS
PERIODS = (1, 2)
SECOND_HALF_KICKOFF_MS = 45 * 60 * 1000
HELD_CLASS_INJURY = "injury_medical"
DATA_KEYS = frozenset({
    "home_goals", "away_goals", "xg_ppm", "distance_m", "difficulty", "speed_kmh_x10", "distance_km",
})
# The console's required keys (console/src/lib/cues.ts on the console branch) are schema, seed,
# charter_sha256 and cues; each cue needs at_s, until_s, kind and text. Everything else is extra.
DOC_KEYS = frozenset({
    "schema", "json_schema", "seed", "match_index", "match_id", "generator", "fixture", "languages",
    "charter_sha256", "charter", "axis", "kinematics", "held", "cues",
})
CHARTER_REF_KEYS = frozenset({"bundle_sha256", "charter_json_sha256", "approved_at"})
CUE_KEYS = frozenset({
    "at_s", "until_s", "kind", "text",
    "cue_id", "template_id", "overlay", "clock", "window", "data", "source_event_ids",
    "charter_sha256", "team", "club_id", "player_id",
})
HELD_KEYS = frozenset({"event_id", "held_class", "clock"})
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_LABEL = re.compile(r"^[0-9]{1,3}(\+[0-9]{1,2})?'$")
_FILE = re.compile(r"^([0-9]{1,9})\.json$")


class CueError(ValueError):
    """Cues cannot be produced or exported honestly; the message says why."""


@dataclass(frozen=True)
class ApprovedCharter:
    """A charter the media officer approved: its JSON, the approved bundle hash and the approval time."""

    charter: Mapping[str, Any]
    bundle_sha256: str
    approved_at: str  # YYYY-MM-DDTHH:MM:SSZ


# ----------------------------------------------------------------- formatting


def _get(event: Any, key: str) -> Any:
    return event[key] if isinstance(event, Mapping) else getattr(event, key)


def _as_dict(event: Any) -> dict[str, Any]:
    return dict(event) if isinstance(event, Mapping) else event.to_dict()


def clock_of(event: Any) -> dict[str, Any]:
    period, minute, second = _get(event, "period"), _get(event, "minute"), _get(event, "second")
    return {"period": period, "ms": (minute * 60 + second) * 1000, "label": f"{display_minute(period, minute)}'"}


def decimal(value_x: int, places: int, language: str) -> str:
    """An integer in units of 10**-places as a decimal string, with the language's separator."""
    sign = "-" if value_x < 0 else ""
    whole, frac = divmod(abs(value_x), 10**places)
    return f"{sign}{whole}{'.' if language == 'en' else ','}{frac:0{places}d}"


def xg_hundredths(ppm: int) -> int:
    """xG in hundredths, half up, integer arithmetic only."""
    return (ppm + 5_000) // 10_000


# ----------------------------------------------------------------- generation


def _approved_problems(approved: ApprovedCharter) -> list[str]:
    problems = []
    if not is_sha256_hex(approved.bundle_sha256):
        problems.append("the approved charter bundle hash must be 64 lowercase hex characters")
    if not isinstance(approved.approved_at, str) or not _UTC.match(approved.approved_at):
        problems.append("the approval time must be YYYY-MM-DDTHH:MM:SSZ")
    charter = approved.charter
    if not isinstance(charter, Mapping) or charter.get("schema") != charter_mod.SCHEMA:
        problems.append(f"the approved charter is not {charter_mod.SCHEMA}")
    return problems


def _kinematics_triggers(kinematics: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if kinematics is None:
        return []
    problems = kinematics_problems(kinematics)
    if problems:
        raise CueError("kinematics input refused: " + "; ".join(problems))
    return list(kinematics["triggers"])


def kinematics_problems(kinematics: Any) -> list[str]:
    """Shape of an agon.kinematics-triggers/1 document (web/cues-format/README.txt)."""
    if not isinstance(kinematics, Mapping) or set(kinematics) != {"format", "source", "triggers"}:
        return ["a kinematics document holds exactly format, source and triggers"]
    problems = []
    if kinematics["format"] != KINEMATICS_FORMAT:
        problems.append(f"format must be {KINEMATICS_FORMAT}")
    if not isinstance(kinematics["source"], str) or not kinematics["source"]:
        problems.append("source must name what produced the tracking data")
    triggers = kinematics["triggers"]
    if not isinstance(triggers, list):
        return problems + ["triggers must be a list"]
    for i, t in enumerate(triggers):
        where = f"trigger {i}"
        if not isinstance(t, Mapping) or t.get("type") not in KINEMATICS_TYPES:
            problems.append(f"{where}: type must be one of {sorted(KINEMATICS_TYPES)}")
            continue
        value_key = "km" if t["type"] == "distance" else "kmh_x10"
        if set(t) != {"type", "player_id", "period", "ms", value_key}:
            problems.append(f"{where}: a {t['type']} trigger holds exactly type, player_id, period, ms and {value_key}")
            continue
        if not isinstance(t["player_id"], str) or t["period"] not in PERIODS:
            problems.append(f"{where}: player_id must be a string and period 1 or 2")
        for key in ("ms", value_key):
            if not isinstance(t[key], int) or isinstance(t[key], bool) or t[key] < 0:
                problems.append(f"{where}: {key} must be a non-negative integer")
    return problems


def generate(
    events: Iterable[Any],
    home_id: str,
    away_id: str,
    approved: ApprovedCharter | None,
    *,
    kinematics: Mapping[str, Any] | None = None,
    model: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(cues, held) for one match. Both are empty when no approved charter is given.

    `events` is the match's event log in order (agon.synth.schema.Event or the same dicts).
    """
    if approved is None:
        return [], []
    problems = _approved_problems(approved)
    if problems:
        raise CueError("; ".join(problems))
    charter = approved.charter
    templates = charter["templates"]
    th = charter["thresholds"]
    names = {pid: entry["name"] for pid, entry in charter["names"]["players"].items()}
    player_club = {pid: entry["club_id"] for pid, entry in charter["names"]["players"].items()}
    clubs = {charter["fixture"][side]["club_id"]: charter["fixture"][side]["name"] for side in ("home", "away")}
    if set(clubs) != {home_id, away_id}:
        raise CueError("the approved charter is for a different fixture")
    log = sorted(events, key=lambda e: _get(e, "seq"))
    if any(_get(e, "match_id") != charter["match_id"] for e in log):
        raise CueError(f"the approved charter governs {charter['match_id']}, and these events belong to another match")
    axis = axis_of(log)
    model = model or load_model()
    goals = {home_id: 0, away_id: 0}
    cues: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []

    def side(club_id: str) -> str:
        return "home" if club_id == home_id else "away"

    def add(template_id: str, event_ids: list[str], clock: dict[str, Any], club_id: str, player_id: str | None,
            values: Mapping[str, Any], data: Mapping[str, int]) -> None:
        template = templates[template_id]
        text = {}
        for lang in LANGUAGES:
            filled = {k: (v(lang) if callable(v) else str(v)) for k, v in values.items()}
            text[lang] = charter_mod.render(template["text"][lang], filled)
        identity = canonical_utf8({"template_id": template_id, "source_event_ids": event_ids, "clock": clock, "player_id": player_id})
        at_s = at_seconds(axis, clock["period"], clock["ms"])
        cues.append({
            "at_s": at_s,
            "until_s": at_s + template["duration_ms"] // 1000,
            "kind": template_id,
            "cue_id": sha256_hex(identity)[:16],
            "template_id": template_id,
            "overlay": template["overlay"],
            "clock": dict(clock),
            "window": {"in_ms": clock["ms"], "out_ms": clock["ms"] + template["duration_ms"]},
            "text": text,
            "data": dict(data),
            "source_event_ids": list(event_ids),
            "charter_sha256": approved.bundle_sha256,
            "team": side(club_id),
            "club_id": club_id,
            "player_id": player_id,
        })

    def tag(player_id: str | None, club_id: str, event_ids: list[str], clock: dict[str, Any]) -> None:
        if player_id in names:
            add("player_tag", event_ids, clock, club_id, player_id, {"player": names[player_id], "club": clubs[club_id]}, {})

    for e in log:
        etype, team, player = _get(e, "type"), _get(e, "team"), _get(e, "player")
        if team not in clubs:
            continue
        eid = _get(e, "event_id")
        clock = clock_of(e)
        minute = clock["label"]
        if etype == "shot" and _get(e, "outcome") == "goal":
            if player not in names:
                continue
            goals[team] += 1  # the score after this event, the way recompute.score_after counts it
            home_goals, away_goals = goals[home_id], goals[away_id]
            add("goal", [eid], clock, team, player, {
                "minute": minute, "player": names[player], "club": clubs[team],
                "home": clubs[home_id], "away": clubs[away_id], "home_goals": home_goals, "away_goals": away_goals,
            }, {"home_goals": home_goals, "away_goals": away_goals})
            tag(player, team, [eid], clock)
        elif etype == "card" and _get(e, "outcome") in ("red", "second_yellow"):
            if player not in names:
                continue
            add("red_card", [eid], clock, team, player, {"minute": minute, "player": names[player], "club": clubs[team]}, {})
            tag(player, team, [eid], clock)
        elif etype == "sub":
            if _get(e, "qualifier") == "injury":
                held.append({"event_id": eid, "held_class": HELD_CLASS_INJURY, "clock": dict(clock)})
                continue
            on, off = player, _get(e, "related_player")
            if on not in names or off not in names:
                continue
            add("substitution", [eid], clock, team, on, {
                "minute": minute, "club": clubs[team], "player_on": names[on], "player_off": names[off],
            }, {})
            tag(on, team, [eid], clock)
        elif etype == "shot":
            ppm = xg_ppm(_as_dict(e), home_id, model)
            if ppm < th["big_chance_xg_ppm"] or player not in names:
                continue
            hundredths = xg_hundredths(ppm)
            add("big_chance", [eid], clock, team, player, {
                "minute": minute, "player": names[player], "club": clubs[team],
                "xg": lambda lang, h=hundredths: decimal(h, 2, lang),
            }, {"xg_ppm": ppm})
            tag(player, team, [eid], clock)
        elif etype == "pass" and _get(e, "outcome") == "complete":
            d = _as_dict(e)
            metres = pass_distance_m(d)
            if metres < th["pass_min_distance_m"] or player not in names:
                continue
            difficulty = pass_difficulty(d, home_id, model)
            if difficulty < th["pass_min_difficulty"]:
                continue
            add("pass_quality", [eid], clock, team, player, {
                "minute": minute, "player": names[player], "club": clubs[team], "distance_m": metres, "difficulty": difficulty,
            }, {"distance_m": metres, "difficulty": difficulty})
            tag(player, team, [eid], clock)

    for trigger in _kinematics_triggers(kinematics):
        pid = trigger["player_id"]
        club_id = player_club.get(pid)
        if pid not in names or club_id not in clubs:
            continue
        minute_of = trigger["ms"] // 60_000
        clock = {"period": trigger["period"], "ms": trigger["ms"], "label": f"{display_minute(trigger['period'], minute_of)}'"}
        kind = trigger["type"]
        base = {"player": names[pid], "club": clubs[club_id]}
        if kind == "distance":
            step_km = th["distance_milestone_dm"] // 10_000
            if trigger["km"] <= 0 or step_km <= 0 or trigger["km"] % step_km:
                continue
            add("distance", [], clock, club_id, pid, {**base, "distance_km": trigger["km"]}, {"distance_km": trigger["km"]})
            continue
        floor = th["sprint_kmh_x10"] if kind == "sprint" else th["top_speed_kmh_x10"]
        if trigger["kmh_x10"] < floor:
            continue
        add(kind, [], clock, club_id, pid, {**base, "speed_kmh": lambda lang, v=trigger["kmh_x10"]: decimal(v, 1, lang)},
            {"speed_kmh_x10": trigger["kmh_x10"]})

    # Ordered by the clock; at the same instant the lower third comes last, because a renderer that
    # shows one cue at a time (the console's cueAt) shows the last cue whose window holds "now".
    order = {tid: i for i, tid in enumerate(TEMPLATE_IDS)}
    cues.sort(key=lambda c: (c["clock"]["period"], c["clock"]["ms"], OVERLAY_RANK[c["overlay"]], order[c["template_id"]], c["cue_id"]))
    return cues, held


def axis_of(events: Iterable[Any]) -> dict[str, int]:
    """The console's drawn time axis (agon.webmatch on the console branch): match seconds, with the
    second half shifted to start where the first half's last minute ends."""
    log = list(events)
    first = [_get(e, "minute") for e in log if _get(e, "period") == 1]
    h1_end = max((max(first) + 1) * 60 if first else HALF_SECONDS, HALF_SECONDS)
    return {"half_offset_s": h1_end - HALF_SECONDS}


def at_seconds(axis: Mapping[str, int], period: int, ms: int) -> int:
    """A match-clock instant on the drawn axis, in whole seconds."""
    return ms // 1000 + (axis["half_offset_s"] if period == 2 else 0)


# ----------------------------------------------------------------- the file


def document(
    *,
    seed: int,
    match_index: int,
    match_id: str,
    generator: str,
    approved: ApprovedCharter,
    cues: list[dict[str, Any]],
    held: list[dict[str, Any]],
    kinematics_source: str | None,
    axis: Mapping[str, int],
) -> dict[str, Any]:
    charter = approved.charter
    return {
        "schema": FORMAT,
        "json_schema": SCHEMA_FILE,
        "seed": seed,
        "match_index": match_index,
        "match_id": match_id,
        "generator": generator,
        "fixture": {
            "league": charter["fixture"]["league"],
            "home": dict(charter["fixture"]["home"]),
            "away": dict(charter["fixture"]["away"]),
        },
        "languages": list(LANGUAGES),
        "charter_sha256": approved.bundle_sha256,
        "charter": {
            "bundle_sha256": approved.bundle_sha256,
            "charter_json_sha256": sha256_hex(charter_mod.charter_bytes(charter)),
            "approved_at": approved.approved_at,
        },
        "axis": {"half_offset_s": int(axis["half_offset_s"])},
        "kinematics": None if kinematics_source is None else {"format": KINEMATICS_FORMAT, "source": kinematics_source},
        "held": held,
        "cues": cues,
    }


def document_bytes(doc: Mapping[str, Any]) -> bytes:
    """Canonical JSON (sorted keys, no whitespace, UTF-8, integers only) plus a final LF."""
    return canonical_utf8(dict(doc)) + b"\n"


def _clock_problems(clock: Any, where: str) -> list[str]:
    if not isinstance(clock, Mapping) or set(clock) != {"period", "ms", "label"}:
        return [f"{where}: clock holds exactly period, ms and label"]
    problems = []
    if clock["period"] not in PERIODS:
        problems.append(f"{where}: period must be 1 or 2")
    if not isinstance(clock["ms"], int) or isinstance(clock["ms"], bool) or clock["ms"] < 0:
        problems.append(f"{where}: ms must be a non-negative integer")
    if not isinstance(clock["label"], str) or not _LABEL.match(clock["label"]):
        problems.append(f"{where}: label must read like 71' or 45+2'")
    return problems


def validate(doc: Any) -> list[str]:
    """Every way `doc` breaks agon.cues/1. Empty means valid. Mirrors web/cues-format/agon.cues-1.schema.json."""
    if not isinstance(doc, Mapping):
        return ["the cues file is not a JSON object"]
    keys = set(doc)
    if keys != DOC_KEYS:
        return [f"keys differ: missing {sorted(DOC_KEYS - keys)}, unexpected {sorted(keys - DOC_KEYS)}"]
    problems: list[str] = []
    if doc["schema"] != FORMAT or doc["json_schema"] != SCHEMA_FILE:
        problems.append(f"schema must be {FORMAT} and json_schema {SCHEMA_FILE}")
    for key in ("seed", "match_index"):
        if not isinstance(doc[key], int) or isinstance(doc[key], bool) or doc[key] < 0:
            problems.append(f"{key} must be a non-negative integer")
    if not isinstance(doc["match_id"], str) or doc["match_id"] != f"s{doc['seed']}-m{doc['match_index']}":
        problems.append("match_id must be s<seed>-m<match_index>")
    if not isinstance(doc["generator"], str) or not doc["generator"]:
        problems.append("generator must name the generator version")
    fixture = doc["fixture"]
    if not isinstance(fixture, Mapping) or set(fixture) != {"league", "home", "away"} or not all(
        isinstance(fixture[s], Mapping) and set(fixture[s]) == {"club_id", "name"} for s in ("home", "away")
    ):
        problems.append("fixture holds league, home and away, each club with club_id and name")
        clubs: dict[str, str] = {}
    else:
        clubs = {fixture["home"]["club_id"]: "home", fixture["away"]["club_id"]: "away"}
    if doc["languages"] != list(LANGUAGES):
        problems.append(f"languages must be exactly {list(LANGUAGES)}")
    ref = doc["charter"]
    if not isinstance(ref, Mapping) or set(ref) != CHARTER_REF_KEYS:
        problems.append(f"charter holds exactly {sorted(CHARTER_REF_KEYS)}")
        approved_hash = None
    else:
        approved_hash = ref["bundle_sha256"]
        if not is_sha256_hex(ref["bundle_sha256"]) or not is_sha256_hex(ref["charter_json_sha256"]):
            problems.append("charter hashes must be 64 lowercase hex characters")
        if not isinstance(ref["approved_at"], str) or not _UTC.match(ref["approved_at"]):
            problems.append("charter.approved_at must be YYYY-MM-DDTHH:MM:SSZ")
    if doc["charter_sha256"] != approved_hash or not is_sha256_hex(doc["charter_sha256"]):
        problems.append("charter_sha256 must be the approved charter bundle hash, charter.bundle_sha256")
    axis = doc["axis"]
    offset = axis.get("half_offset_s") if isinstance(axis, Mapping) and set(axis) == {"half_offset_s"} else None
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        problems.append("axis holds exactly half_offset_s, a non-negative integer")
        offset = None
    kin = doc["kinematics"]
    if kin is not None and (not isinstance(kin, Mapping) or set(kin) != {"format", "source"} or kin.get("format") != KINEMATICS_FORMAT):
        problems.append(f"kinematics is null or {{format: {KINEMATICS_FORMAT}, source}}")
    if not isinstance(doc["held"], list):
        problems.append("held must be a list")
    else:
        for i, h in enumerate(doc["held"]):
            if not isinstance(h, Mapping) or set(h) != HELD_KEYS or h.get("held_class") not in charter_mod.HELD_CLASSES:
                problems.append(f"held {i}: holds exactly event_id, held_class (a charter held class) and clock")
                continue
            if not isinstance(h["event_id"], str) or not _HEX16.match(h["event_id"]):
                problems.append(f"held {i}: event_id must be 16 hex characters")
            problems += _clock_problems(h["clock"], f"held {i}")
    cues = doc["cues"]
    if not isinstance(cues, list):
        return problems + ["cues must be a list"]
    seen: set[str] = set()
    previous: tuple[int, int] | None = None
    speed_cues = 0
    for i, c in enumerate(cues):
        where = f"cue {i}"
        if not isinstance(c, Mapping) or set(c) != CUE_KEYS:
            problems.append(f"{where}: keys must be exactly {sorted(CUE_KEYS)}")
            continue
        if not isinstance(c["cue_id"], str) or not _HEX16.match(c["cue_id"]) or c["cue_id"] in seen:
            problems.append(f"{where}: cue_id must be 16 hex characters and unique")
        seen.add(str(c["cue_id"]))
        if c["template_id"] not in TEMPLATE_IDS:
            problems.append(f"{where}: unknown template_id {c['template_id']!r}")
        if c["kind"] != c["template_id"]:
            problems.append(f"{where}: kind is the template id")
        elif c["overlay"] != charter_mod.TEMPLATE_SPECS[c["template_id"]]["overlay"]:
            problems.append(f"{where}: overlay {c['overlay']!r} is not the {c['template_id']} overlay")
        if c["template_id"] in ("sprint", "top_speed", "distance"):
            speed_cues += 1
        clock_problems = _clock_problems(c["clock"], where)
        problems += clock_problems
        window = c["window"]
        if not isinstance(window, Mapping) or set(window) != {"in_ms", "out_ms"} or not all(
            isinstance(window[k], int) and not isinstance(window[k], bool) for k in ("in_ms", "out_ms")
        ):
            problems.append(f"{where}: window holds integer in_ms and out_ms")
        elif not clock_problems and not (window["in_ms"] >= c["clock"]["ms"] and window["out_ms"] > window["in_ms"]):
            problems.append(f"{where}: the window must open at or after the clock and close after it opens")
        elif offset is not None and (
            c["at_s"] != at_seconds({"half_offset_s": offset}, c["clock"]["period"], window["in_ms"])
            or c["until_s"] != c["at_s"] + (window["out_ms"] - window["in_ms"]) // 1000
        ):
            problems.append(f"{where}: at_s and until_s are the window on the drawn axis (axis.half_offset_s)")
        if not all(isinstance(c[k], int) and not isinstance(c[k], bool) for k in ("at_s", "until_s")) or not c["until_s"] > c["at_s"] >= 0:
            problems.append(f"{where}: at_s and until_s are integers with until_s after at_s")
        text = c["text"]
        if not isinstance(text, Mapping) or set(text) != set(LANGUAGES) or not all(isinstance(t, str) and t.strip() for t in text.values()):
            problems.append(f"{where}: text must be non-empty in exactly {list(LANGUAGES)}")
        elif any(ch in t for t in text.values() for ch in (chr(0x2013), chr(0x2014))):
            problems.append(f"{where}: text carries an en or em dash")
        data = c["data"]
        if not isinstance(data, Mapping) or not set(data) <= DATA_KEYS or not all(isinstance(v, int) and not isinstance(v, bool) for v in data.values()):
            problems.append(f"{where}: data maps known names to integers")
        ids = c["source_event_ids"]
        if not isinstance(ids, list) or not all(isinstance(x, str) and _HEX16.match(x) for x in ids):
            problems.append(f"{where}: source_event_ids must be a list of 16-hex event ids")
        elif not ids and c["template_id"] not in ("sprint", "top_speed", "distance"):
            problems.append(f"{where}: an event cue names its source events")
        if c["charter_sha256"] != approved_hash:
            problems.append(f"{where}: charter_sha256 is not the approved charter bundle hash")
        if c["team"] not in ("home", "away") or clubs.get(c["club_id"]) != c["team"]:
            problems.append(f"{where}: team and club_id must name a side of the fixture")
        if c["player_id"] is not None and not isinstance(c["player_id"], str):
            problems.append(f"{where}: player_id is a string or null")
        if not clock_problems:
            key = (c["clock"]["period"], c["clock"]["ms"])
            if previous is not None and key < previous:
                problems.append(f"{where}: cues must be ordered by (period, ms)")
            previous = key
    if kin is None and speed_cues:
        problems.append("speed or distance cues need a kinematics source; without tracking data a name tag carries no number")
    return problems


# ----------------------------------------------------------------- export


def read_approved_charter(feed: Any, bundle_sha256: str, approved_at: str) -> tuple[ApprovedCharter, Any]:
    """The published charter by its hash, re-checked from the bytes. Returns it and its fixture record."""
    from agon import webrecord
    from agon.fleet.sources import SynthMatchSource

    try:
        published = webrecord.read_published(feed, bundle_sha256, "feed")
    except webrecord.ExportError as exc:
        raise CueError(str(exc)) from exc
    manifest = published.manifest
    if manifest["kind"] != "charter":
        raise CueError(f"bundle {bundle_sha256} is a {manifest['kind']} bundle, not a Match-Day Charter")
    if manifest["event_log_sha256"] != charter_mod.EMPTY_EVENT_LOG_SHA256:
        raise CueError("a charter is sealed before kickoff, over the empty event log")
    raw = published.artifacts.get(charter_mod.ARTIFACT_NAME)
    if raw is None:
        raise CueError(f"{charter_mod.ARTIFACT_NAME} is not published")
    charter = json.loads(raw.decode("utf-8"))
    if charter_mod.charter_bytes(charter) != raw:
        raise CueError(f"{charter_mod.ARTIFACT_NAME} is not in canonical form")
    m = re.match(r"^s([0-9]{1,9})-m([0-9]{1,4})$", str(manifest["match_id"]))
    if not m or charter.get("match_id") != manifest["match_id"]:
        raise CueError(f"match id {manifest['match_id']!r} is not s<seed>-m<index>, or the charter names another match")
    ref = {"source": "synth", "seed": int(m.group(1)), "match_index": int(m.group(2))}
    record = SynthMatchSource().load(ref)
    if record.seed_sha256 != manifest["seed_sha256"]:
        raise CueError("the seed does not reproduce the charter's seed_sha256")
    verdict = charter_mod.charter_check(charter, record.roster)
    if not verdict.passed:
        raise CueError("the published charter no longer passes its gates: " + "; ".join(verdict.charter_reasons + verdict.rights_reasons))
    try:
        when = webrecord.utc_z(approved_at, "--approved-at")
    except webrecord.ExportError as exc:
        raise CueError(str(exc)) from exc
    return ApprovedCharter(charter=charter, bundle_sha256=bundle_sha256, approved_at=when), record


def export(feed: Any, bundle_sha256: str, approved_at: str, kinematics: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The agon.cues/1 document for the match an approved, published charter governs."""
    from agon.synth.engine import GENERATOR_VERSION

    approved, record = read_approved_charter(feed, bundle_sha256, approved_at)
    cues, held = generate(record.events, record.meta["home"], record.meta["away"], approved, kinematics=kinematics)
    doc = document(
        seed=int(record.meta["seed"]),
        match_index=int(record.meta["match_index"]),
        match_id=record.match_id,
        generator=GENERATOR_VERSION,
        approved=approved,
        cues=cues,
        held=held,
        kinematics_source=None if kinematics is None else str(kinematics["source"]),
        axis=axis_of(record.events),
    )
    problems = validate(doc)
    if problems:
        raise CueError("the cues break agon.cues/1: " + "; ".join(problems[:10]))
    return doc


def manifest_bytes(folder: Path) -> bytes:
    """The console's cue list (agon.cues-manifest/1): one entry per <seed>.json, in the exact bytes
    the deploy's stamp step writes (sorted keys, no whitespace, ASCII, final LF), so it can rewrite
    the list without a diff."""
    files = []
    for path in sorted(folder.glob("*.json"), key=lambda q: q.name):
        m = _FILE.match(path.name)
        if not m:
            continue
        data = path.read_bytes()
        body = json.loads(data.decode("utf-8"))
        files.append({"name": path.name, "seed": int(m.group(1)), "sha256": sha256_hex(data), "charter_sha256": body.get("charter_sha256")})
    listing = {"schema": MANIFEST_FORMAT, "files": files}
    return json.dumps(listing, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"


def check_dir(folder: Path) -> list[str]:
    """Validate every <seed>.json in a cues folder and its manifest.json; returns the cue files checked.

    The folder holds only <seed>.json files and the list, because the deploy's stamp step refuses
    any other file there. A folder that does not exist holds no cue file.
    """
    if not folder.is_dir():
        return []
    checked = []
    for path in sorted(folder.iterdir()):
        if path.name == MANIFEST_NAME:
            continue
        m = _FILE.match(path.name)
        if not m or not path.is_file():
            raise CueError(f"{path.name}: the cues folder holds only <seed>.json files and {MANIFEST_NAME}")
        raw = path.read_bytes()
        if b"\r" in raw:
            raise CueError(f"{path.name}: CR bytes; the file is LF only")
        doc = json.loads(raw.decode("utf-8"))
        problems = validate(doc)
        if not problems and doc["seed"] != int(m.group(1)):
            problems = [f"the file name says seed {m.group(1)}, the document seed {doc['seed']}"]
        if not problems and document_bytes(doc) != raw:
            problems = ["not in canonical form (sorted keys, no whitespace, final LF)"]
        if problems:
            raise CueError(f"{path.name}: " + "; ".join(problems[:10]))
        checked.append(path.name)
    listing = folder / MANIFEST_NAME
    if checked and (not listing.is_file() or listing.read_bytes() != manifest_bytes(folder)):
        raise CueError(f"{MANIFEST_NAME} does not list exactly the cue files with their sha256; run the export again")
    return checked


def main(argv: Sequence[str] | None = None) -> int:
    from agon import webrecord

    parser = argparse.ArgumentParser(prog="python -m agon.cues", description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--feed-base-url", help="the public feed container URL, the deploy output feedBaseUrl")
    source.add_argument("--local", type=Path, help="a copy of the container on disk (DIR/<sha>/manifest.json)")
    source.add_argument("--check", type=Path, metavar="DIR", help="only validate the cues files in DIR")
    parser.add_argument("--charter", help="the approved, published charter bundle sha256")
    parser.add_argument("--approved-at", help="the approval time, ISO 8601 with a zone (the Published card in Teams)")
    parser.add_argument("--kinematics", type=Path, help=f"tracking triggers, {KINEMATICS_FORMAT} (optional)")
    parser.add_argument("--out", type=Path, default=Path("web/cues"), help="the cues folder (default web/cues)")
    parser.add_argument("--replace", action="store_true", help="replace an existing <seed>.json")
    args = parser.parse_args(argv)
    try:
        if args.check is not None:
            names = check_dir(args.check)
            sys.stdout.write(f"cues: {len(names)} file(s) valid in {args.check.as_posix()}: {', '.join(names) or 'none'}\n")
            return 0
        if not args.charter or not args.approved_at:
            raise CueError("--charter and --approved-at are required")
        kinematics = None
        if args.kinematics is not None:
            kinematics = json.loads(args.kinematics.read_text(encoding="utf-8"))
        feed = webrecord.LocalFeed(args.local) if args.local is not None else webrecord.HttpFeed(args.feed_base_url)
        doc = export(feed, args.charter, args.approved_at, kinematics)
        data = document_bytes(doc)
        problems = webrecord.text_problems({f"{doc['seed']}.json": data})
        if problems:
            raise CueError("text the site cannot serve: " + "; ".join(problems))
        target = args.out / f"{doc['seed']}.json"
        if target.exists() and not args.replace:
            raise CueError(f"{target.as_posix()} exists; pass --replace to write cues under a newer approved charter")
        args.out.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        (args.out / MANIFEST_NAME).write_bytes(manifest_bytes(args.out))
        check_dir(args.out)
        counts: dict[str, int] = {}
        for c in doc["cues"]:
            counts[c["overlay"]] = counts.get(c["overlay"], 0) + 1
        sys.stdout.write(
            f"cues: wrote {target.as_posix()}: {len(doc['cues'])} cues ({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}), "
            f"{len(doc['held'])} held, under charter {doc['charter']['bundle_sha256']} approved at {doc['charter']['approved_at']}\n"
        )
    except (CueError, webrecord.ExportError, ValueError) as exc:
        sys.stdout.write(f"cues: FAIL: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

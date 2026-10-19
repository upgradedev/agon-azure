"""The Match-Day Charter: the rules for live cues that the club media officer approves once, before kickoff.

What a charter holds (spec 1.2, 3.3 W-charter, 7.2), all built by code from the fictional fixture,
with no language model anywhere:

* per-language cue templates (English, Greek, Spanish, French) for a goal, a red card, a
  substitution, a big chance, a notable pass, a player name tag, and the speed and distance tags
  (sprint at 25.2 km/h or more, top speed at 30 km/h or more, every 2 km), each with its overlay
  type (lower third, player tag, stat card), its display time and the slots it may fill;
* the thresholds that decide when a cue fires, as integers;
* the claim classes that are held and never cued: injury and medical news, judgements about
  refereeing, and quotes;
* the Rights Guard lists the cues run under, named by sha256 only (the lists themselves are deny
  lists of real names and are never copied into a published file);
* the names the cues may use (the two squads of the fictional fixture, each player with its club)
  and the voices.

`charter_check` is the deterministic gate: every template is in all four languages, every slot is
one the template id allows (an allow-list in code, not in the charter), no template carries held
wording, every name is on the fictional roster, the list hashes are the lists in force, and the
Rights Guard passes on every template rendered with roster names in every language.

A charter is approved before kickoff, so it is sealed with the hash of the empty event log
(`EMPTY_EVENT_LOG_SHA256`): the match it governs has not produced an event yet. Its bundle kind is
"charter" (agon.governance.seal), with exactly one artifact, `charter.json`, the canonical bytes of
the charter. The live cue engine (agon.cues) fills these templates from the event log and stamps
every cue with the approved charter bundle hash; without an approved charter it produces nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from agon.agents.speech import DEFAULT_VOICES
from agon.governance import rights_lists
from agon.governance.claims import Claim, Sentence
from agon.governance.recompute import Roster
from agon.governance.rights import DEFAULT_LISTS, RightsLists, RightsReport, fold, run_rights
from agon.governance.seal import canonical_utf8, event_log_sha256, sha256_hex
from agon.interpret.kinematics import MILESTONE_DM, SPRINT_KMH_X10, TOP_SPEED_KMH_X10
from agon.synth import blocklist as synth_blocklist

SCHEMA = "agon.charter/1"
LANGUAGES: tuple[str, ...] = ("en", "el", "es", "fr")
LANGUAGE_NAMES: dict[str, str] = {"en": "English", "el": "Greek", "es": "Spanish", "fr": "French"}
OVERLAYS: tuple[str, ...] = ("lower_third", "player_tag", "stat_card")
EMPTY_EVENT_LOG_SHA256 = event_log_sha256([])
ARTIFACT_NAME = "charter.json"
MEDIA_TYPE = "application/json"
SCOPE_LINE = "This approves the rules for live cues, not a report."

DURATION_MIN_MS = 2000
DURATION_MAX_MS = 20000
TEXT_MAX_CHARS = 160

# Template id -> (overlay, the slots that template may fill, display time, when it fires). The slot
# allow-list lives here, in code: a charter that declares or uses any other slot is refused.
TEMPLATE_SPECS: dict[str, dict[str, Any]] = {
    "goal": {
        "overlay": "lower_third",
        "slots": ("minute", "player", "club", "home", "away", "home_goals", "away_goals"),
        "duration_ms": 10000,
        "trigger": "a shot whose outcome is goal",
    },
    "red_card": {
        "overlay": "lower_third",
        "slots": ("minute", "player", "club"),
        "duration_ms": 8000,
        "trigger": "a card whose outcome is red or second_yellow; the cue states the event-log fact only",
    },
    "substitution": {
        "overlay": "lower_third",
        "slots": ("minute", "club", "player_on", "player_off"),
        "duration_ms": 8000,
        "trigger": "a substitution; an injury replacement is held (injury_medical) and never cued",
    },
    "big_chance": {
        "overlay": "stat_card",
        "slots": ("minute", "player", "club", "xg"),
        "duration_ms": 7000,
        "trigger": "a shot that is not a goal, with interpret xG at or above big_chance_xg_ppm",
    },
    "pass_quality": {
        "overlay": "stat_card",
        "slots": ("minute", "player", "club", "distance_m", "difficulty"),
        "duration_ms": 7000,
        "trigger": "a completed pass at or above pass_min_distance_m and pass_min_difficulty (agon.interpret pass difficulty)",
    },
    "player_tag": {
        "overlay": "player_tag",
        "slots": ("player", "club"),
        "duration_ms": 5000,
        "trigger": "the player at the centre of a goal, red card, big chance or notable pass cue, and the player coming on; no number",
    },
    "sprint": {
        "overlay": "player_tag",
        "slots": ("player", "club", "speed_kmh"),
        "duration_ms": 5000,
        "trigger": "a tracked sprint at or above sprint_kmh_x10; needs tracking data, never an events-only proxy",
    },
    "top_speed": {
        "overlay": "player_tag",
        "slots": ("player", "club", "speed_kmh"),
        "duration_ms": 5000,
        "trigger": "a tracked speed at or above top_speed_kmh_x10; needs tracking data, never an events-only proxy",
    },
    "distance": {
        "overlay": "player_tag",
        "slots": ("player", "club", "distance_km"),
        "duration_ms": 5000,
        "trigger": "each distance_milestone_dm of tracked running; needs tracking data",
    },
}
TEMPLATE_IDS: tuple[str, ...] = tuple(TEMPLATE_SPECS)

# The cue text, per template and language. Plain telegraphic captions, the same facts in every
# language; numbers arrive through slots, formatted per language by agon.cues.
TEMPLATE_TEXT: dict[str, dict[str, str]] = {
    "goal": {
        "en": "GOAL {minute}: {player} ({club}). {home} {home_goals}-{away_goals} {away}",
        "el": "ΓΚΟΛ {minute}: {player} ({club}). {home} {home_goals}-{away_goals} {away}",
        "es": "GOL {minute}: {player} ({club}). {home} {home_goals}-{away_goals} {away}",
        "fr": "BUT {minute} : {player} ({club}). {home} {home_goals}-{away_goals} {away}",
    },
    "red_card": {
        "en": "Red card {minute}: {player} ({club}) is sent off.",
        "el": "Κόκκινη κάρτα {minute}: αποβολή για {player} ({club}).",
        "es": "Tarjeta roja {minute}: expulsión de {player} ({club}).",
        "fr": "Carton rouge {minute} : expulsion de {player} ({club}).",
    },
    "substitution": {
        "en": "Substitution {minute}, {club}: {player_on} on, {player_off} off.",
        "el": "Αλλαγή {minute}, {club}: μπαίνει {player_on}, βγαίνει {player_off}.",
        "es": "Cambio {minute}, {club}: entra {player_on}, sale {player_off}.",
        "fr": "Remplacement {minute}, {club} : entrée de {player_on}, sortie de {player_off}.",
    },
    "big_chance": {
        "en": "Big chance {minute}: {player} ({club}), xG {xg}.",
        "el": "Μεγάλη ευκαιρία {minute}: {player} ({club}), xG {xg}.",
        "es": "Ocasión clara {minute}: {player} ({club}), xG {xg}.",
        "fr": "Grosse occasion {minute} : {player} ({club}), xG {xg}.",
    },
    "pass_quality": {
        "en": "{player} ({club}): {distance_m} m pass, difficulty {difficulty} of 100.",
        "el": "{player} ({club}): πάσα {distance_m} μέτρων, δυσκολία {difficulty} στα 100.",
        "es": "{player} ({club}): pase de {distance_m} m, dificultad {difficulty} sobre 100.",
        "fr": "{player} ({club}) : passe de {distance_m} m, difficulté {difficulty} sur 100.",
    },
    "player_tag": {
        "en": "{player}, {club}",
        "el": "{player}, {club}",
        "es": "{player}, {club}",
        "fr": "{player}, {club}",
    },
    "sprint": {
        "en": "{player}, {club}. Sprint: {speed_kmh} km/h",
        "el": "{player}, {club}. Σπριντ: {speed_kmh} km/h",
        "es": "{player}, {club}. Sprint: {speed_kmh} km/h",
        "fr": "{player}, {club}. Sprint : {speed_kmh} km/h",
    },
    "top_speed": {
        "en": "{player}, {club}. Top speed: {speed_kmh} km/h",
        "el": "{player}, {club}. Μέγιστη ταχύτητα: {speed_kmh} km/h",
        "es": "{player}, {club}. Velocidad máxima: {speed_kmh} km/h",
        "fr": "{player}, {club}. Vitesse de pointe : {speed_kmh} km/h",
    },
    "distance": {
        "en": "{player}, {club}. Distance: {distance_km} km",
        "el": "{player}, {club}. Απόσταση: {distance_km} km",
        "es": "{player}, {club}. Distancia: {distance_km} km",
        "fr": "{player}, {club}. Distance : {distance_km} km",
    },
}

# Integers only (the manifest and the charter are integer JSON). The three speed and distance
# values are the brief's and are pinned to agon.interpret.kinematics; the rest are tunable rules.
THRESHOLDS: dict[str, int] = {
    "big_chance_xg_ppm": 200_000,
    "pass_min_distance_m": 30,
    "pass_min_difficulty": 50,
    "sprint_kmh_x10": SPRINT_KMH_X10,
    "top_speed_kmh_x10": TOP_SPEED_KMH_X10,
    "distance_milestone_dm": MILESTONE_DM,
}
PINNED_THRESHOLDS = ("sprint_kmh_x10", "top_speed_kmh_x10", "distance_milestone_dm")

# Held and never cued. The red card template states the event-log fact only (who, which club,
# which minute); any judgement about a refereeing decision belongs to disciplinary_referee.
HELD_CLASSES: dict[str, str] = {
    "injury_medical": "Injury and medical news is never cued: an injury replacement produces no cue, and no template names an injury.",
    "disciplinary_referee": "Judgements about refereeing are never cued: a red card cue states who was sent off and when, nothing about the decision.",
    "quote_attribution": "Quotes are never cued: no template quotes or attributes words to a person.",
}
# Folded (lower case, accents dropped) fragments that put a template inside a held class.
HELD_LEXICON: dict[str, tuple[str, ...]] = {
    "injury_medical": ("injur", "medical", "stretcher", "τραυμ", "ιατρ", "lesion", "lesio", "bless", "medic"),
    "disciplinary_referee": ("referee", "officiat", "διαιτητ", "arbitr"),
    "quote_attribution": ('"', chr(0x201C), chr(0x201D), chr(0x00AB), chr(0x00BB), chr(0x201E),
                          " said", " says", " told", "δηλωσε", "dijo", "declar", " a dit"),
}
KINEMATICS_RULE = (
    "Speed and distance tags need tracking data. Without it a name tag carries no number, and an "
    "events-only on-ball proxy is never shown as a running speed."
)

CHARTER_KEYS = frozenset({
    "schema", "match_id", "seed_sha256", "scope", "fixture", "names", "languages", "templates",
    "thresholds", "held_classes", "kinematics_rule", "rights_guard", "voices",
})
TEMPLATE_KEYS = frozenset({"overlay", "slots", "duration_ms", "trigger", "text"})
_SLOT = re.compile(r"\{([a-z_]+)\}")


class CharterError(ValueError):
    """A charter cannot be built or rendered from the inputs given."""


# ----------------------------------------------------------------- the Rights Guard lists, by hash


def rights_list_hashes() -> dict[str, str]:
    """sha256 over each deny list's canonical JSON, in its listed order. The names never leave the repo."""
    lists: dict[str, Any] = {
        "field_names": list(synth_blocklist.FIELD_NAMES),
        "real_clubs": list(synth_blocklist.REAL_TOP_FLIGHT_CLUBS),
        "leagues": list(rights_lists.LEAGUES),
        "broadcasters": list(rights_lists.BROADCASTERS),
        "players_full": list(rights_lists.PLAYERS_FULL),
        "player_surnames": list(rights_lists.PLAYER_SURNAMES),
        "greek_stems": list(rights_lists.GREEK_STEMS),
        "prose_ambiguous": sorted(rights_lists.PROSE_AMBIGUOUS),
    }
    return {name: sha256_hex(canonical_utf8(values)) for name, values in sorted(lists.items())}


def roster_sha256(roster: Roster) -> str:
    return sha256_hex(canonical_utf8({
        "league": roster.league_name,
        "clubs": dict(roster.clubs),
        "players": dict(roster.players),
        "player_club": dict(roster.player_club),
    }))


# ----------------------------------------------------------------- build


def build_charter(record: Any, voices: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The charter for one fictional fixture. Pure function of the match record and the voices.

    `record` is an agon.fleet.sources.MatchRecord (or anything with match_id, meta home and away,
    roster and seed_sha256). Only the fixture and the squads are read, never an event: the charter
    is approved before kickoff.
    """
    roster: Roster = record.roster
    home, away = record.meta["home"], record.meta["away"]
    squads = {
        pid: {"name": name, "club_id": roster.player_club[pid]}
        for pid, name in sorted(roster.players.items())
        if roster.player_club.get(pid) in (home, away)
    }
    chosen = dict(DEFAULT_VOICES if voices is None else voices)
    if set(chosen) != set(LANGUAGES):
        raise CharterError(f"voices must name exactly {list(LANGUAGES)}")
    return {
        "schema": SCHEMA,
        "match_id": record.match_id,
        "seed_sha256": record.seed_sha256,
        "scope": SCOPE_LINE,
        "fixture": {
            "league": roster.league_name,
            "home": {"club_id": home, "name": roster.clubs[home]},
            "away": {"club_id": away, "name": roster.clubs[away]},
        },
        "names": {"players": squads},
        "languages": list(LANGUAGES),
        "templates": {
            tid: {
                "overlay": spec["overlay"],
                "slots": list(spec["slots"]),
                "duration_ms": spec["duration_ms"],
                "trigger": spec["trigger"],
                "text": dict(TEMPLATE_TEXT[tid]),
            }
            for tid, spec in TEMPLATE_SPECS.items()
        },
        "thresholds": dict(THRESHOLDS),
        "held_classes": dict(HELD_CLASSES),
        "kinematics_rule": KINEMATICS_RULE,
        "rights_guard": {"lists": rights_list_hashes(), "roster_sha256": roster_sha256(roster)},
        "voices": {lang: chosen[lang] for lang in LANGUAGES},
    }


def charter_bytes(charter: Mapping[str, Any]) -> bytes:
    """The canonical bytes sealed as charter.json (sorted keys, no whitespace, UTF-8, integers only)."""
    return canonical_utf8(dict(charter))


# ----------------------------------------------------------------- render


def placeholders(text: str) -> list[str] | None:
    """The slot names in a template, in order; None when a brace is not part of a `{slot}`."""
    names = _SLOT.findall(text)
    if "{" in _SLOT.sub("", text) or "}" in _SLOT.sub("", text):
        return None
    return names


def render(text: str, values: Mapping[str, str]) -> str:
    """Fill `{slot}` placeholders by plain substitution (never str.format). A missing slot raises."""
    if placeholders(text) is None:
        raise CharterError(f"malformed template {text!r}")

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise CharterError(f"no value for slot {name!r}")
        return str(values[name])

    return _SLOT.sub(fill, text)


# ----------------------------------------------------------------- the gate


@dataclass(frozen=True)
class CharterVerdict:
    """The two gate verdicts a charter bundle seals: its own rules check and the Rights Guard."""

    charter_check: str  # "pass" or "refused"
    rights: str  # "pass" or "refused"
    charter_reasons: tuple[str, ...]
    rights_reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.charter_check == "pass" and self.rights == "pass"


def rule_problems(charter: Any, roster: Roster) -> list[str]:
    """Every way `charter` breaks the charter rules. Empty means the charter_check gate passes."""
    if not isinstance(charter, Mapping):
        return ["the charter is not a JSON object"]
    problems: list[str] = []
    keys = set(charter)
    if keys != CHARTER_KEYS:
        return [f"charter keys differ: missing {sorted(CHARTER_KEYS - keys)}, unexpected {sorted(keys - CHARTER_KEYS)}"]
    if charter["schema"] != SCHEMA:
        problems.append(f"unknown charter schema {charter['schema']!r}")
    if charter["scope"] != SCOPE_LINE:
        problems.append("the scope line must say the charter approves live cue rules, not a report")
    if charter["languages"] != list(LANGUAGES):
        problems.append(f"languages must be exactly {list(LANGUAGES)}")
    problems += _template_problems(charter["templates"])
    problems += _threshold_problems(charter["thresholds"])
    held = charter["held_classes"]
    if not isinstance(held, Mapping) or set(held) != set(HELD_CLASSES):
        problems.append(f"held_classes must be exactly {sorted(HELD_CLASSES)}; a held class cannot be dropped")
    if charter["kinematics_rule"] != KINEMATICS_RULE:
        problems.append("the kinematics rule must say a name tag carries no number without tracking data")
    problems += _name_problems(charter, roster)
    guard = charter["rights_guard"]
    if not isinstance(guard, Mapping) or guard.get("lists") != rights_list_hashes():
        problems.append("rights_guard.lists must name, by sha256, exactly the Rights Guard lists in force")
    elif guard.get("roster_sha256") != roster_sha256(roster):
        problems.append("rights_guard.roster_sha256 is not the fictional roster of this seed")
    voices = charter["voices"]
    if not isinstance(voices, Mapping) or set(voices) != set(LANGUAGES) or not all(isinstance(v, str) and v for v in voices.values()):
        problems.append(f"voices must name one voice for each of {list(LANGUAGES)}")
    return problems


def _template_problems(templates: Any) -> list[str]:
    if not isinstance(templates, Mapping):
        return ["templates must be an object"]
    problems: list[str] = []
    unknown = sorted(set(templates) - set(TEMPLATE_IDS))
    missing = sorted(set(TEMPLATE_IDS) - set(templates))
    if unknown:
        problems.append(f"unknown template ids {unknown}")
    if missing:
        problems.append(f"missing template ids {missing}")
    for tid in sorted(set(templates) & set(TEMPLATE_IDS)):
        t = templates[tid]
        if not isinstance(t, Mapping) or set(t) != TEMPLATE_KEYS:
            problems.append(f"template {tid} must hold exactly {sorted(TEMPLATE_KEYS)}")
            continue
        if t["overlay"] not in OVERLAYS:
            problems.append(f"template {tid}: overlay {t['overlay']!r} is not one of {list(OVERLAYS)}")
        allowed = set(TEMPLATE_SPECS[tid]["slots"])
        declared = t["slots"]
        if not isinstance(declared, list) or not all(isinstance(s, str) for s in declared):
            problems.append(f"template {tid}: slots must be a list of names")
            declared = []
        for slot in sorted(set(declared) - allowed):
            problems.append(f"template {tid}: slot {slot!r} is not allowed (allowed: {sorted(allowed)})")
        duration = t["duration_ms"]
        if not isinstance(duration, int) or isinstance(duration, bool) or not DURATION_MIN_MS <= duration <= DURATION_MAX_MS:
            problems.append(f"template {tid}: duration_ms must be an integer from {DURATION_MIN_MS} to {DURATION_MAX_MS}")
        if not isinstance(t["trigger"], str) or not t["trigger"]:
            problems.append(f"template {tid}: trigger must say when the cue fires")
        texts = t["text"]
        if not isinstance(texts, Mapping) or set(texts) != set(LANGUAGES):
            problems.append(f"template {tid}: text must be given in exactly {list(LANGUAGES)}")
            continue
        for lang in LANGUAGES:
            text = texts[lang]
            where = f"template {tid} ({lang})"
            if not isinstance(text, str) or not text.strip():
                problems.append(f"{where}: empty text")
                continue
            if len(text) > TEXT_MAX_CHARS:
                problems.append(f"{where}: longer than {TEXT_MAX_CHARS} characters")
            used = placeholders(text)
            if used is None:
                problems.append(f"{where}: a brace that is not a {{slot}} placeholder")
                continue
            for slot in sorted(set(used) - allowed):
                problems.append(f"{where}: unknown slot {slot!r} (allowed: {sorted(allowed)})")
            for slot in sorted((set(used) & allowed) - set(declared)):
                problems.append(f"{where}: slot {slot!r} is used but not declared")
            folded = fold(text)
            for held_class, fragments in HELD_LEXICON.items():
                hits = sorted({f.strip() for f in fragments if f in folded})
                if hits:
                    problems.append(f"{where}: held class {held_class} wording {hits}; held classes are never cued")
    return problems


def _threshold_problems(thresholds: Any) -> list[str]:
    if not isinstance(thresholds, Mapping) or set(thresholds) != set(THRESHOLDS):
        return [f"thresholds must hold exactly {sorted(THRESHOLDS)}"]
    problems = []
    for name, value in sorted(thresholds.items()):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"threshold {name} must be a positive integer")
    for name in PINNED_THRESHOLDS:
        if thresholds.get(name) != THRESHOLDS[name]:
            problems.append(f"threshold {name} is the brief's value {THRESHOLDS[name]}, not {thresholds.get(name)!r}")
    if isinstance(thresholds.get("pass_min_difficulty"), int) and thresholds["pass_min_difficulty"] > 100:
        problems.append("pass_min_difficulty is on a 0 to 100 scale")
    return problems


def _name_problems(charter: Mapping[str, Any], roster: Roster) -> list[str]:
    problems: list[str] = []
    fixture = charter["fixture"]
    if not isinstance(fixture, Mapping) or set(fixture) != {"league", "home", "away"}:
        return ["fixture must hold exactly league, home and away"]
    if fixture["league"] != roster.league_name:
        problems.append("fixture league is not the fictional league of this seed")
    clubs = []
    for side in ("home", "away"):
        club = fixture[side]
        if not isinstance(club, Mapping) or set(club) != {"club_id", "name"}:
            problems.append(f"fixture {side} must hold exactly club_id and name")
            continue
        if roster.clubs.get(club["club_id"]) != club["name"]:
            problems.append(f"fixture {side} {club.get('name')!r} is not on the fictional roster as {club.get('club_id')!r}")
        clubs.append(club["club_id"])
    names = charter["names"]
    players = names.get("players") if isinstance(names, Mapping) and set(names) == {"players"} else None
    if not isinstance(players, Mapping) or not players:
        return problems + ["names must hold exactly a non-empty players table"]
    for pid, entry in sorted(players.items()):
        if not isinstance(entry, Mapping) or set(entry) != {"name", "club_id"}:
            problems.append(f"player {pid!r} must hold exactly name and club_id")
            continue
        name = entry["name"]
        if roster.players.get(pid) != name:
            problems.append(f"name {name!r} is not on the fictional roster as {pid!r}")
        elif roster.player_club.get(pid) != entry["club_id"] or entry["club_id"] not in clubs:
            problems.append(f"{name!r} does not play for {entry['club_id']!r}, a club of the fixture")
    return problems


def sample_values(charter: Mapping[str, Any], language: str) -> dict[str, str]:
    """Slot values from the charter's own names, to screen every template as it would be shown."""
    home = charter["fixture"]["home"]
    away = charter["fixture"]["away"]
    players = sorted(entry["name"] for entry in charter["names"]["players"].values())
    first, second = players[0], players[-1]
    decimal = "." if language == "en" else ","
    return {
        "minute": "71'", "player": first, "player_on": first, "player_off": second, "club": home["name"],
        "home": home["name"], "away": away["name"], "home_goals": "1", "away_goals": "0",
        "xg": f"0{decimal}35", "distance_m": "34", "difficulty": "62",
        "speed_kmh": f"31{decimal}4", "distance_km": "4",
    }


def rights_report(charter: Mapping[str, Any], roster: Roster, lists: RightsLists = DEFAULT_LISTS) -> RightsReport:
    """The Rights Guard over every template rendered with roster names, in every language."""
    sentences: list[Sentence] = []
    for tid in TEMPLATE_IDS:
        template = charter["templates"][tid]
        for lang in LANGUAGES:
            values = sample_values(charter, lang)
            text = render(template["text"][lang], values)
            used = placeholders(template["text"][lang]) or []
            claims = tuple(
                Claim("name", values[slot]) for slot in used if slot in ("player", "player_on", "player_off", "club", "home", "away")
            )
            sentences.append(Sentence(sentence_id=f"charter-{tid}-{lang}", text=text, language=lang, claims=claims, author="template"))
    return run_rights(roster, sentences, lists)


def charter_check(charter: Any, roster: Roster, lists: RightsLists = DEFAULT_LISTS) -> CharterVerdict:
    """Run the charter_check gate, then the Rights Guard (only on a charter that passed its rules,
    because rendering needs well-formed templates; a refused charter is refused by both)."""
    problems = rule_problems(charter, roster)
    if problems:
        return CharterVerdict("refused", "refused", tuple(problems), ("not run: the charter failed charter_check",))
    report = rights_report(charter, roster, lists)
    reasons = tuple(f"{r.code} at {r.where}: {r.detail}" for r in report.refusals)
    return CharterVerdict("pass", report.status, (), reasons)


# ----------------------------------------------------------------- what the officer reads


def summary(charter: Mapping[str, Any]) -> str:
    """The plain summary the approval card prints: the English templates, the held classes, the rest."""
    templates = charter["templates"]
    lines = [
        f"Live cue rules for {charter['fixture']['home']['name']} v {charter['fixture']['away']['name']}, "
        f"in {', '.join(LANGUAGE_NAMES[lang] for lang in LANGUAGES[:-1])} and {LANGUAGE_NAMES[LANGUAGES[-1]]}."
    ]
    for tid in TEMPLATE_IDS:
        t = templates[tid]
        lines.append(f"{tid.replace('_', ' ').capitalize()} ({t['overlay'].replace('_', ' ')}, {t['duration_ms'] // 1000} s): {t['text']['en']}")
    th = charter["thresholds"]
    lines.append(
        f"Big chance at xG {th['big_chance_xg_ppm'] / 1_000_000:.2f} or more; notable pass at {th['pass_min_distance_m']} m "
        f"and difficulty {th['pass_min_difficulty']} of 100 or more; sprint {th['sprint_kmh_x10'] / 10:.1f} km/h, "
        f"top speed {th['top_speed_kmh_x10'] / 10:.1f} km/h, distance every {th['distance_milestone_dm'] // 10_000} km."
    )
    lines.append("Held, never cued: " + " ".join(charter["held_classes"][k] for k in sorted(charter["held_classes"])))
    lines.append(charter["kinematics_rule"])
    lines.append("Voices: " + ", ".join(f"{LANGUAGE_NAMES[lang]} {charter['voices'][lang]}" for lang in LANGUAGES) + ".")
    # A blank line between paragraphs: an Adaptive Card text block breaks lines on a blank line.
    return "\n\n".join(lines)

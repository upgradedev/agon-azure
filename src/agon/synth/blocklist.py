"""Names the synthetic world must never produce: real top-flight clubs and rival hackathon entries.

Why this file holds real names: the generator screens every invented club, league and player
name against it, so the only place a real club name appears in this repository is this deny
list. Any forbidden-string scan of the deployed bundle must exclude this file by path.

The list is ASCII on purpose (diacritics dropped). `normalize` strips diacritics from candidates
the same way, so "Munchen" here matches a candidate spelled with an umlaut, and the file reads
the same under any default encoding.

Coverage is the 2025-26 top divisions of England, Spain, Italy, Germany, France, Greece, the
Netherlands, Portugal and Scotland as remembered at authoring time, plus the field names named in
the build spec. It is a screen, not a census: a name passing it is not proof the name is unused.
"""

from __future__ import annotations

import re
import unicodedata

FIELD_NAMES: tuple[str, ...] = (
    "Port Calder",
    "Calder Town",
    "Meteor City",
    "Nova FC",
    "Northbridge",
)

REAL_TOP_FLIGHT_CLUBS: tuple[str, ...] = (
    # England
    "Arsenal", "Aston Villa", "AFC Bournemouth", "Brentford", "Brighton and Hove Albion",
    "Burnley", "Chelsea", "Crystal Palace", "Everton", "Fulham", "Leeds United", "Liverpool",
    "Manchester City", "Manchester United", "Newcastle United", "Nottingham Forest",
    "Sunderland", "Tottenham Hotspur", "West Ham United", "Wolverhampton Wanderers",
    # Spain
    "Athletic Bilbao", "Atletico Madrid", "Barcelona", "Celta Vigo", "Deportivo Alaves",
    "Elche", "Espanyol", "Getafe", "Girona", "Levante", "Mallorca", "Osasuna", "Rayo Vallecano",
    "Real Betis", "Real Madrid", "Real Oviedo", "Real Sociedad", "Sevilla", "Valencia",
    "Villarreal",
    # Italy
    "Atalanta", "Bologna", "Cagliari", "Como", "Cremonese", "Fiorentina", "Genoa",
    "Hellas Verona", "Inter Milan", "Internazionale", "Juventus", "Lazio", "Lecce", "AC Milan",
    "Napoli", "Parma", "Pisa", "Roma", "Sassuolo", "Torino", "Udinese",
    # Germany
    "Augsburg", "Bayer Leverkusen", "Bayern Munchen", "Bayern Munich", "Borussia Dortmund",
    "Borussia Monchengladbach", "Eintracht Frankfurt", "Freiburg", "Hamburger SV",
    "Heidenheim", "Hoffenheim", "Koln", "Cologne", "Mainz", "RB Leipzig", "St Pauli",
    "Stuttgart", "Union Berlin", "Werder Bremen", "Wolfsburg",
    # France
    "Angers", "Auxerre", "Brest", "Le Havre", "Lens", "Lille", "Lorient", "Lyon",
    "Olympique Lyonnais", "Olympique de Marseille", "Marseille", "Metz", "Monaco", "Nantes",
    "Nice", "Paris FC", "Paris Saint-Germain", "Rennes", "Strasbourg", "Toulouse",
    # Greece
    "AEK Athens", "AEL Larissa", "Aris Thessaloniki", "Asteras Tripolis", "Atromitos",
    "Kifisia", "Levadiakos", "OFI Crete", "Olympiacos", "Olympiakos", "Panathinaikos",
    "Panetolikos", "Panserraikos", "PAOK", "Volos",
    # Netherlands
    "Ajax", "AZ Alkmaar", "Feyenoord", "PSV Eindhoven", "Twente", "Utrecht",
    # Portugal
    "Benfica", "Braga", "Porto", "Sporting CP",
    # Scotland
    "Aberdeen", "Celtic", "Hearts", "Hibernian", "Rangers",
)

BLOCKED_NAMES: tuple[str, ...] = FIELD_NAMES + REAL_TOP_FLIGHT_CLUBS

# Words that appear in many club names and carry no identity on their own. A blocked name is
# matched on its other, distinctive tokens. "port" is generic so "Port Calder" is matched on
# "calder", which is the identifying part.
GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "ac", "afc", "and", "as", "athletic", "atletico", "cd", "cf", "city", "club", "cp", "de",
        "deportivo", "fc", "hove", "olympique", "port", "rb", "real", "sc", "sv", "town",
        "united", "wanderers", "albion", "hotspur", "forest", "palace", "villa",
    }
)

# Distinctive tokens shorter than this match only as whole tokens; longer ones also match as a
# substring of a candidate token, so "Calderwick" is caught by "calder".
SUBSTRING_MIN_LEN = 4


def normalize(name: str) -> str:
    """Lowercase, strip diacritics, keep letters and digits separated by single spaces."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    return " ".join(re.findall(r"[a-z0-9]+", lowered))


def _distinctive_tokens(blocked: str) -> tuple[str, ...]:
    tokens = normalize(blocked).split()
    distinct = tuple(t for t in tokens if t not in GENERIC_TOKENS)
    return distinct


_BLOCKED_NORMALIZED: tuple[str, ...] = tuple(normalize(n) for n in BLOCKED_NAMES)
_BLOCKED_TOKENS: tuple[str, ...] = tuple(
    sorted({t for n in BLOCKED_NAMES for t in _distinctive_tokens(n)})
)


def blocked_reason(candidate: str) -> str | None:
    """Return why a candidate name is blocked, or None if it passes the screen."""
    norm = normalize(candidate)
    if not norm:
        return "empty name"
    cand_tokens = norm.split()
    joined = norm.replace(" ", "")
    for blocked in _BLOCKED_NORMALIZED:
        if norm == blocked or f" {blocked} " in f" {norm} ":
            return f"contains blocked name '{blocked}'"
        if blocked.replace(" ", "") == joined:
            return f"equals blocked name '{blocked}' without spaces"
    for token in _BLOCKED_TOKENS:
        for cand in cand_tokens:
            if cand == token:
                return f"token '{cand}' is blocked"
            if len(token) >= SUBSTRING_MIN_LEN and token in cand:
                return f"token '{cand}' contains blocked '{token}'"
    return None


def is_blocked(candidate: str) -> bool:
    return blocked_reason(candidate) is not None

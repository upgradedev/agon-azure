"""Rights Guard: refuse real names, rival field names, off-roster people and injected name fields.

What it checks, and where:

* Name fields (the league, club and player names on the roster, and every `name` claim value):
  a character allowlist (letters, space, hyphen, apostrophe, full stop), a length cap, a list of
  instruction markers, the strict club and field-name screen from `agon.synth.blocklist`, and
  the league, broadcaster and player lists in `rights_lists`.
* Prose (every sentence of every edition): the same real and field names as whole-word phrases,
  Greek-script club stems, and the instruction markers.
* People: a `name` claim whose value is not on the fictional roster is refused. This is checked on
  name fields and claims only. It is NOT named-entity recognition over prose; a person written
  into prose with no claim is caught only if they are on a block list.

Prose matching is deliberately narrower than name-field matching, because the name screen was
built for candidate names and would block ordinary Spanish and French words ("como", "nice").
In prose a Latin-script entry matches only where each of its capitalised words is capitalised in
the text, and a few everyday words (`rights_lists.PROSE_AMBIGUOUS`) are skipped when they open a
sentence. A real name written entirely in lower case in prose is therefore not caught; name
fields, where the matching is case-insensitive, carry that case.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from agon.governance import rights_lists
from agon.governance.claims import Sentence
from agon.governance.recompute import Roster
from agon.synth import blocklist as synth_blocklist

NAME_MAX_LEN = 48
NAME_MAX_TOKENS = 5
_APOSTROPHES = {"'", chr(0x2019)}
_NAME_PUNCT = {" ", "-", "."} | _APOSTROPHES

# Instruction markers, folded (lower case, accents dropped). Checked in name fields and prose.
INJECTION_MARKERS: tuple[str, ...] = (
    "ignore previous", "ignore all", "ignore the above", "disregard", "system prompt",
    "you are now", "new instructions", "instructions", "assistant:", "system:", "developer:",
    "publish now", "approve this", "approve the bundle", "jailbreak", "do anything now",
    "<script", "{{", "}}", "[inst]", "<|", "|>",
    "αγνοησε",  # Greek: "ignore" (imperative)
    "οδηγιεσ",  # Greek: "instructions"
)

_TOKEN = re.compile(r"[^\W_]+")
_SENTENCE_END = re.compile(r"[.!?;" + chr(0x037E) + r"]\s*$")


@dataclass(frozen=True)
class Refusal:
    code: str
    where: str
    detail: str


@dataclass(frozen=True)
class RightsReport:
    status: str  # "pass" or "refused"
    refusals: tuple[Refusal, ...]

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def fold(text: str) -> str:
    """Lower case with diacritics dropped; keeps every script (Greek stays Greek)."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


@dataclass(frozen=True)
class _Phrase:
    tokens: tuple[str, ...]  # folded
    capitals: tuple[bool, ...]  # the listed word starts with an upper-case letter
    label: str
    category: str


def _phrase(name: str, category: str) -> _Phrase:
    raw = _TOKEN.findall(unicodedata.normalize("NFKD", name))
    raw = ["".join(ch for ch in t if not unicodedata.combining(ch)) for t in raw]
    return _Phrase(
        tokens=tuple(t.casefold() for t in raw),
        capitals=tuple(t[:1].isupper() for t in raw),
        label=name,
        category=category,
    )


@dataclass(frozen=True)
class RightsLists:
    """The lists the guard screens against. Injectable so a test can prove an empty list fails."""

    phrases: tuple[_Phrase, ...]
    greek_stems: tuple[str, ...]
    prose_ambiguous: frozenset[str]
    use_synth_screen: bool = True
    markers: tuple[str, ...] = field(default=INJECTION_MARKERS)


def default_lists() -> RightsLists:
    phrases: list[_Phrase] = []
    phrases += [_phrase(n, "field_name") for n in synth_blocklist.FIELD_NAMES]
    phrases += [_phrase(n, "real_club") for n in synth_blocklist.REAL_TOP_FLIGHT_CLUBS]
    phrases += [_phrase(n, "real_league") for n in rights_lists.LEAGUES]
    phrases += [_phrase(n, "real_broadcaster") for n in rights_lists.BROADCASTERS]
    phrases += [_phrase(n, "real_player") for n in rights_lists.PLAYERS_FULL]
    phrases += [_phrase(n, "real_player") for n in rights_lists.PLAYER_SURNAMES]
    return RightsLists(
        phrases=tuple(p for p in phrases if p.tokens),
        greek_stems=tuple(fold(s) for s in rights_lists.GREEK_STEMS),
        prose_ambiguous=rights_lists.PROSE_AMBIGUOUS,
    )


DEFAULT_LISTS = default_lists()


@dataclass(frozen=True)
class _Tok:
    raw: str
    folded: str
    sentence_initial: bool


def _tokens(text: str) -> list[_Tok]:
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    out: list[_Tok] = []
    for m in _TOKEN.finditer(stripped):
        before = stripped[: m.start()]
        initial = not before.strip() or bool(_SENTENCE_END.search(before.rstrip(" \"'(")))
        out.append(_Tok(raw=m.group(0), folded=m.group(0).casefold(), sentence_initial=initial))
    return out


def _is_greek(token: str) -> bool:
    return any("GREEK" in unicodedata.name(ch, "") for ch in token)


def _phrase_hits(toks: list[_Tok], lists: RightsLists, case_sensitive: bool) -> list[_Phrase]:
    hits: list[_Phrase] = []
    for phrase in lists.phrases:
        n = len(phrase.tokens)
        for i in range(len(toks) - n + 1):
            window = toks[i : i + n]
            if tuple(t.folded for t in window) != phrase.tokens:
                continue
            if case_sensitive:
                if any(cap and not t.raw[:1].isupper() for cap, t in zip(phrase.capitals, window)):
                    continue
                if n == 1 and window[0].sentence_initial and phrase.tokens[0] in lists.prose_ambiguous:
                    continue
            hits.append(phrase)
            break
    return hits


def _greek_hits(toks: list[_Tok], lists: RightsLists) -> list[str]:
    found = []
    for t in toks:
        if not _is_greek(t.folded):
            continue
        for stem in lists.greek_stems:
            if t.folded.startswith(stem):
                found.append(t.raw)
                break
    return found


def _marker_hits(text: str, lists: RightsLists) -> list[str]:
    folded = fold(text)
    return [m for m in lists.markers if m in folded]


def screen_name_field(value: str, where: str, lists: RightsLists = DEFAULT_LISTS) -> list[Refusal]:
    """Refusals for one name field (league, club or player name, or a name claim value)."""
    refusals: list[Refusal] = []
    if not isinstance(value, str) or not value.strip():
        return [Refusal("name_field_empty", where, "a name field must be a non-empty string")]
    bad = sorted({ch for ch in value if not (unicodedata.category(ch).startswith("L") or ch in _NAME_PUNCT)})
    if bad:
        shown = ", ".join(f"U+{ord(ch):04X}" for ch in bad)
        refusals.append(Refusal("name_field_charset", where, f"characters not allowed in a name: {shown}"))
    if len(value) > NAME_MAX_LEN or len(value.split()) > NAME_MAX_TOKENS:
        refusals.append(Refusal("name_field_length", where, f"name longer than {NAME_MAX_LEN} characters or {NAME_MAX_TOKENS} words"))
    for marker in _marker_hits(value, lists):
        refusals.append(Refusal("injection_marker", where, f"instruction marker {marker!r} in a name field"))
    if lists.use_synth_screen:
        reason = synth_blocklist.blocked_reason(value)
        if reason is not None:
            refusals.append(Refusal("blocked_name", where, f"club or field name screen: {reason}"))
    for phrase in _phrase_hits(_tokens(value), lists, case_sensitive=False):
        refusals.append(Refusal(phrase.category, where, f"blocked name {phrase.label!r}"))
    for word in _greek_hits(_tokens(value), lists):
        refusals.append(Refusal("real_name_greek", where, f"blocked Greek-script name {word!r}"))
    return refusals


def screen_prose(text: str, where: str, lists: RightsLists = DEFAULT_LISTS) -> list[Refusal]:
    """Refusals for one sentence of prose in any supported language."""
    refusals: list[Refusal] = []
    toks = _tokens(text)
    for phrase in _phrase_hits(toks, lists, case_sensitive=True):
        refusals.append(Refusal(phrase.category, where, f"blocked name {phrase.label!r} in prose"))
    for word in _greek_hits(toks, lists):
        refusals.append(Refusal("real_name_greek", where, f"blocked Greek-script name {word!r} in prose"))
    for marker in _marker_hits(text, lists):
        refusals.append(Refusal("injection_marker", where, f"instruction marker {marker!r} in prose"))
    return refusals


def check_roster(roster: Roster, lists: RightsLists = DEFAULT_LISTS) -> list[Refusal]:
    refusals = screen_name_field(roster.league_name, "league", lists)
    for club_id, name in sorted(roster.clubs.items()):
        refusals += screen_name_field(name, f"club {club_id}", lists)
    for player_id, name in sorted(roster.players.items()):
        refusals += screen_name_field(name, f"player {player_id}", lists)
    return refusals


def check_sentences(sentences: Iterable[Sentence], roster: Roster, lists: RightsLists = DEFAULT_LISTS) -> list[Refusal]:
    refusals: list[Refusal] = []
    for s in sentences:
        where = f"sentence {s.sentence_id} ({s.language})"
        refusals += screen_prose(s.text, where, lists)
        for claim in s.claims:
            if claim.type != "name":
                continue
            refusals += screen_name_field(claim.value, where + " name claim", lists)
            if isinstance(claim.value, str) and not roster.ids_for_name(claim.value):
                refusals.append(Refusal("not_on_roster", where, f"{claim.value!r} is not on the fictional roster"))
    return refusals


def run_rights(roster: Roster, sentences: Iterable[Sentence], lists: RightsLists = DEFAULT_LISTS) -> RightsReport:
    refusals = check_roster(roster, lists) + check_sentences(sentences, roster, lists)
    return RightsReport(status="pass" if not refusals else "refused", refusals=tuple(refusals))

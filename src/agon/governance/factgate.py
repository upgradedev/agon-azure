"""Fact Gate: recompute every claimed number, name, minute and score from the event log.

Two checks per sentence, both deterministic:

1. Claims against the log. Each claim object is recomputed with `recompute` over the WHOLE event
   list. This half never reads the prose, so it works the same for every language.
2. Prose against the claims. The integers written in the prose, extracted with the language's
   digit-grouping rule, must equal the integers the claims carry, as a multiset. A number in the
   prose that no claim carries is a mismatch, and so is a claim value the prose never writes. A
   score written as "a-b" must be home first. Names claimed must appear verbatim in the prose (the
   house style keeps fictional names in Latin script in every edition).

Out of scope, on purpose: decimals and numbers spelled as words. House style writes counts as
digits (the easy-read rules require it too). A spelled-out number is therefore invisible to check 2,
which is why a claim value missing from the prose is itself a mismatch.

The gate fails closed: an unknown claim type, statistic, kind, subject or event id is a mismatch.

Season claims (season_streak, table_position, player_season_total) are recomputed from
`facts.season` after the matchday of the reported match. No season data, a reported match that is
not in the season, or a season record whose score disagrees with this match's log is a mismatch.
Their values go through check 2 like every other claim, so "a 3rd win in a row" must carry 3.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from agon.governance import recompute
from agon.governance.claims import SEASON_CLAIM_TYPES, Claim, Sentence
from agon.governance.recompute import MatchFacts, RecomputeError

MAX_IDS_IN_REASON = 6

# Integer runs, with the thousands grouping each language uses. Only ASCII digits are numbers;
# any other Unicode digit is reported, never silently skipped.
_NBSP = chr(0x00A0)  # no-break space, a thousands separator in el, es and fr
_NNBSP = chr(0x202F)  # narrow no-break space, the same
_DOT_GROUPED = re.compile(r"[0-9]{1,3}(?:[." + _NBSP + _NNBSP + r"][0-9]{3})+(?![0-9])|[0-9]+")
_NUMBER_PATTERNS: dict[str, re.Pattern[str]] = {
    "en": re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+(?![0-9])|[0-9]+"),
    "el": _DOT_GROUPED,
    "es": _DOT_GROUPED,
    "fr": _DOT_GROUPED,
}
# "2-1", "2:1" or with an en dash (built with chr() so this file stays dash-free).
_EN_DASH = chr(0x2013)
_SCORE_PATTERN = re.compile(r"([0-9]+)\s*[-:" + _EN_DASH + r"]\s*([0-9]+)")
_MINUTE_VALUE = re.compile(r"^[0-9]+(?:\+[0-9]+)?$")


@dataclass(frozen=True)
class SentenceVerdict:
    sentence_id: str
    language: str
    status: str  # "pass" or "mismatch"
    reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclass(frozen=True)
class GateReport:
    status: str  # "pass" or "mismatch"
    verdicts: tuple[SentenceVerdict, ...]

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def _ids_text(ids: Iterable[str]) -> str:
    ids = list(ids)
    shown = ", ".join(ids[:MAX_IDS_IN_REASON])
    return shown + (f" and {len(ids) - MAX_IDS_IN_REASON} more" if len(ids) > MAX_IDS_IN_REASON else "")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def prose_numbers(text: str, language: str) -> list[int]:
    """Every integer written in digits in `text`, read with the grouping rule of `language`."""
    if language not in _NUMBER_PATTERNS:
        raise ValueError(f"no number rule for language {language!r}")
    found = []
    for match in _NUMBER_PATTERNS[language].finditer(text):
        found.append(int(re.sub(r"[^0-9]", "", match.group(0))))
    return found


def non_ascii_digits(text: str) -> list[str]:
    return [ch for ch in text if ch.isdigit() and not "0" <= ch <= "9"]


def claim_numbers(claim: Claim) -> list[int]:
    """The integers a claim puts into the prose."""
    if claim.type == "score" and isinstance(claim.value, dict):
        return [v for v in (claim.value.get("home"), claim.value.get("away")) if _is_int(v)]
    if claim.type == "stat" and _is_int(claim.value):
        return [claim.value]
    if claim.type == "minute" and isinstance(claim.value, str) and _MINUTE_VALUE.match(claim.value):
        return [int(part) for part in claim.value.split("+")]
    if claim.type in SEASON_CLAIM_TYPES and _is_int(claim.value):
        # A goal difference can be negative; the prose writes its digits ("-2" is read as 2).
        return [abs(claim.value)]
    return []


def _check_ids_exist(claim: Claim, by_id: dict[str, Any]) -> list[str]:
    missing = [eid for eid in claim.event_ids if eid not in by_id]
    if missing:
        return [f"{claim.type} claim cites event ids not in the log: {_ids_text(missing)}"]
    return []


def _check_score(claim: Claim, facts: MatchFacts, by_id: dict[str, Any]) -> list[str]:
    value = claim.value
    if not isinstance(value, dict) or set(value) != {"home", "away"} or not all(_is_int(v) for v in value.values()):
        return ["score claim value must be {home: int, away: int}"]
    last_seq = max((by_id[e].seq for e in claim.event_ids), default=None)
    actual = recompute.score_after(facts, last_seq)
    if actual != {"home": value["home"], "away": value["away"]}:
        when = "at full time" if last_seq is None else f"after event seq {last_seq}"
        return [f"score claimed {value['home']}-{value['away']}, log says {actual['home']}-{actual['away']} {when}"]
    return []


def _check_stat(claim: Claim, facts: MatchFacts, by_id: dict[str, Any]) -> list[str]:
    if claim.stat not in recompute.STAT_KEYS:
        return [f"unknown statistic {claim.stat!r}"]
    if not claim.subject:
        return [f"stat {claim.stat} has no subject"]
    if not _is_int(claim.value) or claim.value < 0:
        return [f"stat {claim.stat} value must be a non-negative int"]
    try:
        actual = recompute.recompute_stat(facts, claim.stat, claim.subject)
    except RecomputeError as exc:
        return [str(exc)]
    reasons: list[str] = []
    if claim.stat in recompute.RATIO_STATS:
        if actual != claim.value:
            reasons.append(f"stat {claim.stat} for {claim.subject}: claimed {claim.value}, log says {actual}")
        return reasons
    counted = recompute.stat_events(facts, claim.stat, claim.subject)
    counted_ids = [e.event_id for e in counted]
    if actual != claim.value:
        reasons.append(
            f"stat {claim.stat} for {claim.subject}: claimed {claim.value}, log says {actual}"
            f" (event ids: {_ids_text(counted_ids)})"
        )
    stray = [eid for eid in claim.event_ids if eid not in set(counted_ids)]
    if stray:
        reasons.append(f"stat {claim.stat} cites events that are not counted for {claim.subject}: {_ids_text(stray)}")
    if claim.value > 0 and not claim.event_ids:
        reasons.append(f"stat {claim.stat} claims {claim.value} with no supporting event ids")
    return reasons


def _check_minute(claim: Claim, facts: MatchFacts, by_id: dict[str, Any]) -> list[str]:
    if not isinstance(claim.value, str) or not _MINUTE_VALUE.match(claim.value):
        return [f"minute claim value {claim.value!r} is not a display minute like '71' or '45+2'"]
    if not claim.event_ids:
        return ["minute claim cites no event"]
    reasons = []
    for eid in claim.event_ids:
        e = by_id[eid]
        shown = recompute.display_minute(e.period, e.minute)
        if shown != claim.value:
            reasons.append(f"minute claimed {claim.value}, event {eid} is at {shown} (period {e.period})")
    return reasons


def _norm_text(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _check_name(claim: Claim, facts: MatchFacts, by_id: dict[str, Any], text: str) -> list[str]:
    if not isinstance(claim.value, str) or not claim.value.strip():
        return ["name claim value must be a non-empty string"]
    ids = facts.roster.ids_for_name(claim.value)
    if not ids:
        return [f"name {claim.value!r} is not on the fictional roster"]
    if claim.subject is not None:
        if claim.subject not in ids:
            return [f"name {claim.value!r} does not belong to {claim.subject}"]
        ids = [claim.subject]
    reasons = []
    for eid in claim.event_ids:
        if not any(recompute.event_involves(facts, by_id[eid], i) for i in ids):
            reasons.append(f"event {eid} does not involve {claim.value!r}")
    if _norm_text(claim.value) not in _norm_text(text):
        reasons.append(f"name {claim.value!r} is claimed but not written in the sentence")
    return reasons


def _check_event_ref(claim: Claim, facts: MatchFacts, by_id: dict[str, Any]) -> list[str]:
    if claim.value not in recompute.EVENT_KINDS:
        return [f"unknown event kind {claim.value!r}"]
    if not claim.event_ids:
        return [f"event_ref {claim.value} cites no event"]
    pred = recompute.EVENT_KINDS[claim.value]
    wrong = [eid for eid in claim.event_ids if not pred(by_id[eid])]
    if wrong:
        return [f"event_ref {claim.value}: events are not of that kind: {_ids_text(wrong)}"]
    return []


def _check_season(claim: Claim, facts: MatchFacts) -> list[str]:
    season = facts.season
    if season is None:
        return [f"{claim.type} claim needs season data; this match was checked without a season"]
    if not facts.events:
        return [f"{claim.type} claim: the match has no events, so its matchday is unknown"]
    match_id = facts.events[0].match_id
    record = season.match_by_id(match_id)
    if record is None:
        return [f"{claim.type} claim: match {match_id} is not in season {season.season}"]
    logged = recompute.score_after(facts, None)
    if (record.home_id, record.away_id, record.home_goals, record.away_goals) != (
        facts.home_id, facts.away_id, logged["home"], logged["away"]
    ):
        return [f"{claim.type} claim: season {season.season} records {match_id} differently from this match's log"]
    if not _is_int(claim.value):
        return [f"{claim.type} value must be an int"]
    if claim.type == "player_season_total" and claim.subject not in facts.roster.players:
        return [f"player_season_total subject {claim.subject!r} is not a rostered player"]
    if claim.value < 0 and not (claim.type == "table_position" and claim.stat == "goal_difference"):
        return [f"{claim.type} value must be a non-negative int"]
    try:
        actual = recompute.season_claim_value(season, claim.type, claim.stat, claim.subject, record.matchday)
    except RecomputeError as exc:
        return [str(exc)]
    if actual != claim.value:
        stat = claim.stat if claim.stat is not None else "position"
        return [
            f"{claim.type} {stat} for {claim.subject} after matchday {record.matchday}: "
            f"claimed {claim.value}, season says {actual}"
        ]
    return []


def check_claim(claim: Claim, facts: MatchFacts, text: str = "", by_id: dict[str, Any] | None = None) -> list[str]:
    """Reasons this claim disagrees with the log; empty means it holds."""
    by_id = by_id if by_id is not None else facts.by_id()
    missing = _check_ids_exist(claim, by_id)
    if missing:
        return missing
    if claim.type == "score":
        return _check_score(claim, facts, by_id)
    if claim.type == "stat":
        return _check_stat(claim, facts, by_id)
    if claim.type == "minute":
        return _check_minute(claim, facts, by_id)
    if claim.type == "name":
        return _check_name(claim, facts, by_id, text)
    if claim.type == "event_ref":
        return _check_event_ref(claim, facts, by_id)
    if claim.type in SEASON_CLAIM_TYPES:
        return _check_season(claim, facts)
    return [f"unknown claim type {claim.type!r}"]


def check_prose_numbers(sentence: Sentence) -> list[str]:
    """Reasons the digits in the prose differ from the claim values; empty means they agree."""
    reasons: list[str] = []
    odd = non_ascii_digits(sentence.text)
    if odd:
        reasons.append(f"non-ASCII digits in the prose: {''.join(odd)!r}")
    written = Counter(prose_numbers(sentence.text, sentence.language))
    claimed: Counter[int] = Counter()
    for claim in sentence.claims:
        claimed.update(claim_numbers(claim))
    for number, count in sorted((written - claimed).items()):
        reasons.append(f"prose writes {number} ({count}x) but no claim carries it")
    for number, count in sorted((claimed - written).items()):
        reasons.append(f"claim value {number} ({count}x) is not written in the prose")
    scores = [c.value for c in sentence.claims if c.type == "score" and isinstance(c.value, dict)]
    for match in _SCORE_PATTERN.finditer(sentence.text):
        pair = (int(match.group(1)), int(match.group(2)))
        if not any(pair == (s.get("home"), s.get("away")) for s in scores):
            reasons.append(f"score written as {pair[0]}-{pair[1]} matches no score claim (home first)")
    return reasons


def check_sentence(sentence: Sentence, facts: MatchFacts, by_id: dict[str, Any] | None = None) -> SentenceVerdict:
    by_id = by_id if by_id is not None else facts.by_id()
    reasons: list[str] = []
    for claim in sentence.claims:
        reasons.extend(check_claim(claim, facts, sentence.text, by_id))
    reasons.extend(check_prose_numbers(sentence))
    return SentenceVerdict(
        sentence_id=sentence.sentence_id,
        language=sentence.language,
        status="pass" if not reasons else "mismatch",
        reasons=tuple(reasons),
    )


def check_translation(source: Sentence, translated: Sentence, facts: MatchFacts) -> SentenceVerdict:
    """factgate_lang: a translation carries exactly the source claims, and its prose agrees with them."""
    verdict = check_sentence(translated, facts)
    reasons = list(verdict.reasons)
    if [c.to_dict() for c in source.claims] != [c.to_dict() for c in translated.claims]:
        reasons.insert(0, f"translation {translated.sentence_id} does not carry the same claims as {source.sentence_id}")
    return SentenceVerdict(
        sentence_id=translated.sentence_id,
        language=translated.language,
        status="pass" if not reasons else "mismatch",
        reasons=tuple(reasons),
    )


def run_factgate(sentences: Iterable[Sentence], facts: MatchFacts) -> GateReport:
    by_id = facts.by_id()
    verdicts = tuple(check_sentence(s, facts, by_id) for s in sentences)
    status = "pass" if verdicts and all(v.passed for v in verdicts) else "mismatch"
    return GateReport(status=status, verdicts=verdicts)

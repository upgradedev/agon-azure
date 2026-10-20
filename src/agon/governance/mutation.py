"""M8: how many seeded wrong facts the Fact Gate catches, per language and per mutation class.

Input is one recorded run folder (`web/runs/run-<id>/`): its `manifest.json` and the five edition
files `report-en.json`, `report-el.json`, `report-es.json`, `report-fr.json`, `report-easy.json`.
The match is rebuilt from the synthetic generator (the run's match id is `s<seed>-m<index>`) and
refused unless its seed hash and event log hash equal the manifest's; each edition file must hash
to the sha256 the manifest seals; and every original sentence must pass the gate before anything
is mutated, or a catch rate would mean nothing.

A mutant is one sentence with one planted fault. Every sentence of every edition is mutated.
Choices that need a seed (which other name, how far a minute moves, which number is added) come
from sha256 of the seed and the mutant's place, so the same seed gives the same mutants on every
machine. A mutation that leaves the sentence identical (a 1-1 score swapped) is skipped and
counted, as is a claimed number the prose does not write in digits.

Covered classes, each a fault the gate is meant to catch:

* `prose_integer_plus_one`: one integer in the prose goes up by one; the claims stay true. Only
  the prose-against-claims check can see it.
* `claim_value_plus_one`: one score side or one stat value goes up by one in the claim AND in the
  prose, so the two agree and only the recompute from the event log can see it.
* `home_away_swapped`: a score whose sides differ is written away first, in two variants: in the
  prose only (the claim keeps home first), and in the claim and the prose together.
* `minute_shifted`: a minute claim moves by a seeded 1 to 3 minutes, in the claim and the prose.
* `roster_name_swapped`: one roster name written in the prose becomes another roster name of the
  same kind (club for club, player for player), seeded. A claimed name moves in its claim too,
  value and subject, so claim and prose agree; a name the sentence writes without claiming it
  changes in the prose only. Names are not limited to the claimed ones.
* `unclaimed_number_added`: a short clause carrying a seeded integer that no claim carries.

Uncovered by design, reported apart and never counted in the catch rates (the gate reads integers
written in digits only; see the agon.governance.factgate docstring):

* `spelled_out_number_added`: the same clause with the number written as a word.
* `decimal_number_added`: a clause with a decimal. The gate has no decimal rule; it reads the digit
  runs on each side of the decimal mark as integers, so a flag here is incidental.

The gate is run as the pipeline runs it (agon.fleet.steps.factgate and factgate_lang):
`check_sentence` for the English edition, `check_translation(English source, mutant)` for el, es,
fr and easy. The translation check also refuses any claim that differs from the English source,
which alone catches every claim mutation in a translation, so the report also counts
`check_sentence` alone, the part that reads each language's prose.

    python -m agon.governance.mutation --run web/runs/run-c8a19020c98a [--seed 8]
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from agon.governance import factgate
from agon.governance.claims import Claim, Sentence
from agon.governance.recompute import MatchFacts, Roster
from agon.governance.seal import canonical_utf8, event_log_sha256, seed_sha256, sha256_hex
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.world import build_world

DEFAULT_SEED = 8
EDITIONS: tuple[str, ...] = ("en", "el", "es", "fr", "easy")
SOURCE_EDITION = "en"
COVERED: tuple[str, ...] = (
    "prose_integer_plus_one",
    "claim_value_plus_one",
    "home_away_swapped",
    "minute_shifted",
    "roster_name_swapped",
    "unclaimed_number_added",
)
UNCOVERED: tuple[str, ...] = ("spelled_out_number_added", "decimal_number_added")
CLASSES: tuple[str, ...] = COVERED + UNCOVERED
UNCOVERED_NOTE = {
    "spelled_out_number_added": "the gate reads digits only, by design",
    "decimal_number_added": "incidental: the digit runs on each side of the decimal mark are read as integers",
}

_MATCH_ID = re.compile(r"^s([0-9]+)-m([0-9]+)$")
_INT = re.compile(r"(?<![0-9])[0-9]+(?![0-9])")
_EN_DASH = chr(0x2013)  # built with chr() so this file stays dash-free, as in factgate
_SCORE = re.compile(r"(?<![0-9])([0-9]+)\s*[-:" + _EN_DASH + r"]\s*([0-9]+)(?![0-9])")
_EN_ORDINAL = re.compile(r"^(st|nd|rd|th)\b")
_SENTENCE_END = re.compile(r"[.!?]+\s*$")
_MINUTE_SHIFTS: tuple[int, ...] = (-3, -2, -1, 1, 2, 3)
_ADDED_INTEGERS: tuple[int, ...] = tuple(range(2, 100))
_NUMBER_CLAUSE = {
    "en": " in front of {n} fans",
    "el": " μπροστά σε {n} φιλάθλους",
    "es": " ante {n} aficionados",
    "fr": " devant {n} spectateurs",
}
_WORDS = {
    "en": ("two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"),
    "el": ("δύο", "τρεις", "τέσσερις", "πέντε", "έξι", "επτά", "οκτώ", "εννέα", "δέκα", "έντεκα", "δώδεκα"),
    "es": ("dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce"),
    "fr": ("deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze"),
}
_DECIMAL_CLAUSE = {
    "en": ", with {x} expected goals",
    "el": ", με {x} αναμενόμενα γκολ",
    "es": ", con {x} goles esperados",
    "fr": ", avec {x} buts attendus",
}
_DECIMAL_MARK = {"en": ".", "el": ",", "es": ",", "fr": ","}
_DECIMALS: tuple[tuple[int, int], ...] = ((0, 4), (0, 7), (1, 1), (1, 4), (1, 8), (2, 3))


class MutationError(ValueError):
    """The run folder cannot be measured: missing files, hashes that disagree, or a failing original."""


# ----------------------------------------------------------------- the recorded run


@dataclass(frozen=True)
class Edition:
    edition: str
    language: str
    sentences: tuple[Sentence, ...]


@dataclass(frozen=True)
class RecordedRun:
    path: Path
    bundle_sha256: str
    match_id: str
    facts: MatchFacts
    editions: tuple[Edition, ...]

    def edition(self, name: str) -> Edition:
        return next(e for e in self.editions if e.edition == name)

    def sentence_count(self) -> int:
        return sum(len(e.sentences) for e in self.editions)


def _facts_for(match_id: str, manifest: dict[str, Any]) -> MatchFacts:
    m = _MATCH_ID.match(match_id)
    if m is None:
        raise MutationError(f"match id {match_id!r} is not s<seed>-m<index>; only synthetic matches can be rebuilt")
    seed, index = int(m.group(1)), int(m.group(2))
    if seed_sha256(seed, index, GENERATOR_VERSION) != manifest.get("seed_sha256"):
        raise MutationError(f"seed {seed}, match {index}, {GENERATOR_VERSION} does not hash to the manifest's seed_sha256")
    league = build_world(seed)
    result = simulate_match(league, index)
    if event_log_sha256(result.events) != manifest.get("event_log_sha256"):
        raise MutationError(f"the rebuilt event log of {match_id} does not hash to the manifest's event_log_sha256")
    return MatchFacts(
        events=tuple(result.events),
        home_id=result.meta["home"],
        away_id=result.meta["away"],
        roster=Roster.from_league(league),
    )


def _baseline(run: RecordedRun) -> None:
    sources = {s.sentence_id: s for s in run.edition(SOURCE_EDITION).sentences}
    for ed in run.editions:
        for s in ed.sentences:
            verdict = _gate(run, sources, ed.edition, s)
            if not verdict.passed:
                raise MutationError(f"{ed.edition}/{s.sentence_id} fails the gate before any mutation: {'; '.join(verdict.reasons)}")


def load_run(run_dir: Path | str) -> RecordedRun:
    """Read a run folder, rebuild its match and refuse anything that does not hash as sealed."""
    path = Path(run_dir)
    try:
        manifest_bytes = (path / "manifest.json").read_bytes()
    except OSError as exc:
        raise MutationError(f"{path}: no manifest.json") from exc
    manifest = json.loads(manifest_bytes)
    match_id = str(manifest.get("match_id", ""))
    facts = _facts_for(match_id, manifest)
    editions = []
    for name in EDITIONS:
        file_name = f"report-{name}.json"
        entry = manifest.get("artifacts", {}).get(file_name)
        try:
            data = (path / file_name).read_bytes()
        except OSError as exc:
            raise MutationError(f"{path}: no {file_name}") from exc
        if not isinstance(entry, dict) or sha256_hex(data) != entry.get("sha256"):
            raise MutationError(f"{file_name} does not hash to the sha256 the manifest seals")
        doc = json.loads(data)
        if doc.get("edition") != name or doc.get("match_id") != match_id:
            raise MutationError(f"{file_name} names edition {doc.get('edition')!r} of {doc.get('match_id')!r}")
        sentences = tuple(Sentence.from_dict(row) for row in doc.get("sentences", []))
        if not sentences:
            raise MutationError(f"{file_name} has no sentences")
        editions.append(Edition(name, str(doc.get("language")), sentences))
    run = RecordedRun(path, sha256_hex(manifest_bytes), match_id, facts, tuple(editions))
    _baseline(run)
    return run


# ----------------------------------------------------------------- the generator


@dataclass(frozen=True)
class Mutant:
    mutant_id: str
    edition: str
    language: str
    mutation: str
    detail: str
    original: Sentence
    sentence: Sentence

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutant_id": self.mutant_id,
            "edition": self.edition,
            "language": self.language,
            "mutation": self.mutation,
            "detail": self.detail,
            "sentence": self.sentence.to_dict(),
        }


@dataclass(frozen=True)
class Skip:
    edition: str
    sentence_id: str
    mutation: str
    reason: str  # "no_op" or "not_placeable"
    detail: str


@dataclass(frozen=True)
class _Slot:
    """Where one claimed integer is written in the prose."""

    claim: int
    part: str  # "home", "away", "value", "minute", "added"
    start: int
    end: int
    value: int


def _pick(seed: int, key: str, options: Sequence[Any]) -> Any:
    if not options:
        raise MutationError(f"no option to pick for {key}")
    digest = hashlib.sha256(f"agon-mutation|{seed}|{key}".encode("utf-8")).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]


def _parts(claim: Claim) -> list[tuple[str, int]]:
    numbers = factgate.claim_numbers(claim)
    if claim.type == "minute":
        return list(zip(("minute", "added"), numbers))
    return [("value", n) for n in numbers]


def _slots(sentence: Sentence) -> tuple[list[_Slot], list[int]]:
    """Place each claimed integer in the prose. Returns the slots and the claims that could not be placed."""
    text = sentence.text
    scores = list(_SCORE.finditer(text))
    slots: list[_Slot] = []
    unplaced: list[int] = []
    taken_scores: set[int] = set()
    for i, claim in enumerate(sentence.claims):
        if claim.type != "score" or not isinstance(claim.value, dict):
            continue
        pair = (claim.value.get("home"), claim.value.get("away"))
        for k, m in enumerate(scores):
            if k not in taken_scores and (int(m.group(1)), int(m.group(2))) == pair:
                taken_scores.add(k)
                slots.append(_Slot(i, "home", m.start(1), m.end(1), int(pair[0])))
                slots.append(_Slot(i, "away", m.start(2), m.end(2), int(pair[1])))
                break
        else:
            unplaced.append(i)
    in_score = [(m.start(), m.end()) for m in scores]
    tokens = [m for m in _INT.finditer(text) if not any(a <= m.start() < b for a, b in in_score)]
    taken: set[int] = set()
    for i, claim in enumerate(sentence.claims):
        if claim.type == "score":
            continue
        for part, value in _parts(claim):
            for k, m in enumerate(tokens):
                if k not in taken and int(m.group(0)) == value:
                    taken.add(k)
                    slots.append(_Slot(i, part, m.start(), m.end(), value))
                    break
            else:
                unplaced.append(i)
    return slots, sorted(set(unplaced))


def _put(text: str, start: int, end: int, value: int, language: str) -> str:
    """Write `value` over text[start:end]; an English ordinal suffix follows the new number."""
    rest = text[end:]
    suffix = _EN_ORDINAL.match(rest) if language == "en" else None
    if suffix is not None:
        n = value % 100
        new_suffix = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
        rest = new_suffix + rest[suffix.end():]
    return text[:start] + str(value) + rest


def _with_claim(sentence: Sentence, index: int, claim: Claim, text: str | None = None) -> Sentence:
    claims = list(sentence.claims)
    claims[index] = claim
    return dataclasses.replace(sentence, text=sentence.text if text is None else text, claims=tuple(claims))


def _insert(text: str, clause: str) -> str:
    end = _SENTENCE_END.search(text)
    return text + clause if end is None else text[: end.start()] + clause + text[end.start():]


Built = Iterator["tuple[str, Sentence] | Skip"]


def _prose_integer_plus_one(s: Sentence, ed: Edition, roster: Roster, seed: int) -> Built:
    for m in _INT.finditer(s.text):
        n = int(m.group(0))
        yield f"prose {n} -> {n + 1} at character {m.start()}", dataclasses.replace(s, text=_put(s.text, m.start(), m.end(), n + 1, s.language))


def _unplaced(s: Sentence, ed: Edition, mutation: str, claims: list[int], kinds: tuple[str, ...]) -> Iterator[Skip]:
    for i in claims:
        if s.claims[i].type in kinds:
            yield Skip(ed.edition, s.sentence_id, mutation, "not_placeable", f"claim {i} ({s.claims[i].type}) is not written in digits")


def _claim_value_plus_one(s: Sentence, ed: Edition, roster: Roster, seed: int) -> Built:
    slots, unplaced = _slots(s)
    yield from _unplaced(s, ed, "claim_value_plus_one", unplaced, ("score", "stat"))
    for slot in slots:
        claim = s.claims[slot.claim]
        if claim.type == "score":
            value: Any = {**claim.value, slot.part: slot.value + 1}
        elif claim.type == "stat":
            value = slot.value + 1
        else:
            continue
        text = _put(s.text, slot.start, slot.end, slot.value + 1, s.language)
        detail = f"{claim.type} {slot.part if claim.type == 'score' else claim.stat} {slot.value} -> {slot.value + 1} in claim {slot.claim} and the prose"
        yield detail, _with_claim(s, slot.claim, dataclasses.replace(claim, value=value), text)


def _home_away_swapped(s: Sentence, ed: Edition, roster: Roster, seed: int) -> Built:
    slots, unplaced = _slots(s)
    yield from _unplaced(s, ed, "home_away_swapped", unplaced, ("score",))
    for i, claim in enumerate(s.claims):
        if claim.type != "score":
            continue
        pair = {sl.part: sl for sl in slots if sl.claim == i}
        if set(pair) != {"home", "away"}:
            continue
        home, away = pair["home"], pair["away"]
        text = s.text[: home.start] + str(away.value) + s.text[home.end: away.start] + str(home.value) + s.text[away.end:]
        written = f"{away.value}-{home.value}"
        yield f"score written {written} in the prose, claim {i} kept home first", dataclasses.replace(s, text=text)
        swapped = dataclasses.replace(claim, value={"home": away.value, "away": home.value})
        yield f"score {written} in claim {i} and the prose", _with_claim(s, i, swapped, text)


def _minute_shifted(s: Sentence, ed: Edition, roster: Roster, seed: int) -> Built:
    slots, unplaced = _slots(s)
    yield from _unplaced(s, ed, "minute_shifted", unplaced, ("minute",))
    for i, claim in enumerate(s.claims):
        if claim.type != "minute":
            continue
        parts = {sl.part: sl for sl in slots if sl.claim == i}
        target = parts.get("added") or parts.get("minute")
        if target is None:
            continue
        shifts = [d for d in _MINUTE_SHIFTS if target.value + d >= 1]
        delta = _pick(seed, f"{ed.edition}|{s.sentence_id}|minute|{i}", shifts)
        moved = target.value + delta
        if target.part == "added":
            value = f"{parts['minute'].value}+{moved}"
        else:
            value = str(moved)
        text = _put(s.text, target.start, target.end, moved, s.language)
        detail = f"minute {claim.value} -> {value} in claim {i} and the prose"
        yield detail, _with_claim(s, i, dataclasses.replace(claim, value=value), text)


def _name_occurrences(text: str, roster: Roster) -> list[tuple[int, int, str, str]]:
    """(start, end, kind, id) of every club and player name written in `text`, longest name first."""
    entries = [("club", cid, name) for cid, name in roster.clubs.items()]
    entries += [("player", pid, name) for pid, name in roster.players.items()]
    entries.sort(key=lambda e: (-len(e[2]), e[0], e[1]))
    found: list[tuple[int, int, str, str]] = []
    for kind, entity_id, name in entries:
        start = text.find(name)
        while start >= 0:
            end = start + len(name)
            if not any(a < end and start < b for a, b, _, _ in found):
                found.append((start, end, kind, entity_id))
            start = text.find(name, start + 1)
    return sorted(found)


def _roster_name_swapped(s: Sentence, ed: Edition, roster: Roster, seed: int) -> Built:
    names = {"club": dict(roster.clubs), "player": dict(roster.players)}
    used_claims: set[int] = set()
    for start, end, kind, entity_id in _name_occurrences(s.text, roster):
        original = names[kind][entity_id]
        options = sorted((eid, n) for eid, n in names[kind].items() if n != original)
        new_id, new_name = _pick(seed, f"{ed.edition}|{s.sentence_id}|name|{start}", options)
        text = s.text[:start] + new_name + s.text[end:]
        index = next(
            (i for i, c in enumerate(s.claims) if c.type == "name" and c.value == original and i not in used_claims),
            None,
        )
        if index is None:
            yield f"{kind} {original!r} -> {new_name!r} in the prose (the sentence claims no such name)", dataclasses.replace(s, text=text)
            continue
        used_claims.add(index)
        claim = s.claims[index]
        moved = dataclasses.replace(claim, value=new_name, subject=None if claim.subject is None else new_id)
        yield f"{kind} {original!r} -> {new_name!r} in claim {index} (value and subject) and the prose", _with_claim(s, index, moved, text)


def _unclaimed_number_added(s: Sentence, ed: Edition, roster: Roster, seed: int) -> Built:
    present = set(factgate.prose_numbers(s.text, s.language))
    for claim in s.claims:
        present.update(factgate.claim_numbers(claim))
    n = _pick(seed, f"{ed.edition}|{s.sentence_id}|added", [v for v in _ADDED_INTEGERS if v not in present])
    yield f"clause with {n}, which no claim carries", dataclasses.replace(s, text=_insert(s.text, _NUMBER_CLAUSE[s.language].format(n=n)))


def _spelled_out_number_added(s: Sentence, ed: Edition, roster: Roster, seed: int) -> Built:
    word = _pick(seed, f"{ed.edition}|{s.sentence_id}|word", _WORDS[s.language])
    yield f"clause with {word!r}", dataclasses.replace(s, text=_insert(s.text, _NUMBER_CLAUSE[s.language].format(n=word)))


def _decimal_number_added(s: Sentence, ed: Edition, roster: Roster, seed: int) -> Built:
    whole, tenth = _pick(seed, f"{ed.edition}|{s.sentence_id}|decimal", _DECIMALS)
    x = f"{whole}{_DECIMAL_MARK[s.language]}{tenth}"
    yield f"clause with {x}", dataclasses.replace(s, text=_insert(s.text, _DECIMAL_CLAUSE[s.language].format(x=x)))


_BUILDERS: dict[str, Callable[[Sentence, Edition, Roster, int], Built]] = {
    "prose_integer_plus_one": _prose_integer_plus_one,
    "claim_value_plus_one": _claim_value_plus_one,
    "home_away_swapped": _home_away_swapped,
    "minute_shifted": _minute_shifted,
    "roster_name_swapped": _roster_name_swapped,
    "unclaimed_number_added": _unclaimed_number_added,
    "spelled_out_number_added": _spelled_out_number_added,
    "decimal_number_added": _decimal_number_added,
}
assert tuple(_BUILDERS) == CLASSES


def generate(run: RecordedRun, seed: int = DEFAULT_SEED) -> tuple[list[Mutant], list[Skip]]:
    """Every mutant of every sentence of every edition, in a fixed order, and what was skipped."""
    mutants: list[Mutant] = []
    skips: list[Skip] = []
    for ed in run.editions:
        for s in ed.sentences:
            for mutation in CLASSES:
                n = 0
                for item in _BUILDERS[mutation](s, ed, run.facts.roster, seed):
                    if isinstance(item, Skip):
                        skips.append(item)
                        continue
                    detail, mutated = item
                    if mutated.text == s.text and mutated.claims == s.claims:
                        skips.append(Skip(ed.edition, s.sentence_id, mutation, "no_op", detail))
                        continue
                    n += 1
                    mutants.append(Mutant(f"{ed.edition}/{s.sentence_id}/{mutation}/{n}", ed.edition, ed.language, mutation, detail, s, mutated))
    return mutants, skips


def fingerprint(mutants: Sequence[Mutant]) -> str:
    """sha256 of the canonical mutant list: equal fingerprints mean byte-identical mutants."""
    return sha256_hex(canonical_utf8([m.to_dict() for m in mutants]))


# ----------------------------------------------------------------- the gate over the mutants


@dataclass(frozen=True)
class Outcome:
    mutant: Mutant
    caught: bool  # the gate as the pipeline runs it
    caught_alone: bool  # check_sentence alone
    reasons: tuple[str, ...]


def _gate(run: RecordedRun, sources: dict[str, Sentence], edition: str, sentence: Sentence) -> factgate.SentenceVerdict:
    if edition == SOURCE_EDITION:
        return factgate.check_sentence(sentence, run.facts)
    source = sources[sentence.sentence_id.removesuffix(f"-{edition}")]
    return factgate.check_translation(source, sentence, run.facts)


def run_gate(run: RecordedRun, mutants: Sequence[Mutant]) -> list[Outcome]:
    sources = {s.sentence_id: s for s in run.edition(SOURCE_EDITION).sentences}
    by_id = run.facts.by_id()
    outcomes = []
    for m in mutants:
        alone = factgate.check_sentence(m.sentence, run.facts, by_id)
        pipeline = alone if m.edition == SOURCE_EDITION else _gate(run, sources, m.edition, m.sentence)
        outcomes.append(Outcome(m, not pipeline.passed, not alone.passed, pipeline.reasons))
    return outcomes


@dataclass(frozen=True)
class Tally:
    caught: int = 0
    caught_alone: int = 0
    total: int = 0

    def add(self, outcome: Outcome) -> "Tally":
        return Tally(self.caught + outcome.caught, self.caught_alone + outcome.caught_alone, self.total + 1)


def pct(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{100 * part / whole:.1f}%"


@dataclass(frozen=True)
class Report:
    seed: int
    run: RecordedRun
    mutants: tuple[Mutant, ...]
    skips: tuple[Skip, ...]
    outcomes: tuple[Outcome, ...]

    def by_edition(self) -> dict[str, Tally]:
        out = {e: Tally() for e in EDITIONS}
        for o in self.outcomes:
            if o.mutant.mutation in COVERED:
                out[o.mutant.edition] = out[o.mutant.edition].add(o)
        return out

    def by_class(self) -> dict[str, Tally]:
        out = {c: Tally() for c in CLASSES}
        for o in self.outcomes:
            out[o.mutant.mutation] = out[o.mutant.mutation].add(o)
        return out

    def covered_total(self) -> Tally:
        total = Tally()
        for o in self.outcomes:
            if o.mutant.mutation in COVERED:
                total = total.add(o)
        return total

    def missed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.mutant.mutation in COVERED and not o.caught]


def measure(run_dir: Path | str, seed: int = DEFAULT_SEED) -> Report:
    run = load_run(run_dir)
    mutants, skips = generate(run, seed)
    return Report(seed, run, tuple(mutants), tuple(skips), tuple(run_gate(run, mutants)))


def _row(label: str, t: Tally) -> str:
    return f"{label:<26} caught {t.caught:>3} of {t.total:>3} ({pct(t.caught, t.total):>6})   check_sentence alone {t.caught_alone:>3} of {t.total:>3} ({pct(t.caught_alone, t.total):>6})"


def render(report: Report, run_label: str) -> str:
    run = report.run
    skipped: dict[str, int] = {}
    for sk in report.skips:
        skipped[sk.reason] = skipped.get(sk.reason, 0) + 1
    languages = {e.edition: e.language for e in run.editions}
    lines = [
        "M8 Fact Gate mutation test (self-measured)",
        f"run {run_label}, bundle {run.bundle_sha256[:12]}, match {run.match_id}, seed {report.seed}",
        f"sentences {run.sentence_count()}; mutants {len(report.mutants)}; skipped {len(report.skips)}"
        f" (no-op {skipped.get('no_op', 0)}, not written in digits {skipped.get('not_placeable', 0)});"
        f" mutant list sha256 {fingerprint(report.mutants)}",
        "gate: check_sentence for en, check_translation(en source, mutant) for el, es, fr and easy, as the pipeline runs it",
        "",
        "Covered classes, by language",
    ]
    for edition, tally in report.by_edition().items():
        label = edition if languages[edition] == edition else f"{edition} ({languages[edition]})"
        lines.append(_row(label, tally))
    lines.append(_row("all languages", report.covered_total()))
    lines += ["", "Covered classes, by class"]
    by_class = report.by_class()
    for mutation in COVERED:
        lines.append(_row(mutation, by_class[mutation]))
    lines += ["", "Uncovered by design (reported, never counted in the rates above)"]
    for mutation in UNCOVERED:
        t = by_class[mutation]
        lines.append(f"{mutation:<26} uncovered; flagged {t.caught:>3} of {t.total:>3} ({pct(t.caught, t.total):>6}): {UNCOVERED_NOTE[mutation]}")
    missed = report.missed()
    lines += ["", f"Missed by the gate as the pipeline runs it: {len(missed)}"]
    for o in missed:
        lines.append(f"  {o.mutant.mutant_id}: {o.mutant.detail}: {o.mutant.sentence.text}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seeded mutants of a recorded run, through the Fact Gate (M8).")
    parser.add_argument("--run", required=True, help="a recorded run folder, for example web/runs/run-c8a19020c98a")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"mutation seed (default {DEFAULT_SEED})")
    args = parser.parse_args(argv)
    try:
        report = measure(args.run, args.seed)
    except MutationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Greek, Spanish and French prose in the missed list
    sys.stdout.write(render(report, Path(args.run).as_posix()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

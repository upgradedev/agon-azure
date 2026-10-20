"""M8: the seeded mutation test of the Fact Gate over the recorded run.

The generator is pinned (same seed, same mutants, same counts, same sha256 of the canonical mutant
list), and so are the catch counts, so a green CI run is the evidence behind the M8 numbers. A
change to the gate or to the generator that moves any number fails here and must update the pin,
the README row and the command output together.

The headline is check_sentence on every edition. The translation stage is the realistic
translation fault: el, es and fr prose mutated with the claims copied from English, as
agon.fleet.steps copies them. The claims-equality line of check_translation is defence in depth
and never leads.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest

from agon.governance import factgate
from agon.governance import mutation as M

REPO = Path(__file__).resolve().parents[2]
RUN = REPO / "web" / "runs" / "run-c8a19020c98a"

PINNED_FINGERPRINT = "ec17478e219e7a3f69d08cfa27afc86a810839bb0107bd65e41b59700bb8f71d"
PINNED_MUTANTS = {
    "prose_integer_plus_one": 65,
    "claim_value_plus_one": 50,
    "home_away_swapped": 10,
    "minute_shifted": 15,
    "roster_name_swapped": 55,
    "unclaimed_number_added": 30,
    "spelled_out_number_added": 30,
    "decimal_number_added": 30,
}
# check_sentence, covered classes: (caught, mutants)
PINNED_BY_EDITION = {e: (37, 45) for e in M.EDITIONS}
# with check_translation's claims-equality line added (defence in depth): (caught, mutants)
PINNED_WITH_CLAIMS_BY_EDITION = {"en": (37, 45), "el": (43, 45), "es": (43, 45), "fr": (43, 45), "easy": (43, 45)}
# (check_sentence caught, with the claims-equality line caught, mutants)
PINNED_BY_CLASS = {
    "prose_integer_plus_one": (65, 65, 65),
    "claim_value_plus_one": (50, 50, 50),
    "home_away_swapped": (10, 10, 10),
    "minute_shifted": (15, 15, 15),
    "roster_name_swapped": (15, 39, 55),
    "unclaimed_number_added": (30, 30, 30),
    "spelled_out_number_added": (0, 0, 30),
    "decimal_number_added": (30, 30, 30),
}
# The translation stage: (caught, staged sentences)
PINNED_STAGE_BY_EDITION = {"el": (32, 34), "es": (32, 34), "fr": (31, 33)}
PINNED_STAGE_BY_CLASS = {
    "prose_integer_plus_one": (39, 39),
    "claim_value_plus_one": (0, 0),
    "home_away_swapped": (3, 3),
    "minute_shifted": (8, 8),
    "roster_name_swapped": (27, 33),
    "unclaimed_number_added": (18, 18),
}
SEEDED = ("minute_shifted", "roster_name_swapped", "unclaimed_number_added", "spelled_out_number_added", "decimal_number_added")


@pytest.fixture(scope="module")
def report() -> M.Report:
    return M.measure(RUN)


def _by_id(report: M.Report) -> dict[str, M.Mutant]:
    return {m.mutant_id: m for m in report.mutants}


def _pair(t: M.Tally) -> tuple[int, int]:
    return (t.caught, t.total)


# ----------------------------------------------------------------- the generator is pinned


def test_the_generator_is_pinned(report: M.Report) -> None:
    assert report.seed == M.DEFAULT_SEED == 8
    assert report.run.sentence_count() == 30
    assert Counter(m.mutation for m in report.mutants) == PINNED_MUTANTS
    assert Counter(m.edition for m in report.mutants) == {e: 57 for e in M.EDITIONS}
    assert Counter((s.mutation, s.reason) for s in report.skips) == {("home_away_swapped", "no_op"): 20}
    assert len({m.mutant_id for m in report.mutants}) == len(report.mutants) == 285
    assert M.fingerprint(report.mutants) == PINNED_FINGERPRINT


def test_the_same_seed_gives_the_same_mutants() -> None:
    run = M.load_run(RUN)
    first, first_skips = M.generate(run, 8)
    second, second_skips = M.generate(run, 8)
    assert first == second and first_skips == second_skips
    assert M.fingerprint(first) == M.fingerprint(second) == PINNED_FINGERPRINT


def test_another_seed_moves_only_the_seeded_choices() -> None:
    run = M.load_run(RUN)
    eight, _ = M.generate(run, 8)
    nine, _ = M.generate(run, 9)
    assert Counter(m.mutation for m in nine) == PINNED_MUTANTS
    for mutation in M.CLASSES:
        a = [m.to_dict() for m in eight if m.mutation == mutation]
        b = [m.to_dict() for m in nine if m.mutation == mutation]
        if mutation in SEEDED:
            assert a != b, f"{mutation} takes its choices from the seed"
        else:
            assert a == b, f"{mutation} has no seeded choice"


def test_every_mutant_differs_from_its_original(report: M.Report) -> None:
    for m in report.mutants:
        assert (m.sentence.text, m.sentence.claims) != (m.original.text, m.original.claims), m.mutant_id
        assert m.sentence.sentence_id == m.original.sentence_id and m.sentence.language == m.original.language


def test_the_easy_read_prose_equals_the_english_prose_in_this_run(report: M.Report) -> None:
    """Why the easy-read edition is never presented as a separate language result."""
    en = [s.text for s in report.run.edition("en").sentences]
    assert [s.text for s in report.run.edition("easy").sentences] == en


# ----------------------------------------------------------------- each class plants what it says


def test_each_class_plants_its_fault(report: M.Report) -> None:
    by_id = _by_id(report)
    original = "Liro Ellow put Zennmark Town 0-1 ahead in the 4th minute."

    prose = by_id["en/s1/prose_integer_plus_one/3"]
    assert prose.original.text == original
    assert prose.sentence.text == "Liro Ellow put Zennmark Town 0-1 ahead in the 5th minute."
    assert prose.sentence.claims == prose.original.claims

    stat = by_id["en/s5/claim_value_plus_one/1"]
    assert stat.sentence.text == "Pellmoor Harriers recorded 8 shots, while Zennmark Town had 11."
    assert [c.value for c in stat.sentence.claims if c.type == "stat"] == [8, 11]

    written, both = by_id["en/s1/home_away_swapped/1"], by_id["en/s1/home_away_swapped/2"]
    assert written.sentence.text == both.sentence.text == "Liro Ellow put Zennmark Town 1-0 ahead in the 4th minute."
    assert written.sentence.claims == written.original.claims
    assert [c.value for c in both.sentence.claims if c.type == "score"] == [{"home": 1, "away": 0}]

    minute = by_id["en/s3/minute_shifted/1"]
    claimed = next(c.value for c in minute.sentence.claims if c.type == "minute")
    assert claimed != "81" and f" {claimed}" in minute.sentence.text
    assert re.search(r"\b" + claimed + r"(st|nd|rd|th) minute", minute.sentence.text), "the English ordinal follows the number"

    added = by_id["en/s2/unclaimed_number_added/1"]
    n = int(re.search(r"in front of ([0-9]+) fans\.$", added.sentence.text).group(1))
    claimed_numbers = {v for c in added.original.claims for v in factgate.claim_numbers(c)}
    assert claimed_numbers == {1} and n not in claimed_numbers and added.sentence.claims == added.original.claims

    for m in report.mutants:
        if m.mutation != "roster_name_swapped" or "in claim" not in m.detail:
            continue
        moved = [c for c, o in zip(m.sentence.claims, m.original.claims) if c != o]
        assert len(moved) == 1 and moved[0].type == "name" and moved[0].value in m.sentence.text, m.mutant_id
        assert moved[0].subject in report.run.facts.roster.ids_for_name(moved[0].value), m.mutant_id


def test_a_one_one_score_swap_is_skipped_as_a_no_op(report: M.Report) -> None:
    """s2 and s3 write 1-1 in every edition: both swap variants are identical to the original."""
    expected = Counter({(e, sid if e == "en" else f"{sid}-{e}"): 2 for e in M.EDITIONS for sid in ("s2", "s3")})
    assert Counter((s.edition, s.sentence_id) for s in report.skips) == expected
    assert all(s.reason == "no_op" and s.mutation == "home_away_swapped" for s in report.skips)


# ----------------------------------------------------------------- the measured numbers are pinned


def test_check_sentence_counts_are_pinned_and_equal_in_every_edition(report: M.Report) -> None:
    assert {e: _pair(t) for e, t in report.by_edition().items()} == PINNED_BY_EDITION
    assert _pair(report.covered_total()) == (185, 225)
    by_class = report.by_class()
    with_claims = report.by_class(with_claims=True)
    assert {c: (by_class[c].caught, with_claims[c].caught, by_class[c].total) for c in M.CLASSES} == PINNED_BY_CLASS


def test_the_claims_equality_line_is_reported_apart_as_defence_in_depth(report: M.Report) -> None:
    assert {e: _pair(t) for e, t in report.by_edition(with_claims=True).items()} == PINNED_WITH_CLAIMS_BY_EDITION
    assert _pair(report.covered_total(with_claims=True)) == (209, 225)
    extra = [o for o in report.covered() if o.caught_with_claims_check and not o.caught]
    assert len(extra) == 24
    assert all(o.mutant.edition != "en" and o.mutant.sentence.claims != o.mutant.original.claims for o in extra), (
        "every extra catch is a claim-changed translation, which the pipeline cannot produce"
    )


def test_the_translation_stage_is_pinned(report: M.Report) -> None:
    assert (len(report.staged), report.staged_repeats) == (101, 34)
    assert {e: _pair(t) for e, t in report.stage_by_edition().items()} == PINNED_STAGE_BY_EDITION
    assert {c: _pair(t) for c, t in report.stage_by_class().items()} == PINNED_STAGE_BY_CLASS
    assert _pair(report.stage_total()) == (95, 101)


def test_translation_stage_sentences_carry_the_english_claims(report: M.Report) -> None:
    sources = {s.sentence_id: s for s in report.run.edition("en").sentences}
    for s in report.staged:
        assert s.mutant.edition in M.TRANSLATED
        assert s.sentence.claims == sources[s.sentence.sentence_id.rsplit("-", 1)[0]].claims
        assert s.caught == (not factgate.check_sentence(s.sentence, report.run.facts).passed), (
            "with the claims copied, check_translation decides exactly as check_sentence"
        )


def test_a_wrong_club_in_translated_prose_with_the_copied_claims_is_caught(report: M.Report) -> None:
    """The realistic version of a claimed club swap in a translation: caught by the name check."""
    claimed = [s for s in report.staged if s.mutant.mutation == "roster_name_swapped" and "in claim" in s.mutant.detail]
    clubs = [s for s in claimed if s.mutant.detail.startswith("club ")]
    assert (len(claimed), len(clubs)) == (27, 18)
    assert all(s.caught for s in claimed)
    assert all(any("is claimed but not written" in r for r in s.reasons) for s in claimed)


def test_uncovered_classes_are_reported_apart_and_never_counted(report: M.Report) -> None:
    assert report.covered_total().total == len(report.covered()) == 225
    assert all(o.mutant.mutation in M.COVERED for o in report.missed())
    spelled = [o for o in report.outcomes if o.mutant.mutation == "spelled_out_number_added"]
    assert len(spelled) == 30 and not any(o.caught for o in spelled), "invisible to the gate, by design"


def test_every_realistic_miss_is_a_club_name_swap(report: M.Report) -> None:
    missed = report.missed()
    assert len(missed) == 16
    assert {o.mutant.mutation for o in missed} == {"roster_name_swapped"}
    assert all(o.mutant.detail.startswith("club ") for o in missed)
    assert Counter(o.mutant.edition for o in missed) == {"en": 8, "el": 2, "es": 2, "fr": 2, "easy": 2}
    assert sum(1 for o in missed if "in claim" in o.mutant.detail) == 6, "claimed club names with no cited event, en only"


# ----------------------------------------------------------------- the command


def test_the_command_leads_with_check_sentence_per_language_and_class(capsys: pytest.CaptureFixture[str]) -> None:
    assert M.main(["--run", str(RUN)]) == 0
    out = capsys.readouterr().out
    assert "M8 Fact Gate mutation test (self-measured)" in out
    assert "gate: check_sentence, the check every sentence of every edition goes through" in out
    assert "as the pipeline runs it" not in out
    for language in ("en", "el", "es", "fr"):
        assert re.search(rf"^{language} +caught +37 of +45 \( 82\.2%\)$", out, re.M), language
    assert re.search(r"^easy-read edition +caught +37 of +45 \( 82\.2%\) +English; its prose equals the English prose", out, re.M)
    assert re.search(r"^all editions +caught 185 of 225 \( 82\.2%\)$", out, re.M)
    assert re.search(r"^roster_name_swapped +caught +15 of +55 \( 27\.3%\)$", out, re.M)
    assert re.search(r"^el, es and fr +caught +95 of 101 \( 94\.1%\)$", out, re.M)
    assert re.search(r"^el +caught +32 of +34 \( 94\.1%\)$", out, re.M)
    assert "it raises the covered total to 209 of 225 (92.9%)" in out
    assert re.search(r"^spelled_out_number_added +uncovered; flagged +0 of +30 \(  0\.0%\)", out, re.M)
    assert re.search(r"^decimal_number_added +uncovered; flagged +30 of +30 \(100\.0%\): incidental", out, re.M)
    assert PINNED_FINGERPRINT in out
    assert "Missed by check_sentence among mutants a real run can produce: 16" in out
    assert "Missed in the translation stage: 6" in out
    assert out.index("by language (check_sentence)") < out.index("Translation stage") < out.index("Defence in depth")


# ----------------------------------------------------------------- refusals


def _copy_run(tmp_path: Path) -> Path:
    target = tmp_path / RUN.name
    target.mkdir()
    for name in ["manifest.json", *(f"report-{e}.json" for e in M.EDITIONS)]:
        shutil.copyfile(RUN / name, target / name)
    return target


def test_an_edition_that_does_not_hash_as_sealed_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _copy_run(tmp_path)
    (run / "report-el.json").write_bytes((run / "report-el.json").read_bytes() + b" ")
    with pytest.raises(M.MutationError, match="report-el.json does not hash"):
        M.load_run(run)
    assert M.main(["--run", str(run)]) == 2
    assert "refused:" in capsys.readouterr().err


def test_a_manifest_whose_event_log_does_not_match_the_rebuilt_match_is_refused(tmp_path: Path) -> None:
    run = _copy_run(tmp_path)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["event_log_sha256"] = "0" * 64
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(M.MutationError, match="event_log_sha256"):
        M.load_run(run)


def test_an_original_that_fails_the_gate_is_refused_before_any_mutation(tmp_path: Path) -> None:
    run = _copy_run(tmp_path)
    doc = json.loads((run / "report-en.json").read_text(encoding="utf-8"))
    doc["sentences"][4]["text"] = doc["sentences"][4]["text"].replace("7 shots", "8 shots")
    data = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    (run / "report-en.json").write_bytes(data)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"]["report-en.json"]["sha256"] = M.sha256_hex(data)
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(M.MutationError, match="fails the gate before any mutation"):
        M.load_run(run)

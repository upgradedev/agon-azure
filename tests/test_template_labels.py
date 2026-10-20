"""Template lines name every statistic and event kind the Fact Gate knows, in every edition language.

A template line (agon.fleet.templates.render_claims) replaces a refused sentence with exactly its
claims. A statistic without a label would print its raw key ("key_passes: 3") into a published
edition, so the label tables must cover recompute.STAT_KEYS and recompute.EVENT_KINDS in full, and a
label may hold no digit, because the gate reads every digit written in the prose.
"""

from __future__ import annotations

import re

import pytest

from agon.fleet.sources import SynthMatchSource
from agon.fleet.templates import KIND_LABELS, STAT_LABELS, render_claims, template_line_for
from agon.governance import recompute
from agon.governance.claims import Claim, Sentence
from agon.governance.factgate import check_sentence

LANGUAGES = ("en", "el", "es", "fr")
METRICS_PACK_STATS = ("progressive_passes", "progressive_carries", "passes_into_box", "crosses_into_box",
                      "carries_into_box", "box_entries", "deep_completions", "key_passes", "field_tilt_pct",
                      "counter_press_recovery_pct")


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_statistic_and_kind_has_a_label(language):
    assert set(STAT_LABELS[language]) == set(recompute.STAT_KEYS), language
    assert set(KIND_LABELS[language]) == set(recompute.EVENT_KINDS), language


@pytest.mark.parametrize("language", LANGUAGES)
def test_labels_hold_no_digit_and_are_not_raw_keys(language):
    for table in (STAT_LABELS[language], KIND_LABELS[language]):
        for key, label in table.items():
            assert not re.search(r"[0-9]", label), (language, key, label)
            assert "_" not in label, (language, key, label)


@pytest.fixture(scope="module")
def record():
    return SynthMatchSource().load({"source": "synth", "seed": 1, "match_index": 0})


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_template_line_for_a_pack_statistic_reads_as_words(record, language):
    facts = record.facts()
    home = record.meta["home"]
    for stat in METRICS_PACK_STATS:
        value = recompute.recompute_stat(facts, stat, home)
        text = render_claims((Claim(type="stat", value=value, stat=stat, subject=home),), language, record)
        assert stat not in text and STAT_LABELS[language][stat] in text and str(value) in text


def test_a_refused_pack_sentence_becomes_a_template_line_the_gate_passes(record):
    facts = record.facts()
    home = record.meta["home"]
    counted = recompute.stat_events(facts, "progressive_passes", home)
    wrong = Sentence(
        sentence_id="s1",
        text=f"The home side made {len(counted) + 5} progressive passes.",
        language="en",
        claims=(Claim(type="stat", value=len(counted) + 5, stat="progressive_passes", subject=home,
                      event_ids=(counted[0].event_id,)),),
    )
    assert not check_sentence(wrong, facts).passed
    line = template_line_for(wrong, record)
    assert "progressive passes" in line.text and "progressive_passes" not in line.text
    assert check_sentence(line, facts).passed

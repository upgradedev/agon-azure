"""Sentence and claim model: round trip, strict fields, known languages and author tags."""

from __future__ import annotations

import pytest

from agon.governance.claims import Claim, ClaimModelError, Sentence


def test_round_trip():
    s = Sentence(
        sentence_id="s1-el",
        text="Η ομάδα είχε 4 τελικές στην εστία.",
        language="el",
        claims=(Claim(type="stat", value=4, event_ids=("a" * 16,), stat="shots_on_target", subject="C1"),),
        author="template",
    )
    assert Sentence.from_dict(s.to_dict()) == s


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(sentence_id="", text="x", language="en"),
        dict(sentence_id="s", text="x", language="de"),
        dict(sentence_id="s", text="x", language="en", author="robot"),
        dict(sentence_id="s", text="x", language="en", claims=[]),
    ],
)
def test_invalid_sentences(kwargs):
    with pytest.raises(ClaimModelError):
        Sentence(**kwargs)


def test_strict_fields():
    with pytest.raises(ClaimModelError):
        Sentence.from_dict({"sentence_id": "s", "text": "x", "language": "en", "approved": True})
    with pytest.raises(ClaimModelError):
        Claim.from_dict({"type": "score"})
    with pytest.raises(ClaimModelError):
        Claim(type="name", value="X", event_ids=["not-a-tuple"])  # type: ignore[arg-type]

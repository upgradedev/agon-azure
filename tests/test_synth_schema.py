"""Schema, canonical JSON and content-addressed ids."""

from __future__ import annotations

import pytest

from agon.synth.schema import CanonicalError, canonical, compute_event_id, make_event, validate_event


def _sample(**over: object) -> dict:
    base = dict(
        match_id="s1-m0",
        seq=4,
        period=1,
        minute=12,
        second=30,
        type="pass",
        team="C1",
        player="C1P07",
        x_dm=400,
        y_dm=300,
        end_x_dm=520,
        end_y_dm=310,
        possession_chain_id=3,
        under_pressure=False,
        outcome="complete",
        related_player="C1P09",
    )
    base.update(over)
    return base


def test_canonical_is_sorted_compact_ascii() -> None:
    assert canonical({"b": 1, "a": [True, None, "é"]}) == '{"a":[true,null,"\\u00e9"],"b":1}'


def test_canonical_rejects_floats() -> None:
    with pytest.raises(CanonicalError):
        canonical({"x": 1.5})
    with pytest.raises(CanonicalError):
        canonical([1, [2, float("nan")]])


def test_event_id_is_content_addressed() -> None:
    ev = make_event(**_sample())
    assert len(ev.event_id) == 16
    assert ev.event_id == compute_event_id(ev.to_dict())
    other = make_event(**_sample(seq=5))
    assert other.event_id != ev.event_id


def test_valid_event_passes() -> None:
    assert validate_event(make_event(**_sample()).to_dict()) == []


def test_tampered_event_fails() -> None:
    d = make_event(**_sample()).to_dict()
    d["minute"] = 13
    assert "event_id does not match content" in validate_event(d)


def test_bad_values_fail() -> None:
    d = make_event(**_sample(x_dm=1051)).to_dict()
    assert "location off the pitch" in validate_event(d)
    d = make_event(**_sample(outcome="goal")).to_dict()
    assert any("outcome" in p for p in validate_event(d))
    d = make_event(**_sample(type="dribble")).to_dict()
    assert any("unknown type" in p for p in validate_event(d))
    d = make_event(**_sample(type="sub", outcome="on", related_player=None, end_x_dm=None, end_y_dm=None)).to_dict()
    assert any("related_player" in p for p in validate_event(d))

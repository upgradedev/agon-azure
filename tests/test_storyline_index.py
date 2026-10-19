"""The storyline picker index: categories by written rules over verified referee labels."""

from __future__ import annotations

import json
from functools import lru_cache

import pytest

from agon.referee import storyline_index as si
from agon.referee.labels import labels_from_log
from agon.referee.storylines import run_storyline
from agon.webseason import validate_storyline_index

SCAN = tuple(range(2001, 2006))


@lru_cache(maxsize=None)
def index() -> dict:
    return si.storyline_index(seeds=SCAN, per_category=1)


def test_every_category_is_present_with_one_entry_and_a_valid_shape():
    idx = index()
    assert [c["category"] for c in idx["categories"]] == [
        "red_card_comeback", "goalkeeper_heroics", "late_collapse", "tactical_shift",
    ]
    assert all(len(c["entries"]) == 1 for c in idx["categories"])
    assert validate_storyline_index(idx) == []
    assert idx["seeds_scanned"] == [2001, 2005]


def test_entries_point_at_their_storyline_run_and_regenerate_command():
    for cat in index()["categories"]:
        e = cat["entries"][0]
        assert e["match_id"] == f"s{e['seed']}-m0" and e["match_index"] == 0
        assert e["command"] == f"python -m agon.referee.storylines --seeds {e['seed']}"
        assert e["label"]["kind"] == cat["kind"] == e["kind"]
        assert "storyline match" in e["run"]


def test_labels_agree_with_the_log_only_code_path():
    for cat in index()["categories"]:
        e = cat["entries"][0]
        plan, result = run_storyline(e["seed"], e["kind"])
        assert labels_from_log(e["seed"], plan, result.meta, [x.to_dict() for x in result.events]) == e["label"]
        assert e["score"] == result.meta["score"]


def test_red_card_comeback_meets_its_rule_by_an_independent_scan():
    e = index()["categories"][0]["entries"][0]
    _, result = run_storyline(e["seed"], "red_card")
    team = e["label"]["team"]
    events = [x.to_dict() for x in result.events]
    card = next(i for i, x in enumerate(events) if x["event_id"] == e["label"]["anchor_event_id"])
    assert events[card]["type"] == "card" and events[card]["outcome"] == "red" and events[card]["team"] == team
    margins = []
    own = other = 0
    for i, x in enumerate(events):
        if x["type"] == "shot" and x["outcome"] == "goal":
            own, other = (own + 1, other) if x["team"] == team else (own, other + 1)
        if i >= card:
            margins.append(own - other)
    assert min(margins) < 0, "behind at some moment from the card on"
    assert margins[-1] >= 0, "did not lose"


def test_rules_reject_labels_that_do_not_qualify():
    plan, result = run_storyline(2001, "red_card")
    label = labels_from_log(2001, plan, result.meta, [x.to_dict() for x in result.events])
    assert si._red_card_comeback(dict(label, anchor_event_id=None), result) is False
    assert si._turning_point(dict(label, turning_point=False), result) is False


def test_an_empty_category_keeps_its_reason():
    idx = si.storyline_index(seeds=(2001,), per_category=1)
    red = idx["categories"][0]
    assert red["entries"] == []  # seed 2001's red card side never trailed after the card
    assert red["reason"] == "no seed in 2001..2001 met the rule"
    assert validate_storyline_index(idx) == []


def test_per_category_must_be_positive():
    with pytest.raises(ValueError):
        si.storyline_index(seeds=SCAN, per_category=0)


def test_out_writes_canonical_json_that_the_season_writer_reads(tmp_path):
    from agon.synth.schema import canonical_bytes

    out = tmp_path / "build" / "storylines.json"
    assert si.main(["--out", str(out), "--per-category", "1"]) == 0
    raw = out.read_bytes()
    assert raw == canonical_bytes(si.storyline_index(per_category=1)) + b"\n"
    assert validate_storyline_index(json.loads(raw.decode("ascii"))) == []

"""Storyline interventions and turning-point labels (metric M3)."""

from __future__ import annotations

import copy

import pytest

from agon.referee import labels as L
from agon.referee import storylines as S
from agon.referee import verify_labels as V
from agon.synth.engine import simulate_match
from agon.synth.interventions import INTERVENTION_KINDS, Intervention
from agon.synth.manifest import build_bundle
from agon.synth.schema import canonical_bytes, validate_event
from agon.synth.world import build_world


def test_no_interventions_is_byte_identical_to_a_plain_match() -> None:
    for seed in (1, 5):
        league = build_world(seed)
        plain = simulate_match(league, 0)
        empty = simulate_match(league, 0, ())
        assert plain.applied == [] and empty.applied == []
        assert canonical_bytes([e.to_dict() for e in plain.events]) == canonical_bytes([e.to_dict() for e in empty.events])
        assert plain.meta == empty.meta


def test_m3_command_passes_on_the_spec_seeds(capsys) -> None:
    assert V.main(["--seeds", "1,2,3"]) == 0
    out = capsys.readouterr().out
    assert "M3: 15 of 15 labels re-derived identically by the second code path" in out


def test_m3_mutate_fails_with_every_mutation_rejected(capsys) -> None:
    assert V.main(["--seeds", "1,2,3", "--mutate"]) == 1
    out = capsys.readouterr().out
    assert "15 of 15 mutated labels rejected" in out
    assert out.count("MISMATCH mutated:") == 15
    for which in V.MUTATIONS:
        assert f"mutated:{which}" in out


def test_labels_agree_at_a_seed_offset_unlike_the_cli_default() -> None:
    """The CLI default is 1,2,3; this fixture starts at 2047."""
    lines, checked, rejected, labels = V.verify([2047, 2048], do_mutate=False)
    assert (checked, rejected) == (10, 0)
    assert {lab["kind"] for lab in labels} == set(INTERVENTION_KINDS)
    assert all(lab["seed"] in (2047, 2048) for lab in labels)


def _storyline(seed: int, kind: str):
    plan, result = S.run_storyline(seed, kind)
    return plan, result, [e.to_dict() for e in result.events]


def test_path_two_catches_a_deleted_anchor_event() -> None:
    plan, result, events = _storyline(1, "red_card")
    one = L.labels_from_ledger(1, plan, result)
    assert one["anchor_event_id"] is not None
    tampered = [e for e in events if e["event_id"] != one["anchor_event_id"]]
    assert L.labels_from_log(1, plan, result.meta, tampered) != one


def test_path_two_catches_a_goal_rewritten_as_a_save() -> None:
    plan, result, events = _storyline(1, "late_collapse")
    one = L.labels_from_ledger(1, plan, result)
    assert one["effect"]["goals_against_after"] >= 1
    tampered = copy.deepcopy(events)
    for ev in tampered:
        if ev["event_id"] == one["anchor_event_id"]:
            ev["outcome"] = "saved"
    assert L.labels_from_log(1, plan, result.meta, tampered) != one


def test_path_two_catches_tampered_added_time() -> None:
    plan, result, events = _storyline(2, "injury_stoppage")
    one = L.labels_from_ledger(2, plan, result)
    meta = copy.deepcopy(result.meta)
    meta["added_seconds"]["first_half"] += 1
    assert L.labels_from_log(2, plan, meta, events) != one
    assert L.labels_from_log(2, plan, result.meta, events) == one


def test_storyline_logs_are_valid_and_never_name_the_storyline() -> None:
    for kind in INTERVENTION_KINDS:
        plan, result, events = _storyline(3, kind)
        for ev in events:
            assert validate_event(ev) == [], (kind, ev)
        blob = canonical_bytes({"meta": result.meta, "events": events}).decode("ascii")
        for word in ("red_card", "tactical_shift", "late_collapse", "heroics", "storyline", "intervention"):
            assert word not in blob, (kind, word)


def test_red_card_storyline_removes_the_player_for_good() -> None:
    plan, result, events = _storyline(2, "red_card")
    label = L.labels_from_ledger(2, plan, result)
    card = next(e for e in events if e["event_id"] == label["anchor_event_id"])
    assert card["type"] == "card" and card["outcome"] == "red" and card["team"] == label["team"]
    later = events[events.index(card) + 1 :]
    assert all(card["player"] not in (e["player"], e["related_player"]) for e in later)


def test_injury_storyline_adds_its_stoppage_to_the_period() -> None:
    plan, result, events = _storyline(2, "injury_stoppage")
    label = L.labels_from_ledger(2, plan, result)
    assert label["effect"]["stoppage_seconds"] == plan.magnitude > 0
    sub = next(e for e in events if e["event_id"] == label["anchor_event_id"])
    assert sub["type"] == "sub" and sub["qualifier"] == "injury"


def test_plain_match_hash_is_unchanged_by_the_referee() -> None:
    before = build_bundle(4).sha256()
    S.run_storyline(4, "tactical_shift")
    assert build_bundle(4).sha256() == before


def test_plans_are_deterministic_and_inside_their_ranges() -> None:
    for seed in (1, 2001, 4099):
        for kind in INTERVENTION_KINDS:
            a, b = S.plan_storyline(seed, kind), S.plan_storyline(seed, kind)
            assert a == b
            lo, hi = S.MINUTE_RANGES[kind]
            assert lo <= a.minute <= hi
            lo, hi = S.SIZE_RANGES[kind]
            assert lo <= a.magnitude <= hi


def test_intervention_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        Intervention("meteor_strike", "home", 10)
    with pytest.raises(ValueError):
        Intervention("red_card", "neutral", 10)
    with pytest.raises(ValueError):
        Intervention("tactical_shift", "home", 10, magnitude=5000)
    with pytest.raises(ValueError):
        Intervention("red_card", "home", 95)


def test_turning_point_rule() -> None:
    assert L.turning_point("red_card", "a" * 16, {"goals_for_after": 0, "goals_against_after": 0}) is True
    assert L.turning_point("late_collapse", None, {"goals_for_after": 0, "goals_against_after": 3}) is False
    assert L.turning_point("goalkeeper_heroics", "a" * 16, {"saves_after": 2, "goals_against_after": 0}) is False
    shift = {"press_before": 10, "onball_before": 100, "press_after": 12, "onball_after": 100}
    assert L.turning_point("tactical_shift", "a" * 16, shift) is True

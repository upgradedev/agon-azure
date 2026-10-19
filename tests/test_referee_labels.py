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
            assert a.period == (1 if a.minute < 45 else 2)
            lo, hi = S.SIZE_RANGES[kind]
            assert lo <= a.magnitude <= hi


def test_intervention_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        Intervention("meteor_strike", "home", 1, 10)
    with pytest.raises(ValueError):
        Intervention("red_card", "neutral", 1, 10)
    with pytest.raises(ValueError):
        Intervention("tactical_shift", "home", 1, 10, magnitude=5000)
    with pytest.raises(ValueError):
        Intervention("red_card", "home", 2, 95)
    with pytest.raises(ValueError):
        Intervention("red_card", "home", 3, 10)


def test_intervention_period_must_agree_with_its_minute() -> None:
    with pytest.raises(ValueError, match="belongs to period 2"):
        Intervention("red_card", "home", 1, 48)
    with pytest.raises(ValueError, match="belongs to period 1"):
        Intervention("red_card", "home", 2, 44)
    assert Intervention("red_card", "home", 1, 44).to_dict()["period"] == 1
    assert Intervention("red_card", "home", 2, 45).to_dict()["period"] == 2


def test_turning_point_rule() -> None:
    assert L.turning_point("red_card", "a" * 16, {"goals_for_after": 0, "goals_against_after": 0}) is True
    assert L.turning_point("late_collapse", None, {"goals_for_after": 0, "goals_against_after": 3}) is False
    assert L.turning_point("goalkeeper_heroics", "a" * 16, {"saves_after": 2, "goals_against_after": 0}) is False
    shift = {"press_before": 10, "onball_before": 100, "press_after": 12, "onball_after": 100}
    assert L.turning_point("tactical_shift", "a" * 16, shift) is True


def _card_of(seed: int, plan: Intervention) -> dict:
    result = simulate_match(build_world(seed), 0, (plan,))
    events = [e.to_dict() for e in result.events]
    label = L.labels_from_ledger(seed, plan, result)
    assert label == L.labels_from_log(seed, plan, result.meta, events)
    card = next(e for e in events if e["event_id"] == label["anchor_event_id"])
    assert card["type"] == "card" and card["outcome"] == "red"
    assert label["period"] == card["period"]
    return card


def test_reviewer_repro_seed_2006_red_card_minute_48_lands_in_the_second_half() -> None:
    """Before the fix this card was shown at 48:17 of the FIRST half."""
    plan = S.plan_storyline(2006, "red_card")
    assert (plan.period, plan.minute) == (2, 48)
    card = _card_of(2006, plan)
    assert card["period"] == 2 and card["minute"] >= 48


def test_reviewer_repro_seed_2034_red_card_minute_45_lands_in_the_second_half() -> None:
    """Before the fix this card was shown at 45:06 of the first half."""
    plan = S.plan_storyline(2034, "red_card")
    assert (plan.period, plan.minute) == (2, 45)
    assert _card_of(2034, plan)["period"] == 2


def test_minute_44_plan_lands_in_the_first_half() -> None:
    card = _card_of(2006, Intervention("red_card", "home", 1, 44))
    assert card["period"] == 1 and card["minute"] >= 44


def test_path_two_does_not_activate_a_period_two_plan_in_first_half_added_time() -> None:
    events = [
        {"type": "pass", "possession_chain_id": 1, "period": 1, "minute": 47, "second": 0},
        {"type": "pass", "possession_chain_id": 2, "period": 1, "minute": 48, "second": 30},
        {"type": "pass", "possession_chain_id": 3, "period": 2, "minute": 46, "second": 0},
        {"type": "pass", "possession_chain_id": 4, "period": 2, "minute": 48, "second": 5},
    ]
    assert L.activation_index(events, 2, 48) == 3
    assert L.activation_index(events, 1, 47) == 0
    assert L.activation_index(events, 1, 44) == 0


def test_shift_windows_count_only_the_activation_period() -> None:
    """Every event counted in either window of a shift label belongs to the activation period."""
    for seed in (2003, 2047):
        plan, result, events = _storyline(seed, "tactical_shift")
        label = L.labels_from_ledger(seed, plan, result)
        assert label["period"] == plan.period == 2
        m0 = label["at_clock"] // 60
        team = label["team"]
        lo, hi = m0 - L.SHIFT_WINDOW_MINUTES, m0 + L.SHIFT_WINDOW_MINUTES
        in_window = [e for e in events if e["period"] == label["period"] and lo <= e["minute"] < hi]
        press = [e for e in in_window if e["type"] == "pressure" and e["team"] == team]
        onball = [e for e in in_window if e["type"] in L.ONBALL and e["team"] != team]
        eff = label["effect"]
        assert eff["press_before"] + eff["press_after"] == len(press)
        assert eff["onball_before"] + eff["onball_after"] == len(onball)
        # Both windows are full length inside period 2: the shift starts at minute 60 or later.
        assert m0 - L.SHIFT_WINDOW_MINUTES >= 45


def test_window_sums_ignore_the_other_period() -> None:
    by_minute = {(1, 46): 5, (2, 46): 7, (2, 50): 1, (1, 30): 2}
    assert L._window_sums(by_minute, 2, 48) == (7, 1)
    assert L._window_sums(by_minute, 1, 48) == (5, 0)


def test_period_sweep_passes_on_the_reviewer_seeds(capsys) -> None:
    from agon.referee import period_sweep as P

    assert P.main(["--seeds", "2006,2034,2003"]) == 0
    out = capsys.readouterr().out
    assert "activated in period 1: 0;" in out
    assert "label path mismatches: 0" in out
    counts = P.sweep([2006, 2034, 2003])
    assert counts["plans"] >= 3 and counts["minute_45_52"] >= 2


def test_period_sweep_fails_when_a_label_lands_in_period_1(monkeypatch) -> None:
    """Negative control: both paths agree, but on period 1. Only the period check can catch it."""
    from agon.referee import period_sweep as P

    def first_half(fn):
        def wrapped(*args, **kwargs):
            label = fn(*args, **kwargs)
            return {**label, "period": 1}
        return wrapped

    monkeypatch.setattr(P, "labels_from_ledger", first_half(P.labels_from_ledger))
    monkeypatch.setattr(P, "labels_from_log", first_half(P.labels_from_log))
    counts = P.sweep([2006])
    assert counts["mismatch"] == 0 and counts["in_period_1"] >= 1
    assert P.main(["--seeds", "2006"]) == 1


def test_period_sweep_fails_when_the_label_paths_disagree(monkeypatch) -> None:
    from agon.referee import period_sweep as P

    real = P.labels_from_log
    monkeypatch.setattr(P, "labels_from_log", lambda *a, **k: {**real(*a, **k), "at_clock": -1})
    assert P.main(["--seeds", "2006"]) == 1

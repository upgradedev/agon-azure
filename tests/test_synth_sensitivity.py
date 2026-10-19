"""M4: each style knob moves its metric in the stated direction, and the check can fail."""

from __future__ import annotations

from fractions import Fraction

from agon.synth import sensitivity as M
from agon.synth.engine import MatchSimulator
from agon.synth.world import Style


def test_every_knob_moves_its_metric_at_a_seed_offset_unlike_the_cli_default() -> None:
    """CLI default is seeds 1-8; this fixture starts at 5011."""
    rows = M.run([5011, 5012, 5013])
    assert {r.check.knob for r in rows} == {"press_height", "directness", "width"}
    for r in rows:
        assert r.passed, (r.check, r.low, r.high)


def test_cli_passes_on_its_default_seeds(capsys) -> None:
    assert M.main([]) == 0
    assert "M4 passed 4 of 4 knob checks" in capsys.readouterr().out


def test_a_disconnected_knob_fails(monkeypatch, capsys) -> None:
    """Negative control: cut every knob's reader and every check must fail, so M4 exits 1."""
    monkeypatch.setattr(MatchSimulator, "_effective_style", lambda self, team: Style(500, 500, 500))
    rows = M.run([5011])
    assert not any(r.passed for r in rows)
    assert M.main(["--seeds", "5011"]) == 1
    assert "M4 passed 0 of 4" in capsys.readouterr().out


def test_direction_rule_is_strict() -> None:
    up = M.KnobCheck("width", "mean_lateral_pass_dm", +1, "")
    down = M.KnobCheck("directness", "pass_completion", -1, "")
    assert M.Row(up, Fraction(1), Fraction(2)).passed
    assert not M.Row(up, Fraction(2), Fraction(2)).passed
    assert M.Row(down, Fraction(2), Fraction(1)).passed
    assert not M.Row(down, Fraction(1), Fraction(1)).passed


def test_metrics_on_a_hand_built_log() -> None:
    events = [
        {"type": "pressure"},
        {"type": "pass", "outcome": "complete", "x_dm": 100, "end_x_dm": 300, "y_dm": 100, "end_y_dm": 160},
        {"type": "pass", "outcome": "incomplete", "x_dm": 300, "end_x_dm": 320, "y_dm": 300, "end_y_dm": 290},
        {"type": "carry"},
    ]
    m = M.metrics(events)
    assert m["pressures_per_onball"] == (1, 3)
    assert m["long_forward_pass_share"] == (1, 2)
    assert m["pass_completion"] == (1, 2)
    assert m["mean_lateral_pass_dm"] == (70, 2)

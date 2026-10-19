"""The narrow interface the `interpret` executor needs, and the adapter onto `agon.interpret`.

`AgonInterpret` runs the interpret package (spec section 4 row 2: xG, xT, PPDA, control vs chaos,
pressure regimes, tempo bands and the remaining-goals Poisson win probability with `wp.swing`) on
the run's event log and returns the shape documented on `InterpretPort`. It is a pure function of
the log and the frozen model, so a fresh process that resumes a run computes the same output.

What the Draft carries is a digest, not the whole package output: the candidate moments, the five
match metrics the Analyst can cite (control vs chaos and pressure regimes slimmed to their bands),
and a `units` map. The per-minute momentum and win-probability series and the events-only speed
proxy stay out, because the Draft travels in every checkpointed message and is sent to the model.

`source` names the package and the first 12 hex digits of the frozen model's sha256, and the seal
records it as `deployment_ids["interpret"]`, so a bundle says exactly which model produced it.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from agon.fleet.sources import MatchRecord
from agon.governance.recompute import MatchFacts, score_after
from agon.interpret import INTERPRET_VERSION, MatchView, interpret_match, load_model, model_sha256

SOURCE_PREFIX = "agon.interpret/"
SOURCE_HASH_DIGITS = 12

# Candidate reason in agon.interpret.wp -> event kind in the claim vocabulary
# (agon.governance.recompute.EVENT_KINDS), so a candidate's kind is one a claim can cite.
KIND_OF_REASON: dict[str, str] = {
    "goal": "goal",
    "sending_off": "red_card",
    "penalty": "penalty",
    "big_chance": "shot",
}

UNITS: dict[str, str] = {
    "swing": (
        "ppm (parts per million) of the acting club's win probability: the larger of "
        "swing_realised_ppm and swing_expected_ppm in absolute value. The acting club is the club "
        "of the event: the scorer's, the shooter's, or the club of the player sent off."
    ),
    "swing_realised_ppm": "ppm: the acting club's win probability just after the event minus just before",
    "swing_expected_ppm": (
        "ppm: for a shot, its xG times (the acting club's win probability had it scored minus "
        "before); 0 for any other event"
    ),
    "wp_before, wp_after": "ppm for home win, draw and away win, summing to 1000000",
    "xg_ppm, xt_added_ppm": "expected goals and expected threat added, in ppm (1000000 is one goal)",
    "ppda_centi": "PPDA x 100, lower is a more intense press; null when the side made no defensive action",
    "mean_pass_distance_dm": "decimetres",
    "mean_pass_difficulty_centi": "mean pass difficulty (0 to 100) x 100",
    "index_permille": "control vs chaos index in per mille: 0 is control, 1000 is chaos",
    "pressures": "pressure events by that side in the regime",
    "on_ball_actions": "passes, carries and shots by both sides in the 5-minute window",
    "window": "p<period> <from>-<to>: a 5-minute window of engine minutes; first-half added time stays in period 1",
}


class InterpretPort(Protocol):
    """Pure function of the event log. Returns JSON-native data with at least these keys:

    * ``source``: which implementation and model produced it;
    * ``note``: one sentence saying what it is, carried onto the officer's card;
    * ``units``: the unit of every number below, keyed by field name;
    * ``final_score``: ``{"home": int, "away": int}``;
    * ``candidates``: candidate turning points, each ``{event_id, minute, kind, team, player,
      score_before, score_after, swing}``, ordered most important first;
    * ``metrics``: the match metrics the Analyst may cite.
    """

    def interpret(self, record: MatchRecord) -> dict[str, Any]: ...


class AgonInterpret:
    """`agon.interpret` behind `InterpretPort`. Loading verifies the frozen model against its lock."""

    def __init__(self) -> None:
        model = load_model()  # raises ModelError if model.json does not hash to model.lock
        self.model_sha256 = model_sha256()
        self.source = SOURCE_PREFIX + self.model_sha256[:SOURCE_HASH_DIGITS]
        seeds = model["provenance"]["seeds"]
        self.note = (
            f"Computed by code from the event log with agon.interpret {INTERPRET_VERSION}, model fitted "
            f"on synthetic training seeds {seeds}. swing is the larger of the realised and the expected "
            "change in win probability, in ppm, from a remaining-goals Poisson model that does not "
            "model score effects."
        )

    def interpret(self, record: MatchRecord) -> dict[str, Any]:
        view = MatchView.from_bundle(record.meta, record.events)
        full = interpret_match(view)
        if full["model_sha256"] != self.model_sha256:
            raise RuntimeError(f"interpret ran model {full['model_sha256']}, the adapter loaded {self.model_sha256}")
        facts = record.facts()
        return {
            "source": self.source,
            "interpret": INTERPRET_VERSION,
            "model_sha256": self.model_sha256,
            "note": self.note,
            "units": dict(UNITS),
            "final_score": score_after(facts, None),
            "candidates": [_candidate(view, facts, m) for m in full["candidate_moments"]],
            "metrics": _metrics(full),
        }


def _candidate(view: MatchView, facts: MatchFacts, moment: Mapping[str, Any]) -> dict[str, Any]:
    """One `wp.candidate_moments` row in the port's shape. Scores come from the Fact Gate's code."""
    e = view.get(moment["event_id"])
    realised = moment["delta_team_ppm"]
    expected = moment.get("expected_swing_ppm", 0)
    row: dict[str, Any] = {
        "event_id": e["event_id"],
        "minute": moment["minute"],
        "kind": KIND_OF_REASON[moment["reason"]],
        "reason": moment["reason"],
        "team": e["team"],
        "player": e["player"],
        "score_before": score_after(facts, e["seq"] - 1),
        "score_after": score_after(facts, e["seq"]),
        "swing": moment["weight_ppm"],
        "swing_basis": "realised" if abs(realised) >= abs(expected) else "expected",
        "swing_realised_ppm": realised,
        "swing_expected_ppm": expected,
        "wp_before": dict(moment["before"]),
        "wp_after": dict(moment["after"]),
    }
    if e["type"] == "shot":
        row["outcome"] = e["outcome"]
        row["xg_ppm"] = moment["xg_ppm"]
    return row


def _metrics(full: Mapping[str, Any]) -> dict[str, Any]:
    """The match metrics the Analyst can cite, with the per-window detail cut to what it reads."""
    regimes = {}
    for team, data in full["pressure_regimes"].items():
        regimes[team] = {
            "regimes": [{k: r[k] for k in ("band", "start", "end", "pressures")} for r in data["regimes"]],
            "changes": [dict(c) for c in data["changes"]],
        }
    return {
        "totals": full["totals"],
        "ppda": full["ppda"],
        "control_vs_chaos": [{k: w[k] for k in ("window", "index_permille", "band")} for w in full["control_vs_chaos"]],
        "pressure_regimes": regimes,
        "tempo": full["tempo"],
    }


def default_interpret() -> InterpretPort:
    """The interpret port every deps builder uses: fakes for tests and the real Azure worker alike."""
    return AgonInterpret()

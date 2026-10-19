"""interpret: football state in the brief's words, computed from the event log alone.

Pure, deterministic functions over synthetic match events: xG, xT, PPDA, pass distance and
difficulty, speed and distance triggers (an events-only proxy today; see kinematics), control vs
chaos, pressure regimes, tempo bands, momentum and a remaining-goals Poisson win probability with
`wp.swing(event_id)`. Fitted parameters are frozen in model.json, verified against model.lock.

Isolation: this package reads `agon.synth.schema` and the minute convention in
`agon.governance.recompute` only. It never imports the engine, its constants, interventions or the
referee, so an evaluation of interpret against generator outcomes is not circular. The fitter
(`agon.interpret.fit`) is the one exception: it generates training matches through
`agon.synth.manifest`, offline. Method and units: docs/INTERPRET.md.
"""

from __future__ import annotations

from typing import Any, Mapping

from agon.interpret import kinematics, metrics, wp
from agon.interpret.frame import MatchView
from agon.interpret.model import load_model, model_sha256
from agon.synth.schema import canonical_bytes, sha256_hex

INTERPRET_VERSION = "interpret-v1"


def interpret_match(view: MatchView, model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Every interpret output for one match, as integer-only JSON-ready data."""
    m = model or load_model()
    return {
        "interpret": INTERPRET_VERSION,
        "model_sha256": model_sha256() if model is None else sha256_hex(canonical_bytes(dict(model)) + b"\n"),
        "match_id": view.match_id,
        "home": view.home_id,
        "away": view.away_id,
        "totals": metrics.totals(view, m),
        "ppda": metrics.ppda(view),
        "control_vs_chaos": metrics.control_vs_chaos(view, m),
        "pressure_regimes": metrics.pressure_regimes(view, m),
        "tempo": metrics.tempo_bands(view, m),
        "momentum": metrics.momentum_series(view, m),
        "win_probability": wp.series(view, m),
        "candidate_moments": wp.candidate_moments(view, model=m),
        "speed_and_distance": kinematics.carry_proxy(view),
    }


__all__ = ["INTERPRET_VERSION", "MatchView", "interpret_match", "load_model", "model_sha256"]

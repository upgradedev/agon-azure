"""Match metrics from events: PPDA, control vs chaos, pressure regimes, tempo, momentum, xG and xT totals.

Every function is pure over a MatchView and returns integers (ppm, per mille or centi-units), so
the output is the same bytes on every OS. Band thresholds (control vs chaos, pressure, tempo) are
training-set terciles frozen in model.json; they were never tuned on evaluation seeds.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Mapping

from agon.interpret._fixed import normalised_entropy_permille, ratio_round
from agon.interpret.features import pass_difficulty, pass_distance_m, xg_ppm, xt_added_ppm
from agon.interpret.frame import (
    BIN15_LABELS,
    ON_BALL_TYPES,
    MatchView,
    bin15,
    window5,
    window_label,
)
from agon.interpret.model import load_model

# PPDA zone, Opta-style: the opponent's passes in its own 60% of the pitch, against the pressing
# side's defensive actions in the same area. In the opponent's frame that area is x_dm < 630; the
# pressing side's own events are stored in ITS frame, where the same area is x_dm >= 420.
PPDA_OPP_PASS_MAX_X_DM = 630
PPDA_DEF_ACTION_MIN_X_DM = 420
# Turnovers in open play: the possession changed hands without the ball going dead.
OPEN_PLAY_TURNOVERS = ("interception", "tackle")
MOMENTUM_DECAY_PERMILLE = 800
BANDS: dict[str, tuple[str, str, str]] = {
    "control_chaos": ("control", "contested", "chaos"),
    "pressure": ("low", "medium", "high"),
    "tempo": ("slow", "normal", "fast"),
}


def band_of(value: int, thresholds: list[int], names: tuple[str, str, str]) -> str:
    """Lower band below thresholds[0], middle band below thresholds[1], top band otherwise."""
    if value < thresholds[0]:
        return names[0]
    if value < thresholds[1]:
        return names[1]
    return names[2]


# --------------------------------------------------------------------------- PPDA


def _is_def_action(e: Mapping[str, Any]) -> bool:
    if e["type"] in ("tackle", "foul"):
        return True
    return e["type"] == "possession_change" and e["outcome"] == "interception"


def ppda(view: MatchView) -> dict[str, Any]:
    """PPDA per team for the match and per 15-minute bin, in centi-units (PPDA x 100), None when no actions.

    Lower PPDA means a more intense press: fewer opponent passes allowed per defensive action.
    Defensive actions: tackles (won or lost), fouls and interceptions.
    """
    out: dict[str, Any] = {}
    for team in (view.home_id, view.away_id):
        opp = view.opponent(team)
        passes = [0] * 6
        actions = [0] * 6
        for e in view.events:
            if e["team"] == opp and e["type"] == "pass" and e["x_dm"] < PPDA_OPP_PASS_MAX_X_DM:
                passes[bin15(e)] += 1
            elif e["team"] == team and _is_def_action(e) and e["x_dm"] >= PPDA_DEF_ACTION_MIN_X_DM:
                actions[bin15(e)] += 1
        out[team] = {
            "ppda_centi": ratio_round(sum(passes), sum(actions), 100),
            "opponent_passes": sum(passes),
            "defensive_actions": sum(actions),
            "by_15": [
                {"bin": BIN15_LABELS[b], "ppda_centi": ratio_round(passes[b], actions[b], 100), "opponent_passes": passes[b], "defensive_actions": actions[b]}
                for b in range(6)
            ],
        }
    return out


# --------------------------------------------------------------------------- windows


def _windows(view: MatchView) -> "OrderedDict[tuple[int, int], list[dict[str, Any]]]":
    groups: OrderedDict[tuple[int, int], list[dict[str, Any]]] = OrderedDict()
    for e in view.events:
        groups.setdefault(window5(e), []).append(e)
    return groups


def window_raw(events: list[dict[str, Any]]) -> dict[str, int]:
    """Raw per-window counts: on-ball actions, chains, open-play turnovers, entropy, chaos index."""
    on_ball = [e for e in events if e["type"] in ON_BALL_TYPES]
    per_chain: dict[int, int] = {}
    for e in on_ball:
        per_chain[e["possession_chain_id"]] = per_chain.get(e["possession_chain_id"], 0) + 1
    chains = len(per_chain)
    turnovers = sum(1 for e in events if e["type"] == "possession_change" and e["outcome"] in OPEN_PLAY_TURNOVERS)
    entropy = normalised_entropy_permille(per_chain[k] for k in sorted(per_chain))
    turnover_share = min(1000, ratio_round(turnovers, chains, 1000) or 0)
    return {
        "on_ball_actions": len(on_ball),
        "chains": chains,
        "open_play_turnovers": turnovers,
        "chain_entropy_permille": entropy,
        "turnover_share_permille": turnover_share,
        "index_permille": (entropy + turnover_share) // 2,
    }


def control_vs_chaos(view: MatchView, model: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Per 5-minute window: possession-chain entropy, open-play turnover share, their mean, and a band.

    Chain entropy is the normalised entropy of on-ball actions across possession chains: 0 when one
    chain holds every action (one side keeps the ball), 1000 when every action is its own chain.
    Turnover share is open-play turnovers per chain. The index is their mean: low reads as control,
    high as chaos. Band thresholds are frozen training terciles.
    """
    th = (model or load_model())["bands"]["control_chaos"]
    out = []
    for key, events in _windows(view).items():
        raw = window_raw(events)
        out.append({"window": window_label(key), "period": key[0], "start_minute": key[1] * 5, **raw, "band": band_of(raw["index_permille"], th, BANDS["control_chaos"])})
    return out


def pressure_counts(view: MatchView) -> "OrderedDict[tuple[int, int], dict[str, int]]":
    counts: OrderedDict[tuple[int, int], dict[str, int]] = OrderedDict()
    for key, events in _windows(view).items():
        counts[key] = {
            view.home_id: sum(1 for e in events if e["type"] == "pressure" and e["team"] == view.home_id),
            view.away_id: sum(1 for e in events if e["type"] == "pressure" and e["team"] == view.away_id),
        }
    return counts


def pressure_regimes(view: MatchView, model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Pressing regimes per team: consecutive 5-minute windows in the same band merged into one regime.

    A regime boundary is a tactical pressure change in the rules' words. Each regime carries its
    windows, the pressure events it holds and the band.
    """
    th = (model or load_model())["bands"]["pressure"]
    counts = pressure_counts(view)
    out: dict[str, Any] = {}
    for team in (view.home_id, view.away_id):
        regimes: list[dict[str, Any]] = []
        for key, c in counts.items():
            band = band_of(c[team], th, BANDS["pressure"])
            if regimes and regimes[-1]["band"] == band:
                regimes[-1]["windows"].append(window_label(key))
                regimes[-1]["pressures"] += c[team]
                regimes[-1]["end"] = window_label(key)
            else:
                regimes.append({"band": band, "start": window_label(key), "end": window_label(key), "windows": [window_label(key)], "pressures": c[team]})
        changes = [{"at": r["start"], "from": regimes[i - 1]["band"], "to": r["band"]} for i, r in enumerate(regimes) if i > 0]
        out[team] = {"regimes": regimes, "changes": changes}
    return out


def tempo_bands(view: MatchView, model: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """On-ball actions per 5-minute window (both sides), banded slow / normal / fast by training terciles."""
    th = (model or load_model())["bands"]["tempo"]
    out = []
    for key, events in _windows(view).items():
        n = sum(1 for e in events if e["type"] in ON_BALL_TYPES)
        out.append({"window": window_label(key), "on_ball_actions": n, "band": band_of(n, th, BANDS["tempo"])})
    return out


# --------------------------------------------------------------------------- per-event values


def shot_table(view: MatchView, model: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    m = model or load_model()
    return [
        {"event_id": e["event_id"], "team": e["team"], "player": e["player"], "outcome": e["outcome"], "qualifier": e["qualifier"], "xg_ppm": xg_ppm(e, view.home_id, m)}
        for e in view.events
        if e["type"] == "shot"
    ]


def pass_table(view: MatchView, model: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    m = model or load_model()
    return [
        {
            "event_id": e["event_id"],
            "team": e["team"],
            "player": e["player"],
            "outcome": e["outcome"],
            "distance_m": pass_distance_m(e),
            "difficulty": pass_difficulty(e, view.home_id, m),
        }
        for e in view.events
        if e["type"] == "pass"
    ]


def totals(view: MatchView, model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """xG, xT added (completed passes and carries), and mean pass distance and difficulty per team."""
    m = model or load_model()
    out: dict[str, Any] = {}
    shots = shot_table(view, m)
    passes = pass_table(view, m)
    for team in (view.home_id, view.away_id):
        tp = [p for p in passes if p["team"] == team]
        xt = sum(v for e in view.events if e["team"] == team for v in [xt_added_ppm(e, m)] if v is not None)
        out[team] = {
            "xg_ppm": sum(s["xg_ppm"] for s in shots if s["team"] == team),
            "shots": sum(1 for s in shots if s["team"] == team),
            "xt_added_ppm": xt,
            "passes": len(tp),
            "mean_pass_distance_dm": ratio_round(sum(p["distance_m"] for p in tp), len(tp), 10),
            "mean_pass_difficulty_centi": ratio_round(sum(p["difficulty"] for p in tp), len(tp), 100),
        }
    return out


def momentum_series(view: MatchView, model: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Per engine minute with events: threat per side (xG plus positive xT added, ppm) and a decayed balance.

    momentum = previous x 0.8 + (home threat - away threat); positive leans home. Integer only.
    """
    m = model or load_model()
    per_minute: OrderedDict[tuple[int, int], dict[str, int]] = OrderedDict()
    for e in view.events:
        slot = per_minute.setdefault((e["period"], e["minute"]), {view.home_id: 0, view.away_id: 0})
        if e["type"] == "shot":
            slot[e["team"]] += xg_ppm(e, view.home_id, m)
        else:
            v = xt_added_ppm(e, m)
            if v is not None and v > 0:
                slot[e["team"]] += v
    out = []
    bal = 0
    for (period, minute), slot in per_minute.items():
        bal = bal * MOMENTUM_DECAY_PERMILLE // 1000 + slot[view.home_id] - slot[view.away_id]
        out.append({"period": period, "minute": minute, "home_threat_ppm": slot[view.home_id], "away_threat_ppm": slot[view.away_id], "momentum_ppm": bal})
    return out

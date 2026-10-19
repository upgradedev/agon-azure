"""Shot, pass and zone features, and the frozen models that read them: xG, pass difficulty and xT.

All features are integers in the acting team's frame (it attacks towards x_dm = 1050). The feature
lists below are part of the model contract: `fit.py` writes the same names into model.json and
`check_feature_names` refuses a model fitted on a different list.

xG: a logistic model of P(goal | shot location and context), fitted on training seeds 1 to 500.
Pass difficulty: round(100 x (1 - modelled completion)), where completion comes from a second
logistic model fitted on the same seeds. It is the MODELLED completion probability, learned from
outcomes in the event log; it is not the generator's internal success probability, which interpret
never sees.
xT: expected threat on the 16 by 12 grid by value iteration (see fit.py); an action's xT added is
xT(end zone) minus xT(start zone) for a completed pass or carry.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agon.interpret._fixed import linear_micro, sigmoid_ppm
from agon.interpret.frame import (
    BOX_DEPTH_DM,
    BOX_HALF_WIDTH_DM,
    FINAL_THIRD_X_DM,
    GOAL_X_DM,
    GOAL_Y_DM,
    OWN_THIRD_X_DM,
    distance_dm,
    dm_to_m,
    zone_of,
)
from agon.interpret.model import ModelError, load_model

XG_FEATURES: tuple[str, ...] = (
    "distance_m",
    "lateral_m",
    "in_box",
    "under_pressure",
    "header",
    "free_kick",
    "penalty",
    "home",
    "late_minutes",
)
PASS_FEATURES: tuple[str, ...] = (
    "distance_m",
    "forward_m",
    "under_pressure",
    "end_final_third",
    "start_own_third",
    "home",
)


def shot_features(shot: Mapping[str, Any], home_id: str) -> list[int]:
    """Integer features of one shot, in XG_FEATURES order."""
    x, y = shot["x_dm"], shot["y_dm"]
    q = shot["qualifier"]
    in_box = x >= GOAL_X_DM - BOX_DEPTH_DM and abs(y - GOAL_Y_DM) <= BOX_HALF_WIDTH_DM
    return [
        dm_to_m(distance_dm(x, y, GOAL_X_DM, GOAL_Y_DM)),
        dm_to_m(abs(y - GOAL_Y_DM)),
        int(in_box),
        int(bool(shot["under_pressure"])),
        int(q == "header"),
        int(q == "free_kick"),
        int(q == "penalty"),
        int(shot["team"] == home_id),
        max(0, shot["minute"] - 45),
    ]


def pass_features(p: Mapping[str, Any], home_id: str) -> list[int]:
    """Integer features of one pass, in PASS_FEATURES order."""
    x, y, ex, ey = p["x_dm"], p["y_dm"], p["end_x_dm"], p["end_y_dm"]
    return [
        dm_to_m(distance_dm(x, y, ex, ey)),
        dm_to_m(max(0, ex - x)),
        int(bool(p["under_pressure"])),
        int(ex >= FINAL_THIRD_X_DM),
        int(x < OWN_THIRD_X_DM),
        int(p["team"] == home_id),
    ]


def check_feature_names(section: Mapping[str, Any], expected: Sequence[str], what: str) -> None:
    if tuple(section["features"]) != tuple(expected):
        raise ModelError(f"{what} model was fitted on {section['features']}, code expects {list(expected)}")


def xg_ppm(shot: Mapping[str, Any], home_id: str, model: Mapping[str, Any] | None = None) -> int:
    """Modelled probability that this shot is a goal, in ppm."""
    m = (model or load_model())["xg"]
    check_feature_names(m, XG_FEATURES, "xG")
    return sigmoid_ppm(linear_micro(m["coef_micro"], shot_features(shot, home_id)))


def pass_completion_ppm(p: Mapping[str, Any], home_id: str, model: Mapping[str, Any] | None = None) -> int:
    """Modelled probability that this pass is completed, in ppm."""
    m = (model or load_model())["pass"]
    check_feature_names(m, PASS_FEATURES, "pass")
    return sigmoid_ppm(linear_micro(m["coef_micro"], pass_features(p, home_id)))


def pass_difficulty(p: Mapping[str, Any], home_id: str, model: Mapping[str, Any] | None = None) -> int:
    """round(100 x (1 - modelled completion)), 0..100, half up in integers."""
    done = pass_completion_ppm(p, home_id, model)
    return (100 * (1_000_000 - done) + 500_000) // 1_000_000


def pass_distance_m(p: Mapping[str, Any]) -> int:
    return dm_to_m(distance_dm(p["x_dm"], p["y_dm"], p["end_x_dm"], p["end_y_dm"]))


def xt_value_ppm(x_dm: int, y_dm: int, model: Mapping[str, Any] | None = None) -> int:
    zx, zy = zone_of(x_dm, y_dm)
    return (model or load_model())["xt"]["grid"][zx][zy]


def xt_added_ppm(event: Mapping[str, Any], model: Mapping[str, Any] | None = None) -> int | None:
    """xT(end) - xT(start) for a completed pass or carry; None for any other event."""
    if event["type"] not in ("pass", "carry") or event["outcome"] != "complete":
        return None
    m = model or load_model()
    return xt_value_ppm(event["end_x_dm"], event["end_y_dm"], m) - xt_value_ppm(event["x_dm"], event["y_dm"], m)

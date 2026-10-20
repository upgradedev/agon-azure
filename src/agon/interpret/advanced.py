"""Advanced metrics pack: xA, progression, box entries, field tilt, counter-press, set-piece xG and more.

What it adds to `agon.interpret.metrics`, every value from the event log alone:

- xA: for every pass that set up a shot (the key pass, `recompute.key_pass_index`), the modelled xG
  of that shot. A player's xA is the sum over his key passes.
- Progressive passes and carries, passes and crosses into the penalty area, carries into it, box
  entries and deep completions: counted with the predicates in `agon.governance.recompute`, the
  same ones the Fact Gate counts with (definitions there, in "advanced counts").
- Field tilt: a club's share of the passes both clubs attempted from their attacking final third.
- PPDA: reused from `metrics.ppda`, unchanged.
- Defensive action height: the mean x of a club's tackles, fouls and interceptions (the PPDA
  defensive actions) in its own frame, where 0 is its own goal line. A PROXY for how high a side
  defends, not a measured defensive line: the generator has no 22-player tracking.
- 5-second counter-press recovery rate: open-play losses regained in open play within 5 seconds of
  match clock (`recompute.counter_press`).
- Set-piece xG split: each shot's xG by how its possession chain started. Corner, free kick (direct
  or not) and penalty chains are set pieces; throw-ins, goal kicks, kick-offs and chains won in open
  play are open play.
- xT added per player: the sum of `features.xt_added_ppm` over his completed passes and carries.
- Pass quality, the brief's three terms: distance (metres), accuracy (completed over attempted) and
  difficulty (round(100 x (1 - modelled completion))), per pass, per player and per club.

Every function is pure and integer-only (ppm, per mille, decimetres, centi-units), so the output is
the same bytes on every OS. `pack` is a separate entry point on purpose: `interpret_match` and its
pinned hash, which the fleet seals, do not change.

Isolation: like the rest of the package, this module reads the event contract and
`agon.governance.recompute` only.
"""

from __future__ import annotations

from typing import Any, Mapping

import agon.governance.recompute as recompute
from agon.interpret._fixed import ratio_round
from agon.interpret.features import pass_completion_ppm, pass_difficulty, pass_distance_m, xg_ppm, xt_added_ppm
from agon.interpret.frame import BIN15_LABELS, MatchView, bin15
from agon.interpret.metrics import _is_def_action, ppda
from agon.interpret.model import load_model

PACK_VERSION = "advanced-v1"
SET_PIECE_RESTARTS: tuple[str, ...] = ("corner", "free_kick", "penalty")
XG_PHASES: tuple[str, ...] = ("open_play", "corner", "free_kick", "penalty")

# One entry per metric: label, unit, who has it, which way is better, one plain sentence for a
# reader, and the exact rule. The web data carries this table beside the numbers.
METRICS: dict[str, dict[str, str]] = {
    "xg_ppm": {
        "label": "Expected goals (xG)", "unit": "ppm", "scope": "club, player", "better": "higher",
        "description": "How many goals the chances were worth on average, from where and how each shot was taken.",
        "rule": "Sum of the modelled goal probability of every shot (agon.interpret xG model, frozen in model.json).",
    },
    "xa_ppm": {
        "label": "Expected assists (xA)", "unit": "ppm", "scope": "club, player", "better": "higher",
        "description": "How good the chances a player set up were, whether or not the shot went in.",
        "rule": "For each key pass, the xG of the shot it set up; summed per player and club.",
    },
    "key_passes": {
        "label": "Key passes", "unit": "count", "scope": "club, player", "better": "higher",
        "description": "Passes that led straight to a shot by a teammate.",
        "rule": (
            "The assist rule applied to every shot: walking back from the shot inside its possession, skipping the "
            "opponent's events (a pressure, a lost tackle) and the shooter's own carries, the first event must be a "
            "completed pass to the shooter by a teammate; anything else, and every penalty, means no key pass."
        ),
    },
    "xg_open_play_ppm": {
        "label": "Open-play xG", "unit": "ppm", "scope": "club", "better": "higher",
        "description": "Chance quality from normal play, set pieces left out.",
        "rule": "xG of shots in possessions that did not start with a corner, free kick or penalty (throw-ins, goal kicks and kick-offs count as open play).",
    },
    "xg_set_piece_ppm": {
        "label": "Set-piece xG", "unit": "ppm", "scope": "club", "better": "higher",
        "description": "Chance quality from corners, free kicks and penalties.",
        "rule": "xG of shots in possessions that started with a corner, a free kick (direct or not) or a penalty; split further into corner, free kick and penalty.",
    },
    "xt_added_ppm": {
        "label": "Expected threat added (xT)", "unit": "ppm", "scope": "club, player", "better": "higher",
        "description": "How much a player's passes and carries moved the ball into more dangerous areas.",
        "rule": "Sum of xT(end zone) minus xT(start zone) over completed passes and carries, on the 16 by 12 grid in model.json.",
    },
    "progressive_passes": {
        "label": "Progressive passes", "unit": "count", "scope": "club, player", "better": "higher",
        "description": "Completed passes that moved the ball at least 9.1 metres closer to goal.",
        "rule": "Completed pass starting at x of 42 m or more (outside the team's own 40%) and ending at least 9.1 m closer to the centre of the goal attacked.",
    },
    "progressive_carries": {
        "label": "Progressive carries", "unit": "count", "scope": "club, player", "better": "higher",
        "description": "Runs with the ball that moved it at least 9.1 metres closer to goal.",
        "rule": "Carry starting at x of 42 m or more and ending at least 9.1 m closer to the centre of the goal attacked.",
    },
    "passes_into_box": {
        "label": "Passes into the box", "unit": "count", "scope": "club, player", "better": "higher",
        "description": "Completed passes from outside the penalty area to a teammate inside it.",
        "rule": "Completed pass ending inside the penalty area (16.5 m deep, 20.2 m either side of the centre line) and starting outside it; crosses included.",
    },
    "crosses_into_box": {
        "label": "Crosses into the box (proxy)", "unit": "count", "scope": "club, player", "better": "higher",
        "description": "Completed passes into the box from a wide position near goal.",
        "rule": "A geometric proxy, since the events carry no cross field: a pass into the box that starts in the final third and wider than the box. Corners played into the box count.",
    },
    "carries_into_box": {
        "label": "Carries into the box", "unit": "count", "scope": "club, player", "better": "higher",
        "description": "Times a player ran the ball into the penalty area.",
        "rule": "Carry ending inside the penalty area and starting outside it.",
    },
    "box_entries": {
        "label": "Box entries", "unit": "count", "scope": "club, player", "better": "higher",
        "description": "Every time the ball reached the penalty area by a pass or a carry.",
        "rule": "Passes into the box plus carries into the box.",
    },
    "deep_completions": {
        "label": "Deep completions", "unit": "count", "scope": "club, player", "better": "higher",
        "description": "Completed passes that reached a teammate within 20 metres of goal.",
        "rule": "Completed pass ending within 20 m of the centre of the goal attacked; crosses into the box excluded.",
    },
    "field_tilt_permille": {
        "label": "Field tilt", "unit": "per mille", "scope": "club", "better": "higher",
        "description": "Which side spent more of the game passing in the other side's final third.",
        "rule": "A club's passes attempted from its attacking final third (start x of 70 m or more) over both clubs' such passes.",
    },
    "ppda_centi": {
        "label": "PPDA", "unit": "centi (PPDA x 100)", "scope": "club", "better": "lower",
        "description": "How many passes a side let the opponent play in its build-up before winning or contesting the ball; lower means a more intense press.",
        "rule": "agon.interpret.metrics.ppda: opponent passes in its own 60% of the pitch over this side's tackles, fouls and interceptions in the same area.",
    },
    "defensive_action_height_dm": {
        "label": "Defensive action height (proxy)", "unit": "decimetres from own goal line", "scope": "club", "better": "context",
        "description": "How far up the pitch a side made its tackles, fouls and interceptions, on average; a proxy, not a measured defensive line.",
        "rule": "Mean x of the club's tackles, fouls and interceptions (the PPDA defensive actions) in its own frame, where 0 is its own goal line; rounded half up.",
    },
    "counter_press_rate_permille": {
        "label": "5-second counter-press recovery rate", "unit": "per mille", "scope": "club", "better": "higher",
        "description": "How often a side won the ball straight back within 5 seconds of losing it.",
        "rule": "Open-play losses (the opponent's interception or tackle) regained by the club's own interception or tackle within 5 s of match clock in the same period, over all open-play losses.",
    },
    "pass_accuracy_permille": {
        "label": "Pass accuracy", "unit": "per mille", "scope": "club, player", "better": "higher",
        "description": "The share of passes that reached a teammate.",
        "rule": "Completed passes over passes attempted (out of play and intercepted both count as attempted).",
    },
    "mean_pass_distance_dm": {
        "label": "Mean pass distance", "unit": "decimetres", "scope": "club, player", "better": "context",
        "description": "How long the passes were on average.",
        "rule": "Mean of each pass's straight-line length in whole metres (start to end point), times 10, rounded half up.",
    },
    "mean_pass_difficulty_centi": {
        "label": "Mean pass difficulty", "unit": "centi (difficulty x 100)", "scope": "club, player", "better": "context",
        "description": "How hard the attempted passes were, 0 easy to 100 nearly impossible.",
        "rule": "Mean of round(100 x (1 - modelled completion probability)) over passes attempted; the completion model is learned from outcomes in the event log.",
    },
}

TEAM_KEYS: tuple[str, ...] = (
    "xg_ppm", "shots", "xa_ppm", "key_passes",
    "xg_open_play_ppm", "xg_set_piece_ppm", "xg_corner_ppm", "xg_free_kick_ppm", "xg_penalty_ppm",
    "xt_added_ppm", "progressive_passes", "progressive_carries",
    "passes_into_box", "crosses_into_box", "carries_into_box", "box_entries", "deep_completions",
    "final_third_passes", "field_tilt_permille",
    "ppda_centi", "ppda_opponent_passes", "ppda_defensive_actions",
    "defensive_actions", "defensive_action_height_dm",
    "counter_press_losses", "counter_press_recoveries", "counter_press_rate_permille",
    "passes", "passes_completed", "pass_accuracy_permille", "mean_pass_distance_dm", "mean_pass_difficulty_centi",
)
PLAYER_KEYS: tuple[str, ...] = (
    "xg_ppm", "shots", "xa_ppm", "key_passes", "xt_added_ppm",
    "progressive_passes", "progressive_carries",
    "passes_into_box", "crosses_into_box", "carries_into_box", "box_entries", "deep_completions",
    "tackles_and_fouls",
    "passes", "passes_completed", "pass_accuracy_permille", "mean_pass_distance_dm", "mean_pass_difficulty_centi",
)
SERIES_KEYS: tuple[str, ...] = (
    "xg_ppm", "xa_ppm", "xt_added_ppm", "progressive_passes", "progressive_carries", "box_entries",
    "deep_completions", "field_tilt_permille", "ppda_centi", "defensive_action_height_dm",
    "counter_press_losses", "counter_press_rate_permille",
)
# Counts kept per club, per player and per 15-minute bin, keyed by the recompute event kind.
_COUNTED_KINDS: dict[str, str] = {
    "progressive_passes": "progressive_pass",
    "progressive_carries": "progressive_carry",
    "passes_into_box": "pass_into_box",
    "crosses_into_box": "cross_into_box",
    "carries_into_box": "carry_into_box",
    "box_entries": "box_entry",
    "deep_completions": "deep_completion",
}


def permille(num: int, den: int) -> int | None:
    """round(1000 * num / den), half up; None when den is 0."""
    return ratio_round(num, den, 1000)


def shot_phase(view: MatchView) -> dict[str, str]:
    """Shot event id -> 'open_play', 'corner', 'free_kick' or 'penalty', by how its chain started."""
    restart: dict[int, str] = {}
    for e in view.events:
        if e["type"] == "set_piece" and e["possession_chain_id"] not in restart:
            restart[e["possession_chain_id"]] = e["qualifier"]
    out = {}
    for e in view.events:
        if e["type"] == "shot":
            q = restart.get(e["possession_chain_id"])
            out[e["event_id"]] = q if q in SET_PIECE_RESTARTS else "open_play"
    return out


def key_pass_map(view: MatchView, model: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
    """Key pass event id -> (shot event id, that shot's xG in ppm)."""
    out = {}
    for i, e in enumerate(view.events):
        if e["type"] == "shot":
            j = recompute.key_pass_index(view.events, i)
            if j is not None:
                out[view.events[j]["event_id"]] = (e["event_id"], xg_ppm(e, view.home_id, model))
    return out


def pass_rows(view: MatchView, model: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every pass with the brief's pass quality terms and the pack's flags, in sequence order.

    Coordinates stay in the acting team's frame (as stored); a page drawing both clubs converts with
    agon.synth.schema.to_fixed_frame.
    """
    m = model or load_model()
    keys = key_pass_map(view, m)
    rows = []
    for e in view.events:
        if e["type"] != "pass":
            continue
        key = keys.get(e["event_id"])
        rows.append({
            "event_id": e["event_id"],
            "team": e["team"],
            "player": e["player"],
            "receiver": e["related_player"],
            "period": e["period"],
            "minute": e["minute"],
            "second": e["second"],
            "x_dm": e["x_dm"],
            "y_dm": e["y_dm"],
            "end_x_dm": e["end_x_dm"],
            "end_y_dm": e["end_y_dm"],
            "outcome": e["outcome"],
            "under_pressure": bool(e["under_pressure"]),
            "distance_m": pass_distance_m(e),
            "completion_ppm": pass_completion_ppm(e, view.home_id, m),
            "difficulty": pass_difficulty(e, view.home_id, m),
            "progressive": recompute.EVENT_KINDS["progressive_pass"](e),
            "into_box": recompute.is_pass_into_box(e),
            "cross_into_box": recompute.is_cross_into_box(e),
            "deep_completion": recompute.is_deep_completion(e),
            "key_pass": key is not None,
            "xa_ppm": key[1] if key else 0,
        })
    return rows


def _blank(keys: tuple[str, ...]) -> dict[str, int]:
    return {k: 0 for k in keys}


class _Acc:
    """Running sums for one club, one player or one 15-minute bin."""

    def __init__(self) -> None:
        self.n = _blank(("xg_ppm", "shots", "xa_ppm", "key_passes", "xt_added_ppm", "tackles_and_fouls",
                         "passes", "passes_completed", "distance_m", "difficulty", "final_third_passes",
                         "defensive_actions", "defensive_x_dm", "counter_press_losses", "counter_press_recoveries",
                         *XG_PHASES, *_COUNTED_KINDS))

    def add(self, key: str, value: int = 1) -> None:
        self.n[key] += value


def _accumulate(view: MatchView, m: Mapping[str, Any]) -> tuple[dict[str, _Acc], dict[str, _Acc], dict[str, list[_Acc]]]:
    teams = {t: _Acc() for t in (view.home_id, view.away_id)}
    players: dict[str, _Acc] = {}
    bins = {t: [_Acc() for _ in BIN15_LABELS] for t in teams}
    phase = shot_phase(view)
    keys = key_pass_map(view, m)
    kinds = {name: recompute.EVENT_KINDS[kind] for name, kind in _COUNTED_KINDS.items()}

    def hit(team: str, player: str | None, b: int, key: str, value: int = 1) -> None:
        teams[team].add(key, value)
        bins[team][b].add(key, value)
        if player is not None:
            players.setdefault(player, _Acc()).add(key, value)

    for e in view.events:
        team, player, b = e["team"], e["player"], bin15(e)
        if player is not None:
            players.setdefault(player, _Acc())
        if e["type"] == "shot":
            xg = xg_ppm(e, view.home_id, m)
            hit(team, player, b, "xg_ppm", xg)
            hit(team, player, b, "shots")
            teams[team].add(phase[e["event_id"]], xg)
        if e["type"] == "pass":
            hit(team, player, b, "passes")
            if e["outcome"] == "complete":
                hit(team, player, b, "passes_completed")
            hit(team, player, b, "distance_m", pass_distance_m(e))
            hit(team, player, b, "difficulty", pass_difficulty(e, view.home_id, m))
            if e["x_dm"] >= recompute.FINAL_THIRD_MIN_X_DM:
                hit(team, player, b, "final_third_passes")
            if e["event_id"] in keys:
                hit(team, player, b, "key_passes")
                hit(team, player, b, "xa_ppm", keys[e["event_id"]][1])
        xt = xt_added_ppm(e, m)
        if xt is not None:
            hit(team, player, b, "xt_added_ppm", xt)
        for name, pred in kinds.items():
            if pred(e):
                hit(team, player, b, name)
        if _is_def_action(e):
            hit(team, None, b, "defensive_actions")
            hit(team, None, b, "defensive_x_dm", e["x_dm"])
            if player is not None:
                players[player].add("tackles_and_fouls")
    for team in teams:
        for loss, regain in recompute.counter_press(view.events, team):
            b = bin15(loss)
            for acc in (teams[team], bins[team][b]):
                acc.add("counter_press_losses")
                if regain is not None:
                    acc.add("counter_press_recoveries")
    return teams, players, bins


def _pass_quality(n: Mapping[str, int]) -> dict[str, Any]:
    return {
        "passes": n["passes"],
        "passes_completed": n["passes_completed"],
        "pass_accuracy_permille": permille(n["passes_completed"], n["passes"]),
        "mean_pass_distance_dm": ratio_round(n["distance_m"], n["passes"], 10),
        "mean_pass_difficulty_centi": ratio_round(n["difficulty"], n["passes"], 100),
    }


def _counts(n: Mapping[str, int]) -> dict[str, int]:
    return {k: n[k] for k in _COUNTED_KINDS}


def pack(view: MatchView, model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The whole pack for one match: per club, per player, per 15-minute bin, and every pass."""
    m = model or load_model()
    teams, players, bins = _accumulate(view, m)
    pp = ppda(view)
    out_teams: dict[str, Any] = {}
    for team, acc in teams.items():
        n, opp = acc.n, teams[view.opponent(team)].n
        set_piece = n["corner"] + n["free_kick"] + n["penalty"]
        out_teams[team] = {
            "xg_ppm": n["xg_ppm"], "shots": n["shots"], "xa_ppm": n["xa_ppm"], "key_passes": n["key_passes"],
            "xg_open_play_ppm": n["open_play"], "xg_set_piece_ppm": set_piece,
            "xg_corner_ppm": n["corner"], "xg_free_kick_ppm": n["free_kick"], "xg_penalty_ppm": n["penalty"],
            "xt_added_ppm": n["xt_added_ppm"],
            **_counts(n),
            "final_third_passes": n["final_third_passes"],
            "field_tilt_permille": permille(n["final_third_passes"], n["final_third_passes"] + opp["final_third_passes"]),
            "ppda_centi": pp[team]["ppda_centi"],
            "ppda_opponent_passes": pp[team]["opponent_passes"],
            "ppda_defensive_actions": pp[team]["defensive_actions"],
            "defensive_actions": n["defensive_actions"],
            "defensive_action_height_dm": ratio_round(n["defensive_x_dm"], n["defensive_actions"], 1),
            "counter_press_losses": n["counter_press_losses"],
            "counter_press_recoveries": n["counter_press_recoveries"],
            "counter_press_rate_permille": permille(n["counter_press_recoveries"], n["counter_press_losses"]),
            **_pass_quality(n),
        }
    out_players = []
    club_of = {e["player"]: e["team"] for e in view.events if e["player"] is not None}
    for pid in sorted(players):
        n = players[pid].n
        out_players.append({
            "player_id": pid,
            "club_id": club_of[pid],
            "xg_ppm": n["xg_ppm"], "shots": n["shots"], "xa_ppm": n["xa_ppm"], "key_passes": n["key_passes"],
            "xt_added_ppm": n["xt_added_ppm"],
            **_counts(n),
            "tackles_and_fouls": n["tackles_and_fouls"],
            **_pass_quality(n),
        })
    series: dict[str, Any] = {}
    for team in teams:
        opp = view.opponent(team)
        rows = []
        for b, acc in enumerate(bins[team]):
            n, o = acc.n, bins[opp][b].n
            rows.append({
                "xg_ppm": n["xg_ppm"], "xa_ppm": n["xa_ppm"], "xt_added_ppm": n["xt_added_ppm"],
                "progressive_passes": n["progressive_passes"], "progressive_carries": n["progressive_carries"],
                "box_entries": n["box_entries"], "deep_completions": n["deep_completions"],
                "field_tilt_permille": permille(n["final_third_passes"], n["final_third_passes"] + o["final_third_passes"]),
                "ppda_centi": pp[team]["by_15"][b]["ppda_centi"],
                "defensive_action_height_dm": ratio_round(n["defensive_x_dm"], n["defensive_actions"], 1),
                "counter_press_losses": n["counter_press_losses"],
                "counter_press_rate_permille": permille(n["counter_press_recoveries"], n["counter_press_losses"]),
            })
        series[team] = {k: [r[k] for r in rows] for k in SERIES_KEYS}
    return {
        "pack": PACK_VERSION,
        "match_id": view.match_id,
        "home": view.home_id,
        "away": view.away_id,
        "bins": list(BIN15_LABELS),
        "teams": out_teams,
        "players": out_players,
        "series": series,
        "passes": pass_rows(view, m),
    }


__all__ = ["METRICS", "PACK_VERSION", "TEAM_KEYS", "PLAYER_KEYS", "SERIES_KEYS", "pack", "pass_rows", "shot_phase", "key_pass_map"]

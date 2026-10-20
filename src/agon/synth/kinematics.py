"""Synthetic kinematics: ball speeds and 1 Hz positions of every player, derived from an event log.

Usage:
    python -m agon.synth.kinematics --seeds 1,2,3                 # metric M9; exit 1 on any cap violation
    python -m agon.synth.kinematics --seeds 1,2,3 --print-hash --hash-out build/kinematics/hash.txt
    python -m agon.synth.kinematics --seeds 1 --mutate            # plants one teleport; must exit 1

What this is, stated plainly. The match engine produces events, not tracking. This module adds a
synthetic tracking layer ON TOP of a finished event log: it reads the events (and, for player pace
and starting elevens, the seed's world) and never changes them, so every event log, manifest and
season hash stays byte-identical. Its only random draws are on its own stream
(`StreamId.KINEMATICS`), keyed by (seed, match_index + 1, ...), which the engine never draws.
It is a simple, documented motion model, not a physics or biomechanics simulation and not a
measurement of real players.

Ball. Every pass (complete, incomplete or out) and every shot gets a drawn ball speed, within
stated bounds (`drawn_speed_kmh_x10`). A pass speed reads its distance and whether it was played
under pressure. A shot speed does NOT read distance: it is a base per shot type (open play, header,
penalty, free kick), plus `SHOT_PER_FINISHING_KMH_X10` per point of the shooter's finishing above
or below 60 (signed: a finisher at 20 loses 16 km/h), plus `SHOT_PRESSURE_KMH_X10` under pressure;
both carry uniform noise. The flight time is the
distance over that speed, and it must fit inside the gap to the next event; when it would not, the
flight is shortened to the gap and the published speed is the faster average that results. The
published `speed_kmh_x10` is always distance over flight time, so the two never disagree.

Players. One position per second (1 Hz) for every player on the pitch, ball included, in the fixed
frame `home_attacks_high_x_both_periods` (agon.synth.schema.FIXED_FRAME), in decimetres. Internally
the model steps at 10 Hz (`SUBSTEPS`) in units of 0.1 mm (`UNITS_PER_DM` units per decimetre):
- Team shape: each club keeps a fixed 4-3-3 (`FORMATION`, in its own attacking frame). The block
  shifts with the ball (`BLOCK_FOLLOW_X_PCT`, `BLOCK_FOLLOW_Y_PCT`), pushes up and widens in
  possession and drops and narrows out of it (`POSSESSION_PUSH_MM`, `DEFENCE_DROP_MM`,
  `WIDTH_IN_POSSESSION_PCT`, `WIDTH_OUT_OF_POSSESSION_PCT`). Each player has a personal offset of up
  to `PERSONAL_OFFSET_MM` per axis, drawn once per match. Keepers track the ball in their own box.
- Anchors: the model steers the actor of every event (passer, carrier, shooter, presser, tackler,
  fouler, set-piece taker) to the event location at the event second, and the receiver of a
  completed pass to the pass end at the second the ball arrives. A player heads for his next anchor
  when it is close in time or the deadline requires it (`APPROACH_HORIZON_S`, `APPROACH_SLACK_S`);
  otherwise he follows the shape. He arrives carrying the velocity toward the anchor after it when
  that one is within `LEAD_HORIZON_S` (`_leave_velocity`), so he does not stop dead between two
  actions; when that following action is itself out of reach, this pull can make him miss the
  nearer anchor instead.
- Caps: speed at most `PLAYER_VMAX_MM_S` (per player, from pace: `VMAX_BASE_MM_S` plus
  `VMAX_PER_PACE_MM_S` per pace point) and a change of velocity vector of at most
  `PLAYER_AMAX_MM_S2` per second. The model never breaks a cap to reach an anchor, so some anchors
  are missed. `anchor_misses` lists every missed actor event; `receiver_arrivals` lists every pass
  arrival with the receiver's distance from the pass end and `receiver_summary` counts the misses.
  `miss_summary` splits the actor misses into those under 1 m, those next to an action no capped
  path can reach (the engine picks some actors at random, so one player can be asked to cover more
  than the speed cap allows, from his nearest earlier action or to his nearest later one), and the
  rest, which are limits of this simple model. It counts events, so one impossible movement can be
  counted at both of its end events, or twice at one spot (a corner kick and its pass).
- Substitutes start where the player they replace stood; a sent-off player's track ends at the
  card. Each period runs from its kick-off second to its last event second.

Metric M9 (`check`): over the PUBLISHED decimetre samples, every step speed at most
`CHECK_VMAX_DM_S`, every step change of velocity at most `CHECK_AMAX_DM_S2`, every position inside
`CHECK_BOX_DM`, every ball speed inside its bounds and every flight inside its gap. The check caps
add the worst-case decimetre rounding error to the model caps: 0.2 m/s and 0.3 m/s^2.
Integers only (`isqrt`), canonical JSON, sha256: the same bytes on every OS.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from math import isqrt
from typing import Any, Iterable, Mapping, Sequence

from agon import __version__
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.manifest import parse_seeds
from agon.synth.rng import Stream, StreamId
from agon.synth.schema import FIXED_FRAME, PITCH_X_DM, PITCH_Y_DM, canonical_bytes, sha256_hex, to_fixed_frame
from agon.synth.world import League, build_world

KINEMATICS_VERSION = "synth-kin-v1"
SCHEMA = "agon.kinematics/1"
RATE_HZ = 1
HALF_SECONDS = 2700
DEFAULT_SEEDS = "1,2,3"

X_MM = PITCH_X_DM * 100
Y_MM = PITCH_Y_DM * 100

# ---------------------------------------------------------------- ball constants (tenths of km/h)
PASS_BASE_KMH_X10 = 300  # a pass of 0 m
PASS_PER_M_KMH_X10 = 9  # added per metre of pass distance
PASS_PRESSURE_KMH_X10 = 40  # a pass played under pressure is hit harder
PASS_NOISE_KMH_X10 = 60  # uniform +- noise
PASS_BOUNDS_KMH_X10 = (150, 1150)
SHOT_BASE_KMH_X10: dict[str, int] = {"open_play": 700, "header": 420, "penalty": 880, "free_kick": 820}
SHOT_PER_FINISHING_KMH_X10 = 4  # per point of finishing above or below 60 (signed)
SHOT_PRESSURE_KMH_X10 = -60
SHOT_NOISE_KMH_X10 = 120
SHOT_BOUNDS_KMH_X10 = (300, 1350)

# ---------------------------------------------------------------- player constants (mm, mm/s)
FORMATION: tuple[tuple[int, int], ...] = (  # own attacking frame, starting-eleven order GK, DF x4, MF x3, FW x3
    (6000, 34000),
    (20000, 10000), (20000, 26000), (20000, 42000), (20000, 58000),
    (40000, 16000), (40000, 34000), (40000, 52000),
    (58000, 12000), (58000, 34000), (58000, 56000),
)
BLOCK_FOLLOW_X_PCT = 45  # the block's x shift as a share of the ball's distance from halfway
BLOCK_FOLLOW_Y_PCT = 30
POSSESSION_PUSH_MM = 6000
DEFENCE_DROP_MM = 4000
WIDTH_IN_POSSESSION_PCT = 115
WIDTH_OUT_OF_POSSESSION_PCT = 85
KEEPER_BASE_X_MM = 5000
KEEPER_FOLLOW_X_PCT = 8
KEEPER_FOLLOW_Y_PCT = 15
PERSONAL_OFFSET_MM = 2500
VMAX_BASE_MM_S = 7800
VMAX_PER_PACE_MM_S = 20
PLAYER_VMAX_MM_S = 9500  # no player faster than 34.2 km/h
PLAYER_AMAX_MM_S2 = 4500
JOG_MM_S = 5500  # shape-following speed cap ...
FAR_FROM_SHAPE_MM = 20000  # ... unless the shape target is further than this
RELAX_DIVISOR = 2  # close half the distance to the shape target per second
APPROACH_HORIZON_S = 6  # always head for an anchor this close in time ...
APPROACH_SLACK_S = 1  # ... or further away when jogging would leave less than this to spare
BOX_MARGIN_MM = 10000  # players may run up to 10 m beyond the lines (the M9 box)
SUBSTEPS = 10  # the motion model steps at 10 Hz internally; positions are published at 1 Hz
UNITS_PER_DM = 1000  # internal positions are in 0.1 mm, so a velocity in mm/s is units per substep
BOX_LO = -BOX_MARGIN_MM * 10
BOX_HI_X = (X_MM + BOX_MARGIN_MM) * 10
BOX_HI_Y = (Y_MM + BOX_MARGIN_MM) * 10
LEAD_HORIZON_S = 10  # carry the velocity toward the following anchor when it is this close
START_HORIZON_S = 10  # a track starts within jogging reach of an anchor this close

# ---------------------------------------------------------------- M9 check (published decimetres)
# Decimetre rounding allowances, from the worst case: a step (difference of two rounded samples) can
# be off by 1 dm per axis (1.41 dm), a change of step (second difference of three) by 2 dm per axis
# (2.83 dm). Both are rounded up to whole decimetres.
CHECK_VMAX_DM_S = 97  # 9.5 m/s plus 0.2
CHECK_AMAX_DM_S2 = 48  # 4.5 m/s^2 plus 0.3
CHECK_BOX_DM = (-100, PITCH_X_DM + 100, -100, PITCH_Y_DM + 100)

BALL_STREAM_BASE = 100_000
PLAYER_STREAM_BASE = 10_000
POSSESSION_TYPES = frozenset({"pass", "carry", "shot", "set_piece", "possession_change"})


def _tdiv(a: int, b: int) -> int:
    """Integer division truncated toward zero (b > 0)."""
    return a // b if a >= 0 else -((-a) // b)


def _cap(vx: int, vy: int, cap: int) -> tuple[int, int]:
    m = isqrt(vx * vx + vy * vy)
    if m <= cap:
        return vx, vy
    return _tdiv(vx * cap, m + 1), _tdiv(vy * cap, m + 1)


def _rdm(units: int) -> int:
    """Internal units (0.1 mm) to whole decimetres, half up."""
    return (units + UNITS_PER_DM // 2) // UNITS_PER_DM


def _second(e: Mapping[str, Any]) -> int:
    return e["minute"] * 60 + e["second"]


def player_vmax(pace: int) -> int:
    return min(PLAYER_VMAX_MM_S, VMAX_BASE_MM_S + VMAX_PER_PACE_MM_S * pace)


# ---------------------------------------------------------------- ball


def drawn_speed_kmh_x10(e: Mapping[str, Any], dist_dm: int, finishing: int, seed: int, match_index: int) -> int:
    """The ball speed drawn for one pass or shot, inside its bounds, before the flight is fitted to the gap."""
    noise = Stream(seed, match_index + 1, StreamId.KINEMATICS, possession=BALL_STREAM_BASE + e["seq"])
    if e["type"] == "pass":
        lo, hi = PASS_BOUNDS_KMH_X10
        v = PASS_BASE_KMH_X10 + PASS_PER_M_KMH_X10 * dist_dm // 10
        v += PASS_PRESSURE_KMH_X10 if e["under_pressure"] else 0
        v += noise.between(-PASS_NOISE_KMH_X10, PASS_NOISE_KMH_X10)
    else:
        lo, hi = SHOT_BOUNDS_KMH_X10
        v = SHOT_BASE_KMH_X10[e["qualifier"]]
        v += SHOT_PER_FINISHING_KMH_X10 * (finishing - 60)
        v += SHOT_PRESSURE_KMH_X10 if e["under_pressure"] else 0
        v += noise.between(-SHOT_NOISE_KMH_X10, SHOT_NOISE_KMH_X10)
    return min(hi, max(lo, v))


def ball_flights(
    league: League, events: Sequence[Mapping[str, Any]], seed: int, match_index: int
) -> list[dict[str, Any]]:
    """Speed and flight of every pass and shot. Pure function of its inputs."""
    finishing = {p.player_id: p.finishing for c in league.clubs for p in c.players}
    out: list[dict[str, Any]] = []
    for i, e in enumerate(events):
        if e["type"] not in ("pass", "shot"):
            continue
        s = _second(e)
        nxt = events[i + 1] if i + 1 < len(events) and events[i + 1]["period"] == e["period"] else None
        gap_ms = max(1000, (_second(nxt) - s) * 1000) if nxt is not None else 3000
        dist_dm = isqrt((e["end_x_dm"] - e["x_dm"]) ** 2 + (e["end_y_dm"] - e["y_dm"]) ** 2)
        v = drawn_speed_kmh_x10(e, dist_dm, finishing.get(e["player"], 60), seed, match_index)
        dist_mm = dist_dm * 100
        flight_ms = min(gap_ms, -(-dist_mm * 36 // v)) if dist_mm else 0
        speed = dist_mm * 36 // flight_ms if flight_ms else v
        out.append({
            "event_id": e["event_id"],
            "type": e["type"],
            "outcome": e["outcome"],
            "qualifier": e["qualifier"],
            "period": e["period"],
            "t_s": s,
            "dist_dm": dist_dm,
            "gap_ms": gap_ms,
            "flight_ms": flight_ms,
            "speed_kmh_x10": speed,
            "under_pressure": e["under_pressure"],
        })
    return out


# ---------------------------------------------------------------- players


@dataclass
class _Mover:
    """One player's state. Positions in units of 0.1 mm, velocities in mm/s (= units per substep)."""

    player_id: str
    team: str
    slot: int
    vmax: int
    offset: tuple[int, int]
    x: int = 0
    y: int = 0
    vx: int = 0
    vy: int = 0
    start_s: int = 0
    xy: list[int] = field(default_factory=list)
    anchors: list[tuple[int, int, int]] = field(default_factory=list)  # (second, x, y) this period, in units
    next_anchor: int = 0


def _own(x: int, y: int, is_home: bool) -> tuple[int, int]:
    """Millimetres between the fixed frame and a club's own attacking frame (the map is its own inverse)."""
    return (x, y) if is_home else (X_MM - x, Y_MM - y)


def _shape_mm(m: _Mover, is_home: bool, ball: tuple[int, int], in_possession: bool) -> tuple[int, int]:
    """The player's shape spot in his OWN frame, millimetres."""
    bx, by = _own(ball[0], ball[1], is_home)
    if m.slot == 0:
        tx = KEEPER_BASE_X_MM + bx * KEEPER_FOLLOW_X_PCT // 100
        ty = Y_MM // 2 + (by - Y_MM // 2) * KEEPER_FOLLOW_Y_PCT // 100
    else:
        sx, sy = FORMATION[m.slot]
        shift_x = (bx - X_MM // 2) * BLOCK_FOLLOW_X_PCT // 100 + (POSSESSION_PUSH_MM if in_possession else -DEFENCE_DROP_MM)
        shift_y = (by - Y_MM // 2) * BLOCK_FOLLOW_Y_PCT // 100
        width = WIDTH_IN_POSSESSION_PCT if in_possession else WIDTH_OUT_OF_POSSESSION_PCT
        tx = sx + shift_x + m.offset[0]
        ty = Y_MM // 2 + (sy - Y_MM // 2) * width // 100 + shift_y + m.offset[1]
    return min(X_MM - 500, max(500, tx)), min(Y_MM - 500, max(500, ty))


def _shape_target(m: _Mover, is_home: bool, ball: tuple[int, int], in_possession: bool) -> tuple[int, int]:
    """The shape spot in the fixed frame, in units of 0.1 mm."""
    x, y = _own(*_shape_mm(m, is_home, ball, in_possession), is_home)
    return x * 10, y * 10


def _ball_track(keys: list[tuple[int, int, int]], start_s: int, end_s: int) -> list[tuple[int, int]]:
    """Ball position (mm) at every second from (t_ms, x_mm, y_mm) keyframes, linear between them."""
    out: list[tuple[int, int]] = []
    j = 0
    for s in range(start_s, end_s + 1):
        t = s * 1000
        while j + 1 < len(keys) and keys[j + 1][0] <= t:
            j += 1
        a = keys[j]
        if j + 1 < len(keys) and a[0] <= t:
            b = keys[j + 1]
            span = b[0] - a[0]
            if span > 0:
                out.append((a[1] + (b[1] - a[1]) * (t - a[0]) // span, a[2] + (b[2] - a[2]) * (t - a[0]) // span))
                continue
        out.append((a[1], a[2]))
    return out


def simulate_tracking(
    league: League,
    events: Sequence[Mapping[str, Any]],
    home_id: str,
    away_id: str,
    seed: int,
    match_index: int,
    flights: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The kinematics record for one match: 1 Hz ball and player tracks per period, and ball flights."""
    flights = list(flights) if flights is not None else ball_flights(league, events, seed, match_index)
    flight_of = {f["event_id"]: f for f in flights}
    clubs = {home_id: league.club(home_id), away_id: league.club(away_id)}
    pace = {p.player_id: p.pace for c in clubs.values() for p in c.players}
    club_no = {cid: int(cid[1:]) for cid in clubs}

    # anchors[player] = {(period, second): (x, y) in units}; event anchors override receiver anchors
    anchors: dict[str, dict[tuple[int, int], tuple[int, int]]] = {}
    receiver: list[tuple[str, int, int, int, int]] = []
    for e in events:
        if e["type"] == "pass" and e["outcome"] == "complete" and e["related_player"]:
            _, _, fx, fy = to_fixed_frame(e, home_id)
            arrive = _second(e) + max(1, -(-flight_of[e["event_id"]]["flight_ms"] // 1000))
            receiver.append((e["related_player"], e["period"], arrive, fx * UNITS_PER_DM, fy * UNITS_PER_DM))
    for pid, period, s, x, y in receiver:
        anchors.setdefault(pid, {})[(period, s)] = (x, y)
    for e in events:
        if e["type"] in ("sub", "possession_change") or not e["player"]:
            continue
        x, y, _, _ = to_fixed_frame(e, home_id)
        anchors.setdefault(e["player"], {})[(e["period"], _second(e))] = (x * UNITS_PER_DM, y * UNITS_PER_DM)

    on_pitch: dict[str, _Mover] = {}
    for cid, club in clubs.items():
        for slot, p in enumerate(club.starting_eleven()):
            draw = Stream(seed, match_index + 1, StreamId.KINEMATICS, possession=PLAYER_STREAM_BASE + club_no[cid] * 100 + p.shirt)
            offset = (draw.between(-PERSONAL_OFFSET_MM, PERSONAL_OFFSET_MM), draw.between(-PERSONAL_OFFSET_MM, PERSONAL_OFFSET_MM))
            on_pitch[p.player_id] = _Mover(p.player_id, cid, slot, player_vmax(p.pace), offset)

    periods_out: list[dict[str, Any]] = []
    for period in (1, 2):
        evs = [e for e in events if e["period"] == period]
        if not evs:
            continue
        start_s = 0 if period == 1 else HALF_SECONDS
        end_s = max(_second(e) for e in evs)
        keys: list[tuple[int, int, int]] = []
        for e in evs:
            if e["type"] == "sub":
                continue
            x, y, ex, ey = to_fixed_frame(e, home_id)
            t = _second(e) * 1000
            keys.append((t, x * 100, y * 100))
            f = flight_of.get(e["event_id"])
            if f is not None:
                keys.append((t + f["flight_ms"], ex * 100, ey * 100))
        keys.sort(key=lambda k: k[0])
        if keys[0][0] > start_s * 1000:
            keys.insert(0, (start_s * 1000, keys[0][1], keys[0][2]))
        ball = _ball_track(keys, start_s, end_s)
        owner: list[str] = []
        current = evs[0]["team"]
        k = 0
        for s in range(start_s, end_s + 1):
            while k < len(evs) and _second(evs[k]) <= s:
                if evs[k]["type"] in POSSESSION_TYPES:
                    current = evs[k]["team"]
                k += 1
            owner.append(current)
        changes: dict[int, list[Mapping[str, Any]]] = {}
        for e in evs:
            if e["type"] == "sub" or (e["type"] == "card" and e["outcome"] in ("red", "second_yellow")):
                changes.setdefault(_second(e), []).append(e)

        finished: list[_Mover] = []
        for pid in sorted(on_pitch):
            m = on_pitch[pid]
            is_home = m.team == home_id
            ox, oy = _shape_mm(m, is_home, (X_MM // 2, Y_MM // 2), m.team == owner[0])
            fx, fy = _own(min(ox, X_MM // 2 - 1000), oy, is_home)  # kick-off: everyone in his own half
            m.anchors, m.next_anchor = _period_anchors(anchors, m.player_id, period), 0
            m.x, m.y = _start_near((fx * 10, fy * 10), m.anchors, start_s)
            m.vx = m.vy = 0
            m.start_s = start_s
            m.xy = [_rdm(m.x), _rdm(m.y)]
        for s in range(start_s + 1, end_s + 1):
            i = s - start_s
            for e in changes.get(s, ()):
                out_id = e["player"]
                if out_id not in on_pitch:
                    continue
                gone = on_pitch.pop(out_id)
                if e["type"] == "sub" and e["related_player"]:
                    newcomer = _Mover(e["related_player"], gone.team, gone.slot, player_vmax(pace[e["related_player"]]), gone.offset)
                    newcomer.anchors = _period_anchors(anchors, newcomer.player_id, period)
                    newcomer.x, newcomer.y = _start_near((gone.x, gone.y), newcomer.anchors, s)
                    newcomer.start_s = s
                    newcomer.xy = [_rdm(newcomer.x), _rdm(newcomer.y)]
                    on_pitch[newcomer.player_id] = newcomer
                else:
                    _step(gone, s, ball[i], owner[i], home_id)
                finished.append(gone)
            for pid in sorted(on_pitch):
                m = on_pitch[pid]
                if m.start_s != s:
                    _step(m, s, ball[i], owner[i], home_id)
        players = finished + list(on_pitch.values())
        periods_out.append({
            "period": period,
            "start_s": start_s,
            "end_s": end_s,
            "ball": [v for x, y in ball for v in ((x + 50) // 100, (y + 50) // 100)],
            "players": [
                {"player_id": m.player_id, "team": m.team, "start_s": m.start_s, "xy": m.xy}
                for m in sorted(players, key=lambda m: (m.player_id, m.start_s))
            ],
        })
    return {
        "schema": SCHEMA,
        "model": KINEMATICS_VERSION,
        "generator": GENERATOR_VERSION,
        "seed": seed,
        "match_index": match_index,
        "match_id": events[0]["match_id"] if events else "",
        "home": home_id,
        "away": away_id,
        "rate_hz": RATE_HZ,
        "frame": FIXED_FRAME,
        "periods": periods_out,
        "ball_flights": flights,
    }


def _period_anchors(
    anchors: Mapping[str, Mapping[tuple[int, int], tuple[int, int]]], player_id: str, period: int
) -> list[tuple[int, int, int]]:
    return sorted((s, x, y) for (p, s), (x, y) in anchors.get(player_id, {}).items() if p == period)


def _leave_velocity(m: _Mover, t: int, at: tuple[int, int]) -> tuple[int, int]:
    """The velocity (mm/s) to carry through an anchor: toward the following anchor if it is close, else rest."""
    k = m.next_anchor + 1
    if k < len(m.anchors) and m.anchors[k][0] - t <= LEAD_HORIZON_S:
        t2, x2, y2 = m.anchors[k]
        span = SUBSTEPS * max(1, t2 - t)
        return _cap(_tdiv(x2 - at[0], span), _tdiv(y2 - at[1], span), m.vmax)
    return 0, 0


def _start_near(pos: tuple[int, int], anchors: Sequence[tuple[int, int, int]], start_s: int) -> tuple[int, int]:
    """Where a track starts: the given spot, moved toward the first anchor when it comes soon."""
    if not anchors or anchors[0][0] - start_s > START_HORIZON_S:
        return pos
    t, ax, ay = anchors[0]
    dx, dy = ax - pos[0], ay - pos[1]
    dist = isqrt(dx * dx + dy * dy)
    reach = max(0, t - start_s - 1) * JOG_MM_S * SUBSTEPS
    if dist <= reach:
        return pos
    return ax - _tdiv(dx * reach, dist), ay - _tdiv(dy * reach, dist)


def _arrive(dx: int, dy: int, steps: int, lead: tuple[int, int], amax: int, vmax: int) -> tuple[int, int]:
    """Velocity that reaches (dx, dy) in `steps` substeps and leaves with velocity `lead`.

    In the frame moving at `lead`, the remaining displacement D is covered at a cruise speed vc
    that still leaves room to brake to rest by the deadline in whole substeps:
    vc * (n - 1/2) - vc**2 / (2 a) = |D|, so vc = (a (2n - 1) - sqrt(a**2 (2n - 1)**2 - 8 a |D|)) / 2.
    Recomputed every substep, this is a smooth arrive-on-time profile; when no vc exists the player
    runs as fast as he can still stop, and the anchor may be missed.
    """
    rx, ry = dx - lead[0] * steps, dy - lead[1] * steps
    dist = isqrt(rx * rx + ry * ry)
    if dist == 0:
        return _cap(lead[0], lead[1], vmax)
    span = amax * (2 * steps - 1)
    disc = span * span - 8 * amax * dist
    vc = (span - isqrt(disc)) // 2 if disc >= 0 else vmax
    # Never faster than the speed from which he can still stop within the distance left
    # (v / 2 + v**2 / (2 a) = |D| in whole substeps): late is a smaller miss than an overshoot.
    v = min(vc, (isqrt(amax * amax + 8 * amax * dist) - amax) // 2)
    return _cap(lead[0] + _tdiv(rx * v, dist), lead[1] + _tdiv(ry * v, dist), vmax)


def _step(m: _Mover, s: int, ball: tuple[int, int], owner: str, home_id: str) -> None:
    """Advance one player through second s in SUBSTEPS steps, toward his next anchor or his shape spot.

    Each substep the velocity changes by at most PLAYER_AMAX_MM_S2 / SUBSTEPS and its magnitude stays
    at most the player's vmax, so the published 1 Hz samples obey both caps.
    """
    while m.next_anchor < len(m.anchors) and m.anchors[m.next_anchor][0] < s:
        m.next_anchor += 1
    anchor = m.anchors[m.next_anchor] if m.next_anchor < len(m.anchors) else None
    shape = _shape_target(m, m.team == home_id, ball, m.team == owner)
    lead = _leave_velocity(m, anchor[0], (anchor[1], anchor[2])) if anchor is not None else (0, 0)
    amax = PLAYER_AMAX_MM_S2 // SUBSTEPS
    for j in range(SUBSTEPS):
        desired: tuple[int, int] | None = None
        if anchor is not None:
            steps = (anchor[0] - s) * SUBSTEPS + (SUBSTEPS - j)
            dx, dy = anchor[1] - m.x, anchor[2] - m.y
            dist = isqrt(dx * dx + dy * dy)
            if steps <= APPROACH_HORIZON_S * SUBSTEPS or dist >= (steps - APPROACH_SLACK_S * SUBSTEPS) * JOG_MM_S:
                if steps == 1:
                    desired = _cap(dx, dy, m.vmax)
                else:
                    desired = _arrive(dx, dy, steps, lead, amax, m.vmax)
        if desired is None:
            dx, dy = shape[0] - m.x, shape[1] - m.y
            far = isqrt(dx * dx + dy * dy) > FAR_FROM_SHAPE_MM * 10
            desired = _cap(_tdiv(dx, RELAX_DIVISOR * SUBSTEPS), _tdiv(dy, RELAX_DIVISOR * SUBSTEPS), m.vmax if far else JOG_MM_S)
        ax, ay = _cap(desired[0] - m.vx, desired[1] - m.vy, amax)
        vx, vy = m.vx + ax, m.vy + ay
        nx = min(BOX_HI_X, max(BOX_LO, m.x + vx))
        ny = min(BOX_HI_Y, max(BOX_LO, m.y + vy))
        m.vx, m.vy = nx - m.x, ny - m.y
        m.x, m.y = nx, ny
    m.xy.extend((_rdm(m.x), _rdm(m.y)))


# ---------------------------------------------------------------- checks (metric M9)


def check(record: Mapping[str, Any]) -> list[str]:
    """Cap violations in the published record; empty means none (M9 target)."""
    problems: list[str] = []
    x_lo, x_hi, y_lo, y_hi = CHECK_BOX_DM
    for per in record["periods"]:
        for track in per["players"]:
            xy = track["xy"]
            pts = [(xy[i], xy[i + 1]) for i in range(0, len(xy), 2)]
            where = f"period {per['period']} {track['player_id']}"
            for k, (x, y) in enumerate(pts):
                if not (x_lo <= x <= x_hi and y_lo <= y <= y_hi):
                    problems.append(f"{where} second {track['start_s'] + k}: position ({x}, {y}) dm outside the box")
            for k in range(1, len(pts)):
                dx, dy = pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]
                if isqrt(dx * dx + dy * dy) > CHECK_VMAX_DM_S:
                    problems.append(f"{where} second {track['start_s'] + k}: speed {isqrt(dx * dx + dy * dy)} dm/s over the cap")
                if k >= 2:
                    px, py = pts[k - 1][0] - pts[k - 2][0], pts[k - 1][1] - pts[k - 2][1]
                    ax, ay = dx - px, dy - py
                    if isqrt(ax * ax + ay * ay) > CHECK_AMAX_DM_S2:
                        problems.append(f"{where} second {track['start_s'] + k}: acceleration {isqrt(ax * ax + ay * ay)} dm/s2 over the cap")
    for f in record["ball_flights"]:
        lo, hi = PASS_BOUNDS_KMH_X10 if f["type"] == "pass" else SHOT_BOUNDS_KMH_X10
        if f["flight_ms"] and not lo <= f["speed_kmh_x10"] <= hi:
            problems.append(f"{f['type']} {f['event_id']}: ball speed {f['speed_kmh_x10']} outside {lo}..{hi}")
        if f["flight_ms"] > f["gap_ms"]:
            problems.append(f"{f['type']} {f['event_id']}: flight {f['flight_ms']} ms longer than the gap {f['gap_ms']} ms")
    return problems


IMPOSSIBLE_MM_S = 10**9  # the need recorded for two different places in the same second


def _actor_events(events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [e for e in events if e["type"] not in ("sub", "possession_change") and e["player"]]


def _need(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    """Average speed (mm/s) to get from (second, x_dm, y_dm) a to b."""
    dist_mm = isqrt((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) * 100
    if b[0] == a[0]:
        return 0 if dist_mm == 0 else IMPOSSIBLE_MM_S
    return dist_mm // (b[0] - a[0])


def _sample(tracks: Mapping[tuple[int, str], list[tuple[int, list[int]]]], period: int, player: str, s: int) -> tuple[int, int] | None:
    at = None
    for start, xy in tracks.get((period, player), []):
        k = s - start
        if 0 <= k < len(xy) // 2:
            at = (xy[2 * k], xy[2 * k + 1])
    return at


def _tracks(record: Mapping[str, Any]) -> dict[tuple[int, str], list[tuple[int, list[int]]]]:
    out: dict[tuple[int, str], list[tuple[int, list[int]]]] = {}
    for per in record["periods"]:
        for t in per["players"]:
            out.setdefault((per["period"], t["player_id"]), []).append((t["start_s"], t["xy"]))
    return out


def anchor_misses(record: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every actor event whose actor is not at the event location at the event second.

    Each miss carries its distance (`miss_dm`, None when the player has no sample then) and the
    average speed he needed from his nearest action at an earlier second (`need_mm_s`) and to his
    nearest action at a later second (`need_next_mm_s`) in the same period, however far apart in
    time, None where there is none.
    """
    tracks = _tracks(record)
    acts = _actor_events(events)
    points: dict[tuple[int, str], list[tuple[int, int, int]]] = {}
    for e in acts:
        x, y, _, _ = to_fixed_frame(e, record["home"])
        points.setdefault((e["period"], e["player"]), []).append((_second(e), x, y))
    seen: dict[tuple[int, str], int] = {}
    misses: list[dict[str, Any]] = []
    for e in acts:
        key = (e["period"], e["player"])
        i = seen.get(key, 0)
        seen[key] = i + 1
        row = points[key]
        here = row[i]
        # Neighbours are his actions at an EARLIER and a LATER second: a corner kick and the corner
        # pass share one second and one spot, and the action that matters is the one after both.
        earlier = [r for r in row[:i] if r[0] < here[0]]
        later = [r for r in row[i + 1:] if r[0] > here[0]]
        need_prev = _need(earlier[-1], here) if earlier else None
        need_next = _need(here, later[0]) if later else None
        if any(r[0] == here[0] and (r[1], r[2]) != (here[1], here[2]) for r in row):
            need_next = IMPOSSIBLE_MM_S
        at = _sample(tracks, e["period"], e["player"], here[0])
        d = None if at is None else isqrt((at[0] - here[1]) ** 2 + (at[1] - here[2]) ** 2)
        if d is None or d > 0:
            misses.append({
                "event_id": e["event_id"], "player": e["player"], "miss_dm": d,
                "need_mm_s": need_prev, "need_next_mm_s": need_next,
            })
    return misses


def miss_summary(misses: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Actor misses: under 1 m; 1 m or more next to an action beyond the speed cap (from the previous
    action or to the next one); and the rest, the model's own limits."""
    def beyond(m: Mapping[str, Any]) -> bool:
        needs = [v for v in (m["need_mm_s"], m.get("need_next_mm_s")) if v is not None]
        return any(v > PLAYER_VMAX_MM_S for v in needs)

    under = sum(1 for m in misses if m["miss_dm"] is not None and m["miss_dm"] < 10)
    beyond_cap = sum(1 for m in misses if not (m["miss_dm"] is not None and m["miss_dm"] < 10) and beyond(m))
    return {
        "misses": len(misses),
        "under_1m": under,
        "beyond_cap": beyond_cap,
        "model_limit": len(misses) - under - beyond_cap,
        "max_miss_dm": max((m["miss_dm"] or 0 for m in misses), default=0),
    }


def receiver_arrivals(record: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every completed pass: its receiver, the second the ball arrives and the receiver's distance from
    the pass end then (`miss_dm`, None when he has no sample at that second)."""
    flight = {f["event_id"]: f["flight_ms"] for f in record["ball_flights"]}
    tracks = _tracks(record)
    out: list[dict[str, Any]] = []
    for e in events:
        if e["type"] != "pass" or e["outcome"] != "complete" or not e["related_player"]:
            continue
        _, _, fx, fy = to_fixed_frame(e, record["home"])
        arrive = _second(e) + max(1, -(-flight[e["event_id"]] // 1000))
        at = _sample(tracks, e["period"], e["related_player"], arrive)
        d = None if at is None else isqrt((at[0] - fx) ** 2 + (at[1] - fy) ** 2)
        out.append({"event_id": e["event_id"], "player": e["related_player"], "arrive_s": arrive, "miss_dm": d})
    return out


def receiver_summary(arrivals: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    misses = [a for a in arrivals if a["miss_dm"] is None or a["miss_dm"] > 0]
    return {
        "arrivals": len(arrivals),
        "misses": len(misses),
        "no_sample": sum(1 for a in misses if a["miss_dm"] is None),
        "max_miss_dm": max((a["miss_dm"] or 0 for a in misses), default=0),
    }


def record_sha256(record: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_bytes(record))


def for_seed(seed: int, match_index: int = 0) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(kinematics record, event dicts) for one generated match."""
    league = build_world(seed)
    result = simulate_match(league, match_index)
    events = [e.to_dict() for e in result.events]
    return simulate_tracking(league, events, result.meta["home"], result.meta["away"], seed, match_index), events


def mutate(record: dict[str, Any]) -> dict[str, Any]:
    """Plant one teleport: a player jumps 15 m in one second halfway through his period-1 track."""
    track = record["periods"][0]["players"][0]
    k = len(track["xy"]) // 4 * 2
    track["xy"][k] += 150 if track["xy"][k] < PITCH_X_DM // 2 else -150
    return record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.synth.kinematics", description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    parser.add_argument("--match-index", type=int, default=0)
    parser.add_argument("--print-hash", action="store_true")
    parser.add_argument("--hash-out", help="write the hash lines here (binary, LF)")
    parser.add_argument("--mutate", action="store_true", help="plant one teleport before checking; must exit 1")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    lines = [f"kinematics {KINEMATICS_VERSION} generator {GENERATOR_VERSION} package {__version__} rate_hz {RATE_HZ}"]
    violations = 0
    for seed in parse_seeds(args.seeds):
        record, events = for_seed(seed, args.match_index)
        if args.mutate:
            record = mutate(record)
        problems = check(record)
        summary = miss_summary(anchor_misses(record, events))
        arrivals = receiver_summary(receiver_arrivals(record, events))
        violations += len(problems)
        tracks = sum(len(p["players"]) for p in record["periods"])
        lines.append(
            f"seed {seed} match_index {args.match_index} tracks {tracks} flights {len(record['ball_flights'])}"
            f" violations {len(problems)} anchor_misses {summary['misses']} (under_1m {summary['under_1m']}"
            f" beyond_cap {summary['beyond_cap']} model_limit {summary['model_limit']}) max_miss_dm {summary['max_miss_dm']}"
            f" receiver_misses {arrivals['misses']} of {arrivals['arrivals']} (no_sample {arrivals['no_sample']})"
            f" max_receiver_miss_dm {arrivals['max_miss_dm']}"
            f" sha256 {record_sha256(record)}"
        )
        for p in problems[:5]:
            sys.stderr.write(f"seed {seed}: {p}\n")
    lines.append(f"M9 cap violations {violations} (target 0)")
    text = ("\n".join(lines) + "\n").encode("ascii")
    if args.print_hash or not args.hash_out:
        sys.stdout.buffer.write(text)
        sys.stdout.buffer.flush()
    if args.hash_out:
        parent = os.path.dirname(args.hash_out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.hash_out, "wb") as fh:
            fh.write(text)
    sys.stderr.write(f"wall time {int((time.perf_counter() - started) * 1000)} ms total\n")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())

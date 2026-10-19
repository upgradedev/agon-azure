"""Event schema, canonical JSON and content-addressed event ids.

Coordinate convention: x_dm and y_dm are integer decimetres on a 105 m by 68 m pitch, so
0 <= x_dm <= 1050 and 0 <= y_dm <= 680. Stored coordinates are in the ACTING team's attacking
frame (`COORDINATE_FRAME`): the team named in `event.team` always attacks towards x_dm = 1050,
in both periods. Events by the defending side (pressure, tackle, foul, card) are therefore the
180 degree rotation of the attacking team's events in the same chain, and every away-team event
is rotated relative to every home-team event.

Anything that draws or compares events across teams must first call `to_fixed_frame`, which
returns coordinates in `FIXED_FRAME`: the home club attacks towards x_dm = 1050 in BOTH periods
(the generator never swaps ends at half time, so a consumer must not flip again by period).
DATASET_CARD.md section 2 documents the same convention with a worked example.

Why integers: decimetre quantisation keeps every value an integer, so canonical JSON is the same
bytes on every platform and the sha256 over it is a stable identity.

Why the event id is content-addressed: the id is the first 16 hex characters of sha256 over the
canonical event without its id. Anyone can recompute it, and any edit to an event changes its id.
`seq` and `match_id` are part of the content, so two otherwise identical events never share an id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from typing import Any

PITCH_X_DM = 1050
PITCH_Y_DM = 680
EVENT_ID_HEX = 16

# Frame of the stored x_dm/y_dm values. Recorded in every match meta so a reader never guesses.
COORDINATE_FRAME = "acting_team_attacks_high_x"
# Frame returned by `to_fixed_frame`: one pitch picture for both teams and both periods.
FIXED_FRAME = "home_attacks_high_x_both_periods"

EVENT_TYPES: tuple[str, ...] = (
    "pass",
    "carry",
    "shot",
    "tackle",
    "pressure",
    "foul",
    "set_piece",
    "sub",
    "card",
    "possession_change",
)

OUTCOMES: dict[str, frozenset[str]] = {
    "pass": frozenset({"complete", "incomplete", "out"}),
    "carry": frozenset({"complete"}),
    "shot": frozenset({"goal", "saved", "off_target", "blocked"}),
    "tackle": frozenset({"won", "lost"}),
    "pressure": frozenset({"applied"}),
    "foul": frozenset({"committed"}),
    "set_piece": frozenset({"taken"}),
    "sub": frozenset({"on"}),
    "card": frozenset({"yellow", "second_yellow", "red"}),
    "possession_change": frozenset({"kick_off", "interception", "tackle", "out_of_play", "shot_end", "goal", "foul"}),
}

QUALIFIERS: dict[str, frozenset[str]] = {
    "set_piece": frozenset({"kick_off", "free_kick", "corner", "throw_in", "goal_kick", "penalty"}),
    "shot": frozenset({"open_play", "header", "penalty", "free_kick"}),
}

# A planned substitution carries no qualifier; an injury replacement carries "injury".
SUB_QUALIFIERS: tuple[str | None, ...] = (None, "injury")

# Types that must carry an end location.
TYPES_WITH_END: frozenset[str] = frozenset({"pass", "carry", "shot"})
# Types where `player` may be None (team-level events).
TYPES_WITHOUT_PLAYER: frozenset[str] = frozenset({"possession_change"})


class CanonicalError(TypeError):
    """Raised when a value cannot be part of canonical JSON (floats, NaN, unknown types)."""


def _check_canonical(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise CanonicalError(f"float at {path}: canonical JSON is integers only")
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check_canonical(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise CanonicalError(f"non-string key at {path}")
            _check_canonical(v, f"{path}.{k}")
        return
    raise CanonicalError(f"unsupported type {type(value).__name__} at {path}")


def canonical(value: Any) -> str:
    """Canonical JSON: sorted keys, no spaces, ASCII only, no floats, no NaN."""
    _check_canonical(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_bytes(value: Any) -> bytes:
    return canonical(value).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Event:
    """One match event. Build with `make_event`, which fills `event_id`."""

    event_id: str
    match_id: str
    seq: int
    period: int
    minute: int
    second: int
    type: str
    team: str
    player: str | None
    x_dm: int
    y_dm: int
    end_x_dm: int | None
    end_y_dm: int | None
    possession_chain_id: int
    under_pressure: bool
    outcome: str | None
    qualifier: str | None
    related_player: str | None

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


FixedPoint = tuple[int, int, int | None, int | None]


def rotate_point(x_dm: int, y_dm: int) -> tuple[int, int]:
    """Rotate a point 180 degrees about the centre spot; this is how the engine swaps frames."""
    return PITCH_X_DM - x_dm, PITCH_Y_DM - y_dm


def to_fixed_frame(event: Event | dict[str, Any], home_id: str) -> FixedPoint:
    """Return (x, y, end_x, end_y) in `FIXED_FRAME`, where the home club attacks towards x = 1050.

    Why this exists: stored events use the acting team's frame (`COORDINATE_FRAME`), so drawing
    raw x_dm for both teams puts every away event and every defensive event on the wrong half
    with no error. Home events are returned unchanged; every other team's events are rotated 180
    degrees (x -> 1050 - x and y -> 680 - y, not an x-only flip). Missing end points stay None.
    Accepts an `Event` or a plain dict, because downstream readers load NDJSON dicts.
    """
    d = event.to_dict() if isinstance(event, Event) else event
    x, y = d["x_dm"], d["y_dm"]
    ex, ey = d.get("end_x_dm"), d.get("end_y_dm")
    if d["team"] == home_id:
        return x, y, ex, ey
    rx, ry = rotate_point(x, y)
    if ex is None or ey is None:
        return rx, ry, None, None
    rex, rey = rotate_point(ex, ey)
    return rx, ry, rex, rey


def compute_event_id(event_without_id: dict[str, Any]) -> str:
    body = {k: v for k, v in event_without_id.items() if k != "event_id"}
    return sha256_hex(canonical_bytes(body))[:EVENT_ID_HEX]


def make_event(**kwargs: Any) -> Event:
    """Create an Event and fill its content-addressed id."""
    if "event_id" in kwargs:
        raise ValueError("event_id is computed, not supplied")
    kwargs.setdefault("end_x_dm", None)
    kwargs.setdefault("end_y_dm", None)
    kwargs.setdefault("under_pressure", False)
    kwargs.setdefault("outcome", None)
    kwargs.setdefault("qualifier", None)
    kwargs.setdefault("related_player", None)
    kwargs["event_id"] = compute_event_id(kwargs)
    return Event(**kwargs)


def validate_event(event: dict[str, Any]) -> list[str]:
    """Return a list of problems with one event dict; empty means valid."""
    problems: list[str] = []
    expected = {f.name for f in fields(Event)}
    missing = expected - set(event)
    extra = set(event) - expected
    if missing:
        problems.append(f"missing fields {sorted(missing)}")
    if extra:
        problems.append(f"unexpected fields {sorted(extra)}")
    if problems:
        return problems
    etype = event["type"]
    if etype not in EVENT_TYPES:
        problems.append(f"unknown type {etype!r}")
        return problems
    for key in ("seq", "period", "minute", "second", "x_dm", "y_dm", "possession_chain_id"):
        if not isinstance(event[key], int) or isinstance(event[key], bool):
            problems.append(f"{key} must be an int")
    if problems:
        return problems
    if event["period"] not in (1, 2):
        problems.append("period must be 1 or 2")
    if not 0 <= event["second"] <= 59:
        problems.append("second out of range")
    if event["minute"] < 0 or event["minute"] > 130:
        problems.append("minute out of range")
    if not 0 <= event["x_dm"] <= PITCH_X_DM or not 0 <= event["y_dm"] <= PITCH_Y_DM:
        problems.append("location off the pitch")
    if etype in TYPES_WITH_END:
        ex, ey = event["end_x_dm"], event["end_y_dm"]
        if not isinstance(ex, int) or not isinstance(ey, int):
            problems.append("end location required")
        elif not 0 <= ex <= PITCH_X_DM or not 0 <= ey <= PITCH_Y_DM:
            problems.append("end location off the pitch")
    if not isinstance(event["under_pressure"], bool):
        problems.append("under_pressure must be a bool")
    if event["player"] is None and etype not in TYPES_WITHOUT_PLAYER:
        problems.append("player required")
    allowed = OUTCOMES.get(etype)
    if allowed is not None and event["outcome"] not in allowed:
        problems.append(f"outcome {event['outcome']!r} not allowed for {etype}")
    allowed_q = QUALIFIERS.get(etype)
    if allowed_q is not None and event["qualifier"] not in allowed_q:
        problems.append(f"qualifier {event['qualifier']!r} not allowed for {etype}")
    if etype == "sub" and not event["related_player"]:
        problems.append("sub needs related_player (the player coming on)")
    if etype == "sub" and event["qualifier"] not in SUB_QUALIFIERS:
        problems.append(f"qualifier {event['qualifier']!r} not allowed for sub")
    if not isinstance(event["event_id"], str) or len(event["event_id"]) != EVENT_ID_HEX:
        problems.append("event_id must be 16 hex characters")
    elif compute_event_id(event) != event["event_id"]:
        problems.append("event_id does not match content")
    return problems

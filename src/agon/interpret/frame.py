"""The match as interpret reads it: ordered event dicts, the two club ids, time keys and geometry.

Coordinates: stored events are in the ACTING team's frame (agon.synth.schema.COORDINATE_FRAME):
the team named in `event["team"]` always attacks towards x_dm = 1050. That is exactly the frame
xG, xT and pass features need, so interpret reads raw x_dm/y_dm for the acting team and never
rotates. Where a defending action is compared with an attacking one (PPDA), the rule is written
next to the comparison.

Time: the engine stores `minute = clock // 60` (0-based) and the clock does NOT reset at half
time, but first-half added time and the start of the second half share engine minutes 45 to 49.
Every time key here is therefore built from (period, minute), never minute alone.

What this module may import: `agon.synth.schema` (the event contract) and the minute convention
from `agon.governance.recompute`. It never imports the engine, its constants or interventions:
interpret must learn from events, or the M6 comparison would be circular.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isqrt
from typing import Any, Iterable, Mapping

from agon.governance.recompute import display_minute
from agon.synth.schema import PITCH_X_DM, PITCH_Y_DM, Event

GRID_X = 16
GRID_Y = 12
GOAL_X_DM = PITCH_X_DM
GOAL_Y_DM = PITCH_Y_DM // 2
BOX_DEPTH_DM = 165  # penalty area depth, 16.5 m
BOX_HALF_WIDTH_DM = 202  # penalty area half width, 20.16 m rounded
FINAL_THIRD_X_DM = 700
OWN_THIRD_X_DM = 350
HALF_SECONDS = 2700
BIN15_SECONDS = 900
ON_BALL_TYPES = ("pass", "carry", "shot")


def zone_of(x_dm: int, y_dm: int) -> tuple[int, int]:
    """16 by 12 zone of a point. Same formula as the generator's grid; a test pins the equality."""
    zx = min(max(x_dm * GRID_X // (PITCH_X_DM + 1), 0), GRID_X - 1)
    zy = min(max(y_dm * GRID_Y // (PITCH_Y_DM + 1), 0), GRID_Y - 1)
    return zx, zy


def distance_dm(x0: int, y0: int, x1: int, y1: int) -> int:
    """Euclidean distance in whole decimetres, floor, by integer square root."""
    return isqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)


def dm_to_m(dm: int) -> int:
    """Decimetres to whole metres, rounded half up."""
    return (dm + 5) // 10


def window5(event: Mapping[str, Any]) -> tuple[int, int]:
    """5-minute window key: (period, minute // 5). First-half added time stays in period 1."""
    return event["period"], event["minute"] // 5


def bin15(event: Mapping[str, Any]) -> int:
    """15-minute bin 0..5: 0-15, 15-30, 30-45 plus added time, 45-60, 60-75, 75-90 plus added time."""
    if event["period"] == 1:
        return min(event["minute"] // 15, 2)
    return 3 + min(max(event["minute"] - 45, 0) // 15, 2)


BIN15_LABELS: tuple[str, ...] = ("0-15", "15-30", "30-45+", "45-60", "60-75", "75-90+")


def clock_seconds(event: Mapping[str, Any]) -> int:
    return event["minute"] * 60 + event["second"]


def window_label(key: tuple[int, int]) -> str:
    period, w = key
    start = w * 5
    return f"p{period} {start}-{start + 5}"


@dataclass(frozen=True)
class MatchView:
    """Events as dicts in sequence order, plus the two club ids."""

    match_id: str
    home_id: str
    away_id: str
    events: tuple[dict[str, Any], ...]
    _index: dict[str, int] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def build(
        cls,
        events: Iterable[Event | Mapping[str, Any]],
        home_id: str,
        away_id: str,
        match_id: str | None = None,
    ) -> "MatchView":
        rows = [e.to_dict() if isinstance(e, Event) else dict(e) for e in events]
        rows.sort(key=lambda e: e["seq"])
        mid = match_id if match_id is not None else (rows[0]["match_id"] if rows else "")
        index = {e["event_id"]: i for i, e in enumerate(rows)}
        return cls(match_id=mid, home_id=home_id, away_id=away_id, events=tuple(rows), _index=index)

    @classmethod
    def from_bundle(cls, meta: Mapping[str, Any], events: Iterable[Event | Mapping[str, Any]]) -> "MatchView":
        return cls.build(events, meta["home"], meta["away"], meta["match_id"])

    def opponent(self, team: str) -> str:
        if team == self.home_id:
            return self.away_id
        if team == self.away_id:
            return self.home_id
        raise KeyError(f"{team!r} is not in this match")

    def position(self, event_id: str) -> int:
        """Index of an event in sequence order; KeyError when the id is unknown."""
        return self._index[event_id]

    def get(self, event_id: str) -> dict[str, Any]:
        return self.events[self._index[event_id]]

    def is_home(self, team: str) -> bool:
        return team == self.home_id


def minute_label(event: Mapping[str, Any]) -> str:
    return display_minute(event["period"], event["minute"])

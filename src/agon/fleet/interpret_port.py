"""The narrow interface `interpret` needs, and a minimal local stand-in behind it.

TODO(B3): switch `default_interpret()` to the `agon.interpret` package once lane B3 merges it
(xG, xT, PPDA, control-vs-chaos index, pressure regimes and the Poisson win-probability model,
spec section 4 row 2). Only an adapter that returns the shape documented on `InterpretPort` is
needed; no executor changes.

The stand-in is deliberately small and says what it is in its own output (`source` and `note`):
its `swing` is a score-state measure (did the result state change), not a win probability, and
nothing downstream may call it one.
"""

from __future__ import annotations

from typing import Any, Protocol

from agon.fleet.sources import MatchRecord
from agon.governance.recompute import display_minute, events_of_kind, score_after

STANDIN_SOURCE = "fleet-local-standin/1"
STANDIN_NOTE = (
    "Stand-in until agon.interpret merges. swing is a score-state measure "
    "(2 when the result state changes, 1 otherwise), not a win-probability model."
)
WINDOW_MINUTES = 15


class InterpretPort(Protocol):
    """Pure function of the event log. Returns JSON-native data with at least these keys:

    * ``source``: which implementation produced it;
    * ``final_score``: ``{"home": int, "away": int}``;
    * ``candidates``: candidate turning points, each ``{event_id, minute, kind, team, player,
      score_before, score_after, swing}``, ordered most important first.
    """

    def interpret(self, record: MatchRecord) -> dict[str, Any]: ...


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


class LocalInterpret:
    """Goals and sendings-off ranked by how much they moved the result state."""

    def interpret(self, record: MatchRecord) -> dict[str, Any]:
        facts = record.facts()
        candidates: list[dict[str, Any]] = []
        for e in events_of_kind(facts.events, "goal"):
            after = score_after(facts, e.seq)
            before = dict(after)
            before["home" if e.team == facts.home_id else "away"] -= 1
            changed = _sign(after["home"] - after["away"]) != _sign(before["home"] - before["away"])
            candidates.append(self._candidate(e, "goal", before, after, 2 if changed else 1))
        for e in events_of_kind(facts.events, "red_card"):
            score = score_after(facts, e.seq)
            candidates.append(self._candidate(e, "red_card", score, score, 1))
        order = {e.event_id: e.seq for e in facts.events}
        candidates.sort(key=lambda c: (-c["swing"], order[c["event_id"]]))
        return {
            "source": STANDIN_SOURCE,
            "note": STANDIN_NOTE,
            "final_score": score_after(facts, None),
            "windows": self._windows(record),
            "candidates": candidates,
        }

    @staticmethod
    def _candidate(e: Any, kind: str, before: dict[str, int], after: dict[str, int], swing: int) -> dict[str, Any]:
        return {
            "event_id": e.event_id,
            "minute": display_minute(e.period, e.minute),
            "kind": kind,
            "team": e.team,
            "player": e.player,
            "score_before": before,
            "score_after": after,
            "swing": swing,
        }

    @staticmethod
    def _windows(record: MatchRecord) -> list[dict[str, Any]]:
        """Per club, per 15-minute block of each half: passes, completed passes, shots, pressures."""
        home, away = record.meta["home"], record.meta["away"]
        blocks: dict[tuple[int, int], dict[str, dict[str, int]]] = {}
        for e in record.events:
            start = 45 * (e.period - 1)
            block = min((e.minute - start) // WINDOW_MINUTES, 2) if e.minute >= start else 0
            row = blocks.setdefault(
                (e.period, block),
                {side: {"passes": 0, "passes_completed": 0, "shots": 0, "pressures": 0} for side in (home, away)},
            )
            if e.team not in row:
                continue
            counts = row[e.team]
            if e.type == "pass":
                counts["passes"] += 1
                counts["passes_completed"] += 1 if e.outcome == "complete" else 0
            elif e.type == "shot":
                counts["shots"] += 1
            elif e.type == "pressure":
                counts["pressures"] += 1
        out = []
        for (period, block), row in sorted(blocks.items()):
            first = 45 * (period - 1) + block * WINDOW_MINUTES + 1
            out.append({"period": period, "from_minute": first, "to_minute": first + WINDOW_MINUTES - 1, "clubs": row})
        return out


def default_interpret() -> InterpretPort:
    """TODO(B3): return the agon.interpret adapter once it is on main."""
    return LocalInterpret()

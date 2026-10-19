"""Shared kinematics builders for the tests: each seed's synthetic tracking is computed once per session."""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from agon.synth import kinematics as kin


@lru_cache(maxsize=None)
def _for_seed(seed: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return kin.for_seed(seed)


def record(seed: int = 1) -> dict[str, Any]:
    """A deep copy, so a test may plant errors without touching the cache."""
    return copy.deepcopy(_for_seed(seed)[0])


def events(seed: int = 1) -> list[dict[str, Any]]:
    return _for_seed(seed)[1]


def track(rec: dict[str, Any], period: int, player_id: str) -> dict[str, Any]:
    for per in rec["periods"]:
        if per["period"] == period:
            for t in per["players"]:
                if t["player_id"] == player_id:
                    return t
    raise KeyError((period, player_id))

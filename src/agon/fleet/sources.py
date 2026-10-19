"""Where a run's event log comes from, and the check that it is the same log every time.

`ingest` loads the log once and records its sha256 in the Draft. Every later step that needs facts
calls `load_facts`, which reloads the log by reference and refuses it unless it hashes to the
recorded value. That is what lets a fresh process resume a run without the log ever being written
into a checkpoint, and it fails closed if the source changed underneath the run.

`SynthMatchSource` regenerates a match from the seeded generator (synthetic data only). The
production source reads the Cosmos `matchstate` container; it must return the same `MatchRecord`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agon.governance.recompute import MatchFacts, Roster
from agon.governance.seal import event_log_sha256, seed_sha256
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.schema import Event
from agon.synth.world import build_world


class MatchSourceError(ValueError):
    """The match reference cannot be resolved, or the log differs from the one the run recorded."""


@dataclass(frozen=True)
class MatchRecord:
    match_id: str
    meta: Mapping[str, Any]
    events: tuple[Event, ...]
    roster: Roster
    seed_sha256: str
    event_log_sha256: str

    def facts(self) -> MatchFacts:
        return MatchFacts(events=self.events, home_id=self.meta["home"], away_id=self.meta["away"], roster=self.roster)


class MatchSource(Protocol):
    def load(self, match_ref: Mapping[str, Any]) -> MatchRecord: ...


def _synth_key(match_ref: Mapping[str, Any]) -> tuple[int, int]:
    if not isinstance(match_ref, Mapping) or set(match_ref) != {"source", "seed", "match_index"}:
        raise MatchSourceError("a synth match_ref holds exactly source, seed and match_index")
    if match_ref["source"] != "synth":
        raise MatchSourceError(f"unknown match source {match_ref['source']!r}")
    seed, index = match_ref["seed"], match_ref["match_index"]
    for name, value in (("seed", seed), ("match_index", index)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise MatchSourceError(f"{name} must be a non-negative int")
    return seed, index


class SynthMatchSource:
    """Seeded synthetic matches, cached per process. Every record is generator output, not a real match."""

    def __init__(self) -> None:
        self._cache: dict[tuple[int, int], MatchRecord] = {}
        self._lock = threading.Lock()

    def load(self, match_ref: Mapping[str, Any]) -> MatchRecord:
        key = _synth_key(match_ref)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        seed, index = key
        league = build_world(seed)
        result = simulate_match(league, index)
        record = MatchRecord(
            match_id=result.meta["match_id"],
            meta=dict(result.meta),
            events=tuple(result.events),
            roster=Roster.from_league(league),
            seed_sha256=seed_sha256(seed, index, GENERATOR_VERSION),
            event_log_sha256=event_log_sha256(result.events),
        )
        with self._lock:
            self._cache.setdefault(key, record)
        return record


def load_facts(source: MatchSource, match_ref: Mapping[str, Any], expected_event_log_sha256: str) -> MatchRecord:
    """Reload the log and refuse it unless it is byte for byte the log the run started from."""
    record = source.load(match_ref)
    if record.event_log_sha256 != expected_event_log_sha256:
        raise MatchSourceError(
            f"event log for {record.match_id} hashes to {record.event_log_sha256}, the run recorded {expected_event_log_sha256}"
        )
    return record

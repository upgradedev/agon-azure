"""The run ledger stamps every history entry with `at` and every noted field with `<field>_at`.

Every time comes from the injected clock, never from the wall clock, so a fixed clock pins each
stamp to the second. The etag-guarded write is unchanged: one store write per status change, none
for a no-op.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fleet_reference import run_to_request
from fleet_support import OFFICER, run, run_input

from agon.fleet import stores
from agon.fleet.local import fake_deps
from agon.governance import verifier
from agon.governance.store import InMemoryStore

T0 = datetime(2026, 10, 20, 18, 30, 5, tzinfo=timezone.utc)


class Ticks:
    """A fixed clock that moves one second per call and counts its calls."""

    def __init__(self, start: datetime = T0) -> None:
        self.start = start
        self.calls = 0

    def __call__(self) -> datetime:
        moment = self.start + timedelta(seconds=self.calls)
        self.calls += 1
        return moment


class CountingStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[str, str, str]] = []

    def create(self, container: str, key: str, body: dict[str, Any]):
        doc = super().create(container, key, body)
        self.writes.append(("create", container, key))
        return doc

    def replace(self, container: str, key: str, body: dict[str, Any], if_match: str):
        doc = super().replace(container, key, body, if_match)
        self.writes.append(("replace", container, key))
        return doc


def _stamp(seconds: int) -> str:
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- advance


def test_every_history_entry_carries_the_time_of_its_own_write() -> None:
    store = CountingStore()
    ledger = stores.RunLedger(store, clock=Ticks())
    path = (stores.DRAFT, stores.CHECKED, stores.SEALED, stores.AWAITING_APPROVAL, stores.APPROVED)
    for status in path:
        ledger.advance("r", status, f"to {status}")
    assert ledger.history("r") == [{"status": s, "detail": f"to {s}", "at": _stamp(i)} for i, s in enumerate(path)]
    assert _stamp(0) == "2026-10-20T18:30:05Z"
    # One write per advance: a create for the first status, one etag-guarded replace for each next.
    assert store.writes == [("create", "runs", "r")] + [("replace", "runs", "r")] * (len(path) - 1)


def test_a_no_op_advance_reads_no_clock_and_writes_nothing() -> None:
    store = CountingStore()
    clock = Ticks()
    ledger = stores.RunLedger(store, clock=clock)
    ledger.advance("r", stores.DRAFT, "first")
    etag = store.read("runs", "r").etag
    ledger.advance("r", stores.DRAFT, "again")
    assert store.read("runs", "r").etag == etag and len(store.writes) == 1
    assert clock.calls == 1
    assert ledger.history("r") == [{"status": stores.DRAFT, "detail": "first", "at": _stamp(0)}]


def test_a_lost_compare_and_set_restamps_the_retry_and_appends_one_entry() -> None:
    armed = {"on": False}

    def race(container: str, key: str) -> None:
        # Armed once: a competing note lands between the advance's read and its replace.
        if armed["on"] and container == "runs":
            armed["on"] = False
            other.note("r", cards_posted=["teams"])

    store = InMemoryStore(after_read=race)
    ledger = stores.RunLedger(store, clock=Ticks())
    other = stores.RunLedger(store, clock=lambda: T0 + timedelta(hours=1))
    ledger.advance("r", stores.DRAFT, "start")  # tick 0
    armed["on"] = True
    ledger.advance("r", stores.CHECKED, "checked")  # tick 1 loses the compare-and-set, tick 2 lands
    assert armed["on"] is False
    history = ledger.history("r")
    assert [h["status"] for h in history] == [stores.DRAFT, stores.CHECKED]
    assert history[1]["at"] == _stamp(2), "the retry is stamped when it is written, not when it first tried"
    record = ledger.record("r")
    assert record["cards_posted"] == ["teams"] and record["cards_posted_at"] == "2026-10-20T19:30:05Z"


def test_an_aware_clock_in_another_zone_is_written_in_utc() -> None:
    athens = timezone(timedelta(hours=3))
    ledger = stores.RunLedger(InMemoryStore(), clock=lambda: datetime(2026, 10, 20, 21, 30, 5, tzinfo=athens))
    ledger.advance("r", stores.DRAFT)
    assert ledger.history("r")[0]["at"] == "2026-10-20T18:30:05Z"


def test_a_naive_clock_is_refused_and_nothing_is_written() -> None:
    store = CountingStore()
    ledger = stores.RunLedger(store, clock=lambda: datetime(2026, 10, 20, 18, 30, 5))
    with pytest.raises(stores.LedgerError):
        ledger.advance("r", stores.DRAFT)
    assert store.writes == [] and ledger.record("r") is None


def test_sub_second_times_are_cut_to_the_second() -> None:
    ledger = stores.RunLedger(InMemoryStore(), clock=lambda: T0 + timedelta(microseconds=999_999))
    ledger.advance("r", stores.DRAFT)
    assert ledger.history("r")[0]["at"] == _stamp(0)


# ----------------------------------------------------------------- note


def test_note_stamps_each_changed_field_beside_it() -> None:
    store = CountingStore()
    ledger = stores.RunLedger(store, clock=Ticks())
    ledger.advance("r", stores.DRAFT)  # tick 0
    ledger.note("r", cards_posted=["teams"])  # tick 1
    record = ledger.record("r")
    assert record["cards_posted"] == ["teams"] and record["cards_posted_at"] == _stamp(1)
    ledger.note("r", publish_enqueued="abc123")  # tick 2
    record = ledger.record("r")
    assert record["publish_enqueued"] == "abc123" and record["publish_enqueued_at"] == _stamp(2)
    assert record["cards_posted_at"] == _stamp(1), "a field not in this note keeps its stamp"
    assert ledger.history("r") == [{"status": stores.DRAFT, "detail": "", "at": _stamp(0)}]


def test_a_note_with_the_same_value_keeps_its_first_stamp_and_writes_nothing() -> None:
    store = CountingStore()
    clock = Ticks()
    ledger = stores.RunLedger(store, clock=clock)
    ledger.advance("r", stores.DRAFT)
    ledger.note("r", cards_posted=["teams"])
    etag, writes, calls = store.read("runs", "r").etag, len(store.writes), clock.calls
    ledger.note("r", cards_posted=["teams"])
    ledger.note("r", cards_posted=("teams",))  # a tuple stores as the same JSON list
    assert store.read("runs", "r").etag == etag and len(store.writes) == writes and clock.calls == calls
    assert ledger.record("r")["cards_posted_at"] == _stamp(1)


def test_a_note_stamps_only_the_fields_whose_value_changed() -> None:
    ledger = stores.RunLedger(InMemoryStore(), clock=Ticks())
    ledger.advance("r", stores.DRAFT)
    ledger.note("r", cards_posted=["teams"])  # tick 1
    ledger.note("r", cards_posted=["outlook", "teams"], publish_enqueued="abc123")  # tick 2
    ledger.note("r", cards_posted=["outlook", "teams"], publish_enqueued="def456")  # tick 3
    record = ledger.record("r")
    assert record["cards_posted"] == ["outlook", "teams"] and record["cards_posted_at"] == _stamp(2)
    assert record["publish_enqueued"] == "def456" and record["publish_enqueued_at"] == _stamp(3)


def test_note_refuses_a_stamp_field_and_the_reserved_fields() -> None:
    ledger = stores.RunLedger(InMemoryStore(), clock=Ticks())
    ledger.advance("r", stores.DRAFT)
    for name in ("cards_posted_at", "publish_enqueued_at", "anything_at"):
        with pytest.raises(stores.LedgerError):
            ledger.note("r", **{name: "2026-01-01T00:00:00Z"})
    for reserved in ("status", "history", "run_id"):
        with pytest.raises(stores.LedgerError):
            ledger.note("r", **{reserved: "x"})
    assert "cards_posted_at" not in ledger.record("r")


# ----------------------------------------------------------------- the fleet wiring


def test_the_fleet_ledger_stamps_from_the_deps_clock() -> None:
    fixed = datetime(2026, 10, 20, 18, 30, 5, 123456, tzinfo=timezone.utc)
    deps = fake_deps(clock=lambda: fixed)
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    ref = run(run_to_request(deps, run_input()))
    assert ref.request is not None, ref.outcome
    history = deps.ledger.history("run-1")
    assert [h["status"] for h in history] == [stores.DRAFT, stores.CHECKED, stores.SEALED]
    assert all(h["at"] == "2026-10-20T18:30:05Z" for h in history), history
    # A clock swapped on the deps after they were built is the one the ledger reads.
    deps.clock = lambda: fixed + timedelta(minutes=5)
    deps.ledger.advance("run-1", stores.AWAITING_APPROVAL, "test")
    deps.ledger.note("run-1", cards_posted=["teams"])
    record = deps.ledger.record("run-1")
    assert record["history"][-1]["at"] == "2026-10-20T18:35:05Z"
    assert record["cards_posted_at"] == "2026-10-20T18:35:05Z"

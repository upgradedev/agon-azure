"""M15: 50 concurrent approvals of one nonce produce exactly one publish.

The race is forced, not left to the scheduler: the store holds every thread right after its read
of the nonce until all 50 have read it as pending, so all 50 then attempt the compare-and-set.
Negative control: a store that ignores the etag lets every thread consume the nonce, which proves
the etag compare-and-set is what decides the winner.

Scope: this runs against `InMemoryStore`, the reference implementation of the store protocol. The
Cosmos DB etag compare-and-set is NOT exercised here; that needs the Cosmos adapter and a deploy.
"""

from __future__ import annotations

import threading

import govkit
from agon.governance import publisher as P
from agon.governance import verifier
from agon.governance.channels import teams
from agon.governance.store import InMemoryStore, PreconditionFailed

N = 50


class CountingStore(InMemoryStore):
    """Counts successful nonce consumptions; optionally ignores etags (the broken store)."""

    def __init__(self, ignore_etag: bool = False) -> None:
        self.hold_enabled = False
        self._barrier = threading.Barrier(N, timeout=60)
        self._local = threading.local()
        super().__init__(after_read=self._hold)
        self.ignore_etag = ignore_etag
        self.consumed_writes = 0
        self._count_lock = threading.Lock()

    def _hold(self, container: str, key: str) -> None:
        if self.hold_enabled and container == verifier.NONCES and not getattr(self._local, "held", False):
            self._local.held = True
            self._barrier.wait()

    def replace(self, container, key, body, if_match):
        if self.ignore_etag and container == verifier.NONCES:
            current = InMemoryStore.read(self, container, key)
            if_match = current.etag
        doc = super().replace(container, key, body, if_match)
        if container == verifier.NONCES:
            with self._count_lock:
                self.consumed_writes += 1
        return doc


def _race(store: CountingStore):
    env = govkit.make_env(store=store)
    pub = P.Publisher(store, P.InMemorySink(), "feed")
    payload = govkit.teams_payload(env)
    results, publishes, errors = [], [], []
    lock = threading.Lock()

    def worker():
        try:
            r = teams.handle(payload, store, govkit.later(30))
            p = None
            if r.approved:
                p = pub.publish(
                    bundle_sha256=env.bundle.bundle_sha256,
                    approval_id=r.approval_id,
                    manifest_bytes=env.bundle.manifest_bytes,
                    artifacts=env.files,
                    now=govkit.later(31),
                )
            with lock:
                results.append(r)
                if p is not None:
                    publishes.append(p)
        except Exception as exc:  # surfaced below; a thread must never fail silently
            with lock:
                errors.append(repr(exc))

    store.hold_enabled = True
    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads)
    assert not errors, errors
    assert len(results) == N
    return env, results, publishes


def test_fifty_concurrent_approvals_publish_exactly_once():
    store = CountingStore()
    env, results, publishes = _race(store)
    approved = [r for r in results if r.approved]
    assert len(approved) == 1
    assert sorted({r.code for r in results if not r.ok}) == ["already_consumed"]
    assert sum(1 for p in publishes if p.ok) == 1
    assert store.keys(P.PUBLISHED) == [env.bundle.bundle_sha256]
    assert len(store.keys(verifier.APPROVALS)) == 1
    assert store.consumed_writes == 1
    print(
        f"M15 self-measured against the in-memory reference store (Cosmos etag not exercised): "
        f"{N} concurrent approvals of one nonce -> {sum(1 for p in publishes if p.ok)} publish"
    )


def test_negative_control_without_the_etag_check_every_thread_consumes():
    store = CountingStore(ignore_etag=True)
    env, results, publishes = _race(store)
    assert store.consumed_writes == N  # the compare-and-set was the only thing deciding the winner
    # The create-only approval record and receipt still hold the line: defence in depth.
    assert len(store.keys(verifier.APPROVALS)) == 1
    assert store.keys(P.PUBLISHED) == [env.bundle.bundle_sha256]


def test_store_compare_and_set_rejects_a_stale_etag():
    store = InMemoryStore()
    doc = store.create("c", "k", {"v": 1})
    store.replace("c", "k", {"v": 2}, if_match=doc.etag)
    try:
        store.replace("c", "k", {"v": 3}, if_match=doc.etag)
    except PreconditionFailed:
        pass
    else:
        raise AssertionError("a stale etag must be rejected")
    assert store.read("c", "k").body == {"v": 2}

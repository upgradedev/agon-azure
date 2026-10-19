"""The Cosmos DB store adapter against a fake that follows the azure-cosmos ContainerProxy contract.

The fake answers like the service does for the three calls the adapter makes: 404 on a missing
item, 409 on a duplicate create, 412 when the etag of a conditional replace no longer matches, a
fresh `_etag` on every write, and system fields on every item it returns. It also refuses a
replace that does not ask for the IfNotModified condition, so the adapter cannot silently drop
the compare-and-set.

Scope, stated plainly: this proves the adapter's mapping, not the service. The live round trip
against the deployed account runs in a main-only CI job once the api identity holds its roles.
"""

from __future__ import annotations

import itertools
import threading

import pytest

import govkit
from agon.governance import verifier
from agon.governance.channels import teams
from agon.governance.store import Conflict, NotFound, PreconditionFailed
from agon.governance.store_cosmos import GOVERNANCE_CONTAINERS, CosmosStore, StoreUnavailable

IF_NOT_MODIFIED = object()


class FakeCosmosError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FakeContainer:
    def __init__(self, name: str, after_read=None) -> None:
        self.name = name
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.etags = itertools.count(1)
        self.after_read = after_read
        self.fail_with: int | None = None

    def _stamp(self, body: dict) -> dict:
        return {**body, "_etag": f'"{next(self.etags)}"', "_rid": "r", "_self": "s", "_attachments": "a", "_ts": 1}

    def read_item(self, item, partition_key):
        assert item == partition_key, "every governance container is partitioned on /id"
        if self.fail_with:
            raise FakeCosmosError(self.fail_with)
        with self.lock:
            found = self.items.get(item)
            result = None if found is None else dict(found)
        if self.after_read:
            self.after_read(self.name, item)
        if result is None:
            raise FakeCosmosError(404)
        return result

    def create_item(self, body):
        with self.lock:
            if body["id"] in self.items:
                raise FakeCosmosError(409)
            self.items[body["id"]] = self._stamp(body)
            return dict(self.items[body["id"]])

    def replace_item(self, item, body, etag=None, match_condition=None):
        if match_condition is not IF_NOT_MODIFIED or not etag:
            raise AssertionError("replace must be conditional on the etag")
        with self.lock:
            current = self.items.get(item)
            if current is None:
                raise FakeCosmosError(404)
            if current["_etag"] != etag:
                raise FakeCosmosError(412)
            self.items[item] = self._stamp(body)
            return dict(self.items[item])


class FakeDatabase:
    def __init__(self, after_read=None) -> None:
        self.containers: dict[str, FakeContainer] = {}
        self.after_read = after_read

    def get_container_client(self, name):
        return self.containers.setdefault(name, FakeContainer(name, self.after_read))


def make_store(after_read=None) -> tuple[CosmosStore, FakeDatabase]:
    db = FakeDatabase(after_read)
    return CosmosStore(db, match_condition=IF_NOT_MODIFIED), db


def test_round_trip_strips_system_fields_and_returns_the_etag():
    store, db = make_store()
    created = store.create("nonces", "abc", {"status": "pending", "n": 1})
    assert created.body == {"status": "pending", "n": 1}
    raw = db.containers["nonces"].items["abc"]
    assert raw["id"] == "abc" and created.etag == raw["_etag"]
    read = store.read("nonces", "abc")
    assert read.body == {"status": "pending", "n": 1} and read.etag == created.etag
    replaced = store.replace("nonces", "abc", {"status": "consumed"}, if_match=read.etag)
    assert replaced.body == {"status": "consumed"} and replaced.etag != read.etag


def test_missing_item_reads_as_none():
    store, _ = make_store()
    assert store.read("approvals", "nothing") is None


def test_create_never_overwrites():
    store, db = make_store()
    store.create("approvals", "a1", {"v": 1})
    with pytest.raises(Conflict):
        store.create("approvals", "a1", {"v": 2})
    assert db.containers["approvals"].items["a1"]["v"] == 1


def test_a_stale_etag_is_refused():
    store, _ = make_store()
    first = store.create("nonces", "n1", {"status": "pending"})
    store.replace("nonces", "n1", {"status": "consumed"}, if_match=first.etag)
    with pytest.raises(PreconditionFailed):
        store.replace("nonces", "n1", {"status": "pending"}, if_match=first.etag)


def test_replace_of_a_missing_item_is_not_found():
    store, _ = make_store()
    with pytest.raises(NotFound):
        store.replace("nonces", "ghost", {"status": "consumed"}, if_match='"1"')


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_any_other_answer_is_store_unavailable(status):
    store, db = make_store()
    db.get_container_client("bundles").fail_with = status
    with pytest.raises(StoreUnavailable):
        store.read("bundles", "x")


@pytest.mark.parametrize("field", sorted({"id", "_etag", "_rid", "_self", "_attachments", "_ts"}))
def test_a_body_may_not_carry_system_fields(field):
    store, _ = make_store()
    with pytest.raises(ValueError):
        store.create("runs", "r1", {field: "x"})


@pytest.mark.parametrize("key", ["", "a/b", "a\\b", "a?b", "a#b", "x" * 256, "spaced key"])
def test_unsafe_keys_are_refused(key):
    store, _ = make_store()
    with pytest.raises(ValueError):
        store.read("runs", key)


def test_only_governance_containers_are_addressable():
    store, _ = make_store()
    assert GOVERNANCE_CONTAINERS == {"bundles", "nonces", "approvals", "approvers", "runs", "published", "supersessions"}
    for name in ("checkpoints", "matchstate", "provenance", "feed", "artifacts"):
        with pytest.raises(ValueError):
            store.read(name, "k")


def test_the_verifier_approves_end_to_end_over_the_adapter():
    store, _ = make_store()
    env = govkit.make_env(store=store)
    result = teams.handle(govkit.teams_payload(env), store, govkit.later(30))
    assert result.approved
    assert store.read(verifier.NONCES, env.nonce).body["status"] == "consumed"
    assert store.read(verifier.APPROVALS, result.approval_id).body["channel"] == "teams"


def test_fifty_concurrent_approvals_of_one_nonce_consume_it_once_over_the_adapter():
    """M15 shape over the adapter: every thread reads the nonce as pending before any replace."""
    n = 50
    barrier = threading.Barrier(n, timeout=60)
    holding = {"on": False}
    local = threading.local()

    def hold(container, key):
        if holding["on"] and container == verifier.NONCES and not getattr(local, "held", False):
            local.held = True
            barrier.wait()

    store, _ = make_store(after_read=hold)
    env = govkit.make_env(store=store)
    holding["on"] = True
    results = []
    lock = threading.Lock()

    def approve():
        r = teams.handle(govkit.teams_payload(env), store, govkit.later(30))
        with lock:
            results.append(r)

    threads = [threading.Thread(target=approve) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r.approved) == 1
    assert sorted({r.code for r in results if not r.ok}) == ["already_consumed"]

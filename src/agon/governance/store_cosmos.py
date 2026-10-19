"""The governance `Store` protocol over Azure Cosmos DB for NoSQL (spec 3.2 row 8).

Three operations, mapped one to one onto the SDK, and nothing more:

* `read`    -> `read_item(key, partition_key=key)`; HTTP 404 means no document (`None`);
* `create`  -> `create_item(body)`; HTTP 409 raises `Conflict`, so a create never overwrites;
* `replace` -> `replace_item(key, body, etag=..., match_condition=IfNotModified)`; HTTP 412 raises
  `PreconditionFailed` (the compare-and-set the verifier's consume-once rests on) and 404 raises
  `NotFound`.

There is no upsert and no delete here, as in `InMemoryStore`. Every governance container is keyed
by `/id` (infra/modules/cosmos.bicep), so the document id and the partition key are both the
store key. Cosmos system fields (`_rid`, `_self`, `_etag`, `_attachments`, `_ts`) and `id` are
stripped on read, so callers see exactly the body they stored, and a body may not carry them.

The SDK is never imported at module load: the adapter takes a `database` object (an
`azure.cosmos.DatabaseProxy` in production, a fake in tests) and reads only `status_code` from
errors, so the module imports with the standard library only.
"""

from __future__ import annotations

import re
from typing import Any

from agon.governance.store import Conflict, Document, NotFound, PreconditionFailed, StoreError, _snapshot

# Containers this adapter may address: the governance ones, all partitioned on /id.
GOVERNANCE_CONTAINERS: frozenset[str] = frozenset({"bundles", "nonces", "approvals", "approvers", "runs"})
SYSTEM_FIELDS: frozenset[str] = frozenset({"id", "_rid", "_self", "_etag", "_attachments", "_ts"})
# Cosmos refuses '/', '\\', '?' and '#' in an id; keep keys to a safe, printable set.
_KEY = re.compile(r"^[A-Za-z0-9@._:+=-]{1,255}$")


class StoreUnavailable(StoreError):
    """Any Cosmos answer other than the ones the protocol names (throttling, outage, auth)."""


def _status(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    return int(code) if isinstance(code, int) else None


def _if_not_modified() -> Any:
    from azure.core import MatchConditions  # imported here on purpose

    return MatchConditions.IfNotModified


class CosmosStore:
    def __init__(self, database: Any, *, match_condition: Any = None) -> None:
        self._database = database
        self._containers: dict[str, Any] = {}
        self._match_condition = match_condition

    def _container(self, name: str) -> Any:
        if name not in GOVERNANCE_CONTAINERS:
            raise ValueError(f"{name!r} is not a governance container")
        if name not in self._containers:
            self._containers[name] = self._database.get_container_client(name)
        return self._containers[name]

    @staticmethod
    def _check(key: str, body: dict[str, Any] | None = None) -> None:
        if not isinstance(key, str) or not _KEY.match(key):
            raise ValueError("store key must be 1 to 255 characters from [A-Za-z0-9@._:+=-]")
        if body is not None:
            clash = sorted(SYSTEM_FIELDS.intersection(body))
            if clash:
                raise ValueError(f"body may not carry Cosmos system fields {clash}")

    @staticmethod
    def _document(item: dict[str, Any]) -> Document:
        etag = item.get("_etag")
        if not isinstance(etag, str) or not etag:
            raise StoreUnavailable("Cosmos returned an item without an etag")
        return Document({k: v for k, v in item.items() if k not in SYSTEM_FIELDS}, etag)

    def read(self, container: str, key: str) -> Document | None:
        self._check(key)
        client = self._container(container)
        try:
            item = client.read_item(item=key, partition_key=key)
        except Exception as exc:
            if _status(exc) == 404:
                return None
            raise StoreUnavailable(f"read {container}/{key}: {type(exc).__name__}") from exc
        return self._document(item)

    def create(self, container: str, key: str, body: dict[str, Any]) -> Document:
        self._check(key, body)
        client = self._container(container)
        try:
            item = client.create_item(body={**_snapshot(body), "id": key})
        except Exception as exc:
            if _status(exc) == 409:
                raise Conflict(f"{container}/{key} already exists") from exc
            raise StoreUnavailable(f"create {container}/{key}: {type(exc).__name__}") from exc
        return self._document(item)

    def replace(self, container: str, key: str, body: dict[str, Any], if_match: str) -> Document:
        self._check(key, body)
        if not isinstance(if_match, str) or not if_match:
            raise ValueError("replace needs the etag read with the document")
        condition = self._match_condition if self._match_condition is not None else _if_not_modified()
        client = self._container(container)
        try:
            item = client.replace_item(
                item=key, body={**_snapshot(body), "id": key}, etag=if_match, match_condition=condition
            )
        except Exception as exc:
            status = _status(exc)
            if status == 412:
                raise PreconditionFailed(f"{container}/{key} changed since it was read") from exc
            if status == 404:
                raise NotFound(f"{container}/{key} does not exist") from exc
            raise StoreUnavailable(f"replace {container}/{key}: {type(exc).__name__}") from exc
        return self._document(item)


def connect(endpoint: str, database: str, credential: Any) -> CosmosStore:
    """Production wiring: a keyless client (managed identity), one database, governance containers."""
    from azure.cosmos import CosmosClient  # imported here on purpose

    return CosmosStore(CosmosClient(endpoint, credential=credential).get_database_client(database))

"""Storage protocol for governance state, with an in-memory implementation.

The protocol has exactly three operations, chosen to match what Cosmos DB offers and nothing
more: read a document with its etag, create a document that must not exist yet, and replace a
document only if its etag still matches (compare-and-set). There is no upsert and no delete, so
nothing in governance can silently overwrite or remove a record.

`InMemoryStore` is the reference implementation the tests run against. Each operation is atomic
under one lock; a read followed by a replace is NOT atomic, which is exactly the race the etag
compare-and-set exists to settle. `after_read` lets a test hold every thread between its read and
its replace so the race is forced, not left to the scheduler.
"""

from __future__ import annotations

import copy
import itertools
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class StoreError(Exception):
    """Base class for store failures."""


class Conflict(StoreError):
    """create() on a key that already exists."""


class PreconditionFailed(StoreError):
    """replace() with an etag that no longer matches."""


class NotFound(StoreError):
    """replace() on a key that does not exist."""


@dataclass(frozen=True)
class Document:
    body: dict[str, Any]
    etag: str


class Store(Protocol):
    def read(self, container: str, key: str) -> Document | None: ...

    def create(self, container: str, key: str, body: dict[str, Any]) -> Document: ...

    def replace(self, container: str, key: str, body: dict[str, Any], if_match: str) -> Document: ...


def _snapshot(body: dict[str, Any]) -> dict[str, Any]:
    """A JSON round trip: proves the body is storable and isolates the caller's copy."""
    return json.loads(json.dumps(body, sort_keys=True, ensure_ascii=False))


class InMemoryStore:
    def __init__(self, after_read: Callable[[str, str], None] | None = None) -> None:
        self._data: dict[tuple[str, str], Document] = {}
        self._lock = threading.Lock()
        self._versions = itertools.count(1)
        self._after_read = after_read

    def _etag(self) -> str:
        return f'"v{next(self._versions)}"'

    def read(self, container: str, key: str) -> Document | None:
        with self._lock:
            doc = self._data.get((container, key))
            result = None if doc is None else Document(copy.deepcopy(doc.body), doc.etag)
        if self._after_read is not None:
            self._after_read(container, key)
        return result

    def create(self, container: str, key: str, body: dict[str, Any]) -> Document:
        snap = _snapshot(body)
        with self._lock:
            if (container, key) in self._data:
                raise Conflict(f"{container}/{key} already exists")
            doc = Document(snap, self._etag())
            self._data[(container, key)] = doc
            return Document(copy.deepcopy(doc.body), doc.etag)

    def replace(self, container: str, key: str, body: dict[str, Any], if_match: str) -> Document:
        snap = _snapshot(body)
        with self._lock:
            current = self._data.get((container, key))
            if current is None:
                raise NotFound(f"{container}/{key} does not exist")
            if current.etag != if_match:
                raise PreconditionFailed(f"{container}/{key} changed since it was read")
            doc = Document(snap, self._etag())
            self._data[(container, key)] = doc
            return Document(copy.deepcopy(doc.body), doc.etag)

    def keys(self, container: str) -> list[str]:
        """Test and inspection helper; not part of the protocol."""
        with self._lock:
            return sorted(k for (c, k) in self._data if c == container)

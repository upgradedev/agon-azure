"""Where matchfacts reads a match from: a read-only data source protocol and two implementations.

`MatchSource` has three read methods and nothing else. There is no write method on the protocol,
on either implementation, or on the Cosmos reader interface, so a tool built on it cannot write.

- `InMemorySource` holds generated matches in memory (tests, the anonymous demo, local runs).
  Built from seeds through `agon.synth.manifest`, it carries the world, the match meta and the
  events. It never carries the engine's intervention ledger: that is ground truth for the referee.
- `CosmosSource` reads the Cosmos containers `id-mcp` may read (spec 7.1): `matchstate` and
  `charters`, nothing else. It talks to Cosmos through the small `CosmosReader` interface, so this
  module imports no Azure SDK; `ContainerClientsReader` adapts SDK container clients (anything with
  `read_item` and `query_items`) and refuses every other container.

Document layout the Cosmos implementation expects (the ingest lane writes it; confirm there):
- `matchstate`, partition key `/match_id`: one snapshot document per match with
  `id == match_id` and fields `match_id`, `meta` (the generator match meta), `world` (league,
  clubs, players) and `events` (the event list).
- `charters`, partition key `/id`: documents with `match_id`, `status` and `approved_at`; the
  newest document with status `approved` is the match's charter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

READ_CONTAINERS: tuple[str, ...] = ("matchstate", "charters")
_SYSTEM_FIELDS = ("_rid", "_self", "_etag", "_attachments", "_ts", "_lsn")


class UnknownMatch(KeyError):
    """The source holds no match with this id."""


@dataclass(frozen=True)
class MatchRecord:
    match_id: str
    meta: Mapping[str, Any]
    world: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...]


class MatchSource(Protocol):
    def match_ids(self) -> list[str]: ...

    def record(self, match_id: str) -> MatchRecord: ...

    def charter(self, match_id: str) -> Mapping[str, Any] | None: ...


class InMemorySource:
    """Matches held in memory. Read-only after construction."""

    def __init__(self, records: Iterable[MatchRecord], charters: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self._records = {r.match_id: r for r in records}
        self._charters = dict(charters or {})

    @classmethod
    def from_seeds(cls, seeds: Sequence[int], match_index: int = 0) -> "InMemorySource":
        from agon.synth.manifest import build_bundle

        records = []
        for seed in seeds:
            b = build_bundle(seed, match_index)
            records.append(MatchRecord(match_id=b.match["match_id"], meta=b.match, world=b.world, events=tuple(b.events)))
        return cls(records)

    def match_ids(self) -> list[str]:
        return sorted(self._records)

    def record(self, match_id: str) -> MatchRecord:
        try:
            return self._records[match_id]
        except KeyError:
            raise UnknownMatch(match_id) from None

    def charter(self, match_id: str) -> Mapping[str, Any] | None:
        self.record(match_id)
        return self._charters.get(match_id)


class CosmosReader(Protocol):
    """The two Cosmos reads matchfacts needs. Deliberately no create, upsert, replace or delete."""

    def read_item(self, container: str, item_id: str, partition_key: str) -> Mapping[str, Any]: ...

    def query_items(
        self, container: str, query: str, parameters: Sequence[Mapping[str, Any]], partition_key: str | None
    ) -> list[Mapping[str, Any]]: ...


class ContainerClientsReader:
    """Adapts Cosmos SDK container clients (duck-typed) to `CosmosReader`, for READ_CONTAINERS only."""

    def __init__(self, containers: Mapping[str, Any]) -> None:
        extra = sorted(set(containers) - set(READ_CONTAINERS))
        if extra:
            raise ValueError(f"matchfacts reads only {list(READ_CONTAINERS)}; refusing {extra}")
        self._containers = dict(containers)

    def _client(self, container: str) -> Any:
        if container not in READ_CONTAINERS:
            raise PermissionError(f"matchfacts may not read container {container!r}")
        try:
            return self._containers[container]
        except KeyError:
            raise PermissionError(f"no client configured for container {container!r}") from None

    def read_item(self, container: str, item_id: str, partition_key: str) -> Mapping[str, Any]:
        """Read one item; a 404 from the SDK (any exception with status_code 404) becomes KeyError."""
        try:
            return self._client(container).read_item(item=item_id, partition_key=partition_key)
        except Exception as exc:  # the SDK's not-found type is not importable here without the SDK
            if getattr(exc, "status_code", None) == 404:
                raise KeyError(item_id) from exc
            raise

    def query_items(
        self, container: str, query: str, parameters: Sequence[Mapping[str, Any]], partition_key: str | None
    ) -> list[Mapping[str, Any]]:
        client = self._client(container)
        if partition_key is None:
            return list(client.query_items(query=query, parameters=list(parameters), enable_cross_partition_query=True))
        return list(client.query_items(query=query, parameters=list(parameters), partition_key=partition_key))


def _strip(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in _SYSTEM_FIELDS}


class CosmosSource:
    """Matches read from Cosmos `matchstate`, charters from `charters`, through a `CosmosReader`."""

    def __init__(self, reader: CosmosReader) -> None:
        self._reader = reader

    def match_ids(self) -> list[str]:
        rows = self._reader.query_items("matchstate", "SELECT VALUE c.match_id FROM c", [], None)
        return sorted({str(r) for r in rows})

    def record(self, match_id: str) -> MatchRecord:
        try:
            doc = _strip(self._reader.read_item("matchstate", match_id, match_id))
        except KeyError:
            raise UnknownMatch(match_id) from None
        missing = [k for k in ("meta", "world", "events") if k not in doc]
        if missing:
            raise ValueError(f"matchstate document {match_id!r} lacks {missing}")
        return MatchRecord(match_id=match_id, meta=doc["meta"], world=doc["world"], events=tuple(doc["events"]))

    def charter(self, match_id: str) -> Mapping[str, Any] | None:
        rows = self._reader.query_items(
            "charters",
            "SELECT * FROM c WHERE c.match_id = @match_id AND c.status = 'approved'",
            [{"name": "@match_id", "value": match_id}],
            None,
        )
        if not rows:
            return None
        best = max(rows, key=lambda r: (str(r.get("approved_at", "")), str(r.get("id", ""))))
        return _strip(best)

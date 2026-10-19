"""Where the Publisher job reads sealed bytes: the artifact store contract with the fleet.

Contract (build session, 2026-10-19): the fleet worker writes every sealed artifact,
content-addressed and create-only, to the PRIVATE blob container `artifacts` in the same storage
account, at the blob name `<sha256 hex>` with no extension. Destination payloads are sealed
artifacts too (`destination-<id>.json`), so they are there as well. The sealed manifest stays in
the Cosmos `bundles` container (document id = bundle hash, field `manifest`), where
`verifier.register_sealed_bundle` put it.

`ArtifactSource.get(sha256)` returns the stored bytes or None. It never vouches for them: the
Publisher recomputes every hash from the bytes before anything is written (publisher rule 1), so a
corrupted or swapped blob is refused there, by the same code that refuses everything else.

`BlobArtifactSource` (agon.publish.azure_blob) is the Azure implementation, read-only with the
job's managed identity; `InMemoryArtifactSource` is the fake the tests use.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, Protocol

ARTIFACT_CONTAINER = "artifacts"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactSource(Protocol):
    def get(self, sha256: str) -> bytes | None: ...


def check_name(sha256: str) -> str:
    """An artifact is addressed by 64 lowercase hex characters, nothing else (no path, no extension)."""
    if not isinstance(sha256, str) or not _HEX64.match(sha256):
        raise ValueError("artifact names are 64 lowercase hex characters")
    return sha256


class InMemoryArtifactSource:
    def __init__(self, blobs: Iterable[bytes] = ()) -> None:
        self.blobs: dict[str, bytes] = {}
        for data in blobs:
            self.put(data)

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        self.blobs.setdefault(sha, bytes(data))
        return sha

    def get(self, sha256: str) -> bytes | None:
        return self.blobs.get(check_name(sha256))

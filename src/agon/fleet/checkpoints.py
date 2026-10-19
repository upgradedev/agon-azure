"""Checkpoint storage for the fleet workflows (W-fulltime, W-charter): files for tests, Cosmos DB for production, and a save guard.

Three facts from Agent Framework 1.22.0 shape this module (paths under
``python/packages/core/agent_framework/_workflows/`` at tag ``python-1.22.0``):

1. ``_runner.py`` ``create_checkpoint_if_enabled`` catches EVERY exception from a checkpoint save
   and only logs a warning ("this does not fail the workflow run"). A Cosmos 403 or a 2 MB item
   limit would therefore pass silently. `GuardedCheckpointStorage` records every failed save, and
   the runner refuses to report a pause while any save failed.
2. ``agent-framework-azure-cosmos`` 1.0.0b261015, ``CosmosCheckpointStorage._ensure_container_proxy``
   calls ``create_database_if_not_exists`` unless it is handed a container client. A managed
   identity with only a Cosmos data-plane role cannot create databases (spec trap 5), so
   `open_cosmos_storage` always passes a pre-built ``container_client`` for the container Bicep
   pre-creates (``agon``/``checkpoints``, partition key ``/workflow_name``).
3. Checkpoints hold pickled values (``_checkpoint_encoding.py`` module docstring): the store is a
   trusted source, and the allow-list (`STORAGE_ALLOWED_CHECKPOINT_TYPES`) is defence in depth.
   The checkpoints container needs the same access control as a secret store.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, AsyncIterator

from agent_framework import FileCheckpointStorage

from agon.fleet.kinds import STORAGE_ALLOWED_CHECKPOINT_TYPES

COSMOS_DATABASE = "agon"
COSMOS_CONTAINER = "checkpoints"
PROBE_PARTITION = "agon-access-probe"
ROLE_MISSING_MESSAGE = (
    "the identity has no Cosmos DB data-plane role on {database}/{container} "
    "(needs Cosmos DB Built-in Data Contributor scoped to that container; lane A3 adds it for agon-azure-ci)"
)


class CheckpointSaveFailed(RuntimeError):
    """A checkpoint save failed; the framework only logged it, the guard did not forget it."""


class CosmosRoleMissing(PermissionError):
    """The identity cannot read or write the checkpoints container."""


class GuardedCheckpointStorage:
    """Wraps any CheckpointStorage and remembers every save that raised.

    The exception is re-raised, so the framework behaves exactly as before; the difference is that
    `failures` keeps the evidence for the caller, which must check it before reporting a pause.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.failures: list[str] = []
        self.saved: list[str] = []

    async def save(self, checkpoint: Any) -> str:
        try:
            checkpoint_id = await self.inner.save(checkpoint)
        except Exception as exc:
            self.failures.append(f"{type(exc).__name__}: {exc}")
            raise
        self.saved.append(checkpoint_id)
        return checkpoint_id

    async def load(self, checkpoint_id: str) -> Any:
        return await self.inner.load(checkpoint_id)

    async def list_checkpoints(self, *, workflow_name: str) -> list[Any]:
        return await self.inner.list_checkpoints(workflow_name=workflow_name)

    async def delete(self, checkpoint_id: str) -> bool:
        return await self.inner.delete(checkpoint_id)

    async def get_latest(self, *, workflow_name: str) -> Any:
        return await self.inner.get_latest(workflow_name=workflow_name)

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        return await self.inner.list_checkpoint_ids(workflow_name=workflow_name)

    def raise_if_failed(self) -> None:
        if self.failures:
            raise CheckpointSaveFailed("; ".join(self.failures))


def file_storage(directory: Path) -> FileCheckpointStorage:
    """One JSON file per checkpoint, decoded only under the pinned allow-list."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    return FileCheckpointStorage(directory, allowed_checkpoint_types=list(STORAGE_ALLOWED_CHECKPOINT_TYPES))


async def probe_cosmos_access(container: Any, database: str = COSMOS_DATABASE, container_name: str = COSMOS_CONTAINER) -> None:
    """Read a missing item, then write and delete a probe document. 403 anywhere means no role."""
    from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

    message = ROLE_MISSING_MESSAGE.format(database=database, container=container_name)
    try:
        await container.read_item(item="agon-probe-missing", partition_key=PROBE_PARTITION)
    except CosmosResourceNotFoundError:
        pass
    except CosmosHttpResponseError as exc:
        if exc.status_code in (401, 403):
            raise CosmosRoleMissing(f"{message}; read returned {exc.status_code}") from exc
        raise
    probe = {"id": "agon-probe-write", "workflow_name": PROBE_PARTITION}
    try:
        await container.upsert_item(body=probe)
        await container.delete_item(item=probe["id"], partition_key=PROBE_PARTITION)
    except CosmosHttpResponseError as exc:
        if exc.status_code in (401, 403):
            raise CosmosRoleMissing(f"{message}; write returned {exc.status_code}") from exc
        raise


@contextlib.asynccontextmanager
async def open_cosmos_storage(
    endpoint: str,
    *,
    database: str = COSMOS_DATABASE,
    container: str = COSMOS_CONTAINER,
    credential: Any = None,
    probe: bool = True,
) -> AsyncIterator[Any]:
    """CosmosCheckpointStorage over the pre-created container, keyless (Entra ID only).

    With no `credential`, `DefaultAzureCredential` is used: the managed identity in Container
    Apps, the OIDC login in GitHub Actions.
    """
    if not endpoint:
        raise ValueError("a Cosmos DB endpoint is required")
    from agent_framework_azure_cosmos import CosmosCheckpointStorage
    from azure.cosmos.aio import CosmosClient

    owned_credential = None
    if credential is None:
        from azure.identity.aio import DefaultAzureCredential

        credential = owned_credential = DefaultAzureCredential()
    client = CosmosClient(endpoint, credential=credential)
    try:
        container_client = client.get_database_client(database).get_container_client(container)
        if probe:
            await probe_cosmos_access(container_client, database, container)
        yield CosmosCheckpointStorage(
            container_client=container_client,
            database_name=database,
            container_name=container,
            allowed_checkpoint_types=list(STORAGE_ALLOWED_CHECKPOINT_TYPES),
        )
    finally:
        await client.close()
        if owned_credential is not None:
            await owned_credential.close()

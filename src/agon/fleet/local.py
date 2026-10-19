"""Build `FleetDeps` from configuration: fake ports for tests and CI, file or memory stores.

The production wiring (Foundry, Translator, Speech, Cosmos, Storage queues) is assembled by the
Container Apps worker from its environment; the fake wiring here is what every offline test and
the kill-and-resume driver use, and it says so in the bundle (`deployment_ids["ports"] = "fake"`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agon.agents.fakes import FAKE_DEPLOYMENT_IDS, FakeModel, FakeSpeech, FakeTranslator
from agon.fleet.deps import FleetDeps, file_tracer, no_trace
from agon.fleet.interpret_port import default_interpret
from agon.fleet.sources import SynthMatchSource
from agon.fleet.stores import (
    FileArtifactStore,
    FilePublishQueue,
    FileStore,
    InMemoryArtifactStore,
    InMemoryPublishQueue,
)
from agon.governance.store import InMemoryStore


def fake_deps(
    root: Path | None = None,
    *,
    model: FakeModel | None = None,
    translator: FakeTranslator | None = None,
    speech: FakeSpeech | None = None,
    **overrides: Any,
) -> FleetDeps:
    """Fake ports. With `root`, every store is a directory under it, shared by any process using it."""
    if root is None:
        store: Any = InMemoryStore()
        artifacts: Any = InMemoryArtifactStore()
        queue: Any = InMemoryPublishQueue()
        trace = no_trace
    else:
        root = Path(root)
        store = FileStore(root / "store")
        artifacts = FileArtifactStore(root / "artifacts")
        queue = FilePublishQueue(root / "publish-requests")
        trace = file_tracer(root / "trace.ndjson")
    deps = FleetDeps(
        model=model or FakeModel(),
        translator=translator or FakeTranslator(),
        speech=speech or FakeSpeech(),
        interpret=default_interpret(),
        matches=SynthMatchSource(),
        artifacts=artifacts,
        store=store,
        publish_queue=queue,
        deployment_ids=dict(FAKE_DEPLOYMENT_IDS),
        trace=trace,
    )
    for key, value in overrides.items():
        setattr(deps, key, value)
    return deps

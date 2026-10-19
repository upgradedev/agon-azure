"""Everything an executor needs that is not in the checkpoint, injected at build time.

A fresh process rebuilds `FleetDeps` from configuration and rebuilds the graph; nothing in here
is ever pickled. Swapping every port for a fake changes no executor code, which is how CI runs
the whole workflow offline.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agon.agents.ports import ModelPort, SpeechPort, TranslatorPort
from agon.fleet.interpret_port import InterpretPort
from agon.fleet.sources import MatchSource
from agon.fleet.stores import ArtifactStore, PublishQueue, RunLedger
from agon.governance.store import Store


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def no_trace(executor_id: str, step: str) -> None:
    return None


@dataclass
class FleetDeps:
    model: ModelPort
    translator: TranslatorPort
    speech: SpeechPort
    interpret: InterpretPort
    matches: MatchSource
    artifacts: ArtifactStore
    store: Store
    publish_queue: PublishQueue
    deployment_ids: dict[str, str]
    clock: Callable[[], datetime] = utc_now
    approval_ttl_seconds: int = 30 * 60
    desk_editor_enabled: bool = True
    model_timeout_seconds: float = 90.0
    trace: Callable[[str, str], None] = no_trace
    ledger: RunLedger = field(init=False)

    def __post_init__(self) -> None:
        self.ledger = RunLedger(self.store)


def file_tracer(path: Path) -> Callable[[str, str], None]:
    """Append which process ran which step, so a test can prove where each step executed."""

    def trace(executor_id: str, step: str) -> None:
        line = json.dumps({"pid": os.getpid(), "executor": executor_id, "step": step}, sort_keys=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    return trace


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

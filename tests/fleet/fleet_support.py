"""Shared helpers for the fleet tests. Everything here is synthetic and uses fake ports."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from agon.agents.fakes import FakeModel, PlantedStat
from agon.fleet.contracts import RunInput
from agon.synth.fixtures import seed_fixtures

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
SEED = 1  # a published seed: 1-1, two goals, one booking
OFFICER = "officer@media.example"
VISITOR = "console-visitor:session-1"
# Pinned by literal, never derived from the lock: a refit of agon.interpret changes this together
# with MODEL_SHA256 in tests/test_interpret.py, and every sealed bundle's deployment_ids with it.
INTERPRET_SOURCE = "agon.interpret/7ab5e12ab86e"
AF_REQUIRED = os.environ.get("AGON_REQUIRE_AF") == "1"


def require_agent_framework() -> None:
    """Skip without Agent Framework, except in the fleet CI job, where a skip would hide a failure."""
    if AF_REQUIRED:
        import agent_framework  # noqa: F401  - an ImportError here fails the job, as intended
    else:
        pytest.importorskip("agent_framework", reason="agent-framework-core is installed only in the fleet CI job")


def run_input(run_id: str = "run-1", seed: int = SEED, target: str = "feed") -> RunInput:
    return RunInput(run_id=run_id, match_ref={"source": "synth", "seed": seed, "match_index": 0}, target=target)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def wrong_number_fixture(seed: int = SEED) -> dict[str, Any]:
    """The first planted wrong count (not a goal minute: the fixtures write stoppage as minute+1)."""
    return next(w for w in seed_fixtures(seed)["wrong_numbers"] if w["stat"] != "goal_minute")


def planted_model(seed: int = SEED, fix_on_retry: bool = True) -> tuple[FakeModel, dict[str, Any]]:
    w = wrong_number_fixture(seed)
    sentence = w["sentences"]["en"]
    assert sentence.count(str(w["claimed"])) == 1, sentence
    plant = PlantedStat(
        sentence_id="sp",
        stat=w["stat"],
        subject=w["team"],
        template=sentence.replace(str(w["claimed"]), "{n}"),
        planted_value=w["claimed"],
        planted_event_ids=list(w["event_ids"]),
        fix_on_retry=fix_on_retry,
    )
    return FakeModel(plant=plant), w


def subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    parts = [str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


PYTHON = sys.executable

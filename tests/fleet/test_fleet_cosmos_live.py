"""LIVE: S1 on Cosmos DB. Kill and resume against the real `agon`/`checkpoints` container.

Runs only when AGON_LIVE_COSMOS_ENDPOINT is set, which only the main-only `fleet-cosmos` job
does (environment `azure`, OIDC, DefaultAzureCredential). Everywhere else it is skipped; in that
job AGON_REQUIRE_LIVE=1 turns a missing endpoint into a failure. Ports are fakes and the
governance store is a directory; only the checkpoints are in Cosmos DB, and this says so.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fleet_support import require_agent_framework

require_agent_framework()

from test_fleet_kill_resume import assert_split_across_processes, kill, resume, start  # noqa: E402

ENDPOINT = os.environ.get("AGON_LIVE_COSMOS_ENDPOINT", "")
if not ENDPOINT:
    if os.environ.get("AGON_REQUIRE_LIVE") == "1":
        raise RuntimeError("AGON_REQUIRE_LIVE=1 but AGON_LIVE_COSMOS_ENDPOINT is empty")
    pytest.skip("live Cosmos test: runs only in the fleet-cosmos job on main", allow_module_level=True)

COSMOS = ("--storage", "cosmos", "--cosmos-endpoint", ENDPOINT)


def test_live_cosmos_kill_then_resume_publishes_exactly_once(tmp_path: Path) -> None:
    run_dir = tmp_path / "live"
    proc, handoff = start(run_dir, storage=COSMOS)
    assert handoff["state"] == "awaiting_approval" and handoff["storage"] == "cosmos"
    assert not (run_dir / "checkpoints").exists(), "the checkpoint must be in Cosmos DB, not on disk"
    kill(proc)
    code, first = resume(run_dir, "b1", "--decision", "approve", "--confirm-all", storage=COSMOS)
    assert code == 0, first
    assert [o["status"] for o in first["outcomes"]] == ["publish_requested"]
    assert first["save_failures"] == []
    assert_split_across_processes(run_dir, handoff["pid"], first["pid"])
    code, replay = resume(run_dir, "b2", "--decision", "approve", "--confirm-all", storage=COSMOS)
    assert [o["code"] for o in replay["outcomes"]] == ["already_consumed"]
    assert len(list((run_dir / "publish-requests").glob("*.json"))) == 1


def test_live_cosmos_deleted_checkpoint_fails_the_resume(tmp_path: Path) -> None:
    import subprocess

    from fleet_support import PYTHON, subprocess_env

    run_dir = tmp_path / "live-deleted"
    proc, handoff = start(run_dir, storage=COSMOS)
    kill(proc)
    out = subprocess.run(
        [PYTHON, "-m", "agon.fleet.runner", "delete-checkpoint", "--run-dir", str(run_dir), *COSMOS],
        capture_output=True, text=True, env=subprocess_env(), timeout=300,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    code, report = resume(run_dir, "b1", "--decision", "approve", "--confirm-all", storage=COSMOS)
    assert code == 4
    assert any("No checkpoint found" in line for line in report["error_chain"]), report["error_chain"]
    assert list((run_dir / "publish-requests").glob("*.json")) == []

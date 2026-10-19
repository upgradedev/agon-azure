"""The approval survives process death: S1's question asked of the real W-fulltime graph.

Process A runs the workflow to its approval pause, proves the pause checkpoint holds the request,
writes handoff.json (what the Teams card would carry) and is then killed (SIGKILL on POSIX,
TerminateProcess on Windows). Process B is a fresh interpreter: it rebuilds the graph and resumes
from the checkpoint with the officer's decision. Nothing in A's memory is available to B.

Fake ports and file checkpoints here; tests/fleet/test_fleet_cosmos_live.py runs the same driver
against the live Cosmos DB checkpoints container.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from fleet_support import OFFICER, PYTHON, require_agent_framework, subprocess_env

require_agent_framework()

from agon.fleet.deps import read_trace  # noqa: E402
from agon.fleet.stores import FilePublishQueue, FileStore  # noqa: E402
from agon.governance import verifier  # noqa: E402

START_TIMEOUT_SECONDS = 300
PRE_APPROVAL = {
    "ingest", "interpret", "analyst", "writer", "triage", "factgate", "desk_editor", "localise_el", "localise_es",
    "localise_fr", "easy_read", "factgate_lang", "rights", "audio", "seal",
}


def start(run_dir: Path, mode: str = "hang", storage: tuple[str, ...] = ()) -> tuple[subprocess.Popen | None, dict[str, Any]]:
    cmd = [PYTHON, "-m", "agon.fleet.runner", "start", "--run-dir", str(run_dir), "--seed", "1",
           "--run-id", f"run-{run_dir.name}", "--grant-approver", OFFICER, "--mode", mode, *storage]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=subprocess_env())
    handoff_path = run_dir / "handoff.json"
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while not handoff_path.exists():
        if proc.poll() is not None:
            out, err = proc.communicate()
            if handoff_path.exists():
                break
            raise AssertionError(f"process A exited {proc.returncode} without a handoff\n{out}\n{err}")
        if time.monotonic() > deadline:
            proc.kill()
            raise AssertionError("process A did not reach the approval pause in time")
        time.sleep(0.2)
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if mode == "exit":
        proc.wait(timeout=60)
        assert proc.returncode == 0
        return None, handoff
    return proc, handoff


def kill(proc: subprocess.Popen | None) -> None:
    assert proc is not None, "hang mode returns the live process"
    proc.kill()
    proc.wait(timeout=60)
    assert proc.returncode != 0, "process A must die, not exit cleanly"


def resume(run_dir: Path, label: str, *extra: str, storage: tuple[str, ...] = ()) -> tuple[int, dict[str, Any]]:
    cmd = [PYTHON, "-m", "agon.fleet.runner", "resume", "--run-dir", str(run_dir), "--approver", OFFICER,
           "--label", label, *extra, *storage]
    out = subprocess.run(cmd, capture_output=True, text=True, env=subprocess_env(), timeout=600)
    report_path = run_dir / f"resume-{label}.json"
    assert report_path.exists(), out.stderr
    return out.returncode, json.loads(report_path.read_text(encoding="utf-8"))


def assert_split_across_processes(run_dir: Path, a_pid: int, b_pid: int) -> None:
    trace = read_trace(run_dir / "trace.ndjson")
    assert a_pid != b_pid
    assert {r["executor"] for r in trace if r["pid"] == a_pid} == PRE_APPROVAL | {"approval"}
    assert all(r["step"] == "request" for r in trace if r["executor"] == "approval" and r["pid"] == a_pid)
    from_b = [(r["executor"], r["step"]) for r in trace if r["pid"] == b_pid]
    assert ("approval", "response") in from_b
    assert not any(e in PRE_APPROVAL for e, _ in from_b), "B must not re-run any step before the approval"


def test_kill_then_resume_in_a_fresh_process_publishes_exactly_once(tmp_path: Path) -> None:
    run_dir = tmp_path / "kill"
    proc, handoff = start(run_dir)
    assert handoff["state"] == "awaiting_approval"
    sizes = [p.stat().st_size for p in (run_dir / "checkpoints").glob("*.json")]
    assert max(sizes) < 1_500_000
    kill(proc)

    code, first = resume(run_dir, "b1", "--decision", "approve", "--confirm-all")
    assert code == 0, first
    assert [o["status"] for o in first["outcomes"]] == ["publish_requested"]
    queue = FilePublishQueue(run_dir / "publish-requests")
    files = sorted((run_dir / "publish-requests").glob("*.json"))
    assert len(files) == 1
    before = files[0].read_bytes()
    assert queue.items()[0]["bundle_sha256"] == handoff["card"]["bundle_sha256"]
    assert_split_across_processes(run_dir, handoff["pid"], first["pid"])

    code, replay = resume(run_dir, "b2", "--decision", "approve", "--confirm-all")
    assert code == 0
    assert [o["code"] for o in replay["outcomes"]] == ["already_consumed"]
    assert sorted((run_dir / "publish-requests").glob("*.json")) == files and files[0].read_bytes() == before


def test_deleted_checkpoint_makes_the_resume_fail_and_nothing_is_published(tmp_path: Path) -> None:
    """Negative control: without the checkpoint there is nothing to resume, so the claim is real."""
    run_dir = tmp_path / "deleted"
    proc, handoff = start(run_dir)
    kill(proc)
    checkpoint = run_dir / "checkpoints" / f"{handoff['checkpoint_id']}.json"
    assert checkpoint.is_file()
    os.remove(checkpoint)
    code, report = resume(run_dir, "b1", "--decision", "approve", "--confirm-all")
    assert code == 4
    assert any("No checkpoint found" in line for line in report["error_chain"]), report["error_chain"]
    assert list((run_dir / "publish-requests").glob("*.json")) == []
    assert not any(r["step"] == "response" for r in read_trace(run_dir / "trace.ndjson"))


def test_changes_requested_after_a_clean_exit(tmp_path: Path) -> None:
    run_dir = tmp_path / "changes"
    _, handoff = start(run_dir, mode="exit")
    code, report = resume(run_dir, "b1", "--decision", "changes")
    assert code == 0 and [o["status"] for o in report["outcomes"]] == ["changes_requested"]
    assert list((run_dir / "publish-requests").glob("*.json")) == []
    code, late = resume(run_dir, "b2", "--decision", "approve", "--confirm-all", "--channel", "outlook")
    assert [o["code"] for o in late["outcomes"]] == ["already_consumed"]


def test_tampered_hash_across_processes_leaves_the_nonce_unspent(tmp_path: Path) -> None:
    run_dir = tmp_path / "tamper"
    proc, handoff = start(run_dir)
    kill(proc)
    code, bad = resume(run_dir, "b1", "--decision", "approve", "--confirm-all", "--tamper-hash")
    assert code == 0 and [o["code"] for o in bad["outcomes"]] == ["hash_mismatch"]
    nonce = FileStore(run_dir / "store").read(verifier.NONCES, handoff["card"]["nonce"])
    assert nonce is not None and nonce.body["status"] == "pending"
    code, good = resume(run_dir, "b2", "--decision", "approve", "--confirm-all")
    assert [o["status"] for o in good["outcomes"]] == ["publish_requested"]

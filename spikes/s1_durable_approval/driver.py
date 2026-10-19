"""Spike S1 driver: prove an approval request survives process death, with negative controls.

Scenarios, each in its own directory with its own checkpoint store:
  1 exit_then_resume       A exits normally while the request is pending; fresh B approves.
  2 kill_then_resume       A is killed (SIGKILL or TerminateProcess) while pending; fresh B
                           approves; a second fresh B replays the same decision and must be
                           refused because publish.json is create-only.
  3 tampered_hash          B delivers a decision naming a different hash; no publish.
  4 deleted_checkpoint     the pause checkpoint file is deleted; B's resume must fail for that
                           reason ("No checkpoint found"), and nothing is published.

Prints one PASS or FAIL line per check, wall time per scenario and the total wall time, then
``S1 RESULT: PASS`` or ``S1 RESULT: FAIL``. Exit code 0 only on PASS.
"""

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

from s1_bundle import PUBLISHED, REFUSED, REFUSED_ALREADY_PUBLISHED, read_trace, sha256_hex

HERE = Path(__file__).resolve().parent
EXPECTED_FRAMEWORK_VERSION = "1.22.0"
PROCESS_TIMEOUT_S = 240.0
KILL_METHOD = "SIGKILL" if os.name == "posix" else "TerminateProcess"


class Checks:
    """Collects named checks for one scenario and prints them as they happen."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.rows: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: Any = "") -> bool:
        self.rows.append({"check": name, "ok": bool(ok), "detail": str(detail)})
        print(f"{'PASS' if ok else 'FAIL'} {self.scenario}: {name} {detail}".rstrip(), flush=True)
        return bool(ok)

    @property
    def passed(self) -> bool:
        return bool(self.rows) and all(row["ok"] for row in self.rows)


def _spawn(script: str, args: list[str], run_dir: Path, tag: str) -> subprocess.Popen[bytes]:
    """Start a fresh interpreter. Output goes to files so a full pipe can never block a child."""
    out = open(run_dir / f"{tag}.out", "wb")  # noqa: SIM115 - closed by the child exiting
    err = open(run_dir / f"{tag}.err", "wb")  # noqa: SIM115
    try:
        return subprocess.Popen(
            [sys.executable, str(HERE / script), *args],
            cwd=str(HERE),
            stdout=out,
            stderr=err,
        )
    finally:
        out.close()
        err.close()


def _last_json(path: Path) -> dict[str, Any] | None:
    """Return the last JSON object line a child printed, or None."""
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
    return None


def _file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _publish_files(run_dir: Path) -> list[str]:
    return sorted(p.name for p in run_dir.glob("publish*"))


def run_a(run_dir: Path, mode: str) -> dict[str, Any]:
    """Run process A. In hang mode, wait for the handoff and kill A while it is still alive."""
    started = time.perf_counter()
    proc = _spawn("process_a.py", ["--run-dir", str(run_dir), "--mode", mode], run_dir, "a")
    alive_at_kill = None
    if mode == "exit":
        returncode = proc.wait(timeout=PROCESS_TIMEOUT_S)
    else:
        deadline = time.monotonic() + PROCESS_TIMEOUT_S
        while not (run_dir / "handoff.json").exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        alive_at_kill = proc.poll() is None
        if alive_at_kill:
            proc.kill()
        returncode = proc.wait(timeout=30)
    handoff_path = run_dir / "handoff.json"
    handoff = json.loads(handoff_path.read_text(encoding="utf-8")) if handoff_path.exists() else None
    return {
        "returncode": returncode,
        "alive_at_kill": alive_at_kill,
        "handoff": handoff,
        "seconds": round(time.perf_counter() - started, 3),
    }


def run_b(run_dir: Path, tag: str, extra: list[str] | None = None) -> dict[str, Any]:
    """Run process B in a new interpreter and return its exit code and printed report."""
    started = time.perf_counter()
    args = ["--run-dir", str(run_dir), "--label", tag, *(extra or [])]
    proc = _spawn("process_b.py", args, run_dir, tag)
    returncode = proc.wait(timeout=PROCESS_TIMEOUT_S)
    return {
        "returncode": returncode,
        "report": _last_json(run_dir / f"{tag}.out"),
        "seconds": round(time.perf_counter() - started, 3),
    }


def _statuses(b: dict[str, Any]) -> list[str]:
    report = b.get("report") or {}
    return [o.get("status") for o in report.get("outcomes", [])]


def _check_a_paused(c: Checks, a: dict[str, Any], mode: str) -> bool:
    if mode == "exit":
        c.add("A exited normally", a["returncode"] == 0, f"returncode={a['returncode']}")
    else:
        c.add("A was alive when killed", a["alive_at_kill"] is True, f"alive={a['alive_at_kill']}")
        expected = -signal.SIGKILL if os.name == "posix" else None
        killed = a["returncode"] == expected if expected is not None else a["returncode"] != 0
        c.add(f"A died by {KILL_METHOD}", killed, f"returncode={a['returncode']}")
    return c.add("A wrote handoff with a verified pause checkpoint", a["handoff"] is not None, a["seconds"])


def _check_single_publish(c: Checks, run_dir: Path, handoff: dict[str, Any]) -> None:
    files = _publish_files(run_dir)
    c.add("exactly one publish file", files == ["publish.json"], files)
    if files != ["publish.json"]:
        return
    published = json.loads((run_dir / "publish.json").read_text(encoding="utf-8"))
    recomputed = sha256_hex(published["canonical_json"])
    c.add(
        "published hash == sealed hash == hash recomputed from published bytes",
        published["bundle_sha256"] == handoff["bundle_sha256"] == recomputed,
        recomputed,
    )


def _check_trace(c: Checks, run_dir: Path, a_pid: int, resume_pids: list[int]) -> None:
    trace = read_trace(run_dir)
    pids_for = lambda ex, step: sorted({t["pid"] for t in trace if t["executor"] == ex and t["step"] == step})  # noqa: E731
    c.add("draft ran only in A", pids_for("draft", "handler") == [a_pid], pids_for("draft", "handler"))
    c.add("seal ran only in A", pids_for("seal", "handler") == [a_pid], pids_for("seal", "handler"))
    c.add("approval request issued only in A", pids_for("approval", "request") == [a_pid])
    c.add(
        "approval response handled only in fresh B process(es)",
        pids_for("approval", "response") == sorted(set(resume_pids)) and a_pid not in resume_pids,
        pids_for("approval", "response"),
    )


def scenario_exit_then_resume(run_dir: Path) -> Checks:
    c = Checks("exit_then_resume")
    a = run_a(run_dir, "exit")
    if not _check_a_paused(c, a, "exit"):
        return c
    b = run_b(run_dir, "b")
    c.add("B completed", b["returncode"] == 0, f"returncode={b['returncode']} seconds={b['seconds']}")
    c.add("B published once", _statuses(b) == [PUBLISHED], _statuses(b))
    b_pid = (b["report"] or {}).get("pid", -1)
    c.add("B is a different process from A", b_pid not in (-1, a["handoff"]["pid"]), f"A={a['handoff']['pid']} B={b_pid}")
    _check_single_publish(c, run_dir, a["handoff"])
    _check_trace(c, run_dir, a["handoff"]["pid"], [b_pid])
    return c


def scenario_kill_then_resume(run_dir: Path) -> Checks:
    c = Checks("kill_then_resume")
    a = run_a(run_dir, "hang")
    if not _check_a_paused(c, a, "hang"):
        return c
    b = run_b(run_dir, "b")
    c.add("B completed", b["returncode"] == 0, f"returncode={b['returncode']} seconds={b['seconds']}")
    c.add("B published once", _statuses(b) == [PUBLISHED], _statuses(b))
    before = _file_sha256(run_dir / "publish.json")
    replay = run_b(run_dir, "b_replay")
    c.add("replay completed", replay["returncode"] == 0, f"returncode={replay['returncode']}")
    c.add("replay refused by the create-only write", _statuses(replay) == [REFUSED_ALREADY_PUBLISHED], _statuses(replay))
    c.add("publish.json bytes unchanged by the replay", before is not None and before == _file_sha256(run_dir / "publish.json"))
    _check_single_publish(c, run_dir, a["handoff"])
    pids = [(b["report"] or {}).get("pid", -1), (replay["report"] or {}).get("pid", -1)]
    _check_trace(c, run_dir, a["handoff"]["pid"], pids)
    return c


def scenario_tampered_hash(run_dir: Path) -> Checks:
    c = Checks("tampered_hash")
    a = run_a(run_dir, "exit")
    if not _check_a_paused(c, a, "exit"):
        return c
    b = run_b(run_dir, "b", ["--tamper-hash"])
    c.add("B completed", b["returncode"] == 0, f"returncode={b['returncode']}")
    c.add("B refused the mismatched hash", _statuses(b) == [REFUSED], _statuses(b))
    c.add("nothing published", _publish_files(run_dir) == [], _publish_files(run_dir))
    return c


def scenario_deleted_checkpoint(run_dir: Path) -> Checks:
    c = Checks("deleted_checkpoint")
    a = run_a(run_dir, "exit")
    if not _check_a_paused(c, a, "exit"):
        return c
    checkpoint_file = run_dir / "checkpoints" / a["handoff"]["checkpoint_file"]
    checkpoint_file.unlink()
    c.add("pause checkpoint deleted", not checkpoint_file.exists(), checkpoint_file.name)
    b = run_b(run_dir, "b")
    chain = (b["report"] or {}).get("error_chain", [])
    c.add("B resume failed", b["returncode"] == 4, f"returncode={b['returncode']}")
    c.add("failure reason is the missing checkpoint", any("No checkpoint found" in m for m in chain), chain[:2])
    c.add("nothing published", _publish_files(run_dir) == [], _publish_files(run_dir))
    responses = [t for t in read_trace(run_dir) if t["step"] == "response"]
    c.add("response handler never ran", responses == [], len(responses))
    return c


SCENARIOS: list[tuple[str, Callable[[Path], Checks]]] = [
    ("exit_then_resume", scenario_exit_then_resume),
    ("kill_then_resume", scenario_kill_then_resume),
    ("tampered_hash", scenario_tampered_hash),
    ("deleted_checkpoint", scenario_deleted_checkpoint),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=None, help="parent for run directories (kept)")
    args = parser.parse_args()

    total_started = time.perf_counter()
    try:
        version = metadata.version("agent-framework-core")
    except metadata.PackageNotFoundError:
        version = "not installed"
    print(f"python {platform.python_version()} on {platform.system()} {platform.machine()}", flush=True)
    print(f"agent-framework-core {version}; kill method {KILL_METHOD}", flush=True)
    if version != EXPECTED_FRAMEWORK_VERSION:
        print(f"FAIL setup: agent-framework-core must be {EXPECTED_FRAMEWORK_VERSION}, found {version}", flush=True)
        print("S1 RESULT: FAIL", flush=True)
        return 1

    if args.work_dir is not None:
        args.work_dir.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="s1-", dir=args.work_dir))
    print(f"run directories under {base}", flush=True)

    summary: dict[str, Any] = {"framework": version, "kill_method": KILL_METHOD, "scenarios": []}
    for name, fn in SCENARIOS:
        run_dir = base / name
        run_dir.mkdir()
        started = time.perf_counter()
        try:
            checks = fn(run_dir)
        except Exception as exc:  # noqa: BLE001 - a crash in the harness is a FAIL, never a skip
            checks = Checks(name)
            checks.add("scenario raised", False, f"{type(exc).__name__}: {exc}")
        seconds = round(time.perf_counter() - started, 3)
        print(f"{'PASS' if checks.passed else 'FAIL'} scenario {name}: wall time {seconds} s", flush=True)
        summary["scenarios"].append({"name": name, "passed": checks.passed, "seconds": seconds, "checks": checks.rows})

    total = round(time.perf_counter() - total_started, 3)
    passed = all(s["passed"] for s in summary["scenarios"])
    summary.update({"passed": passed, "total_wall_seconds": total})
    (base / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"total wall time {total} s for {len(SCENARIOS)} scenarios", flush=True)
    print(f"S1 RESULT: {'PASS' if passed else 'FAIL'}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

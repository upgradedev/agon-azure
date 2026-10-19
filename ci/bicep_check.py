"""Compile Bicep files and fail on any error or warning.

Why: `bicep build` exits 0 on warnings, and a warning is where an unknown
property name (BCP037), an API version with no type data (BCP081) or an unused
parameter shows up. Those are the mistakes a careful read misses and a deploy
finds an hour later. So every diagnostic line counts as a failure here. A
warning that is genuinely harmless gets a `#disable-next-line <rule>` with a
reason beside it, never a wider gate. infra/bicepconfig.json also raises the
chosen linter rules to errors, so `bicep build` itself exits non-zero on them.

Files are given by path. Zero files is a failure. With --expect-fail every file
must FAIL; CI uses that as the negative control on a fixture with a known
warning, which proves the gate has teeth.

Usage:
    python ci/bicep_check.py --runner az infra/main.bicep infra/rbac.bicep
    python ci/bicep_check.py --runner bicep --bicep ~/.azure/bin/bicep infra/main.bicep
    python ci/bicep_check.py --runner az --expect-fail ci/fixtures/bicep_warning.bicep
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

# "path(line,col) : Warning rule-or-code: message [url]" and the Error form.
DIAGNOSTIC = re.compile(r"\)\s*:\s*(Warning|Error)\s+([A-Za-z0-9-]+)\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class BuildResult:
    """What one compile produced."""

    returncode: int
    stderr: str


Runner = Callable[[Path], BuildResult]


def diagnostics(stderr: str) -> list[str]:
    """Every error or warning line the compiler printed."""
    return [line.strip() for line in stderr.splitlines() if DIAGNOSTIC.search(line)]


def verdict(result: BuildResult) -> tuple[bool, list[str]]:
    """(ok, reasons). ok only when the exit code is 0 and there is no diagnostic."""
    reasons = diagnostics(result.stderr)
    if result.returncode != 0 and not reasons:
        tail = result.stderr.strip().splitlines()[-1:] or ["no output"]
        reasons = [f"exit code {result.returncode}: {tail[0]}"]
    elif result.returncode != 0:
        reasons.append(f"exit code {result.returncode}")
    return (not reasons, reasons)


def command_runner(runner: str, bicep: str) -> Runner:
    """Build a runner that calls `az bicep build` or a standalone bicep binary."""

    def run(path: Path) -> BuildResult:
        if runner == "az":
            cmd = ["az", "bicep", "build", "--file", str(path), "--stdout", "--only-show-errors"]
        else:
            cmd = [bicep, "build", str(path), "--stdout"]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        return BuildResult(proc.returncode, proc.stderr)

    return run


def check(paths: Sequence[Path], run: Runner, expect_fail: bool) -> tuple[str, bool]:
    """Compile every path and report. Returns (report text, gate passed)."""
    lines: list[str] = []
    mismatches = 0
    for path in paths:
        if not path.is_file():
            lines.append(f"FAIL  {path}: not a file")
            mismatches += 1
            continue
        ok, reasons = verdict(run(path))
        if expect_fail:
            good = not ok
            state = "FAILED as required" if good else "PASSED but a failure was required: the gate has no teeth"
        else:
            good = ok
            state = "clean" if ok else "diagnostics found"
        lines.append(f"{'PASS' if good else 'FAIL'}  {path}: {state}")
        lines.extend(f"      {reason}" for reason in reasons)
        mismatches += 0 if good else 1
    passed = bool(paths) and mismatches == 0
    reason = "" if paths else " (zero files given: an empty check fails)"
    mode = "expect-fail" if expect_fail else "expect-clean"
    lines.append(f"bicep files checked: {len(paths)}; mode: {mode}; mismatches: {mismatches}; "
                 f"verdict: {'PASS' if passed else 'FAIL'}{reason}")
    return "\n".join(lines), passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--runner", choices=("az", "bicep"), default="az")
    parser.add_argument("--bicep", default="bicep", help="standalone binary for --runner bicep")
    parser.add_argument("--expect-fail", action="store_true", help="every file must fail (negative control)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    text, ok = check(args.files, command_runner(args.runner, args.bicep), args.expect_fail)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

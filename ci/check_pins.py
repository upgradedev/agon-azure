"""Fail when any requirements file line is not an exact == pin.

Why: the Agent Framework packages change behaviour week to week (one release
changed the checkpoint format), so every dependency CI installs must resolve to
one exact version. Ranges, wildcards such as ==2.7.*, editable installs and bare
names are all rejected.

Files are selected by PATH from git (tracked plus untracked, minus ignored), never
by searching their text: any file named requirements*.txt, and any .txt file
inside a directory named requirements. The list comes from `git ls-files -z`
(see git_paths.py), because the plain form C-quotes non-ASCII names and such a
file once failed the .txt test and was never read. Finding zero such files is a
failure, because a gate that scanned nothing proves nothing, and so is a listed
file that cannot be read.

Allowed lines: blank lines, comments, "-r file" or "-c file" includes (the
included file is checked on its own), and exact pins with optional extras,
environment markers and --hash options.

Usage: python ci/check_pins.py [--root DIR]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath

from git_paths import list_repo_paths

EXACT_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[A-Za-z0-9._,\s-]+\])?"
    r"\s*==\s*(?P<version>[A-Za-z0-9.+!*-]+)"
    r"\s*(?:;\s*(?P<marker>.+))?$"
)
INCLUDE = re.compile(r"^(?:-r|-c|--requirement|--constraint)(?:\s+|=)\S+$")
HASH_OPTION = re.compile(r"\s+--hash[=\s]\S+")


def is_requirements_path(path: str) -> bool:
    """True for requirements*.txt anywhere, or any .txt under a requirements/ dir."""
    p = PurePosixPath(path)
    if p.suffix != ".txt":
        return False
    return p.name.startswith("requirements") or "requirements" in p.parent.parts


def candidate_files(root: Path) -> list[str]:
    """Repo-relative paths of requirements files, selected from git by path."""
    return [p for p in list_repo_paths(root).files if is_requirements_path(p)]


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations, keeping the number of the first physical line."""
    joined: list[tuple[int, str]] = []
    buffer, start = "", 0
    for number, raw in enumerate(text.splitlines(), start=1):
        if not buffer:
            start = number
        if raw.rstrip().endswith("\\"):
            buffer += raw.rstrip()[:-1] + " "
            continue
        joined.append((start, buffer + raw))
        buffer = ""
    if buffer:
        joined.append((start, buffer))
    return joined


def _content(line: str) -> str:
    """The requirement text of a line without its comment and --hash options."""
    content = re.sub(r"(^|\s)#.*$", "", line).strip()
    return HASH_OPTION.sub("", " " + content).strip()


def line_problem(line: str) -> str | None:
    """Return why a requirements line is not acceptable, or None when it is."""
    content = _content(line)
    if not content or INCLUDE.match(content):
        return None
    if content.startswith("-"):
        return "option lines are not pins"
    match = EXACT_PIN.match(content)
    if not match:
        return "not an exact name==version pin"
    if "*" in match.group("version"):
        return "wildcard version is not exact"
    return None


def check_file(path: Path) -> tuple[list[tuple[int, str, str]], int]:
    """Problems in one file as (line number, line text, reason), plus its pin count."""
    problems = []
    pins = 0
    for number, line in logical_lines(path.read_text(encoding="utf-8")):
        reason = line_problem(line)
        if reason:
            problems.append((number, line.strip(), reason))
        elif EXACT_PIN.match(_content(line)):
            pins += 1
    return problems, pins


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on any requirements line without an exact pin.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        # A non-ASCII path printed to a Windows pipe must not crash the gate.
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    files = candidate_files(args.root)
    failures = 0
    pins = 0
    for rel in files:
        try:
            problems, count = check_file(args.root / rel)
        except UnicodeDecodeError as exc:
            print(f"{rel}: not valid UTF-8 ({exc.reason} at byte {exc.start})")
            failures += 1
            continue
        except OSError:
            print(f"{rel}: listed by git but not a readable regular file")
            failures += 1
            continue
        for number, text, reason in problems:
            print(f"{rel}:{number}: {reason}: {text}")
        failures += len(problems)
        pins += count
    print(f"requirements files scanned: {len(files)} ({', '.join(files) or 'none'}); "
          f"exact pins: {pins}; problems: {failures}")
    if not files:
        print("FAIL: zero requirements files found; an empty scan proves nothing")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

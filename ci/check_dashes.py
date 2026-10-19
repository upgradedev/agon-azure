"""Fail on any em dash (U+2014) or en dash (U+2013) in a text file of the repo.

Why: judge-facing copy is written in the owner's voice without these dashes, and
the rule only holds if a machine checks it on every push.

Files are selected by PATH from git (tracked plus untracked, minus ignored), never
by searching their text, so a refactored file cannot silently fall out of scope.
The list comes from `git ls-files -z` (see git_paths.py), because the plain form
C-quotes non-ASCII names and those files were once skipped unseen.

Each listed path lands in exactly one printed count. Binary files are skipped by
extension and gitlinks are skipped as submodules; each has its own count so a
skip can never hide inside another. A listed path that is neither of those and
is not a readable regular file is a failure, and so is a text file that is not
valid UTF-8, because a dash in a legacy encoding would otherwise go unseen.
Scanning zero files is a failure.

This file names the characters only through escapes, so it passes its own check.

Usage: python ci/check_dashes.py [--root DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath

from git_paths import list_repo_paths

FORBIDDEN = {"\u2014": "em dash U+2014", "\u2013": "en dash U+2013"}

BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".avif",
    ".mp3", ".mp4", ".wav", ".ogg", ".m4a", ".webm", ".mov",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".whl",
    ".pdf", ".pyc", ".so", ".dll", ".exe", ".bin", ".parquet",
})


def is_binary_path(path: str) -> bool:
    """Binary by extension only; the decision never depends on file contents."""
    return PurePosixPath(path).suffix.lower() in BINARY_SUFFIXES


def scan_text(text: str) -> list[tuple[int, int, str]]:
    """Every forbidden dash as (line, column, name), 1-based."""
    hits = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for col, char in enumerate(line, start=1):
            if char in FORBIDDEN:
                hits.append((line_no, col, FORBIDDEN[char]))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on em or en dashes in repo text files.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        # A non-ASCII path printed to a Windows pipe must not crash the gate.
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    paths = list_repo_paths(args.root)
    for link in paths.gitlinks:
        print(f"gitlink skipped: {link}")

    scanned = binary = unreadable = problems = 0
    for rel in paths.files:
        if is_binary_path(rel):
            binary += 1
            continue
        path = args.root / rel
        try:
            if not path.is_file():
                raise OSError("not a regular file")
            data = path.read_bytes()
        except OSError:
            print(f"{rel}: listed by git but not a readable regular file")
            unreadable += 1
            problems += 1
            continue
        scanned += 1
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            print(f"{rel}: not valid UTF-8 ({exc.reason} at byte {exc.start})")
            problems += 1
            continue
        for line_no, col, name in scan_text(text):
            print(f"{rel}:{line_no}:{col}: {name}")
            problems += 1

    print(f"text files scanned: {scanned}; binary skipped: {binary}; gitlinks skipped: "
          f"{len(paths.gitlinks)}; unreadable: {unreadable}; problems: {problems}")
    if scanned == 0:
        print("FAIL: zero files scanned; an empty scan proves nothing")
        return 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

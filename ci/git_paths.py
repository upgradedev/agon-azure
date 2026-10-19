"""List a repository's files from git by path, safe for any file name.

Why this exists: plain `git ls-files` C-quotes every non-ASCII path while
core.quotepath is true, which is the git default and what CI runners use. A
Greek file name then comes back as a quoted octal string that matches no file on
disk, and a gate that selects files by path silently skips it. The -z form is
never quoted, so both gates read their file list through this module.

Paths are read as bytes and decoded with the filesystem encoding, because text
mode would decode git's UTF-8 with the locale codepage on Windows.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

GITLINK_MODE = "160000"


@dataclass(frozen=True)
class RepoPaths:
    """Every path git reports for a work tree, split by what a gate can read.

    files: tracked entries that are not gitlinks, plus untracked files that are
    not ignored. gitlinks: submodule entries and untracked nested repositories,
    which hold no text of this repository.
    """

    files: tuple[str, ...]
    gitlinks: tuple[str, ...]


def _git_z(root: Path, *args: str) -> list[str]:
    """Run git with NUL-separated output and return the decoded entries."""
    out = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout
    return [os.fsdecode(chunk) for chunk in out.split(b"\0") if chunk]


def list_repo_paths(root: Path) -> RepoPaths:
    """Tracked plus untracked paths, minus ignored ones, with gitlinks set apart."""
    files: set[str] = set()
    gitlinks: set[str] = set()
    for entry in _git_z(root, "ls-files", "-z", "--cached", "--stage"):
        meta, _, path = entry.partition("\t")
        (gitlinks if meta.split(" ", 1)[0] == GITLINK_MODE else files).add(path)
    for path in _git_z(root, "ls-files", "-z", "--others", "--exclude-standard"):
        # git lists an untracked nested repository as one entry ending in "/".
        if path.endswith("/"):
            gitlinks.add(path.rstrip("/"))
        else:
            files.add(path)
    return RepoPaths(tuple(sorted(files)), tuple(sorted(gitlinks)))

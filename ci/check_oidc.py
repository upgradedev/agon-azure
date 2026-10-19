"""Fail when a workflow that pull requests can trigger is able to mint an OIDC token.

Why: a pull_request workflow runs the YAML of the pull request's own branch,
before anyone reviews it. The repo's only Azure identity holds Contributor on the
resource group, so a pull request workflow that can request an id-token is a
Contributor login that nobody approved. This check stops that shape from coming
back by accident, for example by re-adding pull_request to deploy.yml.

It is an accident guard, not a security boundary: a hostile branch can edit this
file or ci.yml in its own diff. The boundary is on the Entra side, where the
pull_request federated subject is removed (docs/OPERATIONS.md, step 4).

Rule, for every workflow file under .github/workflows (selected by PATH from git,
never by searching text): if any trigger name starts with "pull_request" (this
covers pull_request_target and the review events), the file must declare a
top-level permissions block, and must not contain "id-token: write" or
"permissions: write-all" anywhere. A file with no readable top-level "on:" key is
a failure, and so is scanning zero workflow files.

The parser is line based on purpose (no third-party YAML here). It reads the
three trigger forms GitHub accepts: a scalar (on: push), a flow list
(on: [push, pull_request]) and a block map or block list under on:.

Usage: python ci/check_oidc.py [--root DIR]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath

from git_paths import list_repo_paths

WORKFLOW_DIR = PurePosixPath(".github/workflows")
ON_KEY = re.compile(r"""^(?:on|"on"|'on')\s*:\s*(?P<rest>.*)$""")
TOP_LEVEL_PERMISSIONS = re.compile(r"^permissions\s*:")
GRANTS = (
    re.compile(r"^\s*id-token\s*:\s*['\"]?write['\"]?\s*(?:#.*)?$"),
    re.compile(r"^\s*permissions\s*:\s*['\"]?write-all['\"]?\s*(?:#.*)?$"),
)


def is_workflow(path: str) -> bool:
    """A .yml or .yaml file directly inside .github/workflows."""
    pure = PurePosixPath(path)
    return pure.parent == WORKFLOW_DIR and pure.suffix in {".yml", ".yaml"}


def _strip_comment(text: str) -> str:
    return text.split(" #", 1)[0].split("\t#", 1)[0].strip() if not text.startswith("#") else ""


def triggers(lines: list[str]) -> list[str] | None:
    """Trigger names of the top-level on: key, or None when there is no such key."""
    for index, line in enumerate(lines):
        match = ON_KEY.match(line)
        if not match:
            continue
        rest = _strip_comment(match.group("rest"))
        if rest:
            return [name.strip().strip("'\"") for name in rest.strip("[]").split(",") if name.strip()]
        names: list[str] = []
        level: int | None = None
        for child in lines[index + 1 :]:
            body = child.split("#", 1)[0] if child.lstrip().startswith("#") else child
            if not body.strip():
                continue
            indent = len(child) - len(child.lstrip(" "))
            if indent == 0:
                break
            if level is None:
                level = indent
            if indent != level:
                continue
            item = _strip_comment(child.strip())
            if item.startswith("- "):
                names.append(item[2:].strip().strip("'\""))
            elif ":" in item:
                names.append(item.split(":", 1)[0].strip().strip("'\""))
        return names
    return None


def check_file(rel: str, text: str) -> list[str]:
    """Problems found in one workflow file, as printable lines."""
    lines = text.splitlines()
    names = triggers(lines)
    if names is None:
        return [f"{rel}: no top-level on: key found, cannot tell what triggers it"]
    pr_names = [name for name in names if name.startswith("pull_request")]
    if not pr_names:
        return []
    problems: list[str] = []
    if not any(TOP_LEVEL_PERMISSIONS.match(line) for line in lines):
        problems.append(
            f"{rel}: triggered by {', '.join(pr_names)} with no top-level permissions block"
        )
    for number, line in enumerate(lines, start=1):
        if any(grant.match(line) for grant in GRANTS):
            problems.append(
                f"{rel}:{number}: triggered by {', '.join(pr_names)} and grants a token: {line.strip()}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when a pull request workflow can mint an OIDC token.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()

    selected = [path for path in list_repo_paths(root).files if is_workflow(path)]
    problems: list[str] = []
    pr_triggered = 0
    for rel in selected:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{rel}: unreadable: {exc}")
            continue
        names = triggers(text.splitlines()) or []
        pr_triggered += any(name.startswith("pull_request") for name in names)
        problems.extend(check_file(rel, text))

    for line in problems:
        print(line)
    print(
        f"workflow files scanned: {len(selected)}; pull request triggered: {pr_triggered}; "
        f"problems: {len(problems)}"
    )
    if not selected:
        print("no workflow files found under .github/workflows; a gate that scanned nothing proves nothing")
        return 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

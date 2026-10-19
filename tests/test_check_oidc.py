"""Proof that check_oidc fails when a pull request workflow can mint an OIDC token.

Fixtures are written into throwaway git repositories under tmp_path, so the
checker selects them by path exactly as it selects the real workflows. One case
replays the deploy.yml shape that review found (pull_request plus id-token write).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import check_oidc

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "check_oidc.py"
REPO = Path(__file__).resolve().parents[1]

SAFE_PR = """name: ci
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

MAIN_ONLY_OIDC = """name: deploy
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  deploy:
    permissions:
      id-token: write
    runs-on: ubuntu-latest
    steps:
      - run: echo deploy
"""

PR_WITH_OIDC = """name: deploy
on:
  push:
    branches: [main]
  pull_request:
    paths:
      - "infra/**"
permissions:
  contents: read
jobs:
  what-if:
    permissions:
      contents: read
      id-token: write
    runs-on: ubuntu-latest
    steps:
      - run: echo what-if
"""


def git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return tmp_path


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, encoding="utf-8"
    )


def test_safe_workflows_pass_and_print_counts(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {
        ".github/workflows/ci.yml": SAFE_PR,
        ".github/workflows/deploy.yml": MAIN_ONLY_OIDC,
    })
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "workflow files scanned: 2; pull request triggered: 1; problems: 0" in result.stdout


def test_the_reviewed_deploy_shape_fails_with_file_and_line(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {".github/workflows/deploy.yml": PR_WITH_OIDC})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert ".github/workflows/deploy.yml:14: triggered by pull_request and grants a token" in result.stdout


@pytest.mark.parametrize("on_line", [
    "on: pull_request_target",
    "on: [push, pull_request]",
    'on: ["pull_request"]',
    '"on": pull_request  # quoted key',
    "on:\n  - push\n  - pull_request",
])
def test_every_trigger_form_is_read(tmp_path: Path, on_line: str) -> None:
    text = f"name: x\n{on_line}\npermissions:\n  id-token: write\njobs: {{}}\n"
    root = git_repo(tmp_path, {".github/workflows/x.yaml": text})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "grants a token" in result.stdout


def test_flow_map_grant_is_caught(tmp_path: Path) -> None:
    text = """name: x
on: pull_request
permissions:
  contents: read
jobs:
  a:
    permissions: { contents: read, id-token: write }
    runs-on: ubuntu-latest
"""
    root = git_repo(tmp_path, {".github/workflows/x.yml": text})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "x.yml:7: triggered by pull_request and grants a token" in result.stdout


def test_commented_grant_is_ignored(tmp_path: Path) -> None:
    text = """name: x
on: pull_request
permissions:
  contents: read
  # id-token: write
jobs: {}
"""
    root = git_repo(tmp_path, {".github/workflows/x.yml": text})
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_write_all_counts_as_a_grant(tmp_path: Path) -> None:
    text = "name: x\non: pull_request\npermissions: write-all\njobs: {}\n"
    root = git_repo(tmp_path, {".github/workflows/x.yml": text})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "x.yml:3: triggered by pull_request and grants a token: permissions: write-all" in result.stdout


def test_pull_request_without_top_level_permissions_fails(tmp_path: Path) -> None:
    text = "name: x\non: [pull_request]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
    root = git_repo(tmp_path, {".github/workflows/x.yml": text})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "with no top-level permissions block" in result.stdout


def test_missing_on_key_fails(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {".github/workflows/x.yml": "name: x\njobs: {}\n"})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "no top-level on: key found" in result.stdout


def test_zero_workflow_files_fails(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {"README.md": "# x\n", "workflows/ci.yml": SAFE_PR})
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "workflow files scanned: 0" in result.stdout


def test_nested_keys_are_not_read_as_triggers() -> None:
    names = check_oidc.triggers(PR_WITH_OIDC.splitlines())
    assert names == ["push", "pull_request"]


def test_this_repo_passes() -> None:
    result = run(REPO)
    assert result.returncode == 0, result.stdout
    assert "problems: 0" in result.stdout

"""Proof that check_pins fails on loose pins and passes on exact ones.

Each case runs the real script against a throwaway git repository, so the
selection-by-path logic is exercised too, not just the line parser.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import check_pins

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "check_pins.py"


def git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)


GOOD = (
    "# test tooling\n"
    "pytest==9.0.2\n"
    "uvicorn[standard]==0.30.1\n"
    'colorama==0.4.6 ; sys_platform == "win32"\n'
    "-r base.txt\n"
    "pluggy==1.6.0 \\\n"
    "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
)


def test_exact_pins_pass(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {"requirements/dev.txt": GOOD, "requirements/base.txt": "packaging==26.0\n"})
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "requirements files scanned: 2" in result.stdout
    assert "exact pins: 5" in result.stdout


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("pytest>=9.0", "not an exact name==version pin"),
        ("pytest~=9.0.2", "not an exact name==version pin"),
        ("pytest", "not an exact name==version pin"),
        ("azure-ai-projects==2.7.*", "wildcard version is not exact"),
        ("-e .", "option lines are not pins"),
        ("--extra-index-url https://example.test/simple", "option lines are not pins"),
        ("git+https://example.test/pkg.git", "not an exact name==version pin"),
    ],
)
def test_loose_line_fails(tmp_path: Path, line: str, reason: str) -> None:
    root = git_repo(tmp_path, {"requirements-ci.txt": f"pytest==9.0.2\n{line}\n"})
    result = run(root)
    assert result.returncode == 1
    assert f"requirements-ci.txt:2: {reason}" in result.stdout


def test_zero_requirements_files_fails(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {"notes.txt": "pytest>=1\n"})
    result = run(root)
    assert result.returncode == 1
    assert "zero requirements files found" in result.stdout


def test_ignored_file_is_not_selected(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {
        ".gitignore": "build/\n",
        "build/requirements.txt": "pytest>=1\n",
        "requirements.txt": "pytest==9.0.2\n",
    })
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "requirements files scanned: 1 (requirements.txt)" in result.stdout


def test_path_selection_rule() -> None:
    assert check_pins.is_requirements_path("requirements.txt")
    assert check_pins.is_requirements_path("requirements-dev.txt")
    assert check_pins.is_requirements_path("requirements/dev.txt")
    assert check_pins.is_requirements_path("services/api/requirements/prod.txt")
    assert not check_pins.is_requirements_path("docs/notes.txt")
    assert not check_pins.is_requirements_path("requirements.md")

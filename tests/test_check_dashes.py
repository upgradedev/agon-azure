"""Proof that check_dashes fails on a dash and passes on clean text.

The dashes are written as escapes and only reach disk inside a throwaway git
repository under tmp_path, so this test file passes the repo's own dash check.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import check_dashes

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "check_dashes.py"
EM, EN = "\u2014", "\u2013"


def git_repo(tmp_path: Path, files: dict[str, bytes | str], add: bool = True) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8", newline="\n")
        else:
            path.write_bytes(content)
    if add:
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)


def test_clean_repo_passes_and_prints_counts(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {"README.md": "# Agon\nplain hyphen - is fine\n", "logo.png": b"\x89PNG\x00\x01"})
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "text files scanned: 1; skipped (binary or missing): 1; problems: 0" in result.stdout


def test_em_dash_fails_with_file_line_column(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {"docs/a.md": f"line one\nfull time {EM} 2 to 1\n"})
    result = run(root)
    assert result.returncode == 1
    assert "docs/a.md:2:11: em dash U+2014" in result.stdout


def test_en_dash_fails(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {"src/x.py": f'RANGE = "55{EN}70"\n'})
    result = run(root)
    assert result.returncode == 1
    assert "src/x.py:1:12: en dash U+2013" in result.stdout


def test_untracked_new_file_is_still_scanned(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {"new.txt": f"draft {EM} text\n"}, add=False)
    result = run(root)
    assert result.returncode == 1
    assert "new.txt:1:7: em dash U+2014" in result.stdout


def test_non_utf8_text_fails(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {"legacy.txt": b"caf\xe9 \x97 old encoding\n"})
    result = run(root)
    assert result.returncode == 1
    assert "legacy.txt: not valid UTF-8" in result.stdout


def test_zero_files_fails(tmp_path: Path) -> None:
    root = git_repo(tmp_path, {})
    result = run(root)
    assert result.returncode == 1
    assert "zero files scanned" in result.stdout


def test_binary_decision_uses_the_path_only() -> None:
    assert check_dashes.is_binary_path("media/clip.MP4")
    assert not check_dashes.is_binary_path("ci/readiness.py")

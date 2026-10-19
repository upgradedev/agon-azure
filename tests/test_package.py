"""The package imports from its installed location, not from a stray path."""

from __future__ import annotations

import tomllib
from pathlib import Path

import agon

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_package_imports_from_src_layout() -> None:
    assert Path(agon.__file__).resolve().parent.name == "agon"
    assert Path(agon.__file__).resolve().parent.parent.name == "src"


def test_package_version_matches_pyproject() -> None:
    # One version string, read in two places: the manifest prints __version__,
    # packaging reads pyproject. They must never drift apart.
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert agon.__version__ == project["project"]["version"]

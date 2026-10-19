"""The package imports from its installed location, not from a stray path."""

from __future__ import annotations

from pathlib import Path

import agon


def test_package_imports_from_src_layout() -> None:
    assert agon.__version__ == "0.0.0"
    assert Path(agon.__file__).resolve().parent.name == "agon"
    assert Path(agon.__file__).resolve().parent.parent.name == "src"

"""The referee package is unreachable from every other module under src/agon."""

from __future__ import annotations

import os

import check_referee_isolation as gate

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _expected_files() -> int:
    count = 0
    agon = os.path.join(SRC, "agon")
    for dirpath, _dirs, names in os.walk(agon):
        if os.path.relpath(dirpath, agon).split(os.sep)[0] == "referee":
            continue
        count += sum(1 for n in names if n.endswith(".py"))
    return count


def test_no_module_outside_referee_imports_it() -> None:
    scanned, found = gate.scan(SRC)
    assert found == []
    # Selected by path: every .py outside referee, and at least the synth package.
    assert scanned == _expected_files() >= 10
    assert gate.main([SRC]) == 0


def test_referee_exists_and_imports_synth_one_way() -> None:
    from agon.referee import labels

    assert labels.labels_from_log.__module__ == "agon.referee.labels"


def _plant(tmp_path, rel: str, source: str) -> str:
    root = tmp_path / "src"
    (root / "agon" / "synth").mkdir(parents=True, exist_ok=True)
    (root / "agon" / "referee").mkdir(parents=True, exist_ok=True)
    (root / "agon" / "__init__.py").write_text("", encoding="utf-8")
    (root / "agon" / "referee" / "__init__.py").write_text("import agon.synth\n", encoding="utf-8")
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return str(root)


PLANTED = [
    ("agon/synth/a.py", "import agon.referee\n"),
    ("agon/synth/b.py", "import agon.referee.labels as L\n"),
    ("agon/synth/c.py", "from agon.referee.labels import labels_from_log\n"),
    ("agon/synth/d.py", "from agon import referee\n"),
    ("agon/synth/e.py", "from ..referee import labels\n"),
    ("agon/synth/f.py", "from .. import referee\n"),
    ("agon/mcp/g.py", "import importlib\nm = importlib.import_module('agon.referee.labels')\n"),
    ("agon/synth/h.py", "x = __import__('agon.referee')\n"),
    ("agon/synth/i.py", "import importlib\nm = importlib.import_module('.referee', 'agon')\n"),
    ("agon/synth/j.py", "from importlib import import_module\nm = import_module('..referee.labels', package='agon.synth')\n"),
    # A sibling directory whose name merely starts with "referee" is not the referee package.
    ("agon/referee_tools/k.py", "import agon.referee\n"),
]


def test_each_planted_import_is_caught(tmp_path) -> None:
    """Negative control: every import form the gate claims to catch, planted once each."""
    for i, (rel, source) in enumerate(PLANTED):
        root = _plant(tmp_path / str(i), rel, source)
        scanned, found = gate.scan(root)
        assert scanned >= 2, (rel, scanned)
        assert len(found) == 1, (rel, source, found)
        assert gate.main([root]) == 1


def test_clean_lookalikes_pass(tmp_path) -> None:
    root = _plant(
        tmp_path,
        "agon/synth/clean.py",
        '"""Docs may say the agon.referee package exists; prose is not an import."""\n'
        "from . import referee_notes\n"
        "import agon.synth.rng\n",
    )
    scanned, found = gate.scan(root)
    assert found == [] and scanned >= 2


def test_empty_scan_fails(tmp_path) -> None:
    (tmp_path / "agon").mkdir()
    assert gate.main([str(tmp_path)]) == 1

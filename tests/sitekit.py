"""Where the served site comes from, for the tests that read the page a visitor gets.

The live site is the built console: console/dist after `npm run build` and the prerender, with
web/runs and web/data-fixture copied in by the prerender and stamped by ci/stamp_web.py. Only the
console workflow builds it (no local npm), and it runs the page tests with AGON_SITE_DIR set to the
stamped copy it rehearses the deploy on.

Without AGON_SITE_DIR the page tests skip with that reason; with it set, a missing page is a
failure, never a skip, so the console workflow cannot pass without reading the built HTML.

The retired hand-written page in web/ no longer ships. Its index.html states the design preview
labels in static markup, so it stays a fixture for the checker's rules only with the design preview
as its only run: vanilla_fixture() copies it that way, whatever web/runs lists first.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agon.governance.seal import canonical_utf8

ENV = "AGON_SITE_DIR"
WEB = Path(__file__).resolve().parent.parent / "web"


def served() -> Path:
    site = os.environ.get(ENV)
    if not site:
        pytest.skip(f"the served page is built by the console workflow, which runs this test on it with {ENV}")
    path = Path(site)
    assert (path / "index.html").is_file(), f"{ENV}={site} holds no index.html"
    return path


def served_page() -> str:
    return (served() / "index.html").read_text(encoding="utf-8")


def vanilla_fixture(target: Path) -> Path:
    """A copy of web/ whose runs folder holds only the design preview (sample-s1)."""
    shutil.copytree(WEB, target)
    runs = target / "runs"
    index = json.loads((runs / "index.json").read_text(encoding="utf-8"))
    keep = [row for row in index["runs"] if row["provenance"] == "design_preview"]
    for row in index["runs"]:
        if row not in keep:
            (runs / row["file"]).unlink()
            shutil.rmtree(runs / row["run_id"])
    index["runs"] = keep
    (runs / "index.json").write_bytes(canonical_utf8(index) + b"\n")
    return target

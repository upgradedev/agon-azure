"""Where the served site comes from, for the tests that read the page a visitor gets.

The live site is the built console: console/dist after `npm run build` and the prerender, with
web/runs and web/data copied in by the prerender and stamped by ci/stamp_web.py. Only the console
workflow builds it (no local npm), and it runs the page tests with AGON_SITE_DIR=console/dist.

Without AGON_SITE_DIR the page tests skip with that reason; with it set, a missing page is a
failure, never a skip, so the console workflow cannot pass without reading the built HTML.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ENV = "AGON_SITE_DIR"


def served() -> Path:
    site = os.environ.get(ENV)
    if not site:
        pytest.skip(f"the served page is built by the console workflow, which runs this test on it with {ENV}")
    path = Path(site)
    assert (path / "index.html").is_file(), f"{ENV}={site} holds no index.html"
    return path


def served_page() -> str:
    return (served() / "index.html").read_text(encoding="utf-8")

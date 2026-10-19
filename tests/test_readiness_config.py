"""The shipped readiness config (ci/readiness.json) against what the web lane ships.

Why a separate file from tests/test_readiness.py: that file proves the gate's
rules; this one proves the config's markers are the right ones. Every marker
must occur in the bytes the deploy step actually writes, and the gate must fail
when a marker's subject is missing, checked through readiness.check_target with
a fake fetcher (no network).
"""

from __future__ import annotations

from functools import lru_cache

import pytest

import readiness
from agon.webdata import build_web_data, manifest_bytes

PUBLISHED_SEEDS = (1, 2, 3)  # pinned with PUBLISHED_SEEDS in .github/workflows/deploy.yml


def _targets() -> dict[str, readiness.Target]:
    targets, _, _ = readiness.load_config(readiness.DEFAULT_CONFIG)
    return {t.name: t for t in targets}


@lru_cache(maxsize=None)
def _shipped_manifest() -> str:
    manifest, _ = build_web_data(list(PUBLISHED_SEEDS))
    return manifest_bytes(manifest).decode("ascii")


def _fetcher(url: str, body: str):
    def fetch(asked: str) -> readiness.FetchResult:
        assert asked == url
        return readiness.FetchResult(200, (url,), body)

    return fetch


def test_demo_data_markers_name_every_world_file() -> None:
    markers = _targets()["demo-data"].markers
    for seed in PUBLISHED_SEEDS:
        assert f'"world":"{seed}.world.json"' in markers


def test_every_demo_data_marker_is_in_the_manifest_the_deploy_writes() -> None:
    body = _shipped_manifest()
    for marker in _targets()["demo-data"].markers:
        assert marker in body, marker


def test_demo_data_target_fails_on_a_manifest_without_world_files() -> None:
    target = _targets()["demo-data"]
    target = readiness.Target(target.name, "https://agon.example/data/manifest.json", list(target.markers))
    good = _shipped_manifest()
    rules = readiness.build_rules()
    ok = readiness.check_target(target, _fetcher(target.url, good), rules)
    assert all(f.ok for f in ok), [f for f in ok if not f.ok]
    old_contract = good.replace('"world":', '"x":')
    bad = readiness.check_target(target, _fetcher(target.url, old_contract), rules)
    failed = [f.check for f in bad if not f.ok]
    assert any("world" in check for check in failed), failed


@pytest.mark.parametrize("seed", PUBLISHED_SEEDS)
def test_shipped_world_files_pass_the_live_gate_rules(seed: int) -> None:
    """The live gate does not fetch world files, so run its forbidden-string rules over them here."""
    _, payloads = build_web_data([seed])
    hits = readiness.forbidden_hits(payloads[f"{seed}.world.json"].decode("utf-8"), readiness.build_rules())
    assert hits == []

"""The shipped readiness config (ci/readiness.json) against what the web lane ships.

Why a separate file from tests/test_readiness.py: that file proves the gate's
rules; this one proves the config's markers are the right ones. Every marker
must occur in the bytes the deploy step actually writes, and the gate must fail
when a marker's subject is missing, checked through readiness.check_target with
a fake fetcher (no network).

The page the demo target fetches is the built console (tests/sitekit.py): the
console workflow builds it and runs these tests on it with AGON_SITE_DIR.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest

import readiness
import web_check
from agon.webdata import build_web_data, manifest_bytes
from sitekit import served_page

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


# ---------------------------------------------------------------- demo-run and the design preview label

ROOT = Path(__file__).resolve().parent.parent
RUNS_INDEX = ROOT / "web" / "runs" / "index.json"
LABEL = "Design preview: sample run written by hand, not produced by the pipeline."
LABEL_PREFIXES = ('id="wall-label">', 'id="approve-label">', 'id="recap-label">')


def _rows() -> list[dict]:
    return json.loads(RUNS_INDEX.read_text(encoding="utf-8"))["runs"]


def _demo_target() -> readiness.Target:
    target = _targets()["demo"]
    return readiness.Target(target.name, "https://agon.example/", list(target.markers), scan_assets=False)


def test_demo_label_markers_are_the_labels_of_the_run_the_page_shows_by_default() -> None:
    """The anchored markers are exactly what ci/web_check.py requires on the page for the first run in
    web/runs/index.json: the design preview label today, the recorded run's labels once it is first."""
    shown = web_check.default_run(RUNS_INDEX.parent.parent)
    assert shown is not None and shown[1]["run_id"] == _rows()[0]["run_id"]
    expected = [marker for _, marker in web_check.page_label_markers(shown[1])]
    labels = [m for m in _targets()["demo"].markers if m.startswith(LABEL_PREFIXES)]
    assert labels == expected
    if shown[1]["provenance"] == "design_preview":
        assert labels == [marker for _, marker in web_check.PAGE_LABEL_MARKERS]
        assert "demo-recorded" not in _targets() and "demo-recorded-bundle" not in _targets()


def test_demo_recorded_targets_follow_a_recorded_run_shown_by_default() -> None:
    first = _rows()[0]
    if first["provenance"] != "pipeline":
        pytest.skip("no recorded run is the default yet; the demo-run and demo-bundle targets cover the design preview")
    run = json.loads((RUNS_INDEX.parent / first["file"]).read_text(encoding="utf-8"))
    recorded, bundle = _targets()["demo-recorded"], _targets()["demo-recorded-bundle"]
    assert recorded.url == f"https://__AGON_SWA_HOSTNAME__/runs/{first['file']}"
    sha = run["bundle"]["sha256"]
    run_id = run["run_id"]
    assert recorded.markers == [f'"run_id":"{run_id}"', '"provenance":"pipeline"', run["label"], f'"approved_bundle_sha256":"{sha}"']
    body = (RUNS_INDEX.parent / first["file"]).read_text(encoding="utf-8")
    assert all(marker in body for marker in recorded.markers)
    assert bundle.url == f"https://__AGON_SWA_HOSTNAME__/runs/{run_id}/manifest.json"
    manifest = (RUNS_INDEX.parent / run_id / "manifest.json").read_text(encoding="utf-8")
    assert all(marker in manifest for marker in bundle.markers)


def test_demo_run_target_names_the_design_preview_in_the_runs_index() -> None:
    """The hand-written sample stays listed as the fallback, first or after a recorded run."""
    target = _targets()["demo-run"]
    previews = [row["run_id"] for row in _rows() if row["provenance"] == "design_preview"]
    assert previews == ["sample-s1"]
    assert target.url == f"https://__AGON_SWA_HOSTNAME__/runs/{previews[0]}.json"
    assert f'"run_id":"{previews[0]}"' in target.markers


def test_demo_run_target_passes_on_the_shipped_run_and_fails_without_the_label() -> None:
    target = _targets()["demo-run"]
    target = readiness.Target(target.name, "https://agon.example/runs/sample-s1.json", list(target.markers))
    body = (RUNS_INDEX.parent / "sample-s1.json").read_text(encoding="utf-8")
    rules = readiness.build_rules()
    assert all(f.ok for f in readiness.check_target(target, _fetcher(target.url, body), rules))
    unlabelled = body.replace(LABEL, "Sample run.")
    failed = [f.check for f in readiness.check_target(target, _fetcher(target.url, unlabelled), rules) if not f.ok]
    assert any("Design preview" in check for check in failed), failed


def test_demo_target_passes_on_the_served_page() -> None:
    rules = readiness.build_rules()
    target = _demo_target()
    results = readiness.check_target(target, _fetcher(target.url, served_page()), rules)
    assert all(f.ok for f in results), [f for f in results if not f.ok]


def test_demo_target_fails_on_a_page_without_the_label() -> None:
    target = _demo_target()
    page = served_page()
    stripped = page
    for marker in [m for m in target.markers if m.startswith(LABEL_PREFIXES)]:
        prefix = next(p for p in LABEL_PREFIXES if marker.startswith(p))
        stripped = stripped.replace(marker, prefix + "Sample run.")
    assert stripped != page
    rules = readiness.build_rules()
    failed = [f.check for f in readiness.check_target(target, _fetcher(target.url, stripped), rules) if not f.ok]
    assert len(failed) == 3, failed


@pytest.mark.parametrize("element", ["wall-label", "approve-label", "recap-label"])
def test_demo_target_fails_when_one_screen_loses_its_label(element: str) -> None:
    """Strip only one copy of the label; the other copies stay on the page and must not save it."""
    target = _demo_target()
    page = served_page()
    start = page.index(f'id="{element}">')
    end = page.index("</p>", start)
    stripped = page[:start] + f'id="{element}">Sample run.' + page[end:]
    others = [m for m in target.markers if m.startswith(LABEL_PREFIXES) and not m.startswith(f'id="{element}">')]
    assert len(others) == 2 and all(m in stripped for m in others)
    rules = readiness.build_rules()
    failed = [f.check for f in readiness.check_target(target, _fetcher(target.url, stripped), rules) if not f.ok]
    assert failed and all(element in check for check in failed), failed


def test_demo_bundle_target_passes_on_the_sealed_manifest_and_fails_on_the_fallback_page() -> None:
    target = _targets()["demo-bundle"]
    assert target.url == "https://__AGON_SWA_HOSTNAME__/runs/sample-s1/manifest.json"
    target = readiness.Target(target.name, "https://agon.example/runs/sample-s1/manifest.json", list(target.markers))
    body = (RUNS_INDEX.parent / "sample-s1" / "manifest.json").read_text(encoding="utf-8")
    rules = readiness.build_rules()
    assert all(f.ok for f in readiness.check_target(target, _fetcher(target.url, body), rules))
    assert not all(f.ok for f in readiness.check_target(target, _fetcher(target.url, served_page()), rules))

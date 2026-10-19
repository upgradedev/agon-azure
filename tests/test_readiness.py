"""Proof that the readiness gate fails on broken inputs and passes on a good one.

Every broken fixture is built here at test time, never committed, because a
committed fixture holding a dash or a real league name would trip the repo's own
gates. Each negative test asserts the failure REASON, not only the exit code.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import readiness

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "readiness.py"
URL = "https://agon.example.test/"
GOOD_BODY = (
    "<html><body><h1>Agon</h1><p>Match report, synthetic data only. "
    "Kickoff 2026/10/19 at 19:00, page 3/4 of the replay.</p>"
    '<script src="/app.js"></script></body></html>'
)
GOOD_JS = "console.log('Agon replay ready');"


def run_gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def write_case(tmp_path: Path, index: dict, markers: list[str] | None = None,
               user_gated: list[dict] | None = None, targets: list[dict] | None = None) -> tuple[Path, Path]:
    """Write DIR/index.json plus a config that points one target at URL."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "index.json").write_text(json.dumps(index), encoding="utf-8")
    config = tmp_path / "readiness.json"
    if targets is None:
        targets = [{"name": "demo", "url": URL, "markers": markers or ["Agon"], "scan_assets": True}]
    config.write_text(json.dumps({"targets": targets, "user_gated": user_gated or []}), encoding="utf-8")
    return fixtures, config


def good_index(body: str = GOOD_BODY, js: str = GOOD_JS, status: int = 200, chain: list[str] | None = None) -> dict:
    return {
        URL: {"status": status, "body": body, "chain": chain or []},
        URL + "app.js": {"status": 200, "body": js},
    }


def test_good_fixture_passes(tmp_path: Path) -> None:
    fixtures, config = write_case(tmp_path, good_index())
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 0, result.stdout
    assert "verdict: PASS" in result.stdout
    assert "targets checked: 1" in result.stdout
    assert "asset https://agon.example.test/app.js" in result.stdout


def test_missing_marker_fails(tmp_path: Path) -> None:
    fixtures, config = write_case(tmp_path, good_index(), markers=["Agon", "Approved by the media officer"])
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    assert "marker 'Approved by the media officer'" in result.stdout
    assert "missing from body" in result.stdout


@pytest.mark.parametrize(
    ("injected", "reason"),
    [
        ("Full time \u2014 2 to 1", "em dash U+2014"),
        ("minutes 55\u201370", "en dash U+2013"),
        ("A compliant workflow", "the word 'compliant'"),
        ("Our build scores 9.4/10", "self-score pattern"),
        ("Rated 87 / 100 by us", "self-score pattern"),
        ("As seen in the Premier League", "real-world name 'Premier League'"),
        ("a nike swoosh", "real-world name 'Nike'"),
    ],
)
def test_forbidden_string_in_page_fails(tmp_path: Path, injected: str, reason: str) -> None:
    body = GOOD_BODY.replace("</body>", f"<p>{injected}</p></body>")
    fixtures, config = write_case(tmp_path, good_index(body=body))
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    assert reason in result.stdout


def test_forbidden_string_in_shipped_script_fails(tmp_path: Path) -> None:
    fixtures, config = write_case(tmp_path, good_index(js="const label = 'Full time \u2014 final';"))
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    asset_lines = [ln for ln in result.stdout.splitlines() if "asset https://agon.example.test/app.js" in ln]
    assert len(asset_lines) == 1 and asset_lines[0].startswith("FAIL")
    assert "em dash U+2014" in asset_lines[0]


@pytest.mark.parametrize(("status", "reason"), [(503, "503"), (404, "404"), (401, "auth wall"), (403, "auth wall")])
def test_non_200_fails(tmp_path: Path, status: int, reason: str) -> None:
    fixtures, config = write_case(tmp_path, good_index(status=status))
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    assert "FAIL" in result.stdout and reason in result.stdout


def test_missing_offline_fixture_fails(tmp_path: Path) -> None:
    fixtures, config = write_case(tmp_path, {})
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    assert "no offline fixture" in result.stdout


def test_host_change_after_redirect_fails(tmp_path: Path) -> None:
    chain = ["https://login.other.test/sso", URL]
    fixtures, config = write_case(tmp_path, good_index(chain=chain))
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    assert "redirected to https://login.other.test/sso" in result.stdout


def test_https_downgrade_fails(tmp_path: Path) -> None:
    fixtures, config = write_case(tmp_path, good_index(chain=["http://agon.example.test/"]))
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    assert "https downgraded to http" in result.stdout


def test_zero_targets_fails_even_with_user_gated_items(tmp_path: Path) -> None:
    gated = [{"item": "Public video", "manual_step": "owner uploads it"}]
    fixtures, config = write_case(tmp_path, {}, user_gated=gated, targets=[])
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    assert "zero targets checked" in result.stdout
    assert "USER-GATED  Public video: owner uploads it" in result.stdout


def test_user_gated_items_are_listed_but_not_counted_as_passes(tmp_path: Path) -> None:
    gated = [{"item": "Public video", "manual_step": "owner uploads it"}]
    fixtures, config = write_case(tmp_path, good_index(), user_gated=gated)
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 0
    assert "user-gated: 1 (not a pass)" in result.stdout
    passes = result.stdout.count("\nPASS ")
    assert f"passed: {passes};" in result.stdout


def test_target_without_url_fails_as_not_deployed(tmp_path: Path) -> None:
    fixtures, config = write_case(tmp_path, {}, targets=[{"name": "demo", "url": "", "markers": ["Agon"]}])
    result = run_gate("--offline", str(fixtures), "--config", str(config))
    assert result.returncode == 1
    assert "nothing deployed" in result.stdout


def test_shipped_config_fails_live_until_a_demo_url_exists() -> None:
    targets, gated, _ = readiness.load_config(readiness.DEFAULT_CONFIG)
    demo = [t for t in targets if t.name == "demo"]
    assert demo and gated
    if demo[0].url:
        pytest.skip("a demo URL is configured; the live readiness run covers it")
    findings = readiness.check_target(demo[0], lambda url: pytest.fail("must not fetch"), readiness.build_rules())
    assert [f.ok for f in findings] == [False]


def test_pinned_defaults_cannot_be_removed_by_config() -> None:
    # Fixture held apart from the code: if a default is deleted or edited in
    # readiness.py, this list no longer matches and the test fails.
    pinned_literals = {"\u2014", "\u2013"}
    pinned_terms = {"Premier League", "UEFA", "FIFA", "Olympiacos", "Panathinaikos", "Nike", "Olympic"}
    assert pinned_literals <= {literal for _, literal in readiness.DEFAULT_LITERALS}
    assert pinned_terms <= set(readiness.DEFAULT_REAL_TERMS)
    rules = readiness.build_rules(extra_terms=[])
    labels = [label for label, _ in rules]
    assert "the word 'compliant'" in labels and "self-score pattern" in labels


def test_self_score_rule_ignores_dates_and_paths() -> None:
    rules = readiness.build_rules()
    clean = "Kickoff 2026/10/19, see /reports/10 and page 3/4, minute 71, 10/10/2026"
    hits = readiness.forbidden_hits(clean, rules)
    assert not [h for h in hits if "self-score" in h], hits


class _Handler(BaseHTTPRequestHandler):
    """Tiny local server: /ok is a good page, /missing is 404, /hop leaves the host."""

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        port = self.server.server_address[1]
        if self.path == "/ok":
            self._send(200, GOOD_BODY.replace('<script src="/app.js"></script>', ""))
        elif self.path == "/missing":
            self._send(404, "not here")
        elif self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/ok")
            self.end_headers()
        else:
            self._send(500, "unexpected")

    def _send(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture()
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_live_fetch_passes_on_a_real_200(local_server: str, tmp_path: Path) -> None:
    empty = tmp_path / "none.json"
    result = run_gate("--url", f"{local_server}/ok", "--marker", "Agon", "--config", str(empty))
    assert result.returncode == 0, result.stdout
    assert "verdict: PASS" in result.stdout


def test_live_fetch_fails_on_a_real_404(local_server: str, tmp_path: Path) -> None:
    empty = tmp_path / "none.json"
    result = run_gate("--url", f"{local_server}/missing", "--marker", "Agon", "--config", str(empty))
    assert result.returncode == 1
    assert "404" in result.stdout


def test_live_fetch_records_a_host_change_in_the_redirect_chain(local_server: str) -> None:
    fetched = readiness.live_fetch(f"{local_server}/hop", timeout=5)
    assert any("localhost" in hop for hop in fetched.chain[1:]), fetched
    findings = readiness.check_target(
        readiness.Target("hop", f"{local_server}/hop", ["Agon"]),
        lambda url: fetched,
        readiness.build_rules(),
    )
    host = [f for f in findings if f.check == "host unchanged"][0]
    assert not host.ok and "localhost" in host.detail

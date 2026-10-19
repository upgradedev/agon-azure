"""Readiness gate for the judge-facing surfaces of Agon (spec metric M22).

Why this exists: an uptime check that only reads a status code stays green over a
page that lies. This gate fetches every configured URL and fails on any of:

* a status other than 200 (401 and 403 are called out, because the judge path
  must be login-free);
* a change of host anywhere in the redirect chain, or an https to http downgrade;
* a missing required body marker;
* a forbidden string in the body or in same-host script assets: em and en
  dashes, the word "compliant", a self-score such as "9/10", and real league,
  club and brand words.

User-gated items (things only the owner can do, such as a public video upload)
are printed in their own table and never count as a pass. A run that checks
zero targets fails, because an empty gate proves nothing.

Only the standard library is used, so the gate runs anywhere without installs.

Usage:
    python ci/readiness.py --live                      # targets from ci/readiness.json
    python ci/readiness.py --url https://x --marker Agon
    python ci/readiness.py --offline tests/fixtures/x  # fixture index, no network
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable

DEFAULT_CONFIG = Path(__file__).with_name("readiness.json")
USER_AGENT = "agon-readiness/0.1 (+https://github.com/upgradedev/agon-azure)"

EM_DASH = "\u2014"
EN_DASH = "\u2013"

# Pinned defaults. A config file may ADD terms but can never remove these, so the
# gate cannot be widened by editing JSON. tests/test_readiness.py pins the same
# literals in a fixture, so a silent edit here fails a test.
DEFAULT_LITERALS: tuple[tuple[str, str], ...] = (
    ("em dash U+2014", EM_DASH),
    ("en dash U+2013", EN_DASH),
)
DEFAULT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("the word 'compliant'", r"(?i)compliant"),
    # A self-score: one or two digits, optional decimals, then /10 or /100, with
    # no digit, slash or dot glued on either side, so 2026/10/19 does not match.
    ("self-score pattern", r"(?<![\d/.])\d{1,2}(?:\.\d+)?\s*/\s*100?(?![\d/])"),
)
DEFAULT_REAL_TERMS: tuple[str, ...] = (
    "Premier League", "Premiership", "EPL", "La Liga", "LaLiga", "Serie A",
    "Bundesliga", "Ligue 1", "Eredivisie", "Champions League", "Europa League",
    "Conference League", "UEFA", "FIFA", "Super League Greece", "Olympiacos",
    "Olympiakos", "Panathinaikos", "PAOK", "AEK", "Aris Thessaloniki",
    "Manchester United", "Manchester City", "Arsenal", "Liverpool", "Chelsea",
    "Tottenham", "Real Madrid", "Barcelona", "Bayern", "Juventus", "Olympic",
    "Olympia", "Nike", "Opta", "Sky Sports", "BBC Sport",
)


@dataclass(frozen=True)
class FetchResult:
    """What one GET produced: final status, every URL visited, and the body."""

    status: int
    chain: tuple[str, ...]
    body: str
    error: str = ""


@dataclass
class Target:
    """One URL to check, with the markers its body must contain."""

    name: str
    url: str
    markers: list[str] = field(default_factory=list)
    scan_assets: bool = False


@dataclass
class UserGated:
    """An item only a person can complete; printed, never counted as a pass."""

    item: str
    manual_step: str


@dataclass
class Finding:
    """One row of the result table."""

    target: str
    check: str
    ok: bool
    detail: str


Fetcher = Callable[[str], FetchResult]


class _RecordingRedirects(urllib.request.HTTPRedirectHandler):
    """Records every hop so an A to B to A chain is still seen as a host change."""

    def __init__(self) -> None:
        super().__init__()
        self.hops: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        self.hops.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def live_fetch(url: str, timeout: float = 20.0) -> FetchResult:
    """GET a URL with urllib, following redirects and recording each hop."""
    recorder = _RecordingRedirects()
    opener = urllib.request.build_opener(recorder)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            chain = (url, *recorder.hops)
            if response.geturl() != chain[-1]:
                chain = (*chain, response.geturl())
            return FetchResult(response.status, chain, raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return FetchResult(exc.code, (url, *recorder.hops), body)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return FetchResult(0, (url, *recorder.hops), "", error=str(exc))


def offline_fetcher(fixture_dir: Path) -> Fetcher:
    """Build a fetcher that answers from DIR/index.json instead of the network.

    index.json maps a URL to {"status": int, "chain": [urls after the first],
    "body": str} or {"body_file": name}. A URL with no entry is status 0, so a
    missing fixture fails rather than being skipped.
    """
    index_path = fixture_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))

    def fetch(url: str) -> FetchResult:
        entry = index.get(url)
        if entry is None:
            return FetchResult(0, (url,), "", error=f"no offline fixture for {url}")
        if "body_file" in entry:
            body = (fixture_dir / entry["body_file"]).read_text(encoding="utf-8")
        else:
            body = entry.get("body", "")
        chain = (url, *entry.get("chain", []))
        return FetchResult(int(entry.get("status", 0)), chain, body)

    return fetch


class _ScriptSources(HTMLParser):
    """Collects script src and modulepreload href values from an HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.sources.append(values["src"] or "")
        if tag == "link" and values.get("rel") == "modulepreload" and values.get("href"):
            self.sources.append(values["href"] or "")


def script_assets(base_url: str, html: str) -> list[str]:
    """Absolute URLs of the scripts a page ships, in document order, deduplicated."""
    parser = _ScriptSources()
    parser.feed(html)
    seen: list[str] = []
    for src in parser.sources:
        absolute = urllib.parse.urljoin(base_url, src)
        if absolute not in seen:
            seen.append(absolute)
    return seen


def build_rules(extra_terms: Iterable[str] = ()) -> list[tuple[str, re.Pattern[str]]]:
    """Compile the forbidden-string rules: pinned defaults plus any extra terms."""
    rules: list[tuple[str, re.Pattern[str]]] = [
        (label, re.compile(re.escape(literal))) for label, literal in DEFAULT_LITERALS
    ]
    rules += [(label, re.compile(pattern)) for label, pattern in DEFAULT_PATTERNS]
    for term in (*DEFAULT_REAL_TERMS, *extra_terms):
        word = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
        rules.append((f"real-world name '{term}'", re.compile(word, re.IGNORECASE)))
    return rules


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def forbidden_hits(body: str, rules: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    """Describe every forbidden-rule match as 'label at line N'."""
    hits: list[str] = []
    for label, pattern in rules:
        match = pattern.search(body)
        if match:
            hits.append(f"{label} at line {_line_of(body, match.start())}")
    return hits


def _host(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme.lower(), (parts.netloc or "").lower())


def check_target(
    target: Target, fetch: Fetcher, rules: list[tuple[str, re.Pattern[str]]]
) -> list[Finding]:
    """Run every check for one target and return one finding per check."""
    findings: list[Finding] = []
    if not target.url:
        return [Finding(target.name, "url configured", False, "no URL yet: nothing deployed")]

    result = fetch(target.url)
    if result.status == 200:
        findings.append(Finding(target.name, "status 200", True, "200"))
    elif result.status in (401, 403):
        findings.append(Finding(target.name, "status 200", False,
                                f"{result.status}: auth wall, the judge path must be login-free"))
    elif result.status == 0:
        findings.append(Finding(target.name, "status 200", False, f"no response: {result.error}"))
    else:
        findings.append(Finding(target.name, "status 200", False, str(result.status)))

    start_scheme, start_host = _host(target.url)
    moved = [hop for hop in result.chain[1:] if _host(hop)[1] != start_host]
    downgraded = start_scheme == "https" and any(_host(h)[0] != "https" for h in result.chain[1:])
    if moved:
        findings.append(Finding(target.name, "host unchanged", False, f"redirected to {moved[0]}"))
    elif downgraded:
        findings.append(Finding(target.name, "host unchanged", False, "https downgraded to http"))
    else:
        findings.append(Finding(target.name, "host unchanged", True, f"{len(result.chain) - 1} redirects"))

    for marker in target.markers:
        present = marker in result.body
        findings.append(Finding(target.name, f"marker '{marker}'", present,
                                "present" if present else "missing from body"))

    hits = forbidden_hits(result.body, rules)
    findings.append(Finding(target.name, "forbidden strings", not hits,
                            "none" if not hits else "; ".join(hits)))

    if target.scan_assets and result.status == 200:
        for asset in script_assets(result.chain[-1], result.body):
            if _host(asset)[1] != start_host:
                continue
            fetched = fetch(asset)
            asset_hits = forbidden_hits(fetched.body, rules)
            ok = fetched.status == 200 and not asset_hits
            detail = f"status {fetched.status}" + (f"; {'; '.join(asset_hits)}" if asset_hits else "")
            findings.append(Finding(target.name, f"asset {asset}", ok, detail))
    return findings


def load_config(path: Path) -> tuple[list[Target], list[UserGated], list[str]]:
    """Read targets, user-gated items and extra forbidden terms from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    targets = [
        Target(t["name"], t.get("url", ""), list(t.get("markers", [])), bool(t.get("scan_assets", False)))
        for t in data.get("targets", [])
    ]
    gated = [UserGated(g["item"], g["manual_step"]) for g in data.get("user_gated", [])]
    extra = [str(term) for term in data.get("extra_forbidden_terms", [])]
    return targets, gated, extra


def render(findings: list[Finding], gated: list[UserGated], targets_checked: int) -> tuple[str, bool]:
    """Format the result tables and decide pass or fail."""
    lines = ["AUTOMATED CHECKS", f"{'result':<6}  {'target':<16}  {'check':<28}  detail"]
    for f in findings:
        lines.append(f"{'PASS' if f.ok else 'FAIL':<6}  {f.target:<16}  {f.check:<28}  {f.detail}")
    failed = sum(1 for f in findings if not f.ok)
    passed = len(findings) - failed
    lines.append("")
    lines.append("USER-GATED (manual, never counted as a pass)")
    if gated:
        for g in gated:
            lines.append(f"USER-GATED  {g.item}: {g.manual_step}")
    else:
        lines.append("(none listed)")
    lines.append("")
    ok = targets_checked > 0 and failed == 0
    reason = "" if targets_checked > 0 else " (zero targets checked: an empty gate fails)"
    lines.append(
        f"targets checked: {targets_checked}; checks: {len(findings)}; passed: {passed}; "
        f"failed: {failed}; user-gated: {len(gated)} (not a pass); "
        f"verdict: {'PASS' if ok else 'FAIL'}{reason}"
    )
    return "\n".join(lines), ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="fetch the configured targets over the network")
    mode.add_argument("--offline", type=Path, metavar="DIR", help="answer from DIR/index.json fixtures")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--url", action="append", default=[], help="check this URL instead of the config targets")
    parser.add_argument("--marker", action="append", default=[], help="required body marker for every --url")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    if not (args.live or args.offline or args.url):
        parser.error("choose --live, --offline DIR, or give at least one --url")

    targets, gated, extra = load_config(args.config) if args.config.exists() else ([], [], [])
    if args.url:
        targets = [Target(f"url{i + 1}", u, list(args.marker)) for i, u in enumerate(args.url)]

    if args.offline:
        fetch = offline_fetcher(args.offline)
    else:
        def fetch(url: str) -> FetchResult:
            return live_fetch(url, args.timeout)

    rules = build_rules(extra)
    findings: list[Finding] = []
    for target in targets:
        findings.extend(check_target(target, fetch, rules))

    text, ok = render(findings, gated, len(targets))
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

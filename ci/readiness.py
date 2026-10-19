"""Readiness gate for the judge-facing surfaces of Agon (spec metric M22).

Why this exists: an uptime check that only reads a status code stays green over a
page that lies. This gate fetches every configured URL and fails on any of:

* a status other than 200 (401 and 403 are called out, because the judge path
  must be login-free);
* a change of host anywhere in the redirect chain, or an https to http downgrade;
* a missing required body marker, and a target with no usable marker at all,
  because a static host answers 200 with its fallback page for any path;
* a forbidden string in the body or in same-host script assets: em and en
  dashes, the word "compliant", a self-score such as "9/10", and real league,
  club and brand words.

A dash renders on screen from more than the raw character, so the gate also
matches HTML character references (named, decimal and hex) and JavaScript or
JSON \\u escapes, and it runs every rule again over the body with HTML entities
decoded and over the body with \\u escapes decoded.

Scripts are found through script src, link rel tokens that include
modulepreload, and import specifiers inside each fetched script, so a chunk
loaded through a dynamic import() is scanned too. What the crawl cannot see is
printed as a LIMITATIONS section, which is never counted as a pass.

User-gated items (things only the owner can do, such as a public video upload)
are printed in their own table and never count as a pass. A run that checks
zero targets fails, because an empty gate proves nothing.

Only the standard library is used, so the gate runs anywhere without installs.

The shipped config names the Static Web Apps host by a placeholder,
__AGON_SWA_HOSTNAME__, because the real default hostname is only known after the
first deploy. A placeholder URL is a FAIL that fetches nothing. The deploy
workflow passes the real host with --set-host and adds the deployed commit sha
as a marker with --add-marker, so the live run proves the page serves THAT
commit, not merely some page.

Usage:
    python ci/readiness.py --live                      # targets from ci/readiness.json
    python ci/readiness.py --live --set-host x.azurestaticapps.net --add-marker demo=<sha>
    python ci/readiness.py --url https://x --marker Agon
    python ci/readiness.py --offline tests/fixtures/x  # fixture index, no network
"""

from __future__ import annotations

import argparse
import html
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
# Stands for the Static Web Apps default hostname until the deploy job sets it.
HOST_PLACEHOLDER = "__AGON_SWA_HOSTNAME__"
# Any unreplaced __AGON_...__ token means the URL was never configured.
_PLACEHOLDER_TOKEN = re.compile(r"__AGON_[A-Z0-9_]*__")
# A bare DNS name: labels of letters, digits and inner hyphens, at least one dot.
_HOSTNAME = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
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
    # &mdash; &ndash; and the numeric forms &#8212; &#x2014; (a browser also
    # renders a numeric reference without its semicolon).
    ("em or en dash as an HTML character reference",
     r"(?i)&(?:[mn]dash;|#0*821[12](?![0-9]);?|#x0*201[34](?![0-9a-f]);?)"),
    # JavaScript and JSON escapes, including json.dumps output with its default
    # ensure_ascii=True. A \\u escape always takes exactly four hex digits, so
    # what follows them is plain text and needs no lookahead.
    ("em or en dash as a \\u escape", r"\\u(?:201[34]|\{0*201[34]\})"),
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

# The crawl follows at most this many script assets per target. Reaching the cap
# is a failure, because the scripts left in the queue were never scanned.
MAX_ASSETS = 200

ASSET_LIMITATIONS: tuple[str, ...] = (
    "script URLs computed at run time (for example a bundler's chunk map built by string "
    "concatenation) are not discovered; only script src, link rel modulepreload and import "
    "specifiers ending in .js or .mjs are followed",
    "stylesheets, images, fonts and JSON fetched by scripts are not scanned",
    "text assembled at run time from fragments is only seen in the fragments",
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
        rel_tokens = (values.get("rel") or "").lower().split()
        if tag == "link" and "modulepreload" in rel_tokens and values.get("href"):
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


# import("x"), from "x" and a bare import "x", in minified or spaced form.
_IMPORT_SPECIFIER = re.compile(r"""(?:\bimport\s*\(\s*|\bfrom\s*|\bimport\s*)(["'`])([^"'`\s]+)\1""")


def import_specifiers(js: str) -> list[str]:
    """Module specifiers in a script that name a same-site .js or .mjs file.

    Only relative, root-relative or absolute http(s) specifiers count; a bare
    package name has no URL in a browser. A template with ${...} is computed at
    run time and is left to the printed limitations.
    """
    found: list[str] = []
    for match in _IMPORT_SPECIFIER.finditer(js):
        spec = match.group(2)
        if "${" in spec or not spec.startswith(("./", "../", "/", "http://", "https://")):
            continue
        path = urllib.parse.urlsplit(spec).path
        if path.endswith((".js", ".mjs")) and spec not in found:
            found.append(spec)
    return found


_UNICODE_ESCAPE = re.compile(r"\\u(?:\{([0-9a-fA-F]{1,6})\}|([0-9a-fA-F]{4}))")


def _decode_unicode_escapes(text: str) -> str:
    """Replace JavaScript and JSON \\u escapes with the characters they name."""

    def char(match: re.Match[str]) -> str:
        code = int(match.group(1) or match.group(2), 16)
        return chr(code) if code <= 0x10FFFF else match.group(0)

    return _UNICODE_ESCAPE.sub(char, text)


def decoded_views(body: str) -> list[tuple[str, str]]:
    """The body as served, then as rendered after entity and escape decoding."""
    views = [("", body)]
    for suffix, text in ((" after decoding HTML entities", html.unescape(body)),
                         (" after decoding \\u escapes", _decode_unicode_escapes(body))):
        if text != body:
            views.append((suffix, text))
    return views


def build_rules(extra_terms: Iterable[str] = ()) -> list[tuple[str, re.Pattern[str]]]:
    """Compile the forbidden-string rules: pinned defaults plus any extra terms."""
    rules: list[tuple[str, re.Pattern[str]]] = [
        (label, re.compile(re.escape(literal))) for label, literal in DEFAULT_LITERALS
    ]
    rules += [(label, re.compile(pattern)) for label, pattern in DEFAULT_PATTERNS]
    for term in (*DEFAULT_REAL_TERMS, *extra_terms):
        # Any run of whitespace between words, so a non-breaking space still matches.
        spaced = r"\s+".join(re.escape(part) for part in term.split())
        word = r"(?<![A-Za-z0-9])" + spaced + r"(?![A-Za-z0-9])"
        rules.append((f"real-world name '{term}'", re.compile(word, re.IGNORECASE)))
    return rules


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def forbidden_hits(body: str, rules: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    """Describe every forbidden-rule match as 'label at line N', once per rule.

    The raw body is searched first; a rule that only matches after decoding says
    which decoding exposed it.
    """
    hits: list[str] = []
    views = decoded_views(body)
    for label, pattern in rules:
        for suffix, text in views:
            match = pattern.search(text)
            if match:
                hits.append(f"{label} at line {_line_of(text, match.start())}{suffix}")
                break
    return hits


def _host(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme.lower(), (parts.netloc or "").lower())


def _marker_findings(target: Target, body: str) -> list[Finding]:
    """Check every marker, and fail a target that has no usable marker at all.

    A static host serves its fallback page with 200 for any path, so a target
    without a marker would pass on a typo URL. A blank marker is in every body.
    """
    findings: list[Finding] = []
    usable = [m for m in target.markers if m.strip()]
    for marker in target.markers:
        if not marker.strip():
            findings.append(Finding(target.name, f"marker {marker!r}", False,
                                    "blank marker matches every body"))
    if not usable:
        findings.append(Finding(target.name, "body marker configured", False,
                                "no body marker configured: any 200 page, even an error page, would pass"))
    for marker in usable:
        present = marker in body
        findings.append(Finding(target.name, f"marker '{marker}'", present,
                                "present" if present else "missing from body"))
    return findings


def _asset_findings(
    target: Target, page: FetchResult, fetch: Fetcher,
    rules: list[tuple[str, re.Pattern[str]]], max_assets: int,
) -> list[Finding]:
    """Scan same-host scripts, following import specifiers breadth first.

    A specifier resolves against the URL of the script that contains it, as a
    browser resolves module imports. Hitting the cap is a failure.
    """
    start_host = _host(target.url)[1]
    queue = [a for a in script_assets(page.chain[-1], page.body) if _host(a)[1] == start_host]
    seen = set(queue)
    findings: list[Finding] = []
    scanned = 0
    while queue and scanned < max_assets:
        asset = queue.pop(0)
        fetched = fetch(asset)
        scanned += 1
        asset_hits = forbidden_hits(fetched.body, rules)
        ok = fetched.status == 200 and not asset_hits
        detail = f"status {fetched.status}" + (f"; {'; '.join(asset_hits)}" if asset_hits else "")
        findings.append(Finding(target.name, f"asset {asset}", ok, detail))
        if fetched.status != 200:
            continue
        base = fetched.chain[-1] if fetched.chain else asset
        for spec in import_specifiers(fetched.body):
            url = urllib.parse.urljoin(base, spec)
            if _host(url)[1] == start_host and url not in seen:
                seen.add(url)
                queue.append(url)
    if queue:
        findings.append(Finding(target.name, "asset scan complete", False,
                                f"cap of {max_assets} reached, {len(queue)} not scanned"))
    return findings


def check_target(
    target: Target, fetch: Fetcher, rules: list[tuple[str, re.Pattern[str]]],
    max_assets: int = MAX_ASSETS,
) -> list[Finding]:
    """Run every check for one target and return one finding per check."""
    findings: list[Finding] = []
    if not target.url:
        return [Finding(target.name, "url configured", False, "no URL yet: nothing deployed")]
    if is_placeholder(target.url):
        return [Finding(target.name, "url configured", False,
                        f"placeholder URL {target.url}: nothing fetched; the deploy job sets the host with --set-host")]

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

    findings.extend(_marker_findings(target, result.body))

    hits = forbidden_hits(result.body, rules)
    findings.append(Finding(target.name, "forbidden strings", not hits,
                            "none" if not hits else "; ".join(hits)))

    if target.scan_assets and result.status == 200:
        findings.extend(_asset_findings(target, result, fetch, rules, max_assets))
    return findings


def is_placeholder(url: str) -> bool:
    """True when a URL still holds an unreplaced __AGON_...__ token."""
    return bool(_PLACEHOLDER_TOKEN.search(url))


def set_host(targets: list[Target], host: str) -> int:
    """Replace the host placeholder in every target URL. Returns how many changed.

    The host must be a bare lowercase DNS name: no scheme, port, path or
    credentials, so a value from a deploy output cannot smuggle a different URL.
    """
    if not _HOSTNAME.match(host):
        raise ValueError(f"--set-host needs a bare lowercase hostname, got {host!r}")
    changed = 0
    for target in targets:
        if HOST_PLACEHOLDER in target.url:
            target.url = target.url.replace(HOST_PLACEHOLDER, host)
            changed += 1
    return changed


def add_markers(targets: list[Target], pairs: Iterable[str]) -> None:
    """Apply NAME=TEXT pairs: append TEXT to the markers of the target NAME.

    An unknown target name or a blank marker is an error, so a typo in the
    workflow cannot silently drop the commit check.
    """
    by_name = {t.name: t for t in targets}
    for pair in pairs:
        name, sep, text = pair.partition("=")
        if not sep or not text.strip():
            raise ValueError(f"--add-marker needs NAME=TEXT with a non-blank TEXT, got {pair!r}")
        if name not in by_name:
            raise ValueError(f"--add-marker names unknown target {name!r}; known: {', '.join(sorted(by_name))}")
        by_name[name].markers.append(text)


def _markers(raw: object, name: str) -> list[str]:
    """Markers must be a JSON list of strings; a bare string would split into letters."""
    if not isinstance(raw, list) or not all(isinstance(m, str) for m in raw):
        raise ValueError(f"target '{name}': markers must be a list of strings")
    return list(raw)


def load_config(path: Path) -> tuple[list[Target], list[UserGated], list[str]]:
    """Read targets, user-gated items and extra forbidden terms from JSON.

    Raises ValueError on a malformed file, which main reports as a failure.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    targets = [
        Target(t["name"], t.get("url", ""), _markers(t.get("markers", []), t["name"]),
               bool(t.get("scan_assets", False)))
        for t in data.get("targets", [])
    ]
    gated = [UserGated(g["item"], g["manual_step"]) for g in data.get("user_gated", [])]
    extra = [str(term) for term in data.get("extra_forbidden_terms", [])]
    return targets, gated, extra


def render(
    findings: list[Finding], gated: list[UserGated], targets_checked: int,
    limitations: Iterable[str] = (),
) -> tuple[str, bool]:
    """Format the result tables and decide pass or fail.

    Limitations are printed for the reader and never enter the pass count.
    """
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
    limits = list(limitations)
    if limits:
        lines.append("LIMITATIONS (not checked, never counted)")
        lines.extend(f"LIMITATION  {item}" for item in limits)
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
    parser.add_argument("--set-host", metavar="HOST",
                        help=f"replace {HOST_PLACEHOLDER} in every config target URL with HOST")
    parser.add_argument("--add-marker", action="append", default=[], metavar="NAME=TEXT",
                        help="add a required body marker to the config target NAME")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    if not (args.live or args.offline or args.url):
        parser.error("choose --live, --offline DIR, or give at least one --url")
    if hasattr(sys.stdout, "reconfigure"):
        # A non-ASCII URL or detail printed to a Windows pipe must not crash the gate.
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    try:
        targets, gated, extra = load_config(args.config) if args.config.exists() else ([], [], [])
    except (ValueError, KeyError, TypeError) as exc:
        print(f"config error in {args.config}: {exc}")
        print("verdict: FAIL (the config could not be read)")
        return 1
    if args.url:
        # With no --marker every target fails "no body marker configured".
        targets = [Target(f"url{i + 1}", u, list(args.marker)) for i, u in enumerate(args.url)]
    try:
        if args.set_host is not None:
            changed = set_host(targets, args.set_host)
            print(f"host placeholder replaced in {changed} target URL(s)")
        add_markers(targets, args.add_marker)
    except ValueError as exc:
        print(f"argument error: {exc}")
        print("verdict: FAIL (the overrides could not be applied)")
        return 1

    if args.offline:
        fetch = offline_fetcher(args.offline)
    else:
        def fetch(url: str) -> FetchResult:
            return live_fetch(url, args.timeout)

    rules = build_rules(extra)
    findings: list[Finding] = []
    for target in targets:
        findings.extend(check_target(target, fetch, rules))

    limitations = ASSET_LIMITATIONS if any(t.scan_assets and t.url for t in targets) else ()
    text, ok = render(findings, gated, len(targets), limitations)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

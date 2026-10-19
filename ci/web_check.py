"""Static checks for the Agon web shell, stdlib only.

Why this exists: the shell is served by Azure Static Web Apps under a strict
Content-Security-Policy. An inline script, an inline event handler or a style
attribute works in a lax local preview and then fails silently in production,
so the rules that the CSP enforces at runtime are enforced here at review time.
The same pass covers the accessibility basics a static file can prove (lang,
alt text, button names, a reason beside every disabled control), the house rule
of zero em and en dashes, and the colour contrast table in app.css, which is
recomputed from the tokens rather than trusted.

Files are selected by path and suffix, never by searching their text, and the
scanned count is printed so a run that silently covers nothing is visible.
Zero scanned files is a failure, not a pass.

Usage:
    python ci/web_check.py [web_dir]
    python ci/web_check.py [web_dir] --print-contrast
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

SCANNED_SUFFIXES = (".html", ".css", ".js", ".json", ".svg")
COMMIT_PLACEHOLDER = "__AGON_COMMIT__"
CONFIG_NAME = "staticwebapp.config.json"

# The exact sentence a hand-written run must carry, on the run file and on the
# page. Pinned here as a literal (this checker imports nothing from src/) and
# held equal to agon.webrun.DESIGN_PREVIEW_LABEL by tests/test_web_runs.py.
DESIGN_PREVIEW_LABEL = "Design preview: sample run written by hand, not produced by the pipeline."
RUNS_DIR = "runs/"
# Where the label must sit on the page, anchored to the element that shows it.
# A bare substring check passed with the wall copy gone as long as any other
# copy survived, so each screen's copy is pinned to its own id. The same three
# strings are the demo target's markers in ci/readiness.json.
PAGE_LABEL_MARKERS = (
    ("Newsroom Wall", 'id="wall-label">' + DESIGN_PREVIEW_LABEL),
    ("approval card", 'id="approve-label">' + DESIGN_PREVIEW_LABEL),
    ("recap", 'id="recap-label">Not approved. ' + DESIGN_PREVIEW_LABEL),
)

# Characters and their escaped spellings. Built with chr() on purpose so this
# file itself contains no dash characters, even after an editor round trip.
DASH_CHARS = {chr(0x2014): "em dash", chr(0x2013): "en dash", chr(0x2015): "horizontal bar"}
DASH_SPELLINGS = re.compile(
    r"&(?:mdash|ndash|horbar);"
    r"|&#(?:8212|8211|8213);"
    r"|&#x0*(?:2014|2013|2015);"
    r"|\\u(?:2014|2013|2015)"
    r"|\\0*(?:2014|2013|2015)(?![0-9a-fA-F])",
    re.IGNORECASE,
)

# DOM sinks and string evaluation that a strict CSP blocks or that bypass it.
UNSAFE_JS = (
    (re.compile(r"\.(?:inner|outer)HTML\b"), "assigns HTML strings; use textContent and DOM nodes"),
    (re.compile(r"insertAdjacentHTML"), "inserts HTML strings; use DOM nodes"),
    (re.compile(r"document\.write"), "document.write is forbidden"),
    (re.compile(r"\beval\s*\("), "eval is blocked by the CSP"),
    (re.compile(r"\bnew\s+Function\s*\("), "new Function is blocked by the CSP"),
    (re.compile(r"setAttribute\(\s*['\"](?:style|on\w+)['\"]"), "style or handler attributes are blocked by the CSP"),
    (re.compile(r"set(?:Timeout|Interval)\(\s*['\"`]"), "string timers are blocked by the CSP"),
)

# Only the WCAG 2.2 AA thresholds are accepted, so a row cannot pass by
# quietly lowering its own bar.
AA_THRESHOLDS = (3.0, 4.5)

# Pairs that must appear in the contrast table. Pinned here so a row cannot be
# dropped from app.css to make the check pass.
REQUIRED_PAIRS = (
    ("--text", "--bg"),
    ("--text", "--surface"),
    ("--text-muted", "--bg"),
    ("--text-muted", "--surface"),
    ("--on-primary", "--primary"),
    ("--focus", "--bg"),
    ("--focus", "--surface"),
    ("--border-strong", "--surface"),
    ("--pitch-line", "--pitch-grass"),
)

ALLOWED_REFERRER = {"no-referrer", "same-origin", "strict-origin", "strict-origin-when-cross-origin"}
FORBIDDEN_CSP_TOKENS = ("'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'", "'wasm-unsafe-eval'")


@dataclass(frozen=True)
class Violation:
    """One failed rule at one place. `rule` is a stable id the tests assert on."""

    rule: str
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.rule:<22} {self.path}:{self.line}  {self.message}"


# ---------------------------------------------------------------- HTML rules


class _ShellParser(HTMLParser):
    """Collects HTML violations in one pass; id references are resolved at the end."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, int, str]] = []
        self.ids: dict[str, int] = {}
        self.refs: list[tuple[str, str, int]] = []
        self.html_lang: str | None = None
        self.saw_html = False
        self.has_commit_attr = False
        self._script: dict | None = None
        self._button: dict | None = None

    def _line(self) -> int:
        return self.getpos()[0]

    def _add(self, rule: str, message: str) -> None:
        self.found.append((rule, self._line(), message))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {name: (value or "") for name, value in attrs}
        line = self._line()

        for name, value in a.items():
            if name.startswith("on"):
                self._add("inline-handler", f"<{tag}> has inline handler {name}=")
            if name in ("href", "src", "action", "formaction", "xlink:href") and value.strip().lower().startswith("javascript:"):
                self._add("javascript-url", f"<{tag}> {name} uses a javascript: URL")
        if "style" in a:
            self._add("inline-style", f"<{tag}> has a style attribute, which the CSP blocks")
        if tag == "style":
            self._add("inline-style", "<style> element, which the CSP blocks; use app.css")

        if "id" in a:
            ident = a["id"]
            if ident in self.ids:
                self._add("duplicate-id", f"id '{ident}' already used on line {self.ids[ident]}")
            else:
                self.ids[ident] = line
        for ref_attr in ("aria-labelledby", "aria-describedby", "aria-controls", "for"):
            for ident in a.get(ref_attr, "").split():
                self.refs.append((ref_attr, ident, line))

        if "disabled" in a or a.get("aria-disabled") == "true":
            if not a.get("aria-describedby", "").strip():
                self._add("disabled-reason", f"disabled <{tag}> has no aria-describedby pointing at its reason")

        if "data-commit" in a:
            self.has_commit_attr = True

        if tag == "html":
            self.saw_html = True
            self.html_lang = a.get("lang", "").strip()
        elif tag == "img" and "alt" not in a:
            self._add("img-alt", "<img> without an alt attribute")
        elif tag == "script":
            src = a.get("src", "").strip()
            if src and _is_external(src):
                self._add("external-asset", f"script from another origin: {src}")
            self._script = {"src": src, "line": line, "body": []}
        elif tag == "link" and "stylesheet" in a.get("rel", "").lower().split():
            href = a.get("href", "").strip()
            if _is_external(href):
                self._add("external-asset", f"stylesheet from another origin: {href}")
        elif tag == "input" and a.get("type", "").lower() in ("button", "submit", "reset"):
            if not (a.get("value", "").strip() or a.get("aria-label", "").strip() or a.get("aria-labelledby", "").strip()):
                self._add("button-name", "input button without a value or aria-label")
        elif tag == "button":
            self._button = {
                "line": line,
                "text": [],
                "label": a.get("aria-label", "").strip() or a.get("title", "").strip(),
                "labelledby": a.get("aria-labelledby", "").strip(),
            }

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            body = "".join(self._script["body"]).strip()
            if not self._script["src"]:
                self.found.append(("inline-script", self._script["line"], "<script> without src runs inline and the CSP blocks it"))
            elif body:
                self.found.append(("inline-script", self._script["line"], "<script src> also carries an inline body"))
            self._script = None
        elif tag == "button" and self._button is not None:
            b = self._button
            text = " ".join("".join(b["text"]).split())
            if not (text or b["label"] or b["labelledby"]):
                self.found.append(("button-name", b["line"], "<button> has no text, aria-label, aria-labelledby or title"))
            self._button = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script["body"].append(data)
        if self._button is not None:
            self._button["text"].append(data)

    def finish(self) -> None:
        if not self.saw_html or not self.html_lang:
            self.found.append(("missing-lang", 1, "<html> needs a non-empty lang attribute"))
        for attr, ident, line in self.refs:
            if ident not in self.ids:
                self.found.append(("broken-ref", line, f"{attr} points at missing id '{ident}'"))


def _is_external(url: str) -> bool:
    lowered = url.lower()
    return lowered.startswith(("http://", "https://", "//", "data:"))


def check_html(rel: str, text: str, require_commit: bool) -> list[Violation]:
    """Run the HTML rules on one document."""
    parser = _ShellParser()
    parser.feed(text)
    parser.close()
    parser.finish()
    out = [Violation(rule, rel, line, msg) for rule, line, msg in parser.found]
    if require_commit and not parser.has_commit_attr:
        out.append(Violation("commit-label", rel, 1, f"no data-commit attribute for the deploy step to stamp ({COMMIT_PLACEHOLDER})"))
    return out


# ---------------------------------------------------------------- text rules


def check_dashes(rel: str, text: str) -> list[Violation]:
    """Zero em and en dashes, in any spelling, in every shipped file."""
    out: list[Violation] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for char, name in DASH_CHARS.items():
            if char in line:
                out.append(Violation("dash", rel, number, f"contains an {name} character"))
        for match in DASH_SPELLINGS.finditer(line):
            out.append(Violation("dash", rel, number, f"contains an escaped dash {match.group(0)!r}"))
    return out


def check_js(rel: str, text: str) -> list[Violation]:
    """Forbid the DOM sinks and string evaluation a strict CSP blocks."""
    out: list[Violation] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, why in UNSAFE_JS:
            if pattern.search(line):
                out.append(Violation("unsafe-js", rel, number, why))
    return out


# ---------------------------------------------------------------- config rules


def _parse_csp(policy: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0].lower()] = tokens[1:]
    return directives


def check_config(rel: str, text: str) -> list[Violation]:
    """The deployed headers must carry the strict CSP and the hardening headers."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        return [Violation("config-json", rel, err.lineno, f"invalid JSON: {err.msg}")]

    out: list[Violation] = []
    headers = {str(k).lower(): str(v) for k, v in (data.get("globalHeaders") or {}).items()}
    policy = headers.get("content-security-policy", "")
    if not policy:
        out.append(Violation("csp-missing", rel, 1, "globalHeaders has no Content-Security-Policy"))
    else:
        for token in FORBIDDEN_CSP_TOKENS:
            if token in policy.lower():
                out.append(Violation("csp-unsafe", rel, 1, f"CSP contains {token}"))
        csp = _parse_csp(policy)
        if "default-src" not in csp:
            out.append(Violation("csp-default-src", rel, 1, "CSP has no default-src"))
        if csp.get("script-src") != ["'self'"]:
            out.append(Violation("csp-script-src", rel, 1, "script-src must be exactly 'self'"))
        if csp.get("style-src") != ["'self'"]:
            out.append(Violation("csp-style-src", rel, 1, "style-src must be exactly 'self'"))
        if csp.get("frame-ancestors") != ["'none'"]:
            out.append(Violation("csp-frame-ancestors", rel, 1, "frame-ancestors must be 'none'"))
        if csp.get("object-src") != ["'none'"]:
            out.append(Violation("csp-object-src", rel, 1, "object-src must be 'none'"))
        if "base-uri" not in csp:
            out.append(Violation("csp-base-uri", rel, 1, "CSP has no base-uri"))
    if headers.get("x-content-type-options", "").lower() != "nosniff":
        out.append(Violation("header-nosniff", rel, 1, "X-Content-Type-Options must be nosniff"))
    if headers.get("referrer-policy", "").lower() not in ALLOWED_REFERRER:
        out.append(Violation("header-referrer", rel, 1, "Referrer-Policy missing or too permissive"))

    # Without the exclude list a missing asset comes back as index.html, and
    # nosniff then turns that into a hard-to-read failure.
    fallback = data.get("navigationFallback") or {}
    missing = sorted(FALLBACK_EXCLUDED - _excluded_extensions(fallback.get("exclude") or []))
    if fallback.get("rewrite") != "/index.html" or missing:
        detail = f"; not excluded: {', '.join(missing)}" if missing else ""
        out.append(Violation("spa-fallback", rel, 1, f"navigationFallback must rewrite to /index.html and exclude asset files{detail}"))
    missing_dirs = [d for d in FALLBACK_JSON_DIRS if d not in _excluded_dirs(fallback.get("exclude") or [])]
    if missing_dirs:
        out.append(Violation("spa-fallback-dirs", rel, 1, f"navigationFallback must exclude JSON in each data folder; missing {', '.join(missing_dirs)}"))
    # Static Web Apps refuses the whole deploy when a route or exclude pattern holds
    # more than one '*' (deploy run 39270419700 failed on "/runs/*/*.json"). Catch
    # it here, at pull request time, instead of in the deploy job on main.
    patterns = [p for p in (fallback.get("exclude") or []) if isinstance(p, str)]
    patterns += [r.get("route") for r in (data.get("routes") or []) if isinstance(r, dict) and isinstance(r.get("route"), str)]
    for pattern in patterns:
        if pattern.count("*") > 1:
            out.append(Violation("swa-wildcard", rel, 1, f"Static Web Apps allows at most one '*' per pattern: {pattern!r}"))
    return out


# ---------------------------------------------------------------- run label rules


def check_run_label(rel: str, text: str) -> tuple[list[Violation], bool]:
    """A run marked as a design preview must carry the exact label. Returns (violations, is_preview)."""
    try:
        run = json.loads(text)
    except json.JSONDecodeError as err:
        return [Violation("run-json", rel, err.lineno, f"invalid JSON: {err.msg}")], False
    if not isinstance(run, dict) or "provenance" not in run:
        return [Violation("run-label", rel, 1, "a run file needs a provenance and a label")], False
    preview = run.get("provenance") == "design_preview"
    if preview and run.get("label") != DESIGN_PREVIEW_LABEL:
        return [Violation("run-label", rel, 1, f"a design preview run must carry the label {DESIGN_PREVIEW_LABEL!r}")], True
    return [], preview


def _is_run_file(rel: str) -> bool:
    """runs/<id>.json, not the index and not a file inside a bundle folder."""
    return rel.startswith(RUNS_DIR) and rel.count("/") == 1 and rel.endswith(".json") and rel != RUNS_DIR + "index.json"


# ndjson: the match event files. Without it a missing data/<seed>.ndjson would
# come back as index.html with a 200, and the Verify page would hash a web page.
FALLBACK_EXCLUDED = {"css", "js", "svg", "json", "ndjson"}

# Folders whose JSON the pages fetch. Each needs its own exclude glob, because a
# root glob such as /*.json is not relied on to reach into folders: a missing
# runs/<id>/edition-en.json or season/<file>.json must be a 404, never index.html with a 200.
FALLBACK_JSON_DIRS = ("/data/", "/data-fixture/", "/runs/", "/runs/*/", "/season/")


def _excluded_dirs(patterns: list[str]) -> set[str]:
    """Folder prefixes whose pattern ends in *.json or *.{...json...}, or that a tree
    pattern '/<folder>/*' (one wildcard, everything below the folder) covers."""
    found: set[str] = set()
    for pattern in patterns:
        if pattern.endswith("/*") and pattern.count("*") == 1:
            tree = pattern[:-1]
            found.update(prefix for prefix in FALLBACK_JSON_DIRS if prefix.startswith(tree))
            continue
        for prefix in FALLBACK_JSON_DIRS:
            rest = pattern[len(prefix):] if pattern.startswith(prefix) else None
            if rest is None or "/" in rest:
                continue
            if "json" in _excluded_extensions(["/" + rest]):
                found.add(prefix)
    return found


def _excluded_extensions(patterns: list[str]) -> set[str]:
    """Extensions covered by globs like '/*.{css,js}' or '/*.svg'."""
    found: set[str] = set()
    for pattern in patterns:
        braces = re.search(r"\*\.\{([^}]*)\}$", pattern)
        single = re.search(r"\*\.([\w]+)$", pattern)
        if braces:
            found.update(part.strip().lower() for part in braces.group(1).split(","))
        elif single:
            found.add(single.group(1).lower())
    return found


# ---------------------------------------------------------------- contrast


_TOKEN = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
_LIGHT_BLOCK = re.compile(r"(?m)^:root\s*\{([^}]*)\}")
_DARK_TOGGLE_BLOCK = re.compile(r":root\[data-theme=\"dark\"\]\s*\{([^}]*)\}")
_DARK_MEDIA_BLOCK = re.compile(r":root:not\(\[data-theme=\"light\"\]\)\s*\{([^}]*)\}")
_TABLE_ROW = re.compile(
    r"^\s*\*?\s*(--[\w-]+)\s*\|\s*(--[\w-]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*$"
)
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _tokens(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in _TOKEN.findall(block)}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    digits = value.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def relative_luminance(value: str) -> float:
    """WCAG 2.2 relative luminance of an sRGB hex colour."""

    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours, from 1.0 to 21.0."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def theme_tokens(css: str) -> tuple[dict[str, str], dict[str, str], dict[str, str]] | None:
    """Return (light, dark via toggle, dark via media query) tokens, or None if a block is missing."""
    light = _LIGHT_BLOCK.search(css)
    toggle = _DARK_TOGGLE_BLOCK.search(css)
    media = _DARK_MEDIA_BLOCK.search(css)
    if not (light and toggle and media):
        return None
    return _tokens(light.group(1)), _tokens(toggle.group(1)), _tokens(media.group(1))


def contrast_rows(css: str) -> list[tuple[int, str, str, float, float, float]]:
    """Rows of the CONTRAST TABLE comment: (line, fg, bg, min, light, dark)."""
    rows = []
    for number, line in enumerate(css.splitlines(), start=1):
        match = _TABLE_ROW.match(line)
        if match:
            fg, bg, minimum, light, dark = match.groups()
            rows.append((number, fg, bg, float(minimum), float(light), float(dark)))
    return rows


def check_css(rel: str, css: str) -> list[Violation]:
    """Themes must be complete and in sync, and every listed ratio must recompute and pass."""
    themes = theme_tokens(css)
    if themes is None:
        return [Violation("theme-tokens", rel, 1, "need :root, :root[data-theme=\"dark\"] and the prefers-color-scheme dark block")]
    light, dark_toggle, dark_media = themes
    out: list[Violation] = []
    if dark_toggle != dark_media:
        out.append(Violation("dark-drift", rel, 1, "the two dark token blocks differ; keep them identical"))
    colour_tokens = {k for k, v in light.items() if _HEX.match(v)}
    for name in sorted(colour_tokens - dark_toggle.keys()):
        out.append(Violation("theme-coverage", rel, 1, f"{name} has a light value but no dark value"))

    rows = contrast_rows(css)
    if not rows:
        out.append(Violation("contrast-table", rel, 1, "no CONTRAST TABLE rows found"))
    listed = {(fg, bg) for _, fg, bg, *_ in rows}
    for pair in REQUIRED_PAIRS:
        if pair not in listed:
            out.append(Violation("contrast-table", rel, 1, f"required pair {pair[0]} on {pair[1]} is not in the table"))
    for number, fg, bg, minimum, light_listed, dark_listed in rows:
        if minimum not in AA_THRESHOLDS:
            out.append(Violation("contrast-threshold", rel, number, f"threshold {minimum} is not a WCAG 2.2 AA threshold"))
        for theme_name, tokens, listed_value in (("light", light, light_listed), ("dark", dark_toggle, dark_listed)):
            fg_hex, bg_hex = tokens.get(fg, ""), tokens.get(bg, "")
            if not (_HEX.match(fg_hex) and _HEX.match(bg_hex)):
                out.append(Violation("contrast-token", rel, number, f"{theme_name}: {fg} or {bg} is not a hex token"))
                continue
            ratio = contrast_ratio(fg_hex, bg_hex)
            if abs(round(ratio, 2) - listed_value) > 0.011:
                out.append(Violation("contrast-mismatch", rel, number, f"{theme_name}: {fg} on {bg} is {ratio:.2f}, table says {listed_value:.2f}"))
            if ratio < minimum:
                out.append(Violation("contrast-fail", rel, number, f"{theme_name}: {fg} on {bg} is {ratio:.2f}, below {minimum}"))
    return out


def print_contrast(css: str) -> None:
    """Print table rows with freshly computed values, for pasting into app.css."""
    themes = theme_tokens(css)
    if themes is None:
        print("theme blocks missing")
        return
    light, dark, _ = themes
    rows = contrast_rows(css) or [(0, fg, bg, 4.5, 0.0, 0.0) for fg, bg in REQUIRED_PAIRS]
    for _, fg, bg, minimum, _, _ in rows:
        lv = contrast_ratio(light[fg], light[bg])
        dv = contrast_ratio(dark[fg], dark[bg])
        print(f"   {fg:<17}| {bg:<15}| {minimum:<3} | {lv:5.2f} | {dv:5.2f}")


# ---------------------------------------------------------------- driver


def scan(web_dir: Path) -> tuple[int, list[Violation]]:
    """Run every rule over web_dir. Returns (files scanned, violations)."""
    files = sorted(p for p in web_dir.rglob("*") if p.is_file() and p.suffix.lower() in SCANNED_SUFFIXES)
    out: list[Violation] = []
    if not files:
        return 0, [Violation("no-files", str(web_dir), 0, "no web files found; refusing to pass an empty scan")]

    names = {p.relative_to(web_dir).as_posix() for p in files}
    for required in ("index.html", "app.css", CONFIG_NAME):
        if required not in names:
            out.append(Violation("missing-file", required, 0, "required web file is missing"))

    previews: list[str] = []
    for path in files:
        rel = path.relative_to(web_dir).as_posix()
        text = path.read_text(encoding="utf-8")
        out.extend(check_dashes(rel, text))
        if _is_run_file(rel):
            found, preview = check_run_label(rel, text)
            out.extend(found)
            if preview:
                previews.append(rel)
        suffix = path.suffix.lower()
        if suffix == ".html":
            out.extend(check_html(rel, text, require_commit=(rel == "index.html")))
        elif suffix == ".js":
            out.extend(check_js(rel, text))
        elif suffix == ".css" and rel == "app.css":
            out.extend(check_css(rel, text))
        elif rel == CONFIG_NAME:
            out.extend(check_config(rel, text))
    # The readiness gate reads index.html without running scripts, so the label
    # must be in the page itself whenever a design preview run ships.
    index = web_dir / "index.html"
    if previews and index.is_file():
        page = index.read_text(encoding="utf-8")
        for screen, marker in PAGE_LABEL_MARKERS:
            if marker not in page:
                out.append(Violation("run-label-page", "index.html", 1,
                                     f"{', '.join(previews)} is a design preview, so the {screen} must state "
                                     f"the label in place: {marker!r}"))
    return len(files), out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("web_dir", nargs="?", default=str(Path(__file__).resolve().parent.parent / "web"))
    parser.add_argument("--print-contrast", action="store_true", help="print recomputed contrast rows and exit")
    args = parser.parse_args(argv)
    web_dir = Path(args.web_dir)

    if args.print_contrast:
        print_contrast((web_dir / "app.css").read_text(encoding="utf-8"))
        return 0

    count, violations = scan(web_dir)
    print(f"web_check: scanned {count} files under {web_dir.as_posix()}")
    for v in violations:
        print(f"  {v}")
    if violations:
        print(f"web_check: FAIL ({len(violations)} violations)")
        return 1
    print("web_check: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

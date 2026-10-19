"""Tests for ci/web_check.py.

Why they are shaped this way: a gate is only evidence if it is shown to fail.
Each case copies the real web/ shell into a temporary folder, breaks exactly
one thing, and asserts that the named rule fires. The real shell must pass
untouched, so the fixtures cannot drift from what ships.

Dash characters are built with chr() so this file contains none.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def _load_checker():
    spec = importlib.util.spec_from_file_location("agon_web_check", ROOT / "ci" / "web_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


web_check = _load_checker()

EM = chr(0x2014)
EN = chr(0x2013)


def _copy_web(tmp_path: Path) -> Path:
    target = tmp_path / "web"
    shutil.copytree(WEB, target)
    return target


def _mutate(web: Path, name: str, old: str, new: str) -> None:
    path = web / name
    text = path.read_text(encoding="utf-8")
    assert old in text, f"mutation anchor {old!r} not found in {name}; the fixture went stale"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _rules(web: Path) -> set[str]:
    _, violations = web_check.scan(web)
    return {v.rule for v in violations}


def test_real_shell_passes():
    count, violations = web_check.scan(WEB)
    assert count >= 6
    assert violations == [], "\n".join(str(v) for v in violations)


def test_main_exit_codes(tmp_path, capsys):
    assert web_check.main([str(WEB)]) == 0
    out = capsys.readouterr().out
    assert "scanned" in out and "PASS" in out

    web = _copy_web(tmp_path)
    _mutate(web, "index.html", "</body>", "<script>alert(1)</script></body>")
    assert web_check.main([str(web)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_empty_directory_fails(tmp_path):
    count, violations = web_check.scan(tmp_path)
    assert count == 0
    assert [v.rule for v in violations] == ["no-files"]


# (rule expected, file, anchor, replacement)
MUTATIONS = [
    ("inline-script", "index.html", "</body>", "<script>alert(1)</script></body>"),
    ("inline-script", "index.html", '<script src="app.js" defer></script>', '<script src="app.js" defer>run()</script>'),
    ("inline-handler", "index.html", '<button type="button" class="icon-button', '<button onclick="go()" type="button" class="icon-button'),
    ("inline-handler", "index.html", '<circle class="pitch__ball" ', '<circle onload="go()" class="pitch__ball" '),
    ("inline-style", "index.html", '<p class="lead">Pick', '<p class="lead" style="color:red">Pick'),
    ("inline-style", "index.html", "</head>", "<style>body{}</style></head>"),
    ("javascript-url", "index.html", 'href="#/verify" data-route', 'href="javascript:go()" data-route'),
    ("missing-lang", "index.html", '<html lang="en">', "<html>"),
    ("img-alt", "index.html", "</main>", '<img src="favicon.svg"></main>'),
    ("button-name", "index.html", "</main>", '<button type="button"><svg aria-hidden="true"></svg></button></main>'),
    ("button-name", "index.html", 'aria-label="Switch to dark theme"', ""),
    ("disabled-reason", "index.html", 'aria-disabled="true" aria-describedby="approved-reason"', 'aria-disabled="true"'),
    ("disabled-reason", "index.html", 'disabled aria-describedby="play-reason"', "disabled"),
    ("disabled-reason", "index.html", "</main>", '<button type="button" disabled>Go</button></main>'),
    ("broken-ref", "index.html", 'aria-describedby="seed-hint"', 'aria-describedby="seed-hint-gone"'),
    ("duplicate-id", "index.html", 'id="h-pitch"', 'id="h-setup"'),
    ("external-asset", "index.html", '<script src="theme.js">', '<script src="https://cdn.example.com/x.js">'),
    ("commit-label", "index.html", ' data-commit="__AGON_COMMIT__"', ""),
    ("dash", "index.html", "Pick a seeded", f"Pick {EM} a seeded"),
    ("dash", "index.html", "Pick a seeded", f"Pick {EN} a seeded"),
    ("dash", "index.html", "Pick a seeded", "Pick &mdash; a seeded"),
    ("dash", "index.html", "Pick a seeded", "Pick &#x2013; a seeded"),
    ("dash", "app.js", '"Switch to "', '"Switch \\u2014 to "'),
    ("dash", "app.css", '.runbar__label::before {\n  content: "";', '.runbar__label::before {\n  content: "\\2014";'),
    ("unsafe-js", "app.js", "label.textContent = ", "label.innerHTML = "),
    ("unsafe-js", "app.js", "initRunLabel();\n  initTheme();", "eval(name);\n  initRunLabel();\n  initTheme();"),
    ("unsafe-js", "app.js", 'toggle.setAttribute("data-next", next);', 'toggle.setAttribute("style", next);'),
    ("csp-unsafe", "staticwebapp.config.json", "script-src 'self';", "script-src 'self' 'unsafe-inline';"),
    ("csp-unsafe", "staticwebapp.config.json", "default-src 'self';", "default-src 'self' 'unsafe-eval';"),
    ("csp-script-src", "staticwebapp.config.json", "script-src 'self';", "script-src 'self' https://cdn.example.com;"),
    ("csp-style-src", "staticwebapp.config.json", "style-src 'self';", "style-src 'self' https:;"),
    ("csp-frame-ancestors", "staticwebapp.config.json", " frame-ancestors 'none';", ""),
    ("csp-object-src", "staticwebapp.config.json", " object-src 'none';", ""),
    ("csp-missing", "staticwebapp.config.json", '"Content-Security-Policy"', '"X-Old-Policy"'),
    ("header-nosniff", "staticwebapp.config.json", '"nosniff"', '"sniff"'),
    ("header-referrer", "staticwebapp.config.json", '"no-referrer"', '"unsafe-url"'),
    ("spa-fallback", "staticwebapp.config.json", "/*.{css,js,", "/*.{"),
    ("swa-wildcard", "staticwebapp.config.json", '"/runs/*"', '"/runs/*", "/runs/*/*.json"'),
    ("spa-fallback-dirs", "staticwebapp.config.json", '"/runs/*"', '"/runs/*.json"'),
    ("spa-fallback-dirs", "staticwebapp.config.json", '"/season/*"', '"/season/*.txt"'),
    ("config-json", "staticwebapp.config.json", "{", "{{"),
    ("contrast-fail", "app.css", "--text-muted: #4b5767;", "--text-muted: #a8b0ba;"),
    ("contrast-mismatch", "app.css", "--text-muted: #4b5767;", "--text-muted: #3f4a59;"),
    ("contrast-threshold", "app.css", "| --bg           | 4.5 | 16.94", "| --bg           | 1.5 | 16.94"),
    ("contrast-table", "app.css", "   --on-primary     | --primary      |", "   --on-primary-x   | --primary      |"),
    ("dark-drift", "app.css", ':root[data-theme="dark"] {\n  color-scheme: dark;\n\n  --bg: #0b1016;', ':root[data-theme="dark"] {\n  color-scheme: dark;\n\n  --bg: #000000;'),
    ("theme-coverage", "app.css", "  --ball-edge: #0e1621;\n", "  --ball-edge: #0e1621;\n  --new-colour: #123456;\n"),
]


@pytest.mark.parametrize(("rule", "name", "old", "new"), MUTATIONS, ids=[f"{m[0]}-{i}" for i, m in enumerate(MUTATIONS)])
def test_each_rule_fails_on_a_broken_copy(tmp_path, rule, name, old, new):
    web = _copy_web(tmp_path)
    _mutate(web, name, old, new)
    assert rule in _rules(web)


def test_fallback_must_exclude_ndjson(tmp_path):
    """A missing data/<seed>.ndjson must 404, not come back as index.html with a 200."""
    web = _copy_web(tmp_path)
    path = web / "staticwebapp.config.json"
    text = path.read_text(encoding="utf-8")
    assert "ndjson" in text, "mutation anchor went stale"
    path.write_text(text.replace(",ndjson", "").replace("{ndjson,", "{"), encoding="utf-8")
    assert "spa-fallback" in _rules(web)


def test_missing_required_file_fails(tmp_path):
    web = _copy_web(tmp_path)
    (web / "staticwebapp.config.json").unlink()
    assert "missing-file" in _rules(web)


def test_contrast_math_known_values():
    assert round(web_check.contrast_ratio("#000000", "#ffffff"), 2) == 21.0
    assert round(web_check.contrast_ratio("#ffffff", "#ffffff"), 2) == 1.0
    # #767676 on white is the usual smallest grey that reaches 4.5:1.
    assert web_check.contrast_ratio("#767676", "#ffffff") >= 4.5
    assert web_check.contrast_ratio("#777777", "#ffffff") < 4.5
    assert web_check.contrast_ratio("#fff", "#000") == web_check.contrast_ratio("#ffffff", "#000000")


def test_required_pairs_are_all_in_the_shipped_table():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    listed = {(fg, bg) for _, fg, bg, *_ in web_check.contrast_rows(css)}
    assert set(web_check.REQUIRED_PAIRS) <= listed


def test_no_dash_characters_in_shipped_or_check_files():
    files = [p for p in (ROOT / "web").rglob("*") if p.is_file()]
    files += [ROOT / "ci" / "web_check.py", Path(__file__)]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert EM not in text and EN not in text, path


# ---------------------------------------------------------------- vendor chunk of the React console

# The pinned list, repeated here on purpose: editing web_check.VENDOR_SINKS alone
# fails this test, so a widened allowance cannot slip through in one file.
PINNED_VENDOR_SINKS = {".innerHTML": 3}


def _vendor_text(inner: int = 3, extra: str = "") -> str:
    return ";".join(["function a(e,t){e.innerHTML=t}"] * inner) + ";" + extra + "\n"


def _with_assets(tmp_path: Path, files: dict[str, str]) -> Path:
    web = _copy_web(tmp_path)
    (web / "assets").mkdir()
    for name, text in files.items():
        (web / "assets" / name).write_text(text, encoding="utf-8")
    return web


def test_vendor_allowance_is_pinned_in_two_places():
    assert {needle: count for needle, count, _ in web_check.VENDOR_SINKS} == PINNED_VENDOR_SINKS


def test_vendor_chunk_with_exactly_the_pinned_sinks_passes(tmp_path):
    web = _with_assets(tmp_path, {"vendor-Ab12_c.js": _vendor_text(), "index-Zz9.js": "export const x=1;\n"})
    assert _rules(web) == set()


@pytest.mark.parametrize("inner", [4, 2, 0])
def test_vendor_chunk_with_one_sink_more_or_less_fails(tmp_path, inner):
    web = _with_assets(tmp_path, {"vendor-Ab12.js": _vendor_text(inner)})
    assert "vendor-sink" in _rules(web)


@pytest.mark.parametrize("extra", ['document.write("x")', "eval(s)", "new Function(s)", 'e.setAttribute("onclick","go()")',
                                   'b.setAttribute("oninput","return;")', "e.outerHTML=t", 'setTimeout("go()",1)',
                                   "e.insertAdjacentHTML(p,t)"])
def test_vendor_chunk_with_any_other_sink_fails(tmp_path, extra):
    web = _with_assets(tmp_path, {"vendor-Ab12.js": _vendor_text(extra=extra)})
    assert "unsafe-js" in _rules(web)


@pytest.mark.parametrize("name", ["index-Ab12.js", "vendor.js", "chunk-vendor-Ab12.js"])
def test_the_allowance_never_reaches_a_file_that_is_not_the_vendor_chunk(tmp_path, name):
    web = _with_assets(tmp_path, {name: _vendor_text()})
    assert "unsafe-js" in _rules(web)


def test_the_allowance_needs_the_assets_folder(tmp_path):
    web = _copy_web(tmp_path)
    (web / "vendor-Ab12.js").write_text(_vendor_text(), encoding="utf-8")
    assert "unsafe-js" in _rules(web)

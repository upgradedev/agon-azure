"""Proof that the deploy stamp and the data check fail on broken inputs.

Each negative case asserts the reason, not only the exit code. The good case
generates real seeded data with the package's own manifest writer, so the
check runs against exactly what the deploy job ships.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import stamp_web
from agon.synth.manifest import build_bundle, write_outputs

SHA = "0123456789abcdef0123456789abcdef01234567"
SHELL = '<!doctype html><html><body data-commit="__AGON_COMMIT__"><p>Agon</p></body></html>'


def make_web(tmp_path: Path, html: str = SHELL) -> Path:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_bytes(html.encode("utf-8"))
    (web / "app.js").write_bytes(b"console.log('agon');\n")
    return web


def make_data(web: Path, seeds: tuple[int, ...] = (1,)) -> Path:
    data = web / "data"
    write_outputs(str(data), [build_bundle(s) for s in seeds], 0)
    return data


def test_good_stamp_and_data_pass(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    code = stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(data), "--seeds", "1"])
    assert code == 0
    html = (web / "index.html").read_text(encoding="utf-8")
    assert f'data-commit="{SHA}"' in html and "__AGON_COMMIT__" not in html


@pytest.mark.parametrize("sha", ["", "abc1234", SHA.upper(), SHA + "0", SHA[:-1] + "g", "HEAD"])
def test_sha_must_be_full_lowercase_hex(tmp_path: Path, sha: str) -> None:
    web = make_web(tmp_path)
    with pytest.raises(stamp_web.StampError, match="40 lowercase hex"):
        stamp_web.stamp_commit(web, sha)
    assert "__AGON_COMMIT__" in (web / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("html,count", [("<p>no placeholder</p>", 0), (SHELL + "<!-- __AGON_COMMIT__ -->", 2)])
def test_placeholder_must_appear_exactly_once(tmp_path: Path, html: str, count: int) -> None:
    web = make_web(tmp_path, html)
    with pytest.raises(stamp_web.StampError, match=f"{count} times"):
        stamp_web.stamp_commit(web, SHA)


def test_placeholder_left_in_another_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    (web / "app.js").write_bytes(b"var c = '__AGON_COMMIT__';\n")
    with pytest.raises(stamp_web.StampError, match="app.js"):
        stamp_web.stamp_commit(web, SHA)


def test_tampered_data_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    events = data / "seed-0001" / "events.ndjson"
    raw = bytearray(events.read_bytes())
    raw[10] = ord("X") if raw[10] != ord("X") else ord("Y")
    events.write_bytes(bytes(raw))
    with pytest.raises(stamp_web.StampError, match="events.ndjson: sha256"):
        stamp_web.verify_data(data, [1])


def test_truncated_data_file_fails_on_size(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    world = data / "seed-0001" / "world.json"
    world.write_bytes(world.read_bytes()[:-1])
    with pytest.raises(stamp_web.StampError, match="world.json: .* bytes"):
        stamp_web.verify_data(data, [1])


def test_stray_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    (data / "seed-0001" / "extra.json").write_bytes(b"{}\n")
    with pytest.raises(stamp_web.StampError, match="not listed in the manifest: seed-0001/extra.json"):
        stamp_web.verify_data(data, [1])


def test_missing_listed_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    (data / "seed-0001" / "match.json").unlink()
    with pytest.raises(stamp_web.StampError, match="match.json, which does not exist"):
        stamp_web.verify_data(data, [1])


def test_seed_set_must_match(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    with pytest.raises(stamp_web.StampError, match=r"lists seeds \[1\], expected \[1, 2\]"):
        stamp_web.verify_data(data, [1, 2])


def test_manifest_path_escape_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../index.html"
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(stamp_web.StampError, match="escapes"):
        stamp_web.verify_data(data, [1])


def test_missing_manifest_fails_through_the_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    web = make_web(tmp_path)
    (web / "data").mkdir()
    code = stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(web / "data"), "--seeds", "1"])
    assert code == 1
    assert "manifest.json does not exist" in capsys.readouterr().out

"""Proof that the deploy stamp and the data check fail on broken inputs.

Each negative case asserts the reason, not only the exit code. The good case
writes real seeded data with agon.webdata, the same writer and contract the
browser enforces, so the check runs against exactly what the deploy job ships.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import stamp_web
from agon.webdata import write_web_data

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
    # The first 5 minutes keep the test fast; the contract is the same for full matches.
    write_web_data(str(data), list(seeds), max_minute=5)
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


# The prerendered build label, as console/src/components/shell.tsx BuildLabelView renders it with no
# commit, and the exact markup it renders for SHA (console/tests/ssr.test.tsx holds the same string).
UNSTAMPED_P = '<p class="text-xs text-muted" id="run-label">Preview build, commit not stamped (local build)</p>'
STAMPED_P = (
    '<p class="text-xs text-muted" id="run-label">Preview build, commit '
    '<a href="https://github.com/upgradedev/agon-azure/commit/0123456789abcdef0123456789abcdef01234567" '
    'aria-label="commit 0123456789abcdef0123456789abcdef01234567" class="font-mono text-link">0123456</a></p>'
)
LABELLED_SHELL = f'<!doctype html><html><body data-commit="__AGON_COMMIT__"><div id="root">{UNSTAMPED_P}</div></body></html>'


def test_the_prerendered_build_label_names_the_commit_after_stamping(tmp_path: Path) -> None:
    web = make_web(tmp_path, LABELLED_SHELL)
    stamp_web.stamp_commit(web, SHA)
    html = (web / "index.html").read_text(encoding="utf-8")
    assert STAMPED_P in html
    assert "not stamped" not in html and f'data-commit="{SHA}"' in html


def test_two_unstamped_build_labels_are_refused(tmp_path: Path) -> None:
    web = make_web(tmp_path, LABELLED_SHELL.replace("</div>", UNSTAMPED_P + "</div>"))
    with pytest.raises(stamp_web.StampError, match="unstamped build label 2 times"):
        stamp_web.stamp_commit(web, SHA)
    assert "__AGON_COMMIT__" in (web / "index.html").read_text(encoding="utf-8")


def test_a_not_stamped_sentence_outside_the_build_label_is_refused(tmp_path: Path) -> None:
    web = make_web(tmp_path, SHELL.replace("<p>Agon</p>", "<p>Preview build, commit not stamped (local build)</p>"))
    with pytest.raises(stamp_web.StampError, match="still says the build is not stamped"):
        stamp_web.stamp_commit(web, SHA)


def test_placeholder_left_in_another_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    (web / "app.js").write_bytes(b"var c = '__AGON_COMMIT__';\n")
    with pytest.raises(stamp_web.StampError, match="app.js"):
        stamp_web.stamp_commit(web, SHA)


def test_tampered_data_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    events = data / "1.ndjson"
    raw = bytearray(events.read_bytes())
    raw[10] = ord("X") if raw[10] != ord("X") else ord("Y")
    events.write_bytes(bytes(raw))
    with pytest.raises(stamp_web.StampError, match="1.ndjson: sha256"):
        stamp_web.verify_data(data, [1])


def test_event_count_must_match(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["events"] += 1
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(stamp_web.StampError, match="event lines, manifest says"):
        stamp_web.verify_data(data, [1])


def test_stray_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    (data / "extra.json").write_bytes(b"{}\n")
    with pytest.raises(stamp_web.StampError, match="not listed in the manifest: extra.json"):
        stamp_web.verify_data(data, [1])


def test_missing_listed_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    (data / "1.ndjson").unlink()
    with pytest.raises(stamp_web.StampError, match="1.ndjson, which does not exist"):
        stamp_web.verify_data(data, [1])


def test_seed_set_must_match(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    with pytest.raises(stamp_web.StampError, match=r"lists seeds \[1\], expected \[1, 2\]"):
        stamp_web.verify_data(data, [1, 2])


def test_manifest_name_escape_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["name"] = "../index.html"
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(stamp_web.StampError, match="web data contract"):
        stamp_web.verify_data(data, [1])


def test_generator_manifest_shape_is_refused(tmp_path: Path) -> None:
    # The regression that shipped on 2026-10-19: the deploy wrote the synth CLI's
    # manifest, the browser refused it and fell back to the fixture. Now the deploy refuses it.
    web = make_web(tmp_path)
    data = web / "data"
    data.mkdir()
    (data / "manifest.json").write_text(json.dumps({"seeds": [{"seed": 1}], "files": [{"path": "x", "bytes": 1, "sha256": "0" * 64}]}), encoding="utf-8")
    with pytest.raises(stamp_web.StampError, match="web data contract"):
        stamp_web.verify_data(data, [1])


def test_missing_manifest_fails_through_the_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    web = make_web(tmp_path)
    (web / "data").mkdir()
    code = stamp_web.main([str(web), "--sha", SHA, "--data-dir", str(web / "data"), "--seeds", "1"])
    assert code == 1
    assert "manifest.json does not exist" in capsys.readouterr().out


# ---------------------------------------------------------------- world files


def test_good_data_counts_world_files(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web, seeds=(1, 2))
    assert stamp_web.verify_data(data, [1, 2]) == 4


def test_tampered_world_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    world = data / "1.world.json"
    world.write_bytes(world.read_bytes().replace(b"Pellmoor Harriers", b"Pellmoor Rovers!"))
    with pytest.raises(stamp_web.StampError, match="1.world.json: sha256"):
        stamp_web.verify_data(data, [1])


def test_missing_world_file_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    (data / "1.world.json").unlink()
    with pytest.raises(stamp_web.StampError, match="1.world.json, which does not exist"):
        stamp_web.verify_data(data, [1])


def test_world_file_with_a_matching_hash_but_a_bad_shape_fails(tmp_path: Path) -> None:
    """A re-hashed manifest cannot smuggle a world the browser would refuse."""
    import hashlib

    web = make_web(tmp_path)
    data = make_data(web)
    bad = b'{"clubs":[],"seed":1}\n'
    (data / "1.world.json").write_bytes(bad)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["world_sha256"] = hashlib.sha256(bad).hexdigest()
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(stamp_web.StampError, match="breaks the world contract"):
        stamp_web.verify_data(data, [1])


def test_world_name_outside_the_contract_fails(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    data = make_data(web)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["world"] = "../index.html"
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(stamp_web.StampError, match="web data contract"):
        stamp_web.verify_data(data, [1])

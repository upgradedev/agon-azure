"""Manifest hashes: determinism, sensitivity, file output and the cross-OS comparator's teeth."""

from __future__ import annotations

import os

import pytest

from agon.synth import crosscheck, manifest
from agon.synth.manifest import DEFAULT_MATCH_INDEX, DEFAULT_SEEDS, build_bundle, hash_lines, parse_seeds


def test_parse_seeds() -> None:
    assert parse_seeds("1,2,3") == [1, 2, 3]
    assert parse_seeds("7, 1-3") == [1, 2, 3, 7]
    with pytest.raises(ValueError):
        parse_seeds("")
    with pytest.raises(ValueError):
        parse_seeds("5-2")


def test_same_seed_same_hash() -> None:
    assert build_bundle(2).sha256() == build_bundle(2).sha256()


def test_different_seed_different_hash() -> None:
    hashes = {build_bundle(s).sha256() for s in (1, 2, 3)}
    assert len(hashes) == 3


def test_offset_seeds_and_non_default_match_index() -> None:
    # Fixture deliberately starts away from the code defaults (seeds 1..3, match index 0).
    seeds = [101, 102, 103]
    index = 7
    assert seeds[0] not in DEFAULT_SEEDS and index != DEFAULT_MATCH_INDEX
    first = [build_bundle(s, index).sha256() for s in seeds]
    again = [build_bundle(s, index).sha256() for s in seeds]
    assert first == again
    assert len(set(first)) == 3
    default_index = [build_bundle(s, DEFAULT_MATCH_INDEX).sha256() for s in seeds]
    assert all(a != b for a, b in zip(first, default_index))
    bundle = build_bundle(101, index)
    assert bundle.match["match_index"] == index and bundle.match["seed"] == 101


def test_hash_lines_are_ascii_and_pathless() -> None:
    lines = hash_lines([build_bundle(1)], DEFAULT_MATCH_INDEX)
    assert lines[-1].startswith("combined sha256 ")
    for line in lines:
        line.encode("ascii")
        assert "/" not in line and "\\" not in line


def _run_cli(tmp: str, name: str, seeds: str = "1,2") -> str:
    base = os.path.join(tmp, name)
    code = manifest.main(["--seeds", seeds, "--hash-out", os.path.join(base, "hash.txt"), "--out", os.path.join(base, "out")])
    assert code == 0
    return base


def test_cli_writes_lf_only_files_and_manifest(tmp_path) -> None:
    base = _run_cli(str(tmp_path), "a")
    for root, _dirs, files in os.walk(base):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                assert b"\r" not in fh.read(), f
    assert os.path.exists(os.path.join(base, "out", "manifest.json"))
    assert os.path.exists(os.path.join(base, "out", "seed-0002", "events.ndjson"))


def test_crosscheck_passes_identical_and_fails_on_one_flipped_byte(tmp_path, capsys) -> None:
    a = _run_cli(str(tmp_path), "a")
    b = _run_cli(str(tmp_path), "b")
    assert crosscheck.main([a, b]) == 0
    target = os.path.join(b, "out", "seed-0001", "events.ndjson")
    with open(target, "rb") as fh:
        data = bytearray(fh.read())
    pos = data.index(b'"seq":3,')
    data[pos + 6] = ord("4")
    with open(target, "wb") as fh:
        fh.write(bytes(data))
    assert crosscheck.main([a, b]) == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out and "seed-0001/events.ndjson: first difference at line 4" in out


def test_crosscheck_fails_on_hash_file_difference(tmp_path) -> None:
    a = _run_cli(str(tmp_path), "a")
    b = _run_cli(str(tmp_path), "b", seeds="1,3")
    assert crosscheck.main([a, b]) == 1

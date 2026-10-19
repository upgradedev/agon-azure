"""The machine-readable M2 record (`bands --json-out`) and the committed held-out record.

The committed `src/agon/synth/bands_heldout.json` is re-measured on ubuntu and windows by the
determinism workflow, which fails when the fresh output differs from it. Here: the writer, its
refusals, and that the committed record is the held-out measurement for this generator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agon.synth import bands
from agon.synth.engine import GENERATOR_VERSION
from agon.synth.schema import canonical_bytes


def test_json_out_matches_the_printed_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "m2.json"
    code = bands.main(["--seeds", "1001-1003", "--json-out", str(out)])
    assert code in (0, 1)
    record = json.loads(out.read_bytes())
    printed = capsys.readouterr().out.splitlines()
    assert printed[-1] == f"M2 passed {record['passed']} of 22 (threshold 20)"
    rows = {line.split()[0]: line.split()[-1] for line in printed[3:-1]}
    assert {r["id"]: r["status"].split()[-1] for r in record["rows"]} == rows
    assert out.read_bytes() == canonical_bytes(record) + b"\n"
    assert record["command"] == "python -m agon.synth.bands --seeds 1001-1003"


def test_report_record_refuses_bands_that_are_not_frozen():
    rows = bands.evaluate(bands.load_bands(), bands.observe(bands.collect_stats([1001])))
    with pytest.raises(bands.BandsError, match="NOT THE FROZEN BANDS"):
        bands.report_record(rows, "1001", "0" * 64, 0)
    record = bands.report_record(rows, "1001", bands.FROZEN_BANDS_SHA256, 7)
    assert record["command"] == "python -m agon.synth.bands --seeds 1001 --match-index 7"


def test_committed_record_is_the_held_out_measurement_for_this_generator():
    raw = Path(bands.PACKAGE_DIR, "bands_heldout.json").read_bytes()
    record = json.loads(raw)
    assert raw == canonical_bytes(record) + b"\n"
    assert record["seeds"] == "1001-1200" and record["match_index"] == 0
    assert record["command"] == "python -m agon.synth.bands --seeds 1001-1200"
    assert record["generator"] == GENERATOR_VERSION
    assert record["bands_sha256"] == bands.FROZEN_BANDS_SHA256
    assert [r["id"] for r in record["rows"]] == [b.id for b in bands.load_bands()]
    not_measurable = [r["id"] for r in record["rows"] if r["status"] == bands.NOT_MEASURABLE]
    assert not_measurable == ["sprints_per_outfield_player", "distance_per_outfield_player_km"]

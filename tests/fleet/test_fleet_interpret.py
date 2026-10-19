"""The interpret port: `AgonInterpret` runs agon.interpret on the run's event log (spec 4 row 2).

The checks read the package and the Fact Gate's recompute code independently of the adapter, so a
candidate whose swing, score or kind drifted from them fails here. Seeds 9 and 59 carry a
sending-off and a missed penalty, so every candidate reason is exercised.
"""

from __future__ import annotations

import json

import pytest
from fleet_support import INTERPRET_SOURCE

from agon.fleet.interpret_port import KIND_OF_REASON, AgonInterpret, default_interpret
from agon.fleet.local import fake_deps
from agon.fleet.sources import SynthMatchSource
from agon.governance.recompute import EVENT_KINDS, display_minute, events_of_kind, score_after
from agon.interpret import metrics, wp
from agon.interpret.frame import MatchView
from agon.interpret.model import read_lock
from agon.synth.schema import canonical, canonical_bytes

SEEDS = (1, 2, 3, 9, 59)
REQUIRED_CANDIDATE_KEYS = {"event_id", "minute", "kind", "team", "player", "score_before", "score_after", "swing"}
# Measured 9.7 to 12.4 KB on seeds 1 to 3 (2026-10-20). The Draft carries this in every
# checkpointed message and sends it to the Analyst, so a digest that grows past the cap fails.
MAX_OUTPUT_BYTES = 24_000


def _record(seed: int):
    return SynthMatchSource().load({"source": "synth", "seed": seed, "match_index": 0})


@pytest.fixture(scope="module")
def outputs() -> dict[int, dict]:
    port = default_interpret()
    return {seed: port.interpret(_record(seed)) for seed in SEEDS}


def test_every_deps_builder_uses_the_real_adapter_and_names_the_model() -> None:
    port = default_interpret()
    assert isinstance(port, AgonInterpret)
    assert isinstance(fake_deps().interpret, AgonInterpret), "the fakes exercise the real interpret code"
    assert port.source == INTERPRET_SOURCE
    assert port.model_sha256 == read_lock() and port.model_sha256.startswith(INTERPRET_SOURCE.split("/")[1])
    assert "seeds 1-500" in port.note and "win probability, in ppm" in port.note


@pytest.mark.parametrize("seed", SEEDS)
def test_output_is_json_native_canonical_deterministic_and_small(seed: int, outputs: dict[int, dict]) -> None:
    out = outputs[seed]
    assert {"source", "note", "units", "final_score", "candidates", "metrics"} <= set(out)
    assert out["source"] == INTERPRET_SOURCE and out["model_sha256"] == read_lock()
    assert json.loads(json.dumps(out)) == out, "a checkpoint must decode to the same value"
    canonical(out)  # refuses floats
    size = len(canonical_bytes(out))
    assert size <= MAX_OUTPUT_BYTES, f"interpret output is {size} bytes, the cap is {MAX_OUTPUT_BYTES}"
    assert AgonInterpret().interpret(_record(seed)) == out, "a fresh process computes the same output"


@pytest.mark.parametrize("seed", SEEDS)
def test_candidates_carry_the_package_swing_and_the_fact_gate_scores(seed: int, outputs: dict[int, dict]) -> None:
    record = _record(seed)
    facts = record.facts()
    view = MatchView.from_bundle(record.meta, record.events)
    by_id = {e.event_id: e for e in record.events}
    out = outputs[seed]
    assert out["final_score"] == record.meta["score"]
    candidates = out["candidates"]
    assert candidates
    swings = [c["swing"] for c in candidates]
    assert swings == sorted(swings, reverse=True), "most important first"
    for c in candidates:
        assert REQUIRED_CANDIDATE_KEYS <= set(c)
        e = by_id[c["event_id"]]
        assert EVENT_KINDS[c["kind"]](e), f"{c['kind']} does not describe {c['event_id']}"
        assert c["kind"] == KIND_OF_REASON[c["reason"]]
        assert c["minute"] == display_minute(e.period, e.minute)
        assert (c["team"], c["player"]) == (e.team, e.player)
        assert c["score_after"] == score_after(facts, e.seq)
        sw = wp.swing(view, c["event_id"])
        assert c["score_before"] == sw["score_before"], "interpret and the Fact Gate agree on the score"
        expected = sw.get("expected_swing_ppm", 0)
        assert c["swing_realised_ppm"] == sw["delta_team_ppm"] and c["swing_expected_ppm"] == expected
        assert c["swing"] == max(abs(sw["delta_team_ppm"]), abs(expected))
        assert c["swing_basis"] == ("realised" if abs(sw["delta_team_ppm"]) >= abs(expected) else "expected")
        for probs in (c["wp_before"], c["wp_after"]):
            assert sum(probs.values()) == 1_000_000
        if c["reason"] == "sending_off":
            assert c["swing_realised_ppm"] <= 0, "a sending-off never helps the club of the player sent off"
    if len(candidates) < 16:  # nothing was cut at the package's limit
        decisive = events_of_kind(facts.events, "goal") + events_of_kind(facts.events, "red_card")
        assert {e.event_id for e in decisive} <= {c["event_id"] for c in candidates}


def test_every_candidate_reason_is_exercised(outputs: dict[int, dict]) -> None:
    reasons = {c["reason"] for out in outputs.values() for c in out["candidates"]}
    assert reasons == set(KIND_OF_REASON)


@pytest.mark.parametrize("seed", (1, 9))
def test_metrics_are_the_package_metrics_cut_to_what_the_analyst_reads(seed: int, outputs: dict[int, dict]) -> None:
    record = _record(seed)
    view = MatchView.from_bundle(record.meta, record.events)
    got = outputs[seed]["metrics"]
    assert set(got) == {"totals", "ppda", "control_vs_chaos", "pressure_regimes", "tempo"}
    assert got["totals"] == metrics.totals(view) and set(got["totals"]) == {record.meta["home"], record.meta["away"]}
    assert got["ppda"] == metrics.ppda(view)
    assert got["tempo"] == metrics.tempo_bands(view)
    chaos = metrics.control_vs_chaos(view)
    assert [(w["window"], w["index_permille"], w["band"]) for w in got["control_vs_chaos"]] == [
        (w["window"], w["index_permille"], w["band"]) for w in chaos
    ]
    for team, data in metrics.pressure_regimes(view).items():
        assert got["pressure_regimes"][team]["changes"] == data["changes"]
        assert [(r["band"], r["start"], r["end"], r["pressures"]) for r in got["pressure_regimes"][team]["regimes"]] == [
            (r["band"], r["start"], r["end"], r["pressures"]) for r in data["regimes"]
        ]


def test_units_name_every_unit_the_analyst_reads(outputs: dict[int, dict]) -> None:
    units = outputs[1]["units"]
    assert "ppm" in units["swing"] and "win probability" in units["swing"]
    for key in ("swing_realised_ppm", "swing_expected_ppm", "wp_before, wp_after", "xg_ppm, xt_added_ppm", "ppda_centi", "index_permille", "window"):
        assert units[key], key

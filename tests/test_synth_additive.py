"""The season work is additive: the outputs the live site already depends on must not move.

Every literal below was printed on origin/main at commit 50a8a06, before any season code existed:

    python -m agon.synth.manifest --seeds 1,2,3 --print-hash

and, for the seed 1 event log, sha256 over `build_bundle(1).events_ndjson()` (1897 events). The live
site, the readiness gate and the committed sample run all read seed 1, match index 0. If a later
change moves any of these values, that change is not additive: stop and report, never re-pin.
"""

from __future__ import annotations

from agon.synth.manifest import build_bundle, hash_lines
from agon.synth.schema import sha256_hex

M1_COMBINED_SEEDS_1_2_3 = "1dc93944eadc37731416524a588b5c309120e2fdc9b058baf62aed6815e36ebc"
M1_PER_SEED = {
    1: "976215472f5879d54cf5545896331dbe807b39e9ef3604279309e3752aefcb66",
    2: "a45942c0d16baf32959ecc8d211cb378ee81f20cceb082b1f6b4a9f990374f8c",
    3: "946ae11acd7f4f7958ca34006620ac85ce6ad6a2366f9e1e66cc290cfb650bb8",
}
SEED_1_EVENTS_NDJSON_SHA256 = "8e8951745f559cc6d4f5a89a7a7803ba7c8dfa1ca1e983cf5eae2cefa0ac8801"
SEED_1_EVENT_COUNT = 1897


def test_m1_hash_lines_for_seeds_1_2_3_are_unchanged():
    bundles = [build_bundle(seed) for seed in (1, 2, 3)]
    lines = hash_lines(bundles, 0)
    assert lines[0] == "generator synth-v0.2 package 0.1.0 match_index 0"
    for bundle in bundles:
        assert bundle.sha256() == M1_PER_SEED[bundle.seed], bundle.seed
    assert lines[-1] == f"combined sha256 {M1_COMBINED_SEEDS_1_2_3}"


def test_seed_1_event_log_is_byte_identical():
    bundle = build_bundle(1)
    assert len(bundle.events) == SEED_1_EVENT_COUNT
    assert sha256_hex(bundle.events_ndjson()) == SEED_1_EVENTS_NDJSON_SHA256

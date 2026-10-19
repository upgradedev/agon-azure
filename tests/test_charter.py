"""The Match-Day Charter (agon.charter): deterministic bytes, and a gate that fails on each planted fault."""

from __future__ import annotations

import copy
import json

import pytest

from agon import charter as C
from agon.fleet.sources import SynthMatchSource
from agon.governance import rights_lists
from agon.synth import blocklist as synth_blocklist

SOURCE = SynthMatchSource()
# Pinned: the charter the officer approves is these exact bytes for seed 1, match 0 with the default
# voices. A change to a template, a threshold, a held class or the roster changes this hash, and
# with it the hash on the next Teams card; re-pin only on purpose.
CHARTER_SEED1_SHA256 = "9c7de25e513fd31fccec74d29f1a896d262f77cac3643d390c7ecc75382c07c2"


def _record(seed: int = 1):
    return SOURCE.load({"source": "synth", "seed": seed, "match_index": 0})


def _charter(seed: int = 1) -> dict:
    return C.build_charter(_record(seed))


def _check(charter: dict, seed: int = 1) -> C.CharterVerdict:
    return C.charter_check(charter, _record(seed).roster)


def test_the_charter_is_deterministic_and_pinned():
    first, second = C.charter_bytes(_charter()), C.charter_bytes(_charter())
    assert first == second
    assert C.sha256_hex(first) == CHARTER_SEED1_SHA256
    assert json.loads(first)["schema"] == C.SCHEMA


def test_a_built_charter_passes_both_gates_on_several_seeds():
    for seed in (1, 2, 3, 9):
        verdict = _check(_charter(seed), seed)
        assert verdict.passed, (seed, verdict)
        assert (verdict.charter_check, verdict.rights) == ("pass", "pass")


def test_every_template_is_in_every_language_with_only_allowed_slots():
    charter = _charter()
    assert set(charter["templates"]) == set(C.TEMPLATE_IDS)
    for tid, template in charter["templates"].items():
        assert set(template["text"]) == set(C.LANGUAGES), tid
        assert template["overlay"] in C.OVERLAYS
        for text in template["text"].values():
            assert set(C.placeholders(text)) <= set(C.TEMPLATE_SPECS[tid]["slots"]), (tid, text)


def test_the_overlay_types_cover_lower_third_player_tag_and_stat_card():
    overlays = {t["overlay"] for t in _charter()["templates"].values()}
    assert overlays == {"lower_third", "player_tag", "stat_card"}


def test_the_speed_and_distance_thresholds_are_the_briefs():
    th = _charter()["thresholds"]
    assert (th["sprint_kmh_x10"], th["top_speed_kmh_x10"], th["distance_milestone_dm"]) == (252, 300, 20000)


def test_the_charter_carries_no_real_name_and_no_dash():
    """The Rights Guard lists are named by hash only: no deny-list entry is ever published."""
    text = C.charter_bytes(_charter()).decode("utf-8")
    deny = (
        *synth_blocklist.FIELD_NAMES, *synth_blocklist.REAL_TOP_FLIGHT_CLUBS, *rights_lists.LEAGUES,
        *rights_lists.BROADCASTERS, *rights_lists.PLAYERS_FULL, *rights_lists.PLAYER_SURNAMES,
    )
    found = [name for name in deny if len(name) > 3 and name in text]
    assert found == []
    assert chr(0x2014) not in text and chr(0x2013) not in text
    assert set(_charter()["rights_guard"]["lists"]) == {
        "broadcasters", "field_names", "greek_stems", "leagues", "player_surnames", "players_full", "prose_ambiguous", "real_clubs",
    }


# ----------------------------------------------------------------- the gate fails once on each fault


def test_a_planted_bad_template_is_refused():
    malformed = copy.deepcopy(_charter())
    malformed["templates"]["goal"]["text"]["en"] = "GOAL {minute: {player}"
    verdict = _check(malformed)
    assert verdict.charter_check == "refused" and verdict.rights == "refused"
    assert any("brace" in r for r in verdict.charter_reasons), verdict.charter_reasons

    missing_language = copy.deepcopy(_charter())
    del missing_language["templates"]["red_card"]["text"]["el"]
    assert any("exactly" in r for r in _check(missing_language).charter_reasons)


def test_a_real_name_template_is_refused_by_the_rights_guard():
    charter = copy.deepcopy(_charter())
    real = rights_lists.PLAYER_SURNAMES[0]
    charter["templates"]["goal"]["text"]["en"] = "GOAL {minute}: {player} ({club}), the new " + real + "."
    verdict = _check(charter)
    assert verdict.charter_check == "pass"
    assert verdict.rights == "refused"
    assert any("real_player" in r for r in verdict.rights_reasons), verdict.rights_reasons


def test_an_unknown_slot_is_refused():
    charter = copy.deepcopy(_charter())
    charter["templates"]["substitution"]["text"]["fr"] = "Remplacement {minute}, {club} : {player_on}, {injury_note}."
    verdict = _check(charter)
    assert verdict.charter_check == "refused"
    assert any("unknown slot 'injury_note'" in r for r in verdict.charter_reasons), verdict.charter_reasons

    declared = copy.deepcopy(_charter())
    declared["templates"]["player_tag"]["slots"].append("score")
    assert any("slot 'score' is not allowed" in r for r in _check(declared).charter_reasons)


def test_held_wording_in_a_template_is_refused():
    for lang, text in (
        ("en", "Substitution {minute}, {club}: {player_on} on for the injured {player_off}."),
        ("el", "Αλλαγή {minute}, {club}: τραυματίστηκε ο {player_off}."),
        ("es", "Cambio {minute}, {club}: el árbitro decide, entra {player_on}."),
        ("fr", 'Remplacement {minute}, {club} : "{player_on}" entre.'),
    ):
        charter = copy.deepcopy(_charter())
        charter["templates"]["substitution"]["text"][lang] = text
        reasons = _check(charter).charter_reasons
        assert any("held class" in r for r in reasons), (lang, reasons)


def test_a_held_class_cannot_be_dropped():
    charter = copy.deepcopy(_charter())
    del charter["held_classes"]["quote_attribution"]
    assert any("held_classes" in r for r in _check(charter).charter_reasons)


def test_names_off_the_fictional_roster_are_refused():
    charter = copy.deepcopy(_charter())
    pid = sorted(charter["names"]["players"])[0]
    charter["names"]["players"][pid]["name"] = "Somebody Else"
    assert any("not on the fictional roster" in r for r in _check(charter).charter_reasons)

    club = copy.deepcopy(_charter())
    club["fixture"]["home"]["name"] = "Invented Rovers"
    assert any("fixture home" in r for r in _check(club).charter_reasons)

    moved = copy.deepcopy(_charter())
    away = moved["fixture"]["away"]["club_id"]
    home_player = next(pid for pid, e in sorted(moved["names"]["players"].items()) if e["club_id"] != away)
    moved["names"]["players"][home_player]["club_id"] = away
    assert any("does not play for" in r for r in _check(moved).charter_reasons)


def test_the_briefs_thresholds_and_the_list_hashes_cannot_drift():
    charter = copy.deepcopy(_charter())
    charter["thresholds"]["sprint_kmh_x10"] = 200
    assert any("sprint_kmh_x10" in r for r in _check(charter).charter_reasons)

    lists = copy.deepcopy(_charter())
    lists["rights_guard"]["lists"]["players_full"] = "0" * 64
    assert any("Rights Guard lists in force" in r for r in _check(lists).charter_reasons)


def test_render_is_plain_substitution_and_refuses_a_missing_slot():
    assert C.render("{player}, {club}", {"player": "A", "club": "B"}) == "A, B"
    with pytest.raises(C.CharterError):
        C.render("{player}, {club}", {"player": "A"})
    with pytest.raises(C.CharterError):
        C.render("{player.__class__}", {"player": "A"})
    assert C.placeholders("{a} {b}") == ["a", "b"]
    assert C.placeholders("{a") is None and C.placeholders("a}") is None


def test_the_summary_names_the_fixture_every_template_and_the_held_classes():
    charter = _charter()
    text = C.summary(charter)
    assert charter["fixture"]["home"]["name"] in text and charter["fixture"]["away"]["name"] in text
    for tid in C.TEMPLATE_IDS:
        assert charter["templates"][tid]["text"]["en"] in text, tid
    assert "Held, never cued" in text
    assert chr(0x2014) not in text and chr(0x2013) not in text

"""The fictional league: deterministic, well-formed, and never a real or rival name."""

from __future__ import annotations

from agon.synth.blocklist import FIELD_NAMES, blocked_reason, is_blocked, normalize
from agon.synth.schema import canonical
from agon.synth.world import CLUB_COUNT, build_world, fixture_list


def test_same_seed_same_world() -> None:
    assert canonical(build_world(5).to_dict()) == canonical(build_world(5).to_dict())


def test_different_seed_different_world() -> None:
    assert canonical(build_world(5).to_dict()) != canonical(build_world(6).to_dict())


def test_blocklist_negative_controls() -> None:
    # The screen must reject the field names and real clubs, in any spelling or case.
    for name in FIELD_NAMES:
        assert is_blocked(name), name
    assert is_blocked("NOVA fc")
    assert is_blocked("Calderwick Rovers")  # substring of a distinctive blocked token
    assert is_blocked("Arsenal")
    assert is_blocked("Olympiacos Rovers")
    assert is_blocked("Bayern München")  # diacritics are stripped before matching
    assert is_blocked("North Bridge")  # spacing does not get round it
    assert is_blocked("")


def test_blocklist_passes_a_neutral_invented_name() -> None:
    assert blocked_reason("Tarnholt Rovers") is None
    assert normalize("  Tarn-Holt  ") == "tarn holt"


def test_no_blocked_names_for_seeds_1_to_200() -> None:
    failures = []
    for seed in range(1, 201):
        for name in build_world(seed).all_names():
            reason = blocked_reason(name)
            if reason is not None:
                failures.append((seed, name, reason))
    assert failures == []


def test_world_shape() -> None:
    league = build_world(11)
    assert len(league.clubs) == CLUB_COUNT
    assert len({c.name for c in league.clubs}) == CLUB_COUNT
    assert len({c.short for c in league.clubs}) == CLUB_COUNT
    assert len({c.kit_primary for c in league.clubs}) == CLUB_COUNT
    all_ids = [p.player_id for c in league.clubs for p in c.players]
    assert len(all_ids) == len(set(all_ids))
    all_names = [p.name for c in league.clubs for p in c.players]
    assert len(all_names) == len(set(all_names))
    for club in league.clubs:
        assert club.kit_primary != club.kit_secondary
        for knob in (club.style.press_height, club.style.directness, club.style.width):
            assert 200 <= knob <= 800
        xi = club.starting_eleven()
        assert len(xi) == 11
        assert [p.position for p in xi].count("GK") == 1
        assert len(club.bench()) == len(club.players) - 11
        for p in club.players:
            for value in (p.passing, p.finishing, p.tackling, p.pace, p.discipline, p.keeping, p.stamina):
                assert 1 <= value <= 99


def test_style_vectors_vary_across_seeds() -> None:
    knobs = {build_world(s).clubs[0].style.directness for s in range(1, 30)}
    assert len(knobs) > 10


def test_fixture_list_is_a_double_round_robin() -> None:
    fixtures = fixture_list()
    assert len(fixtures) == 30
    pairs = [(h, a) for _md, h, a in fixtures]
    assert len(set(pairs)) == 30  # every ordered pairing exactly once
    for md in range(1, 11):
        teams = [t for m, h, a in fixtures if m == md for t in (h, a)]
        assert sorted(teams) == [f"C{i}" for i in range(1, 7)]

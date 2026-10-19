"""The RNG is the root of determinism: pin its reference values and its keying properties."""

from __future__ import annotations

from agon.synth.rng import (
    MASK64,
    PPM,
    Stream,
    below,
    chance_ppm,
    draw_u64,
    splitmix64_sequence,
    weighted_index,
)


def test_splitmix64_matches_published_reference_values() -> None:
    # Reference outputs of splitmix64 (Vigna's splitmix64.c). State 0 gives 0xE220A8397B1DCDAF;
    # state 1234567 gives the sequence used as the reference vector in rand_xoshiro's tests.
    assert splitmix64_sequence(0, 1) == [0xE220A8397B1DCDAF]
    assert splitmix64_sequence(1234567, 5) == [
        6457827717110365317,
        3203168211198807973,
        9817491932198370423,
        4593380528125082431,
        16408922859458223821,
    ]


def test_keyed_draw_regression_values() -> None:
    # Self-generated golden values (recorded 2026-10-19 on Windows, CPython 3.11), not reference
    # vectors: they catch any change to the keying and, run on ubuntu too, any platform drift.
    assert draw_u64(0, 0, 0, 0, 0, 0) == 0x412A985274F63E90
    assert [draw_u64(1, 1, 11, 7, d) for d in range(3)] == [
        0xB3E1D28F2D4EFCB2,
        0x7EFE114C39894E56,
        0xF4A66E0DE63F0232,
    ]


def test_each_key_component_changes_the_draw() -> None:
    base = (5, 3, 11, 42, 9, 0)
    reference = draw_u64(*base)
    for position in range(6):
        changed = list(base)
        changed[position] += 1
        assert draw_u64(*changed) != reference, f"component {position} ignored"


def test_swapped_components_do_not_collide() -> None:
    assert draw_u64(1, 1, 2, 3, 0) != draw_u64(1, 1, 3, 2, 0)
    assert draw_u64(2, 1, 0, 0, 0) != draw_u64(1, 2, 0, 0, 0)


def test_values_are_64_bit() -> None:
    for d in range(200):
        v = draw_u64(9, 9, 9, 9, d)
        assert 0 <= v <= MASK64


def test_cursor_resumed_at_offset_matches_fresh_cursor() -> None:
    # Fixture starts at draw 37, not the default 0, so a cursor that ignored `start` fails here.
    fresh = Stream(77, 4, 12, possession=19)
    for _ in range(37):
        fresh.next_u64()
    resumed = Stream(77, 4, 12, possession=19, start=37)
    assert [fresh.next_u64() for _ in range(25)] == [resumed.next_u64() for _ in range(25)]
    assert resumed.draw == 37 + 25


def test_below_stays_in_range_and_covers_it() -> None:
    seen = set()
    for d in range(2000):
        v = below(draw_u64(3, 0, 1, 0, d), 7)
        assert 0 <= v < 7
        seen.add(v)
    assert seen == set(range(7))


def test_chance_extremes() -> None:
    for d in range(500):
        x = draw_u64(4, 0, 1, 0, d)
        assert chance_ppm(x, 0) is False
        assert chance_ppm(x, PPM) is True


def test_chance_rate_is_close_to_target() -> None:
    hits = sum(chance_ppm(draw_u64(8, 0, 1, 0, d), 250_000) for d in range(20_000))
    assert 4_600 <= hits <= 5_400


def test_weighted_never_picks_zero_weight() -> None:
    for d in range(2000):
        assert weighted_index(draw_u64(6, 0, 1, 0, d), [0, 5, 0, 3]) in (1, 3)


def test_shuffle_is_a_permutation() -> None:
    items = list(range(12))
    out = Stream(2, 0, 1).shuffled(items)
    assert sorted(out) == items
    assert out == Stream(2, 0, 1).shuffled(items)

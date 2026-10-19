"""Counter-based deterministic random numbers keyed by (seed, match, stream, possession, draw, replicate).

Why counter-based: every random value is a pure function of its key, so any single draw can be
recomputed without replaying the ones before it. That lets later code restore a possession and
re-run it with identical randomness (the counterfactual twins), and it lets a cross-OS diff point
at the exact key where two machines disagree.

Why integers only: floating point transcendental functions (exp, log, pow) come from the platform
libm and can differ in the last bit between operating systems. All thresholds here are integers
(parts per million) and every sample is produced by integer multiply and shift, so the results are
identical everywhere CPython runs.

The mixer is the splitmix64 finaliser (Steele, Lea and Flood 2014; constants as published by
Vigna). Each key position is folded in with its own odd constant, so swapping two components
changes the output.
"""

from __future__ import annotations

from typing import Sequence

MASK64 = (1 << 64) - 1
GOLDEN_GAMMA = 0x9E3779B97F4A7C15
PPM = 1_000_000

# One odd constant per key position. Distinct per position so (stream=2, possession=3) and
# (stream=3, possession=2) never collide by symmetry.
_POSITION_CONSTANTS: tuple[int, ...] = (
    0xD1B54A32D192ED03,  # seed
    0xAEF17502108EF2D9,  # match
    0xDB4F0B9175AE2165,  # stream
    0x8CB92BA72F3D8DD7,  # possession
    0xC2B2AE3D27D4EB4F,  # draw
    0x165667B19E3779F9,  # replicate
)
_DOMAIN = 0x41474F4E53594E54  # ASCII "AGONSYNT", separates this key space from plain splitmix64


class StreamId:
    """Named random streams, one per concern, so adding draws to one concern never shifts another."""

    WORLD_CLUBS = 1
    WORLD_PLAYERS = 2
    WORLD_STYLE = 3
    FIXTURE = 4
    CHAIN = 10
    ACTION = 11
    SPATIAL = 12
    TIMING = 13
    DISCIPLINE = 14
    SUBS = 15
    PRESSURE = 16
    SHOT = 17
    PERSONNEL = 18
    STOPPAGE = 19


def mix64(z: int) -> int:
    """splitmix64 output function: a bijective avalanche over 64-bit integers."""
    z &= MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
    return z ^ (z >> 31)


def splitmix64_sequence(state: int, count: int) -> list[int]:
    """Plain splitmix64 generator, kept only so a test can pin the published reference values."""
    out: list[int] = []
    for _ in range(count):
        state = (state + GOLDEN_GAMMA) & MASK64
        out.append(mix64(state))
    return out


def draw_u64(seed: int, match: int, stream: int, possession: int, draw: int, replicate: int = 0) -> int:
    """Return the 64-bit random value for one key. Pure function: same key, same value, any OS."""
    h = _DOMAIN
    for value, constant in zip((seed, match, stream, possession, draw, replicate), _POSITION_CONSTANTS):
        if value < 0 or value > MASK64:
            raise ValueError(f"key component out of range: {value}")
        h = mix64((h ^ ((value * constant) & MASK64)) + GOLDEN_GAMMA)
    return h


def below(x: int, n: int) -> int:
    """Map a 64-bit value to [0, n) by multiply and shift (Lemire). Integer only, no modulo bias worth noting at n << 2**64."""
    if n <= 0:
        raise ValueError("n must be positive")
    return (x * n) >> 64


def chance_ppm(x: int, p_ppm: int) -> bool:
    """True with probability p_ppm / 1,000,000. p_ppm is clamped to [0, PPM]."""
    p = min(max(p_ppm, 0), PPM)
    return below(x, PPM) < p


def weighted_index(x: int, weights: Sequence[int]) -> int:
    """Pick an index with probability proportional to its non-negative integer weight."""
    total = 0
    for w in weights:
        if w < 0:
            raise ValueError("weights must be non-negative")
        total += w
    if total <= 0:
        raise ValueError("at least one weight must be positive")
    r = below(x, total)
    acc = 0
    for i, w in enumerate(weights):
        acc += w
        if r < acc:
            return i
    raise AssertionError("unreachable: cumulative weight exhausted")


class Stream:
    """A cursor over one (seed, match, stream, possession, replicate) key with an auto-incrementing draw counter.

    `start` exists so a caller can resume a stream at draw k and get exactly the values a fresh
    cursor would produce after k draws. A test pins that property.
    """

    __slots__ = ("seed", "match", "stream", "possession", "replicate", "draw")

    def __init__(
        self,
        seed: int,
        match: int,
        stream: int,
        possession: int = 0,
        replicate: int = 0,
        start: int = 0,
    ) -> None:
        self.seed = seed
        self.match = match
        self.stream = stream
        self.possession = possession
        self.replicate = replicate
        self.draw = start

    def next_u64(self) -> int:
        value = draw_u64(self.seed, self.match, self.stream, self.possession, self.draw, self.replicate)
        self.draw += 1
        return value

    def below(self, n: int) -> int:
        return below(self.next_u64(), n)

    def between(self, lo: int, hi: int) -> int:
        """Uniform integer in [lo, hi], both inclusive."""
        if hi < lo:
            raise ValueError("hi must be >= lo")
        return lo + self.below(hi - lo + 1)

    def chance_ppm(self, p_ppm: int) -> bool:
        return chance_ppm(self.next_u64(), p_ppm)

    def weighted(self, weights: Sequence[int]) -> int:
        return weighted_index(self.next_u64(), weights)

    def pick(self, items: Sequence[object]) -> object:
        if not items:
            raise ValueError("cannot pick from an empty sequence")
        return items[self.below(len(items))]

    def shuffled(self, items: Sequence[object]) -> list[object]:
        """Fisher-Yates shuffle driven by this stream; returns a new list."""
        out = list(items)
        for i in range(len(out) - 1, 0, -1):
            j = self.below(i + 1)
            out[i], out[j] = out[j], out[i]
        return out

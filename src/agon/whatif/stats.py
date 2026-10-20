"""Integer-only interval arithmetic for the What-If Lab: Wilson intervals and paired mean differences.

No float reaches an output. z = 1.96 is the rational 196/100, square roots are `math.isqrt` on
scaled integers, and every half-width is rounded OUTWARD (up), so an interval printed here is never
narrower than the exact one. Each function names its formula.
"""

from __future__ import annotations

from math import isqrt
from typing import Sequence

PPM = 1_000_000
Z_NUM = 196  # z = 1.96 for a two-sided 95% interval, as the rational 196 / 100
Z_DEN = 100


def _isqrt_ceil(n: int) -> int:
    r = isqrt(n)
    return r if r * r == n else r + 1


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def round_div(a: int, b: int) -> int:
    """round(a / b), half away from zero, in integers (b > 0)."""
    if b <= 0:
        raise ValueError("divisor must be positive")
    if a >= 0:
        return (2 * a + b) // (2 * b)
    return -((-2 * a + b) // (2 * b))


def wilson_ppm(successes: int, n: int) -> dict[str, int]:
    """Share and Wilson score 95% interval, all in ppm.

    centre = (x + z^2 / 2) / (n + z^2); half = z * sqrt(x (n - x) / n + z^2 / 4) / (n + z^2).
    With z = Z_NUM / Z_DEN: half = Z_NUM * sqrt(A / n) / (2 (n Z_DEN^2 + Z_NUM^2)), where
    A = 4 Z_DEN^2 x (n - x) + Z_NUM^2 n.
    """
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("need 0 <= successes <= n and n > 0")
    z2n, z2d = Z_NUM * Z_NUM, Z_DEN * Z_DEN
    denom = n * z2d + z2n
    centre = round_div((2 * successes * z2d + z2n) * PPM, 2 * denom)
    a = 4 * z2d * successes * (n - successes) + z2n * n
    half = _ceil_div(Z_NUM * _isqrt_ceil(_ceil_div(a * PPM * PPM, n)), 2 * denom)
    return {
        "count": successes,
        "ppm": round_div(successes * PPM, n),
        "lo_ppm": max(0, centre - half),
        "hi_ppm": min(PPM, centre + half),
    }


def _spread(values: Sequence[int]) -> int:
    """n * sum(v^2) - (sum v)^2: n (n - 1) times the sample variance, exact."""
    n = len(values)
    return n * sum(v * v for v in values) - sum(values) ** 2


def _half(spread: int, n: int, scale: int) -> int:
    """ceil(1.96 * scale * sqrt(spread / (n - 1)) / n): the 95% half-width of a mean, scaled."""
    return _ceil_div(Z_NUM * _isqrt_ceil(_ceil_div(scale * scale * spread, n - 1)), Z_DEN * n)


def paired_delta(baseline: Sequence[int], scenario: Sequence[int], scale: int) -> dict[str, int | None]:
    """Mean of the per-replicate differences (scenario minus baseline) with its 95% interval, times `scale`.

    Normal approximation: mean +/- 1.96 * s_d / sqrt(n), s_d the sample standard deviation of the
    paired differences. `unpaired_half` is the half-width the same replays would give if the two
    arms had been drawn independently (Welch: 1.96 * sqrt(s_a^2 / n + s_b^2 / n)); common random
    numbers are worth exactly the gap between the two.
    """
    n = len(baseline)
    if n != len(scenario):
        raise ValueError("paired samples must have the same length")
    if n < 2:
        return {"pairs": n, "baseline_mean": None, "scenario_mean": None, "delta_mean": None,
                "delta_lo": None, "delta_hi": None, "unpaired_half": None}
    d = [s - b for b, s in zip(baseline, scenario)]
    mean = round_div(scale * sum(d), n)
    half = _half(_spread(d), n, scale)
    unpaired = _half(_spread(baseline) + _spread(scenario), n, scale)
    return {
        "pairs": n,
        "baseline_mean": round_div(scale * sum(baseline), n),
        "scenario_mean": round_div(scale * sum(scenario), n),
        "delta_mean": mean,
        "delta_lo": mean - half,
        "delta_hi": mean + half,
        "unpaired_half": unpaired,
    }


def mean_scaled(values: Sequence[int], scale: int = 1) -> int:
    return round_div(scale * sum(values), len(values))


def fixed(value: int, scale: int, places: int) -> str:
    """A scaled integer as a decimal string with `places` decimals, half away from zero (no floats)."""
    step = scale // (10 ** places)
    q = round_div(value, step) if step > 1 else value
    sign = "-" if q < 0 else ""
    q = abs(q)
    whole, frac = divmod(q, 10 ** places)
    return f"{sign}{whole}.{frac:0{places}d}" if places else f"{sign}{whole}"

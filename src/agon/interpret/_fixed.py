"""Fixed-point arithmetic for every value that can reach a hash.

Why `decimal` and not `math`: `math.exp` and `math.log` come from the platform's libm, which is
allowed to differ in the last bit between Linux and Windows, and a last-bit difference can move a
value across a rounding boundary. `decimal.Decimal.exp()` and `.ln()` are correctly rounded
(General Decimal Arithmetic specification, ROUND_HALF_EVEN), computed in integer software, so the
same inputs give the same digits on every OS. Every public function here returns an int.

Units used across the package:
- probabilities and xG/xT values: parts per million (ppm), 0 .. 1_000_000
- shares and indices: per mille, 0 .. 1000
- logistic coefficients: micro-units (1e-6) per feature unit
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Iterable, Sequence

PPM = 1_000_000
PERMILLE = 1000
COEF_SCALE = 1_000_000  # logistic coefficients are stored as integer micro-units
RATE_SCALE = 1_000_000_000  # goal rates are stored as integer nano-goals per second

_CTX = Context(prec=34, rounding=ROUND_HALF_EVEN)
_ONE = Decimal(1)


def _round_int(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_EVEN))


def linear_micro(coefs: Sequence[int], features: Sequence[int]) -> int:
    """Integer linear predictor in micro-units: intercept plus sum of coefficient times feature.

    `coefs[0]` is the intercept; `coefs[i + 1]` pairs with `features[i]`. Exact integer arithmetic.
    """
    if len(coefs) != len(features) + 1:
        raise ValueError("coefficient count must be feature count plus one")
    return coefs[0] + sum(c * f for c, f in zip(coefs[1:], features))


def sigmoid_ppm(z_micro: int) -> int:
    """Logistic function of z (given in micro-units), returned in ppm, correctly rounded."""
    with localcontext(_CTX):
        z = Decimal(z_micro) / COEF_SCALE
        p = _ONE / (_ONE + (-z).exp())
        return _round_int(p * PPM)


def poisson_pmf(lam_nano: int, kmax: int) -> list[Decimal]:
    """P(K = k) for k in 0..kmax, for a Poisson mean given in nano-units (1e-9). Exact Decimal steps."""
    if lam_nano < 0:
        raise ValueError("a Poisson mean cannot be negative")
    with localcontext(_CTX):
        lam = Decimal(lam_nano) / RATE_SCALE
        p = (-lam).exp()
        out = [p]
        for k in range(1, kmax + 1):
            p = p * lam / k
            out.append(p)
        return out


def outcome_ppm(lam_home_nano: int, lam_away_nano: int, lead: int, kmax: int = 20) -> tuple[int, int, int]:
    """(home win, draw, away win) in ppm, from independent Poisson remaining goals and the current lead.

    The truncated mass beyond `kmax` goals is renormalised away. Home and away are rounded half to
    even; the draw takes the remainder so the three always sum to exactly 1_000_000.
    """
    ph = poisson_pmf(lam_home_nano, kmax)
    pa = poisson_pmf(lam_away_nano, kmax)
    with localcontext(_CTX):
        win = draw = loss = Decimal(0)
        for i, pi in enumerate(ph):
            for j, pj in enumerate(pa):
                margin = lead + i - j
                if margin > 0:
                    win += pi * pj
                elif margin == 0:
                    draw += pi * pj
                else:
                    loss += pi * pj
        total = win + draw + loss
        home = _round_int(win / total * PPM)
        away = _round_int(loss / total * PPM)
    return home, PPM - home - away, away


def normalised_entropy_permille(counts: Iterable[int]) -> int:
    """Shannon entropy of the share of N items across groups, divided by log(N), in per mille.

    0 means every item sits in one group; 1000 means every group holds one item. With N <= 1 the
    value is 0 by definition (one item cannot be spread). H / log N = 1 - sum(n ln n) / (N ln N).
    """
    ns = [n for n in counts if n > 0]
    total = sum(ns)
    if total <= 1:
        return 0
    with localcontext(_CTX):
        s = sum((Decimal(n) * Decimal(n).ln() for n in ns), Decimal(0))
        big = Decimal(total) * Decimal(total).ln()
        return _round_int((_ONE - s / big) * PERMILLE)


def ratio_round(num: int, den: int, scale: int) -> int | None:
    """round(scale * num / den), half up, in integers; None when den is 0."""
    if den == 0:
        return None
    return (2 * scale * num + den) // (2 * den)

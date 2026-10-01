"""Hedge-consistent position sizing (paper Section 5.2).

Notional ``leverage * equity`` is split 1/(1+beta) to leg A and beta/(1+beta)
to leg B, so the notional ratio is 1 : beta and the position's return tracks
the change in the log spread divided by (1 + beta).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class SizingError(ValueError):
    """Raised when a pair cannot be sized within exchange or hedge constraints."""


def hedge_weights(beta: float) -> tuple[float, float]:
    if not beta > 0:
        raise SizingError(f"hedge weights need beta > 0, got {beta}")
    return 1.0 / (1.0 + beta), beta / (1.0 + beta)


def target_quantities(
    equity: float, leverage: float, beta: float, price_a: float, price_b: float
) -> tuple[float, float]:
    """Unsigned unit quantities (q_A, q_B) for a new position."""
    if not (equity > 0 and leverage > 0 and price_a > 0 and price_b > 0):
        raise SizingError("equity, leverage and prices must be positive")
    w_a, w_b = hedge_weights(beta)
    notional = leverage * equity
    return w_a * notional / price_a, w_b * notional / price_b


def signed_quantities(side: int, q_a: float, q_b: float) -> tuple[float, float]:
    """Long spread (+1): long A, short B. Short spread (-1): short A, long B."""
    if side not in (1, -1):
        raise SizingError(f"side must be +1 or -1, got {side}")
    return side * q_a, -side * q_b


@dataclass(frozen=True)
class LotRules:
    """Exchange constraints for one symbol (from ccxt market info)."""

    step: float  # quantity increment
    min_qty: float = 0.0
    min_notional: float = 0.0


def _floor_to_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    # Round to the step grid, guarding against float noise such as 2.9999999.
    return math.floor(qty / step + 1e-9) * step


def round_to_lots(
    q_a: float,
    q_b: float,
    price_a: float,
    price_b: float,
    rules_a: LotRules,
    rules_b: LotRules,
    beta: float,
    max_hedge_error: float = 0.05,
) -> tuple[float, float]:
    """Round unsigned quantities down to lot steps and check the hedge survives.

    Raises SizingError when a leg falls below the exchange minimums or when
    rounding moves the notional ratio more than ``max_hedge_error`` (relative)
    away from 1 : beta.
    """
    ra = _floor_to_step(q_a, rules_a.step)
    rb = _floor_to_step(q_b, rules_b.step)
    for qty, px, rules, leg in ((ra, price_a, rules_a, "A"), (rb, price_b, rules_b, "B")):
        if qty <= 0 or qty < rules.min_qty:
            raise SizingError(f"leg {leg} quantity {qty} below minimum {rules.min_qty}")
        if qty * px < rules.min_notional:
            raise SizingError(f"leg {leg} notional {qty * px:.4f} below minimum {rules.min_notional}")
    ratio = (rb * price_b) / (ra * price_a)
    if abs(ratio / beta - 1.0) > max_hedge_error:
        raise SizingError(
            f"hedge ratio after rounding {ratio:.4f} deviates from beta {beta:.4f} "
            f"by more than {max_hedge_error:.0%}"
        )
    return ra, rb

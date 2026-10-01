"""Per-pair accounting: cash, signed leg quantities, fees and funding.

The same book is used by the backtester, the paper broker and (in Phase 3)
the live ledger, so PnL is computed one way everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PairBook:
    cash: float
    qty_a: float = 0.0
    qty_b: float = 0.0
    entry_px_a: float = 0.0
    entry_px_b: float = 0.0
    fees_paid: float = 0.0
    funding_paid: float = 0.0

    @property
    def in_position(self) -> bool:
        return self.qty_a != 0.0 or self.qty_b != 0.0

    def equity(self, px_a: float, px_b: float) -> float:
        """Cash plus unrealized PnL of the open legs."""
        if not self.in_position:
            return self.cash
        return (
            self.cash
            + self.qty_a * (px_a - self.entry_px_a)
            + self.qty_b * (px_b - self.entry_px_b)
        )

    def gross_notional(self, px_a: float, px_b: float) -> float:
        return abs(self.qty_a) * px_a + abs(self.qty_b) * px_b

    def open(self, qty_a: float, qty_b: float, fill_a: float, fill_b: float, fee: float) -> None:
        if self.in_position:
            raise RuntimeError("book already holds a position")
        self.qty_a, self.qty_b = qty_a, qty_b
        self.entry_px_a, self.entry_px_b = fill_a, fill_b
        self.cash -= fee
        self.fees_paid += fee

    def close(self, fill_a: float, fill_b: float, fee: float) -> float:
        """Realize the position at the fill prices. Returns the new cash balance."""
        if not self.in_position:
            raise RuntimeError("book has no position to close")
        self.cash = self.equity(fill_a, fill_b) - fee
        self.fees_paid += fee
        self.qty_a = self.qty_b = 0.0
        self.entry_px_a = self.entry_px_b = 0.0
        return self.cash

    def apply_funding(self, rate_a: float, px_a: float, rate_b: float, px_b: float) -> float:
        """Settle one funding event. Longs pay a positive rate; shorts receive it.

        Returns the amount paid (negative = received).
        """
        paid = self.qty_a * px_a * rate_a + self.qty_b * px_b * rate_b
        self.cash -= paid
        self.funding_paid += paid
        return paid

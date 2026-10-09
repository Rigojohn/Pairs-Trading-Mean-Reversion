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

    def reduce(self, qty_a: float, qty_b: float, fill_a: float, fill_b: float, fee: float) -> float:
        """Close part of the position (manual scale-out). Returns the PnL realized.

        ``qty_a`` / ``qty_b`` are the signed quantities traded: against the holdings and no
        larger than them. The closed part's PnL moves into cash and the rest keeps its entry
        prices, so equity at the fill prices only changes by the fee.
        """
        if not self.in_position:
            raise RuntimeError("book has no position to reduce")
        for q, held in ((qty_a, self.qty_a), (qty_b, self.qty_b)):
            if q != 0.0 and (q * held > 0 or abs(q) > abs(held) * (1 + 1e-9)):
                raise RuntimeError("a reduction must trade against the holdings and not exceed them")
        realized = -qty_a * (fill_a - self.entry_px_a) - qty_b * (fill_b - self.entry_px_b)
        self.cash += realized - fee
        self.fees_paid += fee
        self.qty_a = 0.0 if abs(self.qty_a + qty_a) <= abs(self.qty_a) * 1e-9 else self.qty_a + qty_a
        self.qty_b = 0.0 if abs(self.qty_b + qty_b) <= abs(self.qty_b) * 1e-9 else self.qty_b + qty_b
        if not self.in_position:
            self.entry_px_a = self.entry_px_b = 0.0
        return realized

    def add(self, qty_a: float, qty_b: float, fill_a: float, fill_b: float, fee: float) -> None:
        """Add to the position (manual scale-in) at the fill prices.

        ``qty_a`` / ``qty_b`` are the signed quantities traded, in the direction of the holdings.
        Each leg's entry price becomes the quantity-weighted average, so equity at the fill
        prices only changes by the fee.
        """
        if not self.in_position:
            raise RuntimeError("book has no position to add to")
        for q, held in ((qty_a, self.qty_a), (qty_b, self.qty_b)):
            if q * held < 0 or (q != 0.0 and held == 0.0):
                raise RuntimeError("an addition must trade in the direction of the holdings")
        for leg, q, fill in (("a", qty_a, fill_a), ("b", qty_b, fill_b)):
            held, entry = getattr(self, f"qty_{leg}"), getattr(self, f"entry_px_{leg}")
            if q != 0.0:
                setattr(self, f"entry_px_{leg}", (held * entry + q * fill) / (held + q))
                setattr(self, f"qty_{leg}", held + q)
        self.cash -= fee
        self.fees_paid += fee

    def apply_funding(self, rate_a: float, px_a: float, rate_b: float, px_b: float) -> float:
        """Settle one funding event. Longs pay a positive rate; shorts receive it.

        Returns the amount paid (negative = received).
        """
        paid = self.qty_a * px_a * rate_a + self.qty_b * px_b * rate_b
        self.cash -= paid
        self.funding_paid += paid
        return paid

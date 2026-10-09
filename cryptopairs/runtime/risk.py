"""RiskManager: portfolio-level checks that sit between strategy intents and execution.

Pair-level rules (stop-loss, cooldown, deactivation) live in ``PairStrategy``
so backtests enforce them too. This class adds what only a live portfolio
can see: a drawdown kill switch, a cap on open pairs, per-symbol exposure
limits across pairs, and quote freshness.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cryptopairs.config import RiskConfig
from cryptopairs.runtime.execution import PairOrder

log = logging.getLogger(__name__)


@dataclass
class Decision:
    approved: bool
    reason: str = ""


class RiskManager:
    def __init__(self, cfg: RiskConfig) -> None:
        self.cfg = cfg
        self.peak_equity: float | None = None
        self.kill_switch = False

    def update_equity(self, total_equity: float) -> bool:
        """Track the portfolio peak. Returns True when the kill switch has just engaged."""
        if self.peak_equity is None or total_equity > self.peak_equity:
            self.peak_equity = total_equity
        if not self.kill_switch and total_equity <= (1 - self.cfg.portfolio_max_drawdown) * self.peak_equity:
            self.kill_switch = True
            log.error("KILL SWITCH: equity %.2f is %.1f%% below peak %.2f; no new entries",
                      total_equity, 100 * (1 - total_equity / self.peak_equity), self.peak_equity)
            return True
        return False

    def approve(
        self,
        order: PairOrder,
        *,
        open_pairs: int,
        gross_by_symbol: dict[str, float],
        total_equity: float,
        quotes_ok: bool,
    ) -> Decision:
        if order.kind not in ("open", "add"):
            return Decision(True)  # closing or reducing risk is always allowed
        if self.kill_switch:
            return Decision(False, "kill switch engaged")
        if order.kind == "open" and open_pairs >= self.cfg.max_open_pairs:
            return Decision(False, f"max open pairs ({self.cfg.max_open_pairs}) reached")
        if self.cfg.require_quotes_for_entry and not quotes_ok:
            return Decision(False, "no fresh quotes")
        cap = self.cfg.max_symbol_gross_leverage * total_equity
        for sym, qty, px in ((order.symbol_a, order.qty_a, order.ref_a), (order.symbol_b, order.qty_b, order.ref_b)):
            exposure = gross_by_symbol.get(sym, 0.0) + abs(qty) * px
            if exposure > cap:
                return Decision(False, f"{sym} gross exposure {exposure:.2f} would exceed cap {cap:.2f}")
        return Decision(True)

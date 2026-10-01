"""Pure strategy core shared by backtest, paper and live trading (no I/O)."""

from cryptopairs.core.book import PairBook
from cryptopairs.core.signals import executable_zscores, fit_ols, rolling_ols, zscore
from cryptopairs.core.sizing import LotRules, SizingError, round_to_lots, signed_quantities, target_quantities
from cryptopairs.core.strategy import FLAT, LONG, SHORT, PairParams, PairStrategy

__all__ = [
    "FLAT", "LONG", "SHORT", "LotRules", "PairBook", "PairParams", "PairStrategy", "SizingError",
    "executable_zscores", "fit_ols", "rolling_ols", "round_to_lots", "signed_quantities",
    "target_quantities", "zscore",
]

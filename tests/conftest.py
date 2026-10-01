"""Synthetic market data shared by the tests."""

from __future__ import annotations

import asyncio

import ccxt
import numpy as np
import pandas as pd

HOUR = 3_600_000
T0 = 1_700_000_000_000 // HOUR * HOUR


def ou_spread(n: int, phi: float = 0.97, vol: float = 0.01, seed: int = 0, break_at: int | None = None) -> np.ndarray:
    """Mean-reverting AR(1) spread; after ``break_at`` it becomes a random walk."""
    rng = np.random.default_rng(seed)
    u = np.zeros(n)
    eps = rng.normal(0, vol, n)
    for i in range(1, n):
        p = 1.0 if (break_at is not None and i >= break_at) else phi
        u[i] = p * u[i - 1] + eps[i]
    return u


def make_pair(
    n: int = 3000, beta: float = 1.1, alpha: float = 0.2, seed: int = 0, break_at: int | None = None,
    start_ms: int = T0, wick: float = 0.002,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Two kline frames whose log prices satisfy log A = alpha + beta log B + u."""
    rng = np.random.default_rng(seed + 1)
    lb = np.cumsum(rng.normal(0, 0.01, n)) + np.log(10.0)
    la = alpha + beta * lb + ou_spread(n, seed=seed, break_at=break_at)
    ts = start_ms + HOUR * np.arange(n, dtype=np.int64)
    return _klines(ts, np.exp(la), wick), _klines(ts, np.exp(lb), wick)


def _klines(ts: np.ndarray, close: np.ndarray, wick: float) -> pd.DataFrame:
    open_ = np.concatenate(([close[0]], close[:-1]))
    hi = np.maximum(open_, close) * (1 + wick)
    lo = np.minimum(open_, close) * (1 - wick)
    return pd.DataFrame({"ts": ts, "open": open_, "high": hi, "low": lo, "close": close,
                         "quote_volume": np.full(ts.shape[0], 1e6)})


# ------------------------------------------------------------- runtime fakes
A, B = "AAA/USDT:USDT", "BBB/USDT:USDT"


class FakeExchange:
    """Minimal async stand-in for a ccxt.pro exchange."""

    def __init__(self, candles: dict[str, list[list[float]]], fail_first: int = 0):
        self.candles = candles
        self.fail_first = fail_first
        self.calls = 0
        self.tickers: dict[str, dict] = {}

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1500):
        self.calls += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ccxt.NetworkError("simulated disconnect")
        rows = [r for r in self.candles[symbol] if since is None or r[0] >= since]
        return rows[:limit]

    async def fetch_bids_asks(self, symbols):
        return {s: self.tickers[s] for s in symbols if s in self.tickers}


class Clock:
    def __init__(self, ms: int):
        self.ms = ms

    def __call__(self) -> float:
        return self.ms / 1000.0

    async def sleep(self, seconds: float) -> None:
        self.ms += int(seconds * 1000)
        await asyncio.sleep(0)  # yield like a real sleep would


def _rows(closes: np.ndarray, start: int = T0) -> list[list[float]]:
    return [[start + i * HOUR, c, c, c, c, 1.0] for i, c in enumerate(closes)]

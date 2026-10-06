"""Intraday charts on the Pair desk: as many candles as asked (Binance pages them by 1000), and back to an
open position's entry, so a scroll to the left finds its BUY/SELL (the chart still opens on the usual window)."""

import numpy as np
import pandas as pd
import pytest

from cryptopairs.ui import market as M
from cryptopairs.ui import swr
from cryptopairs.ui import tvcharts as TC

HOUR = 3_600_000
MIN = 60_000


class FakeBinance:
    """fetch_ohlcv like Binance through ccxt: at most 1000 candles, the forming one last."""

    def __init__(self, now_ms: int):
        self.now = now_ms
        self.calls = []

    def milliseconds(self) -> int:
        return self.now

    def fetch_ohlcv(self, symbol, tf, since=None, limit=500):
        step = M.TF_MS[tf]
        self.calls.append((since, limit))
        limit = min(limit, 1000)
        last = self.now // step * step
        first = last - (limit - 1) * step if since is None else (since + step - 1) // step * step
        ts = list(range(first, min(last, first + (limit - 1) * step) + 1, step))
        return [[t, 1.0, 1.0, 1.0, 1.0 + t / 1e13, 10.0] for t in ts]


@pytest.fixture
def binance(monkeypatch):
    fake = FakeBinance(now_ms=1_790_000_000_000 + 37 * MIN)
    monkeypatch.setattr(M, "exchange", lambda: fake)
    monkeypatch.setattr(M.time, "time", lambda: fake.now / 1000)
    swr.clear()
    yield fake
    swr.clear()


def test_a_day_of_1m_candles_takes_two_requests(binance):
    df = M._candles("BAND/USDT:USDT", "1m", 1440)
    assert len(df) == 1440 and len(binance.calls) == 2
    assert df["ts"].diff().dropna().eq(MIN).all()  # no gap, no repeat
    assert int(df["ts"].iloc[-1]) == binance.now // MIN * MIN  # the forming candle is the last one
    one = M._candles("BAND/USDT:USDT", "1h", 1000)
    assert len(one) == 1000 and len(binance.calls) == 3  # up to 1000: one request, as before


def test_an_open_position_s_entry_is_reached_and_joined_without_gaps(binance):
    entry = binance.now - 40 * HOUR  # older than the day of 1m candles
    df = M.candles_from("BAND/USDT:USDT", "1m", entry - 20 * MIN)
    assert int(df["ts"].iloc[0]) <= entry - 20 * MIN
    assert int(df["ts"].iloc[-1]) == binance.now // MIN * MIN
    assert df["ts"].diff().dropna().eq(MIN).all()
    recent = M.candles_from("BAND/USDT:USDT", "1m", binance.now - 2 * HOUR)  # inside the day: as before
    assert len(recent) == M.CANDLES["1m"]


def test_the_reach_back_is_capped(binance):
    df = M.candles_from("BAND/USDT:USDT", "1m", binance.now - 10 * 24 * HOUR)
    days = (int(df["ts"].iloc[-1]) - int(df["ts"].iloc[0])) / (24 * HOUR)
    assert 3 <= days < 4  # REACH_BACK: 3 days of 1m (from midnight)
    assert M.candles_from("BAND/USDT:USDT", "1h", 0) is not None  # not intraday: the usual candles


def _trade(entry_ms, exit_ms=None, side=-1):
    return {"pair_id": "X", "side": side, "entry_ts": entry_ms, "exit_ts": exit_ms, "reason": "exit_signal",
            "pnl": 1.0, "entry_reason": "signal", "alpha": 0.0, "beta": 1.0, "sigma": 0.02,
            "entry_px_a": 1.02, "entry_px_b": 1.0, "exit_px_a": None, "exit_px_b": None}


def test_a_1m_spread_z_shows_the_entry_of_a_position_opened_two_days_ago():
    now = 1_790_000_000_000 // HOUR * HOUR + 37 * MIN
    entry = now // HOUR * HOUR - 48 * HOUR  # an hourly decision, stamped at the bar's open
    bars = np.arange(entry - 30 * HOUR, now // HOUR * HOUR, HOUR)
    la = np.log(np.full(bars.size, 1.02))
    fit = pd.DataFrame({"bar_ts": bars, "kf_alpha": 0.0, "kf_beta": 1.0, "kf_sigma": 0.02, "used_alpha": np.nan,
                        "used_beta": np.nan, "used_sigma": np.nan, "z": la / 0.02, "log_a": la, "log_b": 0.0})
    ts = np.arange(entry - 20 * MIN, now // MIN * MIN + 1, MIN)
    px = pd.DataFrame({"ts": ts, "a": 1.02, "b": 1.0})
    trades = pd.DataFrame([_trade(int(entry))])
    spec = TC.z_spec(px, fit, trades, "1m", z_entry=0.8, z_exit=0.0, now_ms=now, side=-1)
    z = next(s for s in spec["series"] if s["id"] == "z")
    sell = [m for m in z["markers"] if m["text"].startswith("SELL")]
    assert len(sell) == 1 and sell[0]["time"] == (entry + HOUR) // 1000 + 60  # the first 1m bar after the decision
    assert spec["visible_bars"] == TC.VISIBLE["1m"]  # it opens on the last 6 hours, as always; a scroll left shows it

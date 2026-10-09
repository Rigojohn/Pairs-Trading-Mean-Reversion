"""The Pair desk's Spread Z loads its older history while the user scrolls left: candles in blocks of 1000
from Binance (one request each, kept, with a request budget), and pieces drawn by the very z_spec that
draws the chart, so a bar's Z is the same whichever way it arrived."""

import numpy as np
import pandas as pd
import pytest

from cryptopairs.ui import history as HI
from cryptopairs.ui import market as M
from cryptopairs.ui import swr
from cryptopairs.ui import tvcharts as TC

HOUR = 3_600_000
MIN = 60_000
SYM = "BAND/USDT:USDT"


class FakeBinance:
    """fetch_ohlcv like Binance: at most 1000 candles from ``since``, none before the listing."""

    def __init__(self, now_ms: int, listed_ms: int = 0):
        self.now, self.listed = now_ms, listed_ms
        self.calls = []

    def milliseconds(self) -> int:
        return self.now

    def fetch_ohlcv(self, symbol, tf, since=None, limit=500):
        step = M.TF_MS[tf]
        self.calls.append((symbol, since, limit))
        first = max((since + step - 1) // step * step, (self.listed + step - 1) // step * step)
        last = min(self.now // step * step, first + (min(limit, 1000) - 1) * step)
        return [[t, 1.0, 1.0, 1.0, 1.0 + t / 1e13, 10.0] for t in range(first, last + 1, step)]


@pytest.fixture
def binance(monkeypatch):
    fake = FakeBinance(now_ms=1_790_000_000_000 + 37 * MIN)
    monkeypatch.setattr(M, "exchange", lambda: fake)
    monkeypatch.setitem(M._older_budget, "tokens", float(M.OLDER_PER_MIN))
    M._older_blocks.clear()
    swr.clear()
    yield fake
    M._older_blocks.clear()
    swr.clear()


def test_older_candles_end_exactly_where_the_chart_starts_one_request_per_block(binance):
    end = binance.now // MIN * MIN - 1440 * MIN  # the chart's earliest 1m bar
    df = M.older_candles(SYM, "1m", end)
    assert int(df["ts"].iloc[-1]) == end - MIN  # the bar just before: no gap, no overlap
    assert df["ts"].diff().dropna().eq(MIN).all()
    assert int(df["ts"].iloc[0]) == M.older_start("1m", end)
    n = len(binance.calls)
    assert n in (1, 2)  # one block, or two when the first would be under a quarter of a block
    nxt = M.older_candles(SYM, "1m", int(df["ts"].iloc[0]))  # the next piece back joins it
    assert int(nxt["ts"].iloc[-1]) == int(df["ts"].iloc[0]) - MIN
    again = M.older_candles(SYM, "1m", end)  # closed candles are kept: no new request
    assert again.equals(df) and len(binance.calls) == n + 1


def test_the_request_budget_says_none_instead_of_failing(binance):
    M._older_budget["tokens"] = 0.0
    M._older_budget["at"] = M.time.monotonic()
    assert M.older_candles(SYM, "5m", binance.now - 10 * 24 * HOUR) is None
    assert binance.calls == []


def test_before_the_listing_there_is_nothing(binance):
    binance.listed = binance.now - 3 * 24 * HOUR
    df = M.older_candles(SYM, "5m", binance.listed + 100 * 5 * MIN)
    assert int(df["ts"].iloc[0]) >= binance.listed and len(df) == 100
    assert M.older_candles(SYM, "5m", binance.listed).empty


# ------------------------------------------------------------- pieces of Spread Z
def _history(now: int, hours: int):
    bars = np.arange(now // HOUR * HOUR - hours * HOUR, now // HOUR * HOUR, HOUR)
    rng = np.random.default_rng(1)
    la = np.cumsum(rng.normal(0, 0.003, bars.size))
    fit = pd.DataFrame({"bar_ts": bars, "kf_alpha": 0.001 * np.arange(bars.size) / bars.size, "kf_beta": 1.0,
                        "kf_sigma": 0.02, "used_alpha": np.nan, "used_beta": np.nan, "used_sigma": np.nan,
                        "z": la / 0.02, "log_a": la, "log_b": 0.0})
    return fit


def _trade(entry_ms, exit_ms, side=1):
    return {"pair_id": "X", "side": side, "entry_ts": entry_ms, "exit_ts": exit_ms, "reason": "exit_signal",
            "pnl": 1.0, "entry_reason": "signal", "alpha": 0.0, "beta": 1.0, "sigma": 0.02,
            "entry_px_a": 0.98, "entry_px_b": 1.0, "exit_px_a": 1.0, "exit_px_b": 1.0}


def _z(spec):
    z = next(s for s in spec["series"] if s["id"] == "z")
    return dict(zip(z["data"]["t"], z["data"]["v"]))


def test_a_bar_has_the_same_z_in_an_older_piece_and_in_the_chart():
    now = 1_790_000_000_000 // HOUR * HOUR + 37 * MIN
    fit = _history(now, 400)
    ts = np.arange(now // MIN * MIN - 3000 * MIN, now // MIN * MIN + 1, MIN)
    rng = np.random.default_rng(2)
    px = pd.DataFrame({"ts": ts, "a": np.exp(np.cumsum(rng.normal(0, 1e-4, ts.size))), "b": 1.0})
    old_trade = _trade(int(ts[200]), int(ts[400]))
    trades = pd.DataFrame([old_trade])
    zkw = dict(z_entry=0.8, z_exit=0.0, now_ms=now, side=0)
    whole = _z(TC.z_spec(px, fit, trades, "1m", **zkw))
    recent = px.tail(1440)
    before_s = int(recent["ts"].iloc[0]) // 1000 + 60  # the close of the chart's earliest bar
    piece = HI.older_piece(px[px["ts"] < int(recent["ts"].iloc[0])], fit, trades, "1m", before_s, zkw, False)
    got = dict(zip(piece["series"]["z"]["t"], piece["series"]["z"]["v"]))
    assert got and max(got) < before_s  # strictly before the chart's earliest point
    assert all(abs(got[t] - whole[t]) < 1e-9 for t in got)  # the same Z
    assert not piece["done"]
    texts = [m["text"] for m in piece["markers"]["z"]]
    assert any(t.startswith("BUY") for t in texts) and any(t.startswith("CLOSE") for t in texts)  # old trades
    box = next(s for s in TC.z_spec(recent, fit, trades, "1m", **zkw)["series"] if s["id"] == "z")["boxes"]
    marks = {m["text"].split()[0]: m["time"] for m in piece["markers"]["z"]}
    assert [(b["from"], b["to"]) for b in box] == [(marks["BUY"], marks["CLOSE"])]  # the chart boxes it, too


def test_the_piece_says_done_at_the_start_of_the_history():
    now = 1_790_000_000_000 // HOUR * HOUR + 37 * MIN
    fit = _history(now, 400)
    first_fit = int(fit["bar_ts"].iloc[0]) + HOUR
    ts = np.arange(first_fit - 600 * MIN, first_fit + 600 * MIN, MIN)  # the model starts inside this piece
    px = pd.DataFrame({"ts": ts, "a": 1.0, "b": 1.0})
    zkw = dict(z_entry=0.8, z_exit=0.0, now_ms=now, side=0)
    piece = HI.older_piece(px, fit, pd.DataFrame(), "1m", int(ts[-1]) // 1000 + 60, zkw, False)
    assert piece["done"] and piece["first"] is not None
    empty = HI.older_piece(px.iloc[:0], fit, pd.DataFrame(), "1m", int(ts[0]) // 1000, zkw, False)
    assert empty["done"]
    listed = HI.older_piece(px.tail(100), fit, pd.DataFrame(), "1m", int(ts[-1]) // 1000 + 60, zkw, True)
    assert listed["done"]

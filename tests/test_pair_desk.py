"""Pair desk building blocks: relationship statistics, order-ticket previews, chart specs."""

import math

import numpy as np
import pandas as pd
import pytest

from cryptopairs.core.signals import executable_zscores
from cryptopairs.research import pairstats as PS
from cryptopairs.ui import ticket as T
from cryptopairs.ui import tvcharts as TC

HOUR = 3_600_000


# --------------------------------------------------------------- statistics
def _ar1(n, phi, sd=0.01, seed=0):
    rng = np.random.default_rng(seed)
    u = np.zeros(n)
    for i in range(1, n):
        u[i] = phi * u[i - 1] + rng.normal(0, sd)
    return u


def test_mean_reverting_spread_is_recognised_and_a_random_walk_is_not():
    n = 3000
    rng = np.random.default_rng(1)
    lb = np.log(5) + np.cumsum(rng.normal(0, 0.01, n))
    st = PS.window_stats(0.1 + 1.3 * lb + _ar1(n, 0.95, 0.004), lb)
    assert st["beta_ols"] == pytest.approx(1.3, abs=0.05)
    assert st["half_life_h"] == pytest.approx(math.log(2) / -math.log(0.95), rel=0.35)  # ~13.5 h
    assert st["adf_t"] < PS.EG_CRITICAL["1%"] and st["adf_level"] == "1%"
    assert st["hurst"] < 0.4 and st["corr_price"] > 0.9
    walk = PS.window_stats(np.log(5) + np.cumsum(rng.normal(0, 0.01, n)), lb)
    assert walk["adf_level"] is None and walk["hurst"] > 0.4


def test_rolling_correlation_matches_numpy():
    rng = np.random.default_rng(2)
    x, y = rng.normal(size=500), rng.normal(size=500)
    y = 0.6 * x + 0.8 * y
    rc = PS.rolling_corr(x, y, 100)
    assert np.isnan(rc[:99]).all()
    assert rc[250] == pytest.approx(np.corrcoef(x[151:251], y[151:251])[0, 1], abs=1e-9)


def test_relationship_windows_and_funding_carry():
    n = 40 * 24
    lb = np.cumsum(np.full(n, 0.0001))
    out = PS.relationship(lb + _ar1(n, 0.9), lb)
    assert list(out) == ["30d"]  # 40 days: no 90d / 1y window
    fa, fb = np.zeros(n), np.zeros(n)
    fa[::8], fb[::8] = 0.0001, -0.0001  # every 8 h: longs of A pay, longs of B receive
    long_carry = PS.funding_carry(fa, fb, 1.0, 1)  # long A pays, short B pays (negative rate)
    assert long_carry == pytest.approx(3 * 0.0001, rel=1e-6)  # 3 settlements/day, half of the notional each
    assert PS.funding_carry(fa, fb, 1.0, -1) == pytest.approx(-long_carry)


# ------------------------------------------------------------------ ticket
def test_vwap_walks_the_book():
    px, filled = T.vwap([[10.0, 1.0], [10.5, 2.0], [11.0, 5.0]], 2.0)
    assert px == pytest.approx((10.0 + 10.5) / 2) and filled == 2.0
    px, filled = T.vwap([[10.0, 1.0]], 3.0)
    assert px == 10.0 and filled == 1.0


def _books(mid_a=5.5, mid_b=5.0, spread=0.0002, depth=1e6):
    mk = lambda m: {"bids": [[m * (1 - spread / 2), depth]], "asks": [[m * (1 + spread / 2), depth]]}  # noqa: E731
    return {"A/USDT:USDT": mk(mid_a), "B/USDT:USDT": mk(mid_b)}


PAIR = {"symbol_a": "A/USDT:USDT", "symbol_b": "B/USDT:USDT", "label_a": "A", "label_b": "B", "equity": 100.0,
        "alpha": 0.1, "beta": 1.0, "sigma": 0.02, "z_entry": 2.0, "z_exit": 0.0}
INSTS = {s: {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0, "funding_rate": 0.0001, "funding_hours": 8.0}
         for s in ("A/USDT:USDT", "B/USDT:USDT")}


def test_open_preview_sizes_like_the_bot_and_prices_from_the_book():
    p = T.preview_open(PAIR, 1, 2.0, "market", _books(), INSTS, 0.0005, 0.0002, 0.2, 0.35)
    assert p.error is None and p.warning is None
    a, b = p.legs
    assert a.qty > 0 > b.qty  # long spread: buy A, sell B
    assert p.gross == pytest.approx(200.0, rel=0.01) and p.hedge_ratio == pytest.approx(1.0, rel=0.01)
    assert a.price > a.mid and b.price < b.mid and a.slip_bps == pytest.approx(1.0, rel=0.01)
    assert p.fees == pytest.approx(0.0005 * sum(abs(l.qty) * l.price for l in p.legs))
    zb, zs = executable_zscores(*(math.log(x) for x in (5.5 * 0.9999, 5.5 * 1.0001, 5 * 0.9999, 5 * 1.0001)),
                                0.1, 1.0, 0.02)
    assert p.z == pytest.approx(zb)
    assert not p.signal and any("No entry signal" in n for n in p.notes)  # Z ~ -0.47
    assert p.stop_level == pytest.approx(0.8 * (100 - p.cost)) and p.catastrophe_level < p.stop_level
    # long A pays the positive rate, short B receives it: they cancel at beta = 1
    assert p.funding_next == pytest.approx(0.0, abs=1e-3)
    mk = T.preview_open(PAIR, 1, 2.0, "maker", _books(), INSTS, 0.0005, 0.0002, 0.2, 0.35)
    assert sum(l.maker for l in mk.legs) == 1 and mk.cost < p.cost


def test_open_preview_reports_problems_without_inventing_numbers():
    assert T.preview_open(PAIR, 1, 2.0, "market", None, INSTS, 5e-4, 2e-4, 0.2, 0.35).warning == T.NO_BOOK
    tiny = {s: {**v, "min_notional": 500.0} for s, v in INSTS.items()}
    assert "cannot be sized" in T.preview_open(PAIR, 1, 2.0, "market", _books(), tiny, 5e-4, 2e-4, 0.2, 0.35).error


def test_close_preview_realizes_the_pnl_of_the_fraction():
    pos = {"symbol_a": "A/USDT:USDT", "symbol_b": "B/USDT:USDT", "label_a": "A", "label_b": "B", "qty_a": 18.0,
           "qty_b": -20.0, "entry_a": 5.0, "entry_b": 5.0, "cash": 99.0}
    p = T.preview_close(pos, 0.5, "market", _books(spread=0.0), INSTS, 0.0005, 0.0002)
    assert p.legs[0].qty == pytest.approx(-9.0) and p.legs[1].qty == pytest.approx(10.0)
    assert p.realized == pytest.approx(9 * 0.5 - p.fees)  # A rose 0.5 on 9 units; B flat
    assert p.remaining == pytest.approx((9.0, -10.0))
    odd = {s: {**v, "step": 10.0, "min_qty": 10.0} for s, v in INSTS.items()}  # 75% of 18 -> 10, leaves 8 < a lot
    assert "close 100% instead" in T.preview_close(pos, 0.75, "market", _books(), odd, 5e-4, 2e-4).error
    assert "smaller than one lot" in T.preview_close(pos, 0.25, "market", _books(), odd, 5e-4, 2e-4).error


# ------------------------------------------------------------------ charts
def _fit(n=48, t0=1_700_000_000_000):
    bar = t0 + np.arange(n) * HOUR
    la = np.log(5.5) + np.linspace(0, 0.01, n)
    lb = np.log(5.0) + np.zeros(n)
    z = np.sin(np.arange(n) / 5) * 3
    return pd.DataFrame({"bar_ts": bar, "pair_id": "P", "kf_alpha": 0.1, "kf_beta": 1.0, "kf_sigma": 0.02,
                         "used_alpha": np.nan, "used_beta": np.nan, "used_sigma": np.nan,
                         "spread_u": z * 0.02, "z": z, "log_a": la, "log_b": lb})


def test_z_spec_has_thresholds_trades_and_a_live_binding():
    fit = _fit()
    t_entry = int(fit["bar_ts"].iloc[10]) + 1800_000
    trades = pd.DataFrame([{"side": 1, "entry_ts": t_entry, "exit_ts": int(fit["bar_ts"].iloc[20]) + 600_000,
                            "reason": "signal", "pnl": 1.5, "entry_reason": "manual"}])
    spec = TC.z_spec(fit, pd.DataFrame(), trades, "1h", z_entry=2.0, z_exit=0.5, exit_plan=None, what_if=(2.5, 0.5),
                     max_entry_z=10.0, fit_in_use={"alpha": 0.1, "beta": 1.0, "sigma": 0.02},
                     symbols=("AUSDT", "BUSDT"), now_ms=int(fit["bar_ts"].iloc[-1]))
    by = {s["id"]: s for s in spec["series"]}
    z = by["z"]
    prices = sorted(l["price"] for l in z["lines"] if l["title"] in ("entry", "exit"))
    assert prices == [-2.0, -0.5, 0.5, 2.0]
    assert {l["price"] for l in z["lines"] if l["title"] == "what-if"} == {2.5, -2.5}
    times = z["data"]["t"]
    assert times == sorted(set(times)) and times[0] == (fit["bar_ts"].iloc[0] + HOUR) // 1000
    m = z["markers"]
    assert [x["text"] for x in m] == ["M BUY", "CLOSE +1.50"]
    assert m[0]["time"] == (int(fit["bar_ts"].iloc[10]) + HOUR) // 1000  # the first close after the entry
    auto = trades.assign(entry_ts=int(fit["bar_ts"].iloc[10]), entry_reason="signal")  # decided at that bar's close
    spec2 = TC.z_spec(fit, pd.DataFrame(), auto, "1h", z_entry=2.0, z_exit=0.5, now_ms=int(fit["bar_ts"].iloc[-1]))
    m2 = {s["id"]: s for s in spec2["series"]}["z"]["markers"]
    assert m2[0]["text"] == "BUY" and m2[0]["time"] == m[0]["time"]
    assert by["z_live"]["live"]["kind"] == "z" and by["z_live"]["live"]["label"] == "end"
    assert "shade0" in by and sum(v is not None for v in by["shade0"]["data"]["v"]) >= 10
    assert all(s["pane"] == 1 for k, s in by.items() if k.startswith(("band", "spread")))
    again = TC.z_spec(fit, pd.DataFrame(), trades, "1h", z_entry=2.0, z_exit=0.5, now_ms=int(fit["bar_ts"].iloc[-1]))
    assert {s["id"]: s["hash"] for s in again["series"]}["z"] == z["hash"]  # same data, same hash


def test_intraday_z_buckets_the_live_marks():
    ts = 1_699_999_980_000 + np.arange(0, 600_000, 5000)  # 10 whole minutes
    live = pd.DataFrame({"ts": ts, "z_buy": np.linspace(-1, 1, ts.size) + 0.01, "z_sell": np.linspace(-1, 1, ts.size) - 0.01,
                         "spread_u": 0.0, "sigma": 0.02, "beta": 1.0})
    spec = TC.z_spec(pd.DataFrame(), live, pd.DataFrame(), "1m", z_entry=2.0, z_exit=0.0, now_ms=int(ts[-1]))
    z = {s["id"]: s for s in spec["series"]}["z"]
    assert len(z["data"]["t"]) == 10 and all(t % 60 == 0 for t in z["data"]["t"])
    assert z["data"]["v"][-1] == pytest.approx(1.0)


def test_legs_spec_draws_candles_volume_and_entry_lines():
    ts = 1_700_000_000_000 + np.arange(30) * 900_000
    c = pd.DataFrame({"ts": ts, "o": 1.0, "h": 1.1, "l": 0.9, "c": np.linspace(1, 1.05, 30), "v": 10.0})
    c["qv"] = c["v"] * c["c"]
    spec = TC.legs_spec(c, c, ("A", "B"), ("AUSDT", "BUSDT"), (4, 4), "15m", pd.DataFrame(), (1.0, 1.02), side_a=-1)
    by = {s["id"]: s for s in spec["series"]}
    assert by["c0"]["type"] == "Candlestick" and len(by["c0"]["data"]["o"]) == 30
    assert by["c0"]["lines"][0]["title"] == "entry short" and by["c1"]["lines"][0]["title"] == "entry long"
    assert by["vol0"]["margins"] == {"top": 0.8, "bottom": 0.0} and by["c1"]["live"]["kind"] == "kline"

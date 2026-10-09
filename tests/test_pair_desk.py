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
    fit = _model()
    now = int(fit["bar_ts"].iloc[-1]) + HOUR
    t_entry = int(fit["bar_ts"].iloc[10]) + 1800_000
    trades = pd.DataFrame([{"side": 1, "entry_ts": t_entry, "exit_ts": int(fit["bar_ts"].iloc[20]) + 600_000,
                            "reason": "signal", "pnl": 1.5, "entry_reason": "manual"}])
    no_px = TC.closes(None, None)  # Binance unreachable: the hourly view falls back to the bot's history
    spec = TC.z_spec(no_px, fit, trades, "1h", z_entry=2.0, z_exit=0.5, exit_plan=None, what_if=(2.5, 0.5),
                     max_entry_z=10.0, fit_now={"alpha": 0.1, "beta": 1.0, "sigma": 0.02},
                     symbols=("AUSDT", "BUSDT"), now_ms=now)
    by = {s["id"]: s for s in spec["series"]}
    assert not {"z_model", "z_live", "spread_live"} & set(by)  # one Z, one spread: no second or third line
    z = by["z"]
    titled = {l["title"]: l["price"] for l in z["lines"] if l["title"].endswith(("entry", "exit"))}
    assert titled == {"long entry": -2.0, "long exit": -0.5, "short entry": 2.0, "short exit": 0.5}  # flat: both ways
    assert {l["price"] for l in z["lines"] if l["title"] == "what-if"} == {2.5, -2.5}
    times = z["data"]["t"]
    assert times == sorted(set(times)) and times[0] == (fit["bar_ts"].iloc[0] + HOUR) // 1000
    m = z["markers"]
    assert [x["text"] for x in m] == ["M BUY", "CLOSE +1.50"]
    assert m[0]["time"] == (int(fit["bar_ts"].iloc[10]) + HOUR) // 1000  # the first close after the entry
    auto = trades.assign(entry_ts=int(fit["bar_ts"].iloc[10]), entry_reason="signal")  # decided at that bar's close
    spec2 = TC.z_spec(no_px, fit, auto, "1h", z_entry=2.0, z_exit=0.5, now_ms=now)
    m2 = {s["id"]: s for s in spec2["series"]}["z"]["markers"]
    assert m2[0]["text"] == "BUY" and m2[0]["time"] == m[0]["time"]
    assert z["live"]["kind"] == "z" and z["live"]["label"] == "end" and z["live"]["fit"]["sigma"] == 0.02
    assert by["spread"]["live"]["kind"] == "spread" and by["spread"]["live"]["scale"] == 100.0
    assert all("live" not in s for s in spec2["series"])  # no fit from the bot: nothing to follow live
    assert [(b["from"], b["to"]) for b in z["boxes"]] == [(m[0]["time"], m[1]["time"])]  # from BUY to CLOSE
    assert z["boxes"][0]["fill"].startswith("rgba(") and not any(k.startswith("shade") for k in by)
    assert all(s["pane"] == 1 for k, s in by.items() if k.startswith(("band", "spread")))
    u = np.asarray(by["spread"]["data"]["v"], dtype=float)  # spread in %, bands = z x sigma in %
    assert np.allclose(u, np.asarray(z["data"]["v"], dtype=float) * 0.02 * 100)
    assert by["band_short_entry"]["data"]["v"][0] == pytest.approx(2.0 * 2.0)
    assert by["band_long_exit"]["data"]["v"][0] == pytest.approx(-0.5 * 2.0)
    assert by["z"]["include"] == [-2.0, -0.5, 2.0, 0.5]  # the levels stay in view
    # A long opened at Z -1.70 with exit 0: its entry and its exit, both kept on the scale.
    lng = {s["id"]: s for s in TC.z_spec(no_px, fit, trades, "1h", z_entry=0.8, z_exit=0.0, now_ms=now, side=1,
                                          entered=-1.70, fit_now={"alpha": 0.1, "beta": 1.0, "sigma": 0.02},
                                          symbols=("AUSDT", "BUSDT"))["series"]}
    assert {(l["title"], l["price"]) for l in lng["z"]["lines"] if l["title"]} == {("entry", -1.70), ("exit", 0.0)}
    assert lng["z"]["include"] == [-1.70, 0.0] and not any(k.startswith("band") for k in lng)
    assert {(l["title"], round(l["price"], 6)) for l in lng["spread"]["lines"] if l["title"]} == {
        ("entry", round(-1.70 * 2.0, 6)), ("exit", 0.0)}  # Z x the frozen sigma, in %
    again = TC.z_spec(no_px, fit, trades, "1h", z_entry=2.0, z_exit=0.5, now_ms=now,
                      fit_now={"alpha": 0.1, "beta": 1.0, "sigma": 0.02})
    assert {s["id"]: s["hash"] for s in again["series"]}["z"] == z["hash"]  # same data, same hash
    four = {s["id"]: s for s in TC.z_spec(no_px, fit, trades, "4h", z_entry=2.0, z_exit=0.5, now_ms=now)["series"]}
    assert all(t % (4 * 3600) == 0 for t in four["z"]["data"]["t"]) and len(four["z"]["data"]["t"]) >= 11


def test_a_position_exits_on_its_own_side_of_zero():
    # In a position: where it was opened and its exit, on the side it came from.
    assert TC.z_levels(-1, 2.0, 0.5, entered=2.4) == [(2.4, "entry", "entry"), (0.5, "exit", "exit")]
    assert TC.z_levels(1, 0.8, 0.0, entered=-1.70) == [(-1.70, "entry", "entry"), (0.0, "exit", "exit")]
    assert TC.z_levels(1, 2.0, 0.5) == [(-2.0, "entry", "entry"), (-0.5, "exit", "exit")]  # entry Z unknown
    assert TC.z_levels(-1, 2.0, 0.5, exit_plan=1.0, entered=np.nan)[1] == (1.0, "exit", "exit · plan")
    # Flat: the thresholds both ways.
    assert TC.z_levels(0, 2.0, 0.0) == [(-2.0, "entry", "long entry"), (2.0, "entry", "short entry"),
                                         (0.0, "exit", "exit")]
    assert TC.z_levels(0, 2.0, 0.5)[1] == (-0.5, "exit", "long exit")
    from cryptopairs.ui import desk as K
    assert K.rule_text(-1, 2.0, 0.5) == "exit when Z falls to +0.5"
    assert K.rule_text(1, 2.0, 0.0) == "exit when Z rises to 0"
    assert K.rule_text(0, 2.5, 0.5) == "long < -2.5 · short > +2.5 · exits -0.5 / +0.5"


def _model(n=48, t0=472_224 * HOUR, seed=3):
    """Model history that agrees with its prices: z = (log_a - kf_alpha - kf_beta log_b) / kf_sigma."""
    rng = np.random.default_rng(seed)
    bar = t0 + np.arange(n) * HOUR
    lb = np.log(5.0) + np.cumsum(rng.normal(0, 0.01, n))
    al, be, si = 0.1 + np.linspace(0, 0.01, n), 1.0 + np.linspace(0, 0.05, n), np.full(n, 0.02)
    la = al + be * lb + si * 3 * np.sin(np.arange(n) / 5)
    return pd.DataFrame({"bar_ts": bar, "pair_id": "P", "kf_alpha": al, "kf_beta": be, "kf_sigma": si,
                         "used_alpha": np.nan, "used_beta": np.nan, "used_sigma": np.nan,
                         "spread_u": la - al - be * lb, "z": (la - al - be * lb) / si, "log_a": la, "log_b": lb})


def _trade(fit, i, j, fit_abs=(0.5, 0.9, 0.03), side=1, exit_ms=None):
    """A trade decided at the close of bar i (stamped with its open time) and closed at bar j's close."""
    bar = fit["bar_ts"].to_numpy()
    return {"side": side, "entry_ts": int(bar[i]), "exit_ts": exit_ms if exit_ms is not None else
            (int(bar[j]) if j is not None else np.nan), "reason": "signal", "pnl": 1.0, "entry_reason": "signal",
            "alpha": fit_abs[0], "beta": fit_abs[1], "sigma": fit_abs[2], "z_entry": -2.1}


def test_fit_in_force_is_the_recorded_one_else_the_position_s_else_the_model_s():
    fit = _model()
    fit.loc[40, ["used_alpha", "used_beta", "used_sigma"]] = (0.3, 1.2, 0.05)  # recorded by the bot
    bar = fit["bar_ts"].to_numpy()
    stop_ms = int(bar[35]) + HOUR + 600_000  # an intrabar stop 10 minutes into the hour after bar 35's close
    trades = pd.DataFrame([_trade(fit, 10, 20), _trade(fit, 30, None, (0.6, 0.8, 0.04), exit_ms=stop_ms)])
    f = TC.fits_in_force(fit, trades, now_ms=int(bar[-1]) + HOUR)
    assert (f["from_ms"].to_numpy() == bar + HOUR).all()
    assert (f.loc[10:20, "beta"] == 0.9).all()  # frozen from the entry decision through the exit bar
    assert f.loc[9, "beta"] == fit.loc[9, "kf_beta"] and f.loc[21, "beta"] == fit.loc[21, "kf_beta"]
    assert (f.loc[30:35, "sigma"] == 0.04).all() and f.loc[36, "sigma"] == 0.02
    assert tuple(f.loc[40, ["alpha", "beta", "sigma"]]) == (0.3, 1.2, 0.05)
    assert np.allclose(f["model_z"], fit["z"])


def test_the_same_moment_shows_the_same_z_on_every_timeframe():
    fit = _model()
    bar = fit["bar_ts"].to_numpy()
    trades = pd.DataFrame([_trade(fit, 10, 20)])
    now = int(bar[-1]) + HOUR
    forces = TC.fits_in_force(fit, trades, now)
    hourly = TC.z_points(TC.closes_from_fit(fit, "1h"), forces, "1h", now)
    # The 1-minute bars that close on the hour share the hourly close; others in between.
    rows = [(int(b) + HOUR - 60_000, np.exp(la), np.exp(lb)) for b, la, lb in zip(bar, fit["log_a"], fit["log_b"])]
    rows += [(int(b) + 30 * 60_000, np.exp(la) * 1.001, np.exp(lb)) for b, la, lb in zip(bar, fit["log_a"], fit["log_b"])]
    minute = TC.z_points(pd.DataFrame(rows, columns=["ts", "a", "b"]).sort_values("ts"), forces, "1m", now)
    m = hourly.merge(minute, on="t", suffixes=("_h", "_m"))
    assert len(m) == len(hourly) and np.allclose(m["z_h"], m["z_m"])
    flat = ~hourly["t"].isin((bar[10:21] + HOUR) // 1000)
    assert np.allclose(hourly.loc[flat, "z"], fit.loc[flat.to_numpy(), "z"])  # flat: refitted, the model's Z
    k = 15  # in the position: the entry fit, not the model's
    expect = (fit["log_a"][k] - 0.5 - 0.9 * fit["log_b"][k]) / 0.03
    assert hourly.loc[k, "z"] == pytest.approx(expect)
    # A bar closing between decisions uses the last decision's fit (bar 15's half-hour: the entry fit).
    half = minute[minute["t"] == (int(bar[15]) + 31 * 60_000) // 1000].iloc[0]
    assert half["z"] == pytest.approx((fit["log_a"][15] + np.log(1.001) - 0.5 - 0.9 * fit["log_b"][15]) / 0.03)


def test_an_hourly_decision_is_marked_on_the_first_intraday_bar_after_the_close():
    fit = _model()
    bar = fit["bar_ts"].to_numpy()
    trades = pd.DataFrame([_trade(fit, 10, 20)])
    times = np.arange(int(bar[9]) + HOUR, int(bar[22]) + HOUR, 60_000) // 1000 + 60
    m = TC.trade_markers(trades, times, 60, "end")
    assert [x["time"] for x in m] == [(int(bar[10]) + HOUR) // 1000 + 60, (int(bar[20]) + HOUR) // 1000 + 60]
    hourly = TC.trade_markers(trades, (bar + HOUR) // 1000, 3600, "end")
    assert [x["time"] for x in hourly] == [(int(bar[10]) + HOUR) // 1000, (int(bar[20]) + HOUR) // 1000]


def test_each_trade_is_its_own_box_from_its_entry_marker_to_its_exit_marker_on_every_timeframe():
    fit = _model(n=200)
    bar = fit["bar_ts"].to_numpy()
    stop_ms = int(bar[60]) + HOUR + 23 * 60_000 + 1804  # an intrabar stop, 23 minutes after a close
    trades = pd.DataFrame([
        _trade(fit, 10, 30, side=-1),                                             # decided at closes
        _trade(fit, 31, 50, side=1),                                              # one hour after the exit
        {**_trade(fit, 50, None, side=-1), "entry_ts": int(bar[50]) + HOUR + 35 * 60_000 + 7732,  # manual,
         "exit_ts": stop_ms, "entry_reason": "manual"},                          # minutes after the exit
        _trade(fit, 61, None, side=1),                                            # still open
    ])
    now = int(bar[-1]) + HOUR
    for tf, sec in TC.TF_SECONDS.items():
        times = np.arange((int(bar[0]) + HOUR) // 1000, now // 1000 + sec, sec) // sec * sec
        boxes = TC.position_boxes(trades, sec)
        marks = TC.trade_markers(trades, np.unique(times), sec, "end")
        ins = [m["time"] for m in marks if m["text"].split()[-1] in ("BUY", "SELL")]
        outs = [m["time"] for m in marks if m["text"].startswith(("CLOSE", "STOP"))]
        assert len(boxes) == 4, tf  # never merged, even where an exit and the next entry share a bar
        assert [b["from"] for b in boxes] == ins, tf
        assert [b["to"] for b in boxes[:3]] == outs and boxes[3]["to"] is None, tf
        assert all(b["from"] <= b["to"] for b in boxes[:3]), tf
        assert [b["fill"] for b in boxes] == [TC._rgba(TC.RED, 0.1), TC._rgba(TC.GREEN, 0.1)] * 2, tf


def test_every_spread_z_timeframe_opens_with_the_same_bar_width_and_keeps_the_user_s_zoom():
    """As in TradingView: a timeframe switch keeps the width of a bar (js/chart.js keep_zoom), so 1m shows
    less time than 1h; it used to open each timeframe on its own bar count (1.9 px at 1h, 3.7 at 4h, 0.6 at 1d)."""
    fit = _model(n=200)
    now = int(fit["bar_ts"].iloc[-1]) + HOUR
    for tf in TC.TF_SECONDS:
        spec = TC.z_spec(TC.closes(None, None), fit, pd.DataFrame(), tf, z_entry=2.0, z_exit=0.5, now_ms=now)
        assert spec["bar_px"] == TC.OPENING_PX and spec["keep_zoom"] is True and "visible_bars" not in spec, tf


def test_markers_sit_at_the_z_the_fills_got():
    fit = _model()
    bar = fit["bar_ts"].to_numpy()
    a, b, sg = 0.5, 0.9, 0.03
    z_in, z_out = 1.25, 0.31  # the fills, not the line at the bar's close
    lb_in, lb_out = np.log(5.0), np.log(5.1)
    t = {"side": -1, "entry_ts": int(bar[10]), "exit_ts": int(bar[20]), "reason": "signal", "pnl": 1.5,
         "entry_reason": "signal", "alpha": a, "beta": b, "sigma": sg,
         "entry_px_a": np.exp(a + b * lb_in + sg * z_in), "entry_px_b": np.exp(lb_in),
         "exit_px_a": np.exp(a + b * lb_out + sg * z_out), "exit_px_b": np.exp(lb_out)}
    times = (bar + HOUR) // 1000
    sell, close = TC.trade_markers(pd.DataFrame([t]), times, 3600, "end", at_fill_z=True)
    assert sell["position"] == "atPriceTop" and sell["price"] == pytest.approx(z_in) and sell["text"] == "SELL +1.25"
    assert close["position"] == "atPriceMiddle" and close["price"] == pytest.approx(z_out)
    assert close["text"] == "CLOSE +1.50 @ +0.31"
    plain = TC.trade_markers(pd.DataFrame([t]), times, 3600, "end")  # candles: on the bar, as before
    assert plain[0]["position"] == "aboveBar" and "price" not in plain[0]
    no_px = TC.trade_markers(pd.DataFrame([{k: v for k, v in t.items() if "px" not in k}]), times, 3600, "end",
                             at_fill_z=True)
    assert no_px[0]["position"] == "aboveBar"  # older rows without fill prices


def test_the_forming_bar_uses_the_bot_s_fit_now():
    fit = _model()
    bar = fit["bar_ts"].to_numpy()
    now = int(bar[-1]) + HOUR + 20 * 60_000  # 20 minutes after the last decision
    px = pd.DataFrame({"ts": [int(bar[-1]), int(bar[-1]) + HOUR], "a": [6.0, 6.1], "b": [5.0, 5.0]})
    fit_now = {"alpha": 0.2, "beta": 1.1, "sigma": 0.025}
    pts = TC.z_points(px, TC.fits_in_force(fit, None, now), "1h", now, fit_now)
    assert pts["t"].iloc[-1] == (int(bar[-1]) + 2 * HOUR) // 1000  # labelled at its (future) close
    assert pts["z"].iloc[-1] == pytest.approx((np.log(6.1) - 0.2 - 1.1 * np.log(5.0)) / 0.025)
    assert pts["z"].iloc[0] == pytest.approx((np.log(6.0) - 0.2 - 1.1 * np.log(5.0)) / 0.025)  # closed at the decision
    early = TC.z_points(px.assign(ts=px["ts"] - 400 * HOUR), TC.fits_in_force(fit, None, now), "1h", now)
    assert early.empty  # before the first fit there is no Z


def test_model_view_compares_the_estimate_with_the_fit_in_use():
    fit = _model()
    trades = pd.DataFrame([_trade(fit, 10, 20)])
    spec = TC.model_spec(fit, trades, int(fit["bar_ts"].iloc[-1]) + HOUR, z_entry=2.0)
    by = {s["id"]: s for s in spec["series"]}
    zu, zm = (np.asarray(by[k]["data"]["v"], dtype=float) for k in ("z_used", "z_model"))
    assert np.allclose(zu[:10], zm[:10]) and np.allclose(zu[21:], zm[21:])
    assert not np.allclose(zu[10:21], zm[10:21]) and by["beta_used"]["data"]["v"][15] == 0.9
    assert by["z_used"]["pane"] == by["z_model"]["pane"] == 2


def test_relative_view_is_prices_only():
    ts = 1_700_000_000_000 + np.arange(30) * 900_000
    c = pd.DataFrame({"ts": ts, "o": 1.0, "h": 1.1, "l": 0.9, "c": np.linspace(1, 1.05, 30), "v": 10.0})
    spec = TC.relative_spec(c, c, ("A", "B"), ("AUSDT", "BUSDT"), "15m", pd.DataFrame())
    assert spec["panes"] == [1] and {s["id"] for s in spec["series"]} == {"rb_c_a", "rb_c_b"}


def test_legs_spec_draws_candles_volume_and_entry_lines():
    ts = 1_700_000_000_000 + np.arange(30) * 900_000
    c = pd.DataFrame({"ts": ts, "o": 1.0, "h": 1.1, "l": 0.9, "c": np.linspace(1, 1.05, 30), "v": 10.0})
    c["qv"] = c["v"] * c["c"]
    spec = TC.legs_spec(c, c, ("A", "B"), ("AUSDT", "BUSDT"), (4, 4), "15m", pd.DataFrame(), (1.0, 1.02), side_a=-1)
    by = {s["id"]: s for s in spec["series"]}
    assert by["c0"]["type"] == "Candlestick" and len(by["c0"]["data"]["o"]) == 30
    assert by["c0"]["lines"][0]["title"] == "entry short" and by["c1"]["lines"][0]["title"] == "entry long"
    assert by["vol0"]["margins"] == {"top": 0.8, "bottom": 0.0} and by["c1"]["live"]["kind"] == "kline"

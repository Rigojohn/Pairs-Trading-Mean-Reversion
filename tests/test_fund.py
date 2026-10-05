"""Fund numbers of the Overview and the dashboard's local time (Athens)."""

import math

import numpy as np
import pandas as pd
import pytest

from cryptopairs.ui import data as D
from cryptopairs.ui import fund as F
from cryptopairs.ui import tvcharts as TC

HOUR = 3_600_000


def test_times_are_shown_in_athens_time_with_daylight_saving():
    summer = int(pd.Timestamp("2026-07-01 12:00", tz="UTC").value // 10**6)
    winter = int(pd.Timestamp("2026-12-01 12:00", tz="UTC").value // 10**6)
    assert D.TZ_NAME == "Europe/Athens"
    assert D.to_time(summer) == pd.Timestamp("2026-07-01 15:00")  # EEST, UTC+3
    assert D.to_time(winter) == pd.Timestamp("2026-12-01 14:00")  # EET, UTC+2
    s = D.to_time(pd.Series([summer, np.nan], index=[5, 6]))
    assert list(s.index) == [5, 6] and s.iloc[0].hour == 15 and pd.isna(s.iloc[1])
    assert D.clock(summer, "%H:%M") == "15:00" and D.clock(None) == "—"
    # 00:30 Athens on 2 July is still 1 July in UTC: the local day starts at 21:00 UTC
    t = int(pd.Timestamp("2026-07-01 21:30", tz="UTC").value // 10**6)
    assert D.day_start_ms(t) == int(pd.Timestamp("2026-07-01 21:00", tz="UTC").value // 10**6)


def _curve():
    idx = pd.date_range("2026-09-29 00:00", "2026-10-02 12:00", freq="h")
    v = 1000 + np.arange(len(idx)) * 0.5  # +12 USDT a day
    return pd.Series(v, index=idx)


def test_period_returns_and_daily_pnl():
    c = _curve()
    now = pd.Timestamp("2026-10-02 12:00")
    r = F.period_returns(c, 1000.0, now).set_index("period")
    assert r.loc["Today", "PnL"] == pytest.approx(13 * 0.5)  # from the last NAV before local midnight (23:00)
    assert r.loc["Yesterday", "PnL"] == pytest.approx(24 * 0.5)
    assert r.loc["Month to date", "PnL"] == pytest.approx(r.loc["Today", "PnL"] + r.loc["Yesterday", "PnL"])  # 1 Oct
    assert r.loc["Since inception", "PnL"] == pytest.approx(c.iloc[-1] - 1000.0)
    assert r.loc["30 days", "from"] == c.index[0]  # the curve is younger than 30 days
    daily = F.daily_pnl(c, 1000.0)
    assert len(daily) == 4 and daily["PnL"].sum() == pytest.approx(c.iloc[-1] - 1000.0)
    assert daily["PnL"].iloc[1] == pytest.approx(12.0)
    assert daily["PnL"].iloc[-1] == pytest.approx(r.loc["Today", "PnL"])  # one definition of "today" everywhere


def test_risk_stats_sortino_uses_downside_only():
    rng = np.random.default_rng(3)
    eq = 1000 * np.cumprod(1 + rng.normal(0.0002, 0.002, 500))
    st = F.risk_stats(eq, pd.Series(eq))
    r = np.diff(eq) / eq[:-1]
    assert st["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(8760))
    assert st["sortino"] > st["sharpe"] and st["max_dd"] < 0 and st["hours"] == 499
    assert math.isnan(F.risk_stats(eq[:10])["sharpe"])  # under a day of bars


def test_trade_stats_and_attribution():
    t = pd.DataFrame({"entry_ts": [0, HOUR, 2 * HOUR, 3 * HOUR], "exit_ts": [HOUR, 3 * HOUR, 4 * HOUR, np.nan],
                      "pnl": [2.0, -1.0, 3.0, np.nan], "entry_reason": ["signal", "manual", "signal", "signal"]})
    s = F.trade_stats(t)
    assert (s["n"], s["wins"], s["manual"]) == (3, 2, 1)
    assert s["hit"] == pytest.approx(2 / 3) and s["profit_factor"] == pytest.approx(5.0)
    assert s["avg_hold_h"] == pytest.approx((1 + 2 + 2) / 3)
    rows = pd.DataFrame({"pair": ["P", "Q"], "PnL": [3.0, -2.0], "unrealized": [1.0, 0.0], "fees": [0.5, 0.2],
                         "funding": [0.3, -0.1]})
    a = F.attribution(rows).set_index("pair")
    assert a.loc["P", "spread closed"] + a.loc["P", "spread open"] == pytest.approx(3.0 + 0.5 + 0.3)
    assert a.loc["Q", "funding"] == pytest.approx(0.1)  # received
    assert (a["spread closed"] + a["spread open"] + a["fees"] + a["funding"]).tolist() == pytest.approx(a["total"].tolist())


def test_book_carry_and_beta():
    rows = pd.DataFrame([{"pair": "P", "side": 1, "notional": 200.0, "qty A": 10.0, "mark A": 10.0, "qty B": -50.0,
                          "mark B": 2.0, "sym_a": "A", "sym_b": "B"},
                         {"pair": "F", "side": 0, "notional": 0.0, "qty A": 0.0, "mark A": 1.0, "qty B": 0.0,
                          "mark B": 1.0, "sym_a": "C", "sym_b": "D"}])
    b = F.book_summary(rows, 100.0)
    assert b["gross"] == 200.0 and b["net"] == pytest.approx(0.0) and b["gross_x"] == 2.0 and b["open"] == 1
    insts = {"A": {"funding_rate": 0.0001, "funding_hours": 8}, "B": {"funding_rate": -0.0002, "funding_hours": 4}}
    carry = F.funding_carry(rows, insts)
    # long A pays 100 x 0.0001 x 3; short B (rate < 0) pays 100 x 0.0002 x 6
    assert carry["P"] == pytest.approx(-(0.03 + 0.12))
    dollar, cover = F.market_beta(pd.Series({"A": 100.0, "B": -100.0, "X": 50.0}), {"A": 1.2, "B": 0.8})
    assert dollar == pytest.approx(40.0) and cover == pytest.approx(0.8)
    rng = np.random.default_rng(0)
    m = rng.normal(0, 0.01, 500)
    assert F.beta_to(1.5 * m + rng.normal(0, 0.001, 500), m) == pytest.approx(1.5, abs=0.05)


def test_pnl_correlation_needs_two_active_pairs():
    bars = np.arange(48) * HOUR
    rng = np.random.default_rng(1)
    x = np.cumsum(rng.normal(0, 1, 48))
    marks = pd.concat([pd.DataFrame({"bar_ts": bars, "pair_id": "P", "equity": 100 + x}),
                       pd.DataFrame({"bar_ts": bars, "pair_id": "Q", "equity": 100 - x}),
                       pd.DataFrame({"bar_ts": bars, "pair_id": "Z", "equity": 100.0})])
    c = F.pnl_correlation(marks)
    assert list(c.columns) == ["P", "Q"] and c.loc["P", "Q"] == pytest.approx(-1.0)
    assert F.pnl_correlation(marks[marks["pair_id"] != "Q"]).empty


def test_nav_grid_breaks_where_the_bot_was_down_and_bridges_hourly_history():
    t0 = 1_700_000_000_000 // 60_000 * 60_000
    ts = np.r_[t0 + np.arange(0, 600_000, 5000), t0 + 1_200_000 + np.arange(0, 300_000, 5000)]
    live = pd.DataFrame({"ts": ts, "equity": 100.0 + np.arange(ts.size) * 0.01})
    pts = F.nav_points(pd.DataFrame(columns=["bar_ts", "equity"]), live)
    g = F.nav_grid(pts, int(ts[0]), int(ts[-1]), 60_000)
    v = g["nav"].to_numpy()
    assert np.isnan(v[11:19]).all() and np.isfinite(v[:9]).all() and np.isfinite(v[-4:]).all()  # 10 min without marks
    # Older history with bar closes only: one value an hour still makes a continuous line.
    bars = pd.DataFrame({"bar_ts": t0 - 10 * HOUR + np.arange(9) * HOUR, "equity": 100.0 + np.arange(9)})
    pts = F.nav_points(bars, live)
    g = F.nav_grid(pts, t0 - 9 * HOUR, int(ts[-1]), 15 * 60_000)
    old = g[g["t"] < t0]
    assert old["nav"].notna().all() and len(old) >= 30
    for tf in F.TIMEFRAMES:  # every timeframe starts at the first observation
        start, end, step = F.timeframe_window(pts, tf, int(ts[-1]))
        assert start == int(pts["ts"].iloc[0]) and end == int(ts[-1]) and step == F.TIMEFRAMES[tf]
    assert F.default_timeframe(pts, int(ts[-1])) == "1m"  # ~10 h of history: 1-minute points fit
    long = pd.DataFrame({"ts": [0, 400 * 24 * HOUR], "nav": [1.0, 1.0], "tol": F.HOURLY_TOL})
    assert F.timeframe_window(long, "1m", 400 * 24 * HOUR)[2] > F.TIMEFRAMES["1m"]  # coarsened beyond MAX_BARS
    assert F.default_timeframe(long, 400 * 24 * HOUR) == "1D"
    # 4-hour bars close on local hours: with a +3 h offset the bar ends fall on 01, 05, 09 ... UTC
    g4 = F.nav_grid(pts, int(pts["ts"].iloc[0]), int(ts[-1]), 4 * HOUR, offset_ms=3 * HOUR)
    assert all(((t + 3 * HOUR) % (4 * HOUR)) == 0 for t in g4["t"].iloc[:-1])


def test_high_water_and_drawdown_are_the_same_at_every_timeframe():
    t0 = 1_700_000_000_000 // HOUR * HOUR
    ts = t0 + np.arange(0, 6 * HOUR, 5000)
    nav = 100 + np.sin(np.arange(ts.size) / 400.0) * 2
    nav[1500] = 97.0  # a brief dip inside a bar
    nav[2500] = 103.5  # a brief peak inside a bar
    pts = F.nav_points(pd.DataFrame(columns=["bar_ts", "equity"]), pd.DataFrame({"ts": ts, "equity": nav}))
    true_dd = 100 * (nav / np.maximum.accumulate(np.maximum(nav, 100.0)) - 1)
    for tf in ("1m", "15m", "1H", "4H"):
        g = F.nav_grid(pts, int(ts[0]), int(ts[-1]), F.TIMEFRAMES[tf], capital=100.0)
        assert np.nanmin(g["dd"]) == pytest.approx(true_dd.min()), tf  # the dip is never lost
        assert np.nanmax(g["hwm"]) == pytest.approx(103.5), tf  # nor the peak
        spec = TC.performance_spec(g, capital=100.0, peak_before=100.0)
        dd = {s["id"]: s for s in spec["series"]}["dd"]["data"]["v"]
        assert min(x for x in dd if x is not None) == pytest.approx(true_dd.min()), tf


def test_drawdown_episodes_and_underwater():
    ts = 1_700_000_000_000 + np.arange(10) * HOUR
    nav = np.array([100, 104, 102, 99, 103, 105, 104.5, 101, 102, 103.0])
    pts = pd.DataFrame({"ts": ts, "nav": nav, "tol": F.HOURLY_TOL})
    ep = F.drawdown_episodes(pts, 100.0)
    assert len(ep) == 2
    first = ep.iloc[0]  # the deepest: 104 -> 99, recovered when the NAV reached 105
    assert first["depth %"] == pytest.approx(100 * (99 / 104 - 1)) and first["recovered"] == ts[5]
    assert first["peak"] == ts[1] and first["trough"] == ts[3] and first["length h"] == pytest.approx(4)
    second = ep.iloc[1]  # 105 -> 101, still under water
    assert second["depth %"] == pytest.approx(100 * (101 / 105 - 1)) and pd.isna(second["recovered"])
    assert second["length h"] == pytest.approx(4)  # to the last observation
    hwm, dd = F.underwater(np.array([np.nan, 99.0, 101.0, 100.0]), peak_before=100.0)
    assert np.isnan(dd[0]) and dd[1] == pytest.approx(-1.0) and dd[2] == 0.0 and dd[3] == pytest.approx(100 / 101 * 100 - 100)
    assert F.high_water(pts, int(ts[3]), 100.0) == 104.0


def test_performance_spec_has_nav_high_water_drawdown_and_trades():
    t0 = 1_700_000_000_000 // 60_000 * 60_000
    grid = pd.DataFrame({"t": t0 + np.arange(60) * 60_000, "nav": np.r_[np.linspace(100, 103, 30), np.linspace(103, 99, 30)]})
    trades = pd.DataFrame([{"pair_id": "P", "exit_ts": t0 + 20 * 60_000 + 7_000, "pnl": 1.5},
                           {"pair_id": "P", "exit_ts": np.nan, "pnl": np.nan}])
    spec = TC.performance_spec(grid, capital=100.0, peak_before=100.0, kill_level=75.0, trades=trades,
                               labels={"P": "A/B"})
    by = {s["id"]: s for s in spec["series"]}
    assert by["nav"]["options"]["baseValue"]["price"] == 100.0 and by["nav"]["pane"] == 0
    assert {l["title"] for l in by["nav"]["lines"]} == {"capital", "kill switch"}
    assert [m["text"] for m in by["nav"]["markers"]] == ["A/B +1.50"]
    assert by["hwm"]["data"]["v"][-1] == pytest.approx(103.0)
    dd = by["dd"]
    assert dd["pane"] == 1 and min(x for x in dd["data"]["v"] if x is not None) == pytest.approx(100 * (99 / 103 - 1))
    assert any(l["title"].startswith("max -3.88") for l in dd["lines"])
    assert by["hwm"]["role"] == "guide"  # an old peak never squashes a short range
    exact = TC.performance_spec(grid, capital=100.0, peak_before=100.0, max_dd=-4.2)
    assert any(l["title"] == "max -4.20%" for l in {s["id"]: s for s in exact["series"]}["dd"]["lines"])
    pts = pd.DataFrame({"ts": grid["t"], "nav": grid["nav"], "tol": F.LIVE_TOL})
    assert F.range_max_drawdown(pts, int(grid["t"].iloc[0]), int(grid["t"].iloc[-1]), 104.0) == pytest.approx(
        100 * (99 / 104 - 1))


def test_capital_added_later_is_a_deposit_not_a_gain():
    import json

    from cryptopairs.ui import data as D

    starts = pd.DataFrame({"ts": [1_000, 5_000, 9_000],
                           "payload": [json.dumps({"pairs": ["P1", "P2"]}), json.dumps({"pairs": ["P1", "P2"]}),
                                       json.dumps({"pairs": ["P1", "P2", "P3", "P4"]})]})
    caps = pd.DataFrame({"pair_id": ["P1", "P2", "P3", "P4"], "capital": [100.0, 100.0, 100.0, 50.0]})
    steps = D.capital_added(starts, caps)
    assert steps.to_dict("list") == {"ts": [9_000, 9_000], "capital": [100.0, 50.0]}  # restarts add nothing
    nav = pd.DataFrame({"ts": [2_000, 8_999, 9_000, 9_500], "equity": [200.0, 210.0, 360.0, 361.0]})
    adj = D.with_capital(nav, nav["ts"], steps)
    assert adj["equity"].tolist() == [350.0, 360.0, 360.0, 361.0]  # no jump at the deposit
    assert D.with_capital(nav, nav["ts"], D.capital_added(starts.head(2), caps)).equals(nav)  # nothing added
    # With sessions: the deposit lands when the adding session started (the event comes later), and
    # the bar that session records on start-up (stamped earlier, with the new equity) is not counted twice.
    sessions = pd.DataFrame({"id": ["s1", "s2", "s3"], "started_at": [900, 4_900, 8_700]})
    steps = D.capital_added(starts.assign(session_id=["s1", "s2", "s3"]), caps, sessions)
    assert steps["ts"].tolist() == [8_700, 8_700]
    bars = pd.DataFrame({"bar_ts": [4_000, 7_000, 8_000], "session_id": ["s2", "s2", "s3"],
                         "equity": [205.0, 209.0, 359.0]})  # the last one: start-up evaluation of s3
    adj = D.with_capital(bars, bars["bar_ts"] + 1_000, steps, sessions)
    assert adj["equity"].tolist() == [355.0, 359.0, 359.0]


def test_funding_is_shown_as_pnl_with_who_pays():
    from cryptopairs.ui import data as D

    # Longs pay a positive rate, shorts a negative one.
    assert D.funding_word(0.0001, +1) == "you pay" and D.funding_word(0.0001, -1) == "you receive"
    assert D.funding_word(-0.0029, -1) == "you pay" and D.funding_word(-0.0029, +1) == "you receive"
    assert D.funding_word(0.0001) == "longs pay" and D.funding_word(-0.0029) == "shorts pay"
    assert D.funding_word(None, 1) == "" and D.funding_word(0.0, 1) == ""
    trades = pd.DataFrame([{"pair_id": "P", "side": 1, "entry_reason": "signal", "entry_ts": 3_600_000, "exit_ts": 7_200_000,
                            "z_entry": -2.0, "beta": 0.8, "reason": "signal", "pnl": 1.0, "equity_before": 100.0,
                            "fees": 0.0664, "funding": 0.0834}])  # stored as paid
    tv = D.trade_view(trades)
    assert tv["funding"].iloc[0] == pytest.approx(-0.0834) and tv["fees"].iloc[0] == pytest.approx(-0.0664)


def test_daily_funding_of_a_short_on_a_negative_rate_is_a_cost():
    rows = pd.DataFrame([{"pair": "OGN_ANKR", "side": -1, "sym_a": "OGN", "sym_b": "ANKR",
                          "qty A": -4758.0, "mark A": 0.02147, "qty B": 19694.0, "mark B": 0.00497}])
    insts = {"OGN": {"funding_rate": -0.0028955, "funding_hours": 4.0},
             "ANKR": {"funding_rate": 0.00005, "funding_hours": 4.0}}
    day = F.funding_carry(rows, insts)["OGN_ANKR"]
    per_settlement = -(4758 * 0.02147 * 0.0028955) - 19694 * 0.00497 * 0.00005  # short OGN pays, long ANKR pays
    assert day == pytest.approx(6 * per_settlement) and day < 0  # every 4 h

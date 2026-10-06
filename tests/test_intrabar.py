"""Entries on minute closes (research/intrabar.py): the hourly rule reproduces the backtest, and the
spike / momentum / distance filters act only where they should."""

import math

import numpy as np
import pandas as pd
import pytest

from cryptopairs.config import CostConfig, StrategyConfig
from cryptopairs.core.strategy import PairParams
from cryptopairs.research.backtest import run_backtest
from cryptopairs.research.data import PairData
from cryptopairs.research.intrabar import RULES, EntryRule, Minutes, entry_marks, run_intrabar

H, M = 3_600_000, 60_000
T0 = 1_780_000_000_000 // H * H
SIGMA = 0.01  # the fit used in the scenarios: alpha 0, beta 1, sigma 1%: log A - log B = Z * sigma
NB = 40
START, END = T0 + 12 * H, T0 + (NB - 1) * H
PARAMS = PairParams(lookback=10, z_entry=2.0, z_exit=0.0, kalman_delta=None)
CFG = StrategyConfig(max_entry_z=10.0)
COSTS = CostConfig(taker_fee=0.0, fill_on="next_open", maker_legs=0)  # no costs: Z at a fill = Z at the price


def _build(z_minutes: np.ndarray) -> tuple[PairData, Minutes]:
    """Hourly bars and their minutes from a Z path per minute (B stays at 1.0, A = exp(Z * sigma))."""
    n = z_minutes.shape[0]
    ts = T0 + M * np.arange(n, dtype=np.int64)
    close_a = np.exp(z_minutes * SIGMA)
    open_a = np.concatenate(([close_a[0]], close_a[:-1]))
    one = np.ones(n)
    mins = Minutes(ts=ts, open_a=open_a, high_a=np.maximum(open_a, close_a), low_a=np.minimum(open_a, close_a),
                   close_a=close_a, open_b=one, high_b=one, low_b=one, close_b=one)
    hb = n // 60
    g = lambda x: x[: hb * 60].reshape(hb, 60)  # noqa: E731
    data = PairData(a="A", b="B", ts=T0 + H * np.arange(hb, dtype=np.int64),
                    open_a=g(open_a)[:, 0], high_a=g(mins.high_a).max(1), low_a=g(mins.low_a).min(1),
                    close_a=g(close_a)[:, -1], open_b=np.ones(hb), high_b=np.ones(hb), low_b=np.ones(hb),
                    close_b=np.ones(hb), funding_a=np.zeros(hb), funding_b=np.zeros(hb))
    return data, mins


def _fits(n: int):
    return np.zeros(n), np.ones(n), np.full(n, SIGMA)


def _run(z: np.ndarray, rule: EntryRule):
    data, mins = _build(z)
    return run_intrabar(data, mins, PARAMS, CFG, COSTS, START, END, 1000.0, rule, fits=_fits(len(data)))


def _path(**bars) -> np.ndarray:
    """Z = 0.5 everywhere, with the given minutes of the given bars replaced: _path(b20={10: 2.5})."""
    z = np.full(NB * 60, 0.5)
    for b, mins in bars.items():
        for m, v in mins.items():
            z[int(b[1:]) * 60 + m] = v
    return z


def _entries(res) -> list[tuple[int, int]]:
    """(bar, minute) of each entry fill."""
    return [((t["entry_ts"] - T0) // H, (t["entry_ts"] - T0) % H // M) for t in res.trades]


def test_the_hourly_rule_without_minute_exits_is_the_hourly_backtest():
    rng = np.random.default_rng(7)
    z = np.cumsum(rng.normal(0, 0.12, NB * 60))  # a wandering spread: entries, exits and stops happen
    z = z - pd.Series(z).rolling(600, min_periods=1).mean().to_numpy()
    data, mins = _build(z * 2.0)
    costs = CostConfig(taker_fee=5e-4, half_spread_bps=5, slippage_bps=5, fill_on="next_open", maker_legs=1,
                       maker_fee=2e-4, maker_adverse_bps=1.0)
    cfg = StrategyConfig(max_entry_z=10.0, catastrophe_stop_pct=0.35, min_edge_cost_multiple=1.5)
    fits = _fits(len(data))
    ref = run_backtest(data, PARAMS, cfg, costs, START, END, 1000.0, fits=fits)
    got = run_intrabar(data, mins, PARAMS, cfg, costs, START, END, 1000.0, EntryRule(minute_exits=False), fits=fits)
    assert len(ref.trades) >= 3
    np.testing.assert_allclose(got.equity, ref.equity, rtol=1e-12)
    assert [(t["entry_ts"], t.get("exit_ts"), t.get("reason")) for t in got.trades] == \
           [(t["entry_ts"], t.get("exit_ts"), t.get("reason")) for t in ref.trades]


def test_a_one_minute_spike_is_traded_without_a_filter_and_ignored_by_the_5_minute_rule():
    z = _path(b20={10: 2.5})
    assert _entries(_run(z, RULES["i0"])) == [(20, 11)]  # decided at minute 10's close, filled at 11's open
    assert _entries(_run(z, RULES["i5"])) == []
    assert _entries(_run(z, RULES["hourly"])) == []  # back to 0.5 by the hourly close


def test_five_minutes_beyond_the_threshold_enter_on_the_fifth():
    z = _path(b25={m: 2.5 for m in range(10, 21)})
    assert _entries(_run(z, RULES["i5"])) == [(25, 15)]  # minutes 10..14 beyond: decided at 14, filled at 15


def test_turn_waits_while_z_runs_away_and_enters_once_it_comes_back():
    z = _path(b20={10: 2.1, 11: 2.5, 12: 2.9, 13: 2.75, 14: 2.6, 15: 2.4})
    assert _entries(_run(z, RULES["turn"])) == [(20, 15)]  # 2.9 -> 2.6 is the first 0.3 back (minute 14)
    assert _entries(_run(z, RULES["turn_cap"])) == [(20, 15)]  # the extreme 2.9 is within 2.0 + 1.5


def test_no_entry_once_the_spread_ran_beyond_the_cap():
    z = _path(b20={10: 2.5, 11: 3.2, 12: 3.8, 13: 3.4, 14: 3.0, 15: 2.6})
    assert _entries(_run(z, RULES["turn"])) == [(20, 14)]  # 3.8 -> 3.4 back 0.4 at minute 13
    assert _entries(_run(z, RULES["turn_cap"])) == []  # 3.8 > 3.5: the spread broke away
    # the hourly rule with the cap: still beyond at the close, but the run reached 3.8
    zh = _path(b20={**{m: 3.8 for m in range(10, 30)}, **{m: 2.6 for m in range(30, 60)}})
    assert _entries(_run(zh, RULES["hourly"])) == [(21, 0)]
    assert _entries(_run(zh, RULES["hourly_cap"])) == []


def test_a_minute_exit_ends_the_bar_no_reentry_in_it():
    # short at bar 20, Z back below 0 at bar 22 minute 5 (exit), beyond again in the same bar
    z = _path(b20={m: 2.5 for m in range(0, 60)}, b22={**{m: 0.5 for m in range(0, 5)}, 5: -0.2,
                                                       **{m: 2.5 for m in range(6, 60)}}, b23={3: 2.5})
    res = _run(z, RULES["i0"])
    exits = [((t["exit_ts"] - T0) // H, (t["exit_ts"] - T0) % H // M) for t in res.trades if "exit_ts" in t]
    assert exits[0] == (22, 6)  # decided at minute 5's close
    assert all(e[0] != 22 for e in _entries(res))  # no entry again in bar 22
    assert (23, 4) in _entries(res)  # the next bar may: refitted at bar 22's close


def test_entry_marks_streak_extreme_and_cap():
    zs = np.array([1.0, 2.2, 2.6, 2.4, 1.0, 2.3])
    zb = zs.copy()
    c, ext_s, _ = entry_marks(zb, zs, 2.0, EntryRule(minute=True, turn=0.2))
    assert list(c) == [False, False, False, True, False, False]
    assert ext_s[3] == 2.6 and ext_s[5] == 2.3 and not np.isfinite(ext_s[4])
    c, _, _ = entry_marks(zb, zs, 2.0, EntryRule(minute=True, confirm=2))
    assert list(c) == [False, False, True, True, False, False]
    c, _, _ = entry_marks(zb, zs, 2.0, EntryRule(minute=True, cap=0.5))
    assert list(c) == [False, True, False, False, False, True]  # 2.6 > 2.5 closes that run

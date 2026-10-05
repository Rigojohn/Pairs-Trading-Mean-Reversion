"""Adaptive entry thresholds and common thresholds (audit 2026-10-05: no per-pair threshold search)."""

import math

import numpy as np

from cryptopairs.config import AppConfig, CostConfig, StrategyConfig
from cryptopairs.core.signals import adaptive_entry_threshold, rolling_ols
from cryptopairs.core.strategy import FLAT, LONG, SHORT, PairParams, PairStrategy
from cryptopairs.research.backtest import run_backtest
from cryptopairs.research.experiments import VARIANTS, _apply
from cryptopairs.research.data import make_pair_data
from tests.conftest import T0, make_pair


def test_threshold_uses_only_previous_bars_and_has_a_floor():
    z = np.ones(400)
    z[200:210] = 9.0  # a 10-bar burst of large swings starting at bar 200
    thr = adaptive_entry_threshold(z, window=100, quantile=0.95, min_z=1.5)
    assert np.isnan(thr[:50]).all()  # warm-up: less than half a window
    assert thr[150] == 1.5  # calm history: the floor
    assert thr[200] == 1.5  # the burst is not known before its own bars
    assert thr[205] == 1.5  # 5 of 100 bars: not yet above the 95th percentile
    assert thr[206] > 8.0  # 6 of 100: the band jumps
    assert thr[399] == 1.5  # and relaxes once the burst leaves the window


def _strategy() -> PairStrategy:
    s = PairStrategy(PairParams(100, 2.0, 0.0), StrategyConfig(), 100.0)
    s.set_fit(0.0, 1.0, 0.05, 10)
    return s


def test_check_entry_uses_the_given_threshold_instead_of_the_pairs_own():
    s = _strategy()
    ts = T0 + 10 * 3_600_000
    assert s.check_entry(ts, 10, -1.8, -1.8) == FLAT  # pair's own z_entry 2.0
    assert s.check_entry(ts, 10, -1.8, -1.8, z_entry=1.6) == LONG
    assert s.check_entry(ts, 10, 2.5, 2.5, z_entry=3.0) == FLAT
    assert s.check_entry(ts, 10, 3.1, 3.1, z_entry=3.0) == SHORT
    assert s.check_entry(ts, 10, -5.0, -5.0, z_entry=math.nan) == FLAT  # no threshold yet
    assert s.check_entry(ts, 10, -5.0, -5.0, z_entry=0.0) == FLAT  # never inside the exit band


def test_backtest_with_adaptive_entries_respects_each_bars_threshold():
    ka, kb = make_pair(n=3000, seed=4)
    data = make_pair_data("AUSDT", "BUSDT", ka, kb)
    cfg = StrategyConfig(entry_rule="rolling_quantile", adaptive_window=240)
    start, end = int(data.ts[600]), int(data.ts[-1])
    res = run_backtest(data, PairParams(168, 2.0, 0.0), cfg, CostConfig(), start, end, 100.0)
    assert res.trades, "the synthetic spread reverts, so the adaptive rule must trade"
    for t in res.trades:
        assert t["z_threshold"] >= cfg.adaptive_min_z
        assert abs(t["z_entry"]) > t["z_threshold"]
    # the thresholds really vary over time (not just the floor)
    al, be, si = rolling_ols(data.log_a, data.log_b, 168)
    thr = adaptive_entry_threshold((data.log_a - al - be * data.log_b) / si, 240, 0.95, 1.5)
    assert np.nanstd(thr[600:]) > 0.05


def test_threshold_variants_share_k1_calibration_and_only_change_scoring():
    base = AppConfig()
    for name in ("C1 common thresholds 2.0 / 0", "W1 adaptive: 95th pct of |Z| over 30d"):
        ov = VARIANTS[name]
        vcfg = _apply(base, {k: v for k, v in ov.items() if k != "calibrate_with"})
        rules = ov.get("calibrate_with", ov["strategy"])
        assert rules == VARIANTS["K1 Kalman filter"]["strategy"]  # same grid cache key as K1
        assert vcfg.research.fixed_z_exit == 0.0
    assert _apply(base, {"strategy": VARIANTS["W1 adaptive: 95th pct of |Z| over 30d"]["strategy"]}
                  ).strategy.entry_rule == "rolling_quantile"

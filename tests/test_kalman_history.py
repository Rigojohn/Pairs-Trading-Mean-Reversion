"""The bot filters the same series as the backtest (audit 2026-10-05), and the tools around it."""

import argparse
import asyncio
import json

import numpy as np
import pandas as pd
import yaml

from cryptopairs.cli import cmd_final
from cryptopairs.config import AppConfig
from cryptopairs.core.signals import estimate_fits, kalman_filter, kalman_filter_windowed
from cryptopairs.core.strategy import PairParams
from cryptopairs.research.data import DataStore
from cryptopairs.runtime.engine import PairSpec, StrategyEngine
from cryptopairs.runtime.execution import PaperExecutionEngine
from cryptopairs.runtime.feed import DataFeed
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import StateStore
from tests.conftest import HOUR, T0, A, B, Clock, FakeExchange, _klines, _rows, ou_spread


def _series(n: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    lb = np.log(5.0) + np.cumsum(rng.normal(0, 0.01, n))
    la = 0.3 + 0.9 * lb + ou_spread(n, seed=seed, vol=0.004)
    return la, lb


# ------------------------------------------------------------ windowed Kalman
def test_windowed_kalman_equals_the_full_filter_when_the_window_covers_the_series():
    la, lb = _series(900)
    full = kalman_filter(la, lb, 1e-5, 100)
    win = kalman_filter_windowed(la, lb, 1e-5, 100, window=900, step=50)
    for f, w in zip(full, win):
        assert np.allclose(f, w, equal_nan=True)


def test_windowed_kalman_replays_a_bot_on_its_last_window_at_each_restart():
    la, lb = _series(1500, seed=3)
    window, step = 600, 100
    win = kalman_filter_windowed(la, lb, 1e-5, 100, window=window, step=step)
    for s in (window, window + step, 1300):  # a block start: exactly the bot's window ending at s
        bot = kalman_filter(la[s - window + 1 : s + 1], lb[s - window + 1 : s + 1], 1e-5, 100)
        assert np.allclose([w[s] for w in win], [b[-1] for b in bot])
    weekly = kalman_filter_windowed(la, lb, 1e-5, 100, window=window)  # default step: one week
    assert np.allclose(estimate_fits(la, lb, 100, 1e-5, history=window)[1], weekly[1], equal_nan=True)


# ---------------------------------------------------- feed seeded from the archive
def test_feed_starts_from_the_archive_and_fills_the_rest_over_rest():
    n, stored = 400, 380
    closes = np.exp(np.linspace(1.0, 2.0, n))
    rows = _rows(closes)
    ex = FakeExchange({A: rows, B: rows})
    clock = Clock(T0 + n * HOUR + 5000)
    feed = DataFeed(ex, [A, B], history_bars=50, clock=clock, sleep=clock.sleep,
                    archive=lambda sym: [r[:5] for r in rows[:stored]], capacity=10_000)
    asyncio.run(feed.backfill())
    assert len(feed.candles._ts[A]) == n  # 380 archive + 20 REST, not the 50 of history_bars
    assert feed.candles.last_ts(A) == T0 + (n - 1) * HOUR
    assert np.allclose(feed.candles._close[A], closes)
    assert not feed.candles.gaps


def test_bot_on_the_whole_archive_fits_the_same_model_as_the_backtest(tmp_path):
    n, stored, warm, delta = 1500, 1400, 100, 1e-5
    la, lb = _series(n, seed=7)
    ra, rb = _rows(np.exp(la)), _rows(np.exp(lb))
    ex = FakeExchange({A: ra, B: rb})
    archive = {A: [r[:5] for r in ra[:stored]], B: [r[:5] for r in rb[:stored]]}
    clock = Clock(T0 + n * HOUR + 5000)
    feed = DataFeed(ex, [A, B], history_bars=200, clock=clock, sleep=clock.sleep,
                    archive=archive.get, capacity=10_000)
    cfg = AppConfig()
    cfg.strategy.kalman_history = None
    spec = PairSpec("AAA_BBB", A, B, PairParams(warm, 2.0, 0.0, delta), capital=100.0)
    store = StateStore(tmp_path / "kalman_b.db")
    store.begin_session("paper", "kalman_b")
    engine = StrategyEngine([spec], cfg, feed, RiskManager(cfg.risk),
                            PaperExecutionEngine(feed, cfg.costs.taker_fee), store)
    pr = engine.pairs[0]
    asyncio.run(feed.backfill())
    bar = T0 + (n - 1) * HOUR
    window = engine._window(pr, bar)
    assert window[0].shape[0] == n
    bot = engine._estimate(pr, window)
    backtest = [f[-1] for f in estimate_fits(la, lb, warm, delta)]
    assert np.allclose(bot, backtest)
    rows = engine.fit_rows(bar)  # the dashboard's fit history reuses the same (cached) run
    assert np.isclose(rows[-1]["kf_beta"], backtest[1])


# ------------------------------------------------------------- research --final
def test_final_research_trains_on_all_data_and_writes_pairs(tmp_path):
    n = 24 * 160
    ts = T0 + HOUR * np.arange(n, dtype=np.int64)
    rng = np.random.default_rng(1)
    market = np.cumsum(rng.normal(0, 0.008, n))
    store = DataStore(tmp_path / "data")
    for sym, logp in (("AAAUSDT", 2.0 + market + ou_spread(n, seed=2, vol=0.004)),
                      ("BBBUSDT", 1.0 + market),
                      ("CCCUSDT", 3.0 + np.cumsum(rng.normal(0, 0.01, n)))):
        path = store.kline_path(sym)
        path.parent.mkdir(parents=True, exist_ok=True)
        _klines(ts, np.exp(logp), 0.001).to_pickle(path)

    cfg = AppConfig()
    cfg.data.dir = str(tmp_path / "data")
    rc = cfg.research
    rc.runs_dir = str(tmp_path / "runs")
    rc.same_chain, rc.workers = False, 1
    rc.min_history_days, rc.funnel_days, rc.calibration_days = 100, 120, 120
    rc.ic_lookbacks, rc.ic_horizons = [168], [1, 24]
    rc.grid_lookback, rc.grid_z_entry, rc.grid_z_exit = [168], [1.5, 2.0], [0.0]
    cmd_final(cfg, argparse.Namespace(tag="final", top=5))

    assert rc.train_end_ms == int(ts[-1])  # nothing held out
    out = tmp_path / "runs" / "final"
    pairs = yaml.safe_load((out / "pairs.yaml").read_text())["pairs"]
    assert {(p["a"], p["b"]) for p in pairs} <= {("AAAUSDT", "BBBUSDT"), ("BBBUSDT", "AAAUSDT")}
    assert len(pairs) == 1
    meta = json.loads((out / "final.json").read_text())
    assert pd.Timestamp(meta["data_end"]) == pd.Timestamp(int(ts[-1]), unit="ms")

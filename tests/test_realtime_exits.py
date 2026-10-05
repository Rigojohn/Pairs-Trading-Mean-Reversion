"""Exits on live quotes between bar closes (runtime.realtime_exits, bot B from 2026-10-05)."""

import asyncio
import math

import numpy as np

from cryptopairs.config import AppConfig, StrategyConfig
from cryptopairs.core.strategy import FLAT, LONG, PairParams, PairStrategy
from cryptopairs.runtime.engine import PairSpec, StrategyEngine
from cryptopairs.runtime.execution import PaperExecutionEngine
from cryptopairs.runtime.feed import DataFeed
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import StateStore
from tests.conftest import HOUR, T0, A, B, Clock, FakeExchange, _rows


def test_check_exit_without_stops_only_reports_the_signal():
    s = PairStrategy(PairParams(100, 2.0, 0.0), StrategyConfig(), 100.0)
    s.set_fit(0.0, 1.0, 0.05, 10)
    s.on_opened(LONG, T0, 10, 100.0, -2.5, -2.5)
    assert s.check_exit(T0, 11, 70.0, -2.6, -2.6, check_stop=False) is None  # 30% down: no stop here
    assert s.check_exit(T0, 11, 70.0, -2.6, -2.6) == "stop_loss"  # the bar-close check still stops
    assert s.check_exit(T0, 11, 101.0, 0.1, 0.1, check_stop=False) == "signal"


def _setup(tmp_path):
    L = 50
    rng = np.random.default_rng(0)
    lb = np.log(5.0) + np.cumsum(rng.normal(0, 0.02, L))
    la = 0.1 + lb + rng.normal(0, 0.01, L)
    la[-1] = 0.1 + lb[-1] - 0.06  # A suddenly cheap vs B -> long spread
    clock = Clock(T0 + L * HOUR + 5000)
    ex = FakeExchange({A: _rows(np.exp(la)), B: _rows(np.exp(lb))})
    feed = DataFeed(ex, [A, B], history_bars=L, clock=clock, sleep=clock.sleep)
    cfg = AppConfig()
    cfg.risk.max_symbol_gross_leverage = 10.0
    cfg.runtime.realtime_exits = True
    spec = PairSpec("AAA_BBB", A, B, PairParams(L, 2.0, 0.0), capital=100.0)
    store = StateStore(tmp_path / "kalman_b.db")
    store.begin_session("paper", "kalman_b")
    engine = StrategyEngine([spec], cfg, feed, RiskManager(cfg.risk),
                            PaperExecutionEngine(feed, cfg.costs.taker_fee), store)
    return engine, feed, clock, store, la, lb


def _quote(feed, pa, pb):
    feed._ingest({A: {"bid": pa * 0.9999, "ask": pa * 1.0001}, B: {"bid": pb * 0.9999, "ask": pb * 1.0001}})


def test_live_exit_closes_at_once_and_blocks_reentry_in_that_bar(tmp_path):
    engine, feed, clock, store, la, lb = _setup(tmp_path)
    pr = engine.pairs[0]

    async def go():
        await feed.backfill()
        bar = feed.candles.last_ts(A)
        _quote(feed, math.exp(la[-1]), math.exp(lb[-1]))
        await engine.on_bar(bar)
        assert pr.strat.side == LONG
        # 20 minutes later, still inside the next bar: A snaps back above the exit band.
        clock.ms = bar + HOUR + 20 * 60_000
        _quote(feed, math.exp(0.1 + lb[-1] + 0.03), math.exp(lb[-1]))
        assert await engine.check_exits_live() == 1
        await engine.wait_background()
        assert pr.strat.side == FLAT and not pr.book.in_position
        assert pr.exit_bar == bar + HOUR
        # The bar-close decision for that bar must not open a new position on the same bar.
        _quote(feed, math.exp(0.1 + lb[-1] - 0.06), math.exp(lb[-1]))  # A cheap again
        await engine.on_bar(bar + HOUR)
        assert pr.strat.side == FLAT

    asyncio.run(go())
    trades = store.query("SELECT reason, pnl FROM trades")
    assert len(trades) == 1 and trades[0]["reason"] == "signal" and trades[0]["pnl"] > 0
    assert store.query("SELECT kind FROM events WHERE kind = 'realtime_exit'")


def test_live_exit_ignores_stale_quotes_and_leaves_stops_to_the_bar_close(tmp_path):
    engine, feed, clock, store, la, lb = _setup(tmp_path)
    pr = engine.pairs[0]

    async def go():
        await feed.backfill()
        bar = feed.candles.last_ts(A)
        _quote(feed, math.exp(la[-1]), math.exp(lb[-1]))
        await engine.on_bar(bar)
        assert pr.strat.side == LONG
        # A collapses through the 20% stop level: the live exit check is not a stop-loss.
        clock.ms += 60_000
        _quote(feed, math.exp(la[-1]) * 0.6, math.exp(lb[-1]))
        m = engine._marks(pr)
        assert pr.book.equity(m.mid_a, m.mid_b) < pr.strat.stop_level()
        assert await engine.check_exits_live() == 0
        assert pr.strat.side == LONG
        # A reverts above the exit band, but the quotes are older than quote_stale_seconds.
        _quote(feed, math.exp(0.1 + lb[-1] + 0.03), math.exp(lb[-1]))
        clock.ms += 120_000
        assert await engine.check_exits_live() == 0
        assert pr.strat.side == LONG

    asyncio.run(go())

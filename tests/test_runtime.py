import asyncio
import math

import numpy as np
import pytest

from cryptopairs.config import AppConfig, RiskConfig
from cryptopairs.core.strategy import FLAT, LONG, PairParams
from cryptopairs.runtime.engine import PairSpec, StrategyEngine
from cryptopairs.runtime.execution import PairOrder, PaperExecutionEngine
from cryptopairs.runtime.feed import CandleStore, DataFeed
from cryptopairs.runtime.store import StateStore
from cryptopairs.runtime.risk import RiskManager
from tests.conftest import HOUR, T0, A, B, Clock, FakeExchange, _rows

# ------------------------------------------------------------------ CandleStore
def test_candle_store_dedupes_revises_and_counts_gaps():
    cs = CandleStore(capacity=100)
    assert cs.add(A, _rows(np.array([1.0, 2.0, 3.0]))) == 3
    assert cs.add(A, [[T0 + 2 * HOUR, 0, 0, 0, 3.5, 0]]) == 0  # revision of the last bar
    assert cs.last_close(A) == 3.5
    assert cs.add(A, [[T0 + 5 * HOUR, 0, 0, 0, 4.0, 0], [T0 + 6 * HOUR, 0, 0, 0, float("nan"), 0]]) == 1
    assert cs.gaps[A] == 2
    cs.add(B, _rows(np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])))
    la, lb = cs.aligned_logs(A, B, T0 + 5 * HOUR, 4)
    assert np.allclose(np.exp(la), [1.0, 2.0, 3.5, 4.0]) and lb.shape == (4,)
    assert cs.aligned_logs(A, B, T0 + 5 * HOUR, 5) is None  # only 4 shared bars


# --------------------------------------------------------------------- DataFeed
def test_feed_backfill_and_refresh_with_retries():
    now = T0 + 10 * HOUR + 1234
    clock = Clock(now)
    ex = FakeExchange({A: _rows(np.arange(1, 13.0)), B: _rows(np.arange(1, 13.0))}, fail_first=2)
    feed = DataFeed(ex, [A, B], history_bars=8, clock=clock, sleep=clock.sleep)

    async def go():
        await feed.backfill()
        assert feed.candles.last_ts(A) == T0 + 9 * HOUR  # bar 10 is still open
        assert len(feed.candles._ts[A]) == 8
        bar = await feed.wait_for_bar_close()
        return bar

    bar = asyncio.run(go())
    assert bar == T0 + 10 * HOUR
    assert feed.candles.last_ts(A) == bar and feed.candles.last_ts(B) == bar


def test_quote_snapshots_and_staleness():
    clock = Clock(T0)
    ex = FakeExchange({A: [], B: []})
    ex.tickers = {A: {"bid": 9.9, "ask": 10.1}, B: {"bid": None, "ask": 5.0}}
    feed = DataFeed(ex, [A, B], history_bars=5, clock=clock, sleep=clock.sleep, quote_stale_s=10, snapshot_s=5)

    async def go():
        feed._running = True
        assert await feed._stream_loop() is None  # no watch_bids_asks -> snapshots only
        task = asyncio.create_task(feed._snapshot_loop())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        feed._running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())
    q = feed.quote(A)
    assert q is not None and q.mid == pytest.approx(10.0)
    assert feed.quote(B) is None  # invalid ticker ignored
    clock.ms += 60_000
    assert feed.quote(A) is None  # stale


# ------------------------------------------------------------------------- risk
def test_risk_manager_caps_and_kill_switch():
    rm = RiskManager(RiskConfig(max_open_pairs=2, max_symbol_gross_leverage=1.0, portfolio_max_drawdown=0.1))
    order = PairOrder("p", "open", A, B, 10.0, -5.0, 10.0, 10.0)
    ok = dict(open_pairs=0, gross_by_symbol={}, total_equity=1000.0, quotes_ok=True)
    assert rm.approve(order, **ok).approved
    assert not rm.approve(order, **{**ok, "gross_by_symbol": {A: 950.0}}).approved
    assert not rm.approve(order, **{**ok, "open_pairs": 2}).approved
    assert not rm.approve(order, **{**ok, "quotes_ok": False}).approved
    rm.update_equity(1000.0)
    assert rm.update_equity(899.0) is True
    assert not rm.approve(order, **ok).approved
    close = PairOrder("p", "close", A, B, -10.0, 5.0, 10.0, 10.0)
    assert rm.approve(close, **ok).approved  # closing is always allowed


# -------------------------------------------------------------- paper execution
def test_paper_fills_cross_the_spread():
    clock = Clock(T0)
    feed = DataFeed(FakeExchange({}), [A, B], history_bars=5, clock=clock, sleep=clock.sleep)
    feed._ingest({A: {"bid": 9.9, "ask": 10.1}, B: {"bid": 4.9, "ask": 5.1}})
    ex = PaperExecutionEngine(feed, taker_fee=0.001, slippage_bps=10)
    rep = asyncio.run(ex.execute(PairOrder("p", "open", A, B, 2.0, -4.0, 10.0, 5.0)))
    assert rep.ok
    assert rep.price(A) == pytest.approx(10.1 * 1.001) and rep.price(B) == pytest.approx(4.9 * 0.999)
    assert rep.fee == pytest.approx(0.001 * (2 * 10.1 * 1.001 + 4 * 4.9 * 0.999))
    clock.ms += 3_600_000  # quotes now stale
    assert not asyncio.run(ex.execute(PairOrder("p", "open", A, B, 1.0, -1.0, 10.0, 5.0))).ok
    assert asyncio.run(ex.execute(PairOrder("p", "close", A, B, -1.0, 1.0, 10.0, 5.0))).ok


# ------------------------------------------------------------ engine end-to-end
def test_engine_opens_and_closes_a_paper_trade(tmp_path):
    L = 50
    rng = np.random.default_rng(0)
    lb = np.log(5.0) + np.cumsum(rng.normal(0, 0.02, L))
    la = 0.1 + lb + rng.normal(0, 0.01, L)
    la[-1] = 0.1 + lb[-1] - 0.06  # A suddenly cheap vs B -> long spread
    clock = Clock(T0 + L * HOUR + 5000)
    ex = FakeExchange({A: _rows(np.exp(la)), B: _rows(np.exp(lb))})
    feed = DataFeed(ex, [A, B], history_bars=L, clock=clock, sleep=clock.sleep)
    cfg = AppConfig()
    cfg.risk.max_symbol_gross_leverage = 10.0  # a one-pair book needs a looser per-coin cap
    spec = PairSpec("AAA_BBB", A, B, PairParams(L, 2.0, 0.0), capital=100.0)
    store = StateStore(tmp_path / "paper.db")
    store.begin_session("paper", "strict")
    engine = StrategyEngine([spec], cfg, feed, RiskManager(cfg.risk),
                            PaperExecutionEngine(feed, cfg.costs.taker_fee), store)
    pr = engine.pairs[0]

    def quote_at_last_close():
        pa, pb = feed.candles.last_close(A), feed.candles.last_close(B)
        feed._ingest({A: {"bid": pa * 0.9999, "ask": pa * 1.0001}, B: {"bid": pb * 0.9999, "ask": pb * 1.0001}})

    async def go():
        await feed.backfill()
        bar = feed.candles.last_ts(A)
        quote_at_last_close()
        await engine.on_bar(bar)
        assert pr.strat.side == LONG and pr.book.qty_a > 0 > pr.book.qty_b
        # Next bar: the spread snaps back above the exit band.
        ex.candles[A].append([bar + HOUR, *[math.exp(0.1 + lb[-1] + 0.03)] * 4, 1.0])
        ex.candles[B].append([bar + HOUR, *[math.exp(lb[-1])] * 4, 1.0])
        clock.ms = bar + 2 * HOUR + 1000
        await feed.refresh(bar + HOUR)
        quote_at_last_close()
        await engine.on_bar(bar + HOUR)

    asyncio.run(go())
    assert pr.strat.side == FLAT and not pr.book.in_position
    assert pr.book.cash > 100.0  # profitable round trip after fees
    trades = store.query("SELECT * FROM trades")
    assert len(trades) == 1 and trades[0]["reason"] == "signal" and trades[0]["pnl"] > 0
    orders = store.query("SELECT kind, status FROM orders ORDER BY id")
    assert orders == [{"kind": "open", "status": "filled"}, {"kind": "close", "status": "filled"}]
    assert len(store.query("SELECT * FROM fills")) == 4


def test_pairs_file_defaults_to_the_profile_and_suggests_a_fix(tmp_path):
    from cryptopairs.cli import resolve_pairs_file
    from cryptopairs.config import AppConfig

    cfg = AppConfig(profile="kalman")
    cfg.research.runs_dir = str(tmp_path)
    (tmp_path / "kalman").mkdir()
    (tmp_path / "kalman" / "pairs.yaml").write_text("pairs: []\n")
    assert resolve_pairs_file(cfg, None) == str(tmp_path / "kalman" / "pairs.yaml")
    with pytest.raises(SystemExit) as exc:
        resolve_pairs_file(cfg, str(tmp_path / "kalmann" / "pairs.yaml"))  # the typo that stopped the bot
    assert "did you mean:  " + str(tmp_path / "kalman" / "pairs.yaml") in str(exc.value)

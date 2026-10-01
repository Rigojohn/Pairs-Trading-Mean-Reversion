"""StateStore and crash recovery: the bot must resume exactly where it stopped."""

import asyncio
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cryptopairs.config import AppConfig, StrategyConfig
from cryptopairs.core.book import PairBook
from cryptopairs.core.strategy import FLAT, LONG, PairParams, PairStrategy
from cryptopairs.runtime.engine import PairRuntime, PairSpec, StrategyEngine, orphan_specs
from cryptopairs.runtime.execution import ExecutionReport, LegFill, PairOrder, PaperExecutionEngine
from cryptopairs.runtime.feed import DataFeed
from cryptopairs.runtime.report import export_csv, status_text
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import StateStore
from tests.conftest import HOUR, T0, A, B, Clock, FakeExchange, _rows

L = 50


class Rig:
    """A two-symbol market where bar 1 opens a long spread and bar 2 reverts."""

    def __init__(self, tmp_path):
        rng = np.random.default_rng(0)
        lb = np.log(5.0) + np.cumsum(rng.normal(0, 0.02, L))
        la = 0.1 + lb + rng.normal(0, 0.01, L)
        la[-1] = 0.1 + lb[-1] - 0.06  # A suddenly cheap vs B
        self.lb_last = lb[-1]
        self.ex = FakeExchange({A: _rows(np.exp(la)), B: _rows(np.exp(lb))})
        self.clock = Clock(T0 + L * HOUR + 5000)
        self.db = tmp_path / "paper.db"
        self.bar = T0 + (L - 1) * HOUR

    def engine(self, specs, cfg=None):
        cfg = cfg or AppConfig()
        cfg.risk.max_symbol_gross_leverage = 10.0  # one-pair book
        store = StateStore(self.db)
        store.begin_session("paper", cfg.profile)
        symbols = sorted({s.symbol_a for s in specs} | {s.symbol_b for s in specs})
        feed = DataFeed(self.ex, symbols, history_bars=L, clock=self.clock, sleep=self.clock.sleep)
        eng = StrategyEngine(specs, cfg, feed, RiskManager(cfg.risk),
                             PaperExecutionEngine(feed, cfg.costs.taker_fee), store)
        return eng, feed, store

    def quote(self, feed):
        q = {}
        for s in (A, B):
            px = feed.candles.last_close(s)
            q[s] = {"bid": px * 0.9999, "ask": px * 1.0001}
        feed._ingest(q)

    def add_bar(self, log_a_offset: float):
        """Append the next bar: A at 0.1 + log B + offset."""
        ts = self.ex.candles[A][-1][0] + HOUR
        self.ex.candles[A].append([ts, *[math.exp(0.1 + self.lb_last + log_a_offset)] * 4, 1.0])
        self.ex.candles[B].append([ts, *[math.exp(self.lb_last)] * 4, 1.0])
        self.clock.ms = ts + HOUR + 1000
        return ts

    async def step(self, eng, feed, bar_ts):
        await feed.backfill()
        self.quote(feed)
        await eng.on_bar(bar_ts)


def spec(z_entry=2.0, pid="AAA_BBB"):
    return PairSpec(pid, A, B, PairParams(L, z_entry, 0.0), capital=100.0)


def open_then_crash(rig: Rig) -> tuple:
    async def go():
        eng, feed, store = rig.engine([spec()])
        await rig.step(eng, feed, rig.bar)
        pr = eng.pairs[0]
        assert pr.strat.side == LONG
        state = (pr.book.qty_a, pr.book.qty_b, pr.book.cash, pr.strat.entry_equity, pr.strat.fit)
        store.close()  # abrupt stop: no session end, nothing else saved
        return state

    return asyncio.run(go())


# ----------------------------------------------------------------- tests
def test_strategy_snapshot_roundtrip():
    s = PairStrategy(PairParams(100, 1.5, 0.0), StrategyConfig(), 1000.0)
    s.set_fit(0.01, 1.2, 0.03, 7)
    s.on_opened(LONG, 7 * HOUR, 7, 990.0, -1.8, -1.9)
    s.observe_equity(1200.0)
    snap = json.loads(json.dumps(s.snapshot()))
    t = PairStrategy(PairParams(100, 1.5, 0.0), StrategyConfig(), 1.0)
    t.restore(snap)
    assert t.snapshot() == s.snapshot()
    assert t.check_exit(8 * HOUR, 8, 1000.0, 0.2, 0.1) == "signal"  # prev_exit_z survived


def test_restart_restores_open_position_and_closes_it(tmp_path):
    rig = Rig(tmp_path)
    before = open_then_crash(rig)
    bar2 = rig.add_bar(+0.03)

    async def go():
        eng, feed, store = rig.engine([spec()])
        pr = eng.pairs[0]
        assert pr.strat.side == LONG
        assert (pr.book.qty_a, pr.book.qty_b, pr.book.cash, pr.strat.entry_equity, pr.strat.fit) == before
        assert pr.trade["id"] == 1
        await rig.step(eng, feed, bar2)
        return eng, store

    eng, store = asyncio.run(go())
    pr = eng.pairs[0]
    assert pr.strat.side == FLAT and pr.book.cash > 100.0
    trades = store.query("SELECT * FROM trades")
    assert len(trades) == 1 and trades[0]["reason"] == "signal" and trades[0]["pnl"] > 0
    assert trades[0]["equity_after"] == pytest.approx(pr.book.cash)
    row = store.load_pairs()["AAA_BBB"]
    assert row.strategy["side"] == FLAT and row.open_trade is None and row.book["cash"] == pr.book.cash
    assert len(store.query("SELECT * FROM sessions")) == 2

    text = status_text(store)
    assert "Closed trades: 1" in text and "Open positions: 0" in text
    files = export_csv(store, tmp_path / "export")
    assert len(pd.read_csv(tmp_path / "export" / "trades.csv")) == 1 and len(files) == 6


def test_pending_order_survives_restart_and_is_abandoned(tmp_path):
    store = StateStore(tmp_path / "x.db")
    store.begin_session("paper", "strict")
    oid, client_id = store.order_pending(PairOrder("p", "open", A, B, 1.0, -1.0, 10.0, 5.0, "long_spread"))
    assert client_id.startswith("cp")
    store.close()  # crash between "pending" and the fill
    store = StateStore(tmp_path / "x.db")
    assert [o["id"] for o in store.pending_orders()] == [oid]
    assert store.abandon_pending("paper restart") == 1
    assert store.pending_orders() == []


def test_fill_commit_is_all_or_nothing(tmp_path):
    store = StateStore(tmp_path / "x.db")
    store.begin_session("paper", "strict")
    sp = spec()
    pr = PairRuntime(sp, PairStrategy(sp.params, StrategyConfig(), 100.0), PairBook(cash=100.0),
                     trade={"pair_id": sp.id, "side": 1, "entry_ts": T0})
    order = PairOrder(sp.id, "open", A, B, 1.0, -1.0, 10.0, 5.0)
    oid, _ = store.order_pending(order)
    bad = ExecutionReport(order, ok=True, fills=[LegFill(A, 1.0, 10.0, 0.01), LegFill(None, -1.0, 5.0, 0.01)])
    with pytest.raises(sqlite3.IntegrityError):
        store.commit_open(oid, bad, pr)
    # Nothing from the failed transaction may remain.
    assert store.pending_orders()[0]["id"] == oid
    assert store.query("SELECT * FROM fills") == [] and store.query("SELECT * FROM trades") == []
    assert store.load_pairs() == {}


def test_pair_removed_from_config_is_managed_close_only(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)
    bar2 = rig.add_bar(+0.03)

    async def go():
        stored = StateStore(rig.db).load_pairs()
        orphans = orphan_specs([], stored)
        assert [o.id for o in orphans] == ["AAA_BBB"] and orphans[0].close_only
        eng, feed, store = rig.engine(orphans)
        await rig.step(eng, feed, bar2)
        assert eng.pairs[0].strat.side == FLAT  # exited on the signal
        bar3 = rig.add_bar(-0.06)  # a fresh entry signal...
        await rig.step(eng, feed, bar3)
        return eng

    eng = asyncio.run(go())
    assert eng.pairs[0].strat.side == FLAT  # ...is ignored for a close-only pair


def test_new_parameters_wait_until_position_closes(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)
    bar2 = rig.add_bar(+0.03)

    async def go():
        eng, feed, store = rig.engine([spec(z_entry=2.5)])
        pr = eng.pairs[0]
        assert pr.spec.params.z_entry == 2.0 and pr.next_params.z_entry == 2.5
        await rig.step(eng, feed, bar2)
        return eng, store

    eng, store = asyncio.run(go())
    pr = eng.pairs[0]
    assert pr.strat.side == FLAT and pr.strat.params.z_entry == 2.5 and pr.next_params is None
    assert store.load_pairs()["AAA_BBB"].z_entry == 2.5


def test_changed_symbols_with_open_position_refuse_to_start(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)
    with pytest.raises(RuntimeError, match="new pair id"):
        rig.engine([PairSpec("AAA_BBB", B, A, PairParams(L, 2.0, 0.0), capital=100.0)])


def test_store_migrates_v1_database(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE pair_state (pair_id TEXT PRIMARY KEY, symbol_a TEXT NOT NULL, symbol_b TEXT NOT NULL,
            lookback INTEGER NOT NULL, z_entry REAL NOT NULL, z_exit REAL NOT NULL, capital REAL NOT NULL,
            strategy TEXT NOT NULL, book TEXT NOT NULL, open_trade_id INTEGER, updated_at INTEGER NOT NULL);
        INSERT INTO pair_state VALUES ('P', 'A', 'B', 100, 1.5, 0.0, 100.0, '{"side": 0}', '{"cash": 100.0}', NULL, 0);
    """)
    con.commit()
    con.close()
    store = StateStore(path)
    rec = store.load_pairs()["P"]
    assert rec.kalman_delta is None and rec.params == PairParams(100, 1.5, 0.0)
    assert store.kv_get("x") is None
    from cryptopairs.runtime.store import SCHEMA_VERSION
    assert store.query("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == str(SCHEMA_VERSION)
    assert store.query("SELECT COUNT(*) AS n FROM commands")[0]["n"] == 0  # v3 tables exist


def test_kalman_pair_round_trips_through_the_store(tmp_path):
    rig = Rig(tmp_path)
    sp = PairSpec("K", A, B, PairParams(20, 2.0, 0.0, kalman_delta=1e-6), capital=100.0)

    async def go():
        eng, feed, store = rig.engine([sp])
        await rig.step(eng, feed, rig.bar)  # Kalman path in the live engine runs without error
        return store

    store = asyncio.run(go())
    assert store.load_pairs()["K"].params.kalman_delta == 1e-6

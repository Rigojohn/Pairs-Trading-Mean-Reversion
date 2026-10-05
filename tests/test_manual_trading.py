"""Manual trading from the dashboard: open, scale out, maker closes and the exit plan."""

import asyncio
import json
import sqlite3

import pytest

from cryptopairs.config import HOUR_MS, StrategyConfig
from cryptopairs.core.book import PairBook
from cryptopairs.core.sizing import LotRules
from cryptopairs.core.strategy import FLAT, LONG, SHORT, PairParams, PairStrategy
from cryptopairs.runtime.commands import CommandProcessor
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import SCHEMA_VERSION, StateStore, submit_command
from tests.test_persistence import Rig, open_then_crash, spec


# ------------------------------------------------------------------ core
def test_book_reduce_realizes_part_of_the_pnl_and_keeps_the_entry_prices():
    b = PairBook(cash=100.0)
    b.open(10.0, -20.0, 5.0, 2.0, fee=0.1)
    eq_before = b.equity(5.5, 1.9)
    realized = b.reduce(-4.0, 8.0, 5.5, 1.9, fee=0.05)
    assert realized == pytest.approx(4 * 0.5 + 8 * 0.1)
    assert (b.qty_a, b.qty_b, b.entry_px_a, b.entry_px_b) == (6.0, -12.0, 5.0, 2.0)
    assert b.equity(5.5, 1.9) == pytest.approx(eq_before - 0.05)  # only the fee changes equity
    assert b.fees_paid == pytest.approx(0.15)
    with pytest.raises(RuntimeError):
        b.reduce(1.0, 0.0, 5.5, 1.9, 0.0)  # adding to the position is not a reduction
    with pytest.raises(RuntimeError):
        b.reduce(-7.0, 0.0, 5.5, 1.9, 0.0)  # more than is held
    b.reduce(-6.0, 12.0, 5.0, 2.0, 0.0)
    assert not b.in_position and b.entry_px_a == 0.0


def test_exit_plan_overrides_apply_to_the_open_position_only():
    cfg = StrategyConfig(stop_loss_pct=0.2)
    s = PairStrategy(PairParams(100, 2.0, 0.0), cfg, 100.0)
    s.set_fit(0.0, 1.0, 0.02, 1)
    s.on_opened(LONG, HOUR_MS, 1, 100.0, -2.5, -2.5)
    assert s.stop_level() == pytest.approx(80.0)
    s.z_exit_override, s.stop_pct_override = 1.0, 0.1
    assert s.stop_level() == pytest.approx(90.0) and s.exit_z() == 1.0
    snap = json.loads(json.dumps(s.snapshot()))
    t = PairStrategy(PairParams(100, 2.0, 0.0), cfg, 100.0)
    t.restore(snap)
    assert (t.z_exit_override, t.stop_pct_override) == (1.0, 0.1)
    assert t.check_exit(2 * HOUR_MS, 2, 100.0, -0.9, -0.9) == "signal"  # crossed -1.0, not yet 0
    assert t.check_exit(2 * HOUR_MS, 2, 89.0, -2.0, -2.0) == "stop_loss"  # the tighter stop
    t.on_closed("signal", 3 * HOUR_MS)
    assert (t.z_exit_override, t.stop_pct_override) == (None, None)
    old = {k: v for k, v in snap.items() if not k.endswith("_override")}  # a pre-v4 snapshot
    u = PairStrategy(PairParams(100, 2.0, 0.0), cfg, 100.0)
    u.restore(old)
    assert u.z_exit_override is None and u.stop_level() == pytest.approx(80.0)



# ---------------------------------------------------------------- engine
async def _boot(rig, specs=None):
    eng, feed, store = rig.engine(specs or [spec()])
    await feed.backfill()
    rig.quote(feed)
    return eng, feed, store


def test_manual_open_through_a_command_is_tagged_and_managed_by_the_strategy(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        eng, feed, store = await _boot(rig)
        eng.set_paused("AAA_BBB", True)  # a pause only stops automatic entries
        proc = CommandProcessor(store, eng, RiskManager(eng.cfg.risk), max_age_s=120)
        bad = [submit_command(rig.db, "open_pair", "AAA_BBB", side=2),
               submit_command(rig.db, "open_pair", "AAA_BBB", side=1, leverage=50.0),
               submit_command(rig.db, "open_pair", "AAA_BBB")]
        ok = submit_command(rig.db, "open_pair", "AAA_BBB", side=-1, leverage=1.0, execution="market")
        store.conn.execute("UPDATE commands SET ts=?", (feed.now_ms(),))
        assert await proc.poll() == 4
        await eng.wait_background()
        res = {r["id"]: r for r in store.query("SELECT * FROM commands")}
        assert [res[i]["status"] for i in bad] == ["rejected"] * 3
        assert res[ok]["status"] == "done" and "SHORT spread submitted" in res[ok]["result"]
        assert "no entry signal" in res[ok]["result"]  # Z is about -6: short is against the signal
        pr = eng.pairs[0]
        assert pr.strat.side == SHORT and not pr.busy
        notional = abs(pr.book.qty_a) * feed.quote(pr.spec.symbol_a).mid + abs(pr.book.qty_b) * feed.quote(pr.spec.symbol_b).mid
        assert notional == pytest.approx(1.0 * 100.0, rel=0.01)  # 1x of the pair equity
        t = store.query("SELECT * FROM trades")
        assert len(t) == 1 and t[0]["entry_reason"] == "manual" and t[0]["exit_ts"] is None
        again = await eng.open_pair("AAA_BBB", LONG)
        assert again == (False, "the pair already holds a position; close it first")
        store.close()

    asyncio.run(go())


def test_manual_open_is_refused_when_unsafe(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        eng, feed, store = await _boot(rig)
        pr = eng.pairs[0]
        eng.risk.kill_switch = True
        ok, msg = await eng.open_pair("AAA_BBB", LONG)
        assert not ok and "kill switch" in msg
        eng.risk.kill_switch = False
        pr.strat.deactivated = True
        assert (await eng.open_pair("AAA_BBB", LONG))[0] is False
        pr.strat.deactivated = False
        eng.cfg.strategy.beta_max = 0.5  # the fitted beta is ~1
        ok, msg = await eng.open_pair("AAA_BBB", LONG)
        assert not ok and "hedge ratio" in msg
        eng.cfg.strategy.beta_max = 20.0
        rig.clock.ms += 120_000  # quotes are now older than quote_stale_seconds
        ok, msg = await eng.open_pair("AAA_BBB", LONG)
        assert not ok and "fresh quotes" in msg
        assert pr.strat.side == FLAT and store.query("SELECT COUNT(*) AS n FROM trades")[0]["n"] == 0
        store.close()

    asyncio.run(go())


def test_partial_close_at_market_then_maker_close_in_the_background(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)  # a long spread from the signal

    async def go():
        sp = spec()
        sp.lots_a = sp.lots_b = LotRules(step=0.001)
        eng, feed, store = await _boot(rig, [sp])
        pr = eng.pairs[0]
        qa, qb, cash = pr.book.qty_a, pr.book.qty_b, pr.book.cash
        ok, msg = await eng.close_pair("AAA_BBB", fraction=0.5, execution="market")
        assert ok and "reduced by 50%" in msg
        assert pr.strat.side == LONG and pr.book.qty_a == pytest.approx(qa / 2, abs=0.002)
        assert pr.book.qty_b == pytest.approx(qb / 2, abs=0.002) and pr.book.cash != cash
        trade = store.query("SELECT * FROM trades")[0]
        assert trade["exit_ts"] is None and trade["fees"] > 0  # still open, fees accrued
        assert store.query("SELECT kind FROM events WHERE kind='position_reduced'")
        fees_after_reduce = trade["fees"]

        ok, msg = await eng.close_pair("AAA_BBB", execution="maker")  # worked in the background
        assert ok and "close submitted" in msg and pr.busy
        assert (await eng.close_pair("AAA_BBB"))[1].startswith("an order is being executed")
        await eng.wait_background()
        assert pr.strat.side == FLAT and not pr.busy and not pr.book.in_position
        trade = store.query("SELECT * FROM trades")[0]
        assert trade["reason"] == "manual_close" and trade["fees"] > fees_after_reduce
        assert trade["pnl"] == pytest.approx(trade["equity_after"] - trade["equity_before"])
        store.close()

    asyncio.run(go())


def test_partial_close_refuses_a_remainder_below_one_lot(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)

    async def go():
        sp = spec()
        eng, feed, store = await _boot(rig, [sp])
        pr = eng.pairs[0]
        sp.lots_a = LotRules(step=abs(pr.book.qty_a) * 0.6)  # one lot is 60% of the holding
        sp.lots_b = LotRules(step=0.001)
        ok, msg = await eng.close_pair("AAA_BBB", fraction=0.75)
        assert not ok and "close 100% instead" in msg
        assert (await eng.close_pair("AAA_BBB", fraction=1.5))[0] is False
        store.close()

    asyncio.run(go())


def test_exit_plan_command_is_validated_and_cleared_on_close(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)

    async def go():
        eng, feed, store = await _boot(rig)
        proc = CommandProcessor(store, eng, RiskManager(eng.cfg.risk), max_age_s=120)
        good = submit_command(rig.db, "set_exit_plan", "AAA_BBB", z_exit=0.5, stop_loss_pct=0.1)
        bad = [submit_command(rig.db, "set_exit_plan", "AAA_BBB", z_exit=5.0),  # beyond z_entry 2.0
               submit_command(rig.db, "set_exit_plan", "AAA_BBB", stop_loss_pct=1.5)]
        store.conn.execute("UPDATE commands SET ts=?", (feed.now_ms(),))
        await proc.poll()
        res = {r["id"]: r for r in store.query("SELECT * FROM commands")}
        assert res[good]["status"] == "done" and [res[i]["status"] for i in bad] == ["rejected"] * 2
        pr = eng.pairs[0]
        assert (pr.strat.z_exit_override, pr.strat.stop_pct_override) == (0.5, 0.1)
        snap = json.loads(store.query("SELECT strategy FROM pair_state")[0]["strategy"])
        assert snap["z_exit_override"] == 0.5  # survives a restart
        await eng.close_pair("AAA_BBB")
        assert pr.strat.z_exit_override is None and pr.strat.stop_pct_override is None
        ok, msg = await eng.set_exit_plan("AAA_BBB", 0.5)
        assert not ok and "no open position" in msg
        store.close()

    asyncio.run(go())


def test_schema_v3_database_gains_entry_reason(tmp_path):
    path = tmp_path / "paper.db"
    StateStore(path).close()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE trades DROP COLUMN entry_reason")
    con.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    con.commit()
    con.close()
    store = StateStore(path)
    cols = {r["name"] for r in store.query("PRAGMA table_info(trades)")}
    assert "entry_reason" in cols
    assert store.query("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == str(SCHEMA_VERSION)
    store.close()

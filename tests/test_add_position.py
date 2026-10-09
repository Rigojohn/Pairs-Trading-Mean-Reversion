"""Manual additions to an open position (dashboard "Add"): the opposite of a partial close."""

import asyncio

import pytest

from cryptopairs.core.book import PairBook
from cryptopairs.core.sizing import LotRules
from cryptopairs.core.strategy import FLAT, LONG
from cryptopairs.runtime.commands import CommandProcessor
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import submit_command
from tests.test_manual_trading import _boot
from tests.test_persistence import Rig, open_then_crash, spec


def test_book_add_averages_the_entry_and_keeps_equity():
    b = PairBook(cash=100.0)
    b.open(10.0, -20.0, 5.0, 2.0, fee=0.1)
    eq_before = b.equity(5.5, 1.9)
    b.add(10.0, -20.0, 5.5, 1.9, fee=0.05)
    assert (b.qty_a, b.qty_b) == (20.0, -40.0)
    assert (b.entry_px_a, b.entry_px_b) == (pytest.approx(5.25), pytest.approx(1.95))
    assert b.equity(5.5, 1.9) == pytest.approx(eq_before - 0.05)  # only the fee changes equity
    assert b.equity(6.0, 1.9) - b.equity(5.5, 1.9) == pytest.approx(20 * 0.5)  # twice the exposure now
    with pytest.raises(RuntimeError):
        b.add(-1.0, 0.0, 5.5, 1.9, 0.0)  # against the holdings is a reduction, not an addition
    with pytest.raises(RuntimeError):
        PairBook(cash=100.0).add(1.0, -1.0, 5.0, 2.0, 0.0)  # nothing to add to


def test_add_at_market_grows_the_position_and_keeps_the_stops(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)  # a long spread from the signal

    async def go():
        sp = spec()
        sp.lots_a = sp.lots_b = LotRules(step=0.001)
        eng, feed, store = await _boot(rig, [sp])
        pr = eng.pairs[0]
        qa, qb = pr.book.qty_a, pr.book.qty_b
        stop, cat = pr.strat.stop_level(), pr.strat.catastrophe_level()
        ok, msg = await eng.add_pair("AAA_BBB", 0.5, execution="market")
        assert ok and "added 50%" in msg
        assert pr.strat.side == LONG and pr.book.qty_a == pytest.approx(1.5 * qa, abs=0.002)
        assert pr.book.qty_b == pytest.approx(1.5 * qb, abs=0.002)
        assert (pr.strat.stop_level(), pr.strat.catastrophe_level()) == (stop, cat)  # same USDT levels
        trade = store.query("SELECT * FROM trades")[0]
        assert trade["exit_ts"] is None and trade["fees"] > 0
        assert store.query("SELECT kind FROM events WHERE kind='position_added'")
        saved = store.query("SELECT book FROM pair_state")[0]["book"]
        assert f"{pr.book.qty_a}" in saved or str(round(pr.book.qty_a, 6))[:6] in saved  # persisted

        q15 = pr.book.qty_a
        ok, msg = await eng.add_pair("AAA_BBB", 0.25, execution="maker")  # worked in the background
        assert ok and "submitted" in msg and pr.busy
        await eng.wait_background()
        assert not pr.busy and pr.book.qty_a == pytest.approx(1.25 * q15, abs=0.002)

        ok, msg = await eng.close_pair("AAA_BBB")  # the whole, larger position closes as one trade
        assert ok and pr.strat.side == FLAT and not pr.book.in_position
        trade = store.query("SELECT * FROM trades")[0]
        assert trade["pnl"] == pytest.approx(trade["equity_after"] - trade["equity_before"])
        store.close()

    asyncio.run(go())


def test_add_is_refused_when_unsafe(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)

    async def go():
        eng, feed, store = await _boot(rig)
        pr = eng.pairs[0]
        qa = pr.book.qty_a
        assert (await eng.add_pair("AAA_BBB", 1.5))[0] is False
        assert (await eng.add_pair("AAA_BBB", 0.0))[0] is False
        eng.cfg.risk.manual_add_max_leverage = eng.cfg.strategy.leverage * 1.2  # room for +20% only
        ok, msg = await eng.add_pair("AAA_BBB", 0.5)
        assert not ok and "cap for additions" in msg
        eng.cfg.risk.manual_add_max_leverage = None
        eng.risk.kill_switch = True
        ok, msg = await eng.add_pair("AAA_BBB", 0.5)
        assert not ok and "kill switch" in msg
        eng.risk.kill_switch = False
        eng.cfg.risk.max_symbol_gross_leverage = 0.0001
        ok, msg = await eng.add_pair("AAA_BBB", 0.5)
        assert not ok and "exceed cap" in msg
        eng.cfg.risk.max_symbol_gross_leverage = 2.0
        rig.clock.ms += 120_000  # stale quotes
        ok, msg = await eng.add_pair("AAA_BBB", 0.5)
        assert not ok and "fresh quotes" in msg
        assert pr.book.qty_a == qa
        store.close()

    asyncio.run(go())


def test_add_command_from_the_dashboard(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        eng, feed, store = await _boot(rig)
        proc = CommandProcessor(store, eng, RiskManager(eng.cfg.risk), max_age_s=120)
        flat = submit_command(rig.db, "add_pair", "AAA_BBB", fraction=0.5)
        bad = submit_command(rig.db, "add_pair", "AAA_BBB")
        store.conn.execute("UPDATE commands SET ts=?", (feed.now_ms(),))
        await proc.poll()
        res = {r["id"]: r for r in store.query("SELECT * FROM commands")}
        assert res[flat]["status"] == "rejected" and "no open position" in res[flat]["result"]
        assert res[bad]["status"] == "rejected" and "invalid addition" in res[bad]["result"]
        store.close()

    asyncio.run(go())


def test_dashboard_preview_of_an_addition():
    from cryptopairs.ui import ticket as T
    from tests.test_pair_desk import _books
    pair = {"symbol_a": "A/USDT:USDT", "symbol_b": "B/USDT:USDT", "label_a": "A", "label_b": "B",
            "qty_a": 10.0, "qty_b": -10.0, "entry_a": 5.5, "entry_b": 5.0, "cash": 100.0, "stop_level": 80.0}
    p = T.preview_add(pair, 1.0, "market", _books(), None, 0.0005, 0.0002, cap=4.0)
    assert p.error is None and [l.qty for l in p.legs] == [10.0, -10.0]  # 100% doubles it, same direction
    assert p.lev_now == pytest.approx(1.05) and p.lev_after == pytest.approx(2.1, rel=0.01)
    assert p.stop_pct_now == pytest.approx(100 * 20 / 105)  # 20 USDT of room on 105 USDT
    assert p.stop_pct_after == pytest.approx(p.stop_pct_now / 2, rel=0.01)  # half the room in the spread
    assert T.preview_add(pair, 1.0, "market", _books(), None, 0.0005, 0.0002, cap=1.5).error  # over the cap

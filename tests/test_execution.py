"""Maker-first pair execution, the paper venue's fill model and the maker cost model."""

import asyncio
import itertools

import numpy as np
import pytest

from cryptopairs.config import CostConfig, StrategyConfig
from cryptopairs.core.strategy import PairParams
from cryptopairs.research.backtest import run_backtest
from cryptopairs.research.data import make_pair_data
from cryptopairs.runtime.execution import OrderState, PairExecutor, PairOrder, PaperVenue, Venue
from cryptopairs.runtime.feed import DataFeed, Quote
from tests.conftest import T0, A, B, Clock, FakeExchange, make_pair


# ------------------------------------------------------------------ paper venue
def _feed(clock: Clock) -> DataFeed:
    feed = DataFeed(FakeExchange({}), [A, B], history_bars=5, clock=clock, sleep=clock.sleep)
    feed._ingest({A: {"bid": 9.9, "ask": 10.1}, B: {"bid": 4.9, "ask": 5.1}})
    return feed


def test_paper_venue_fills_only_when_the_market_trades_through():
    clock = Clock(T0)
    feed = _feed(clock)
    venue = PaperVenue(feed, taker_fee=5e-4, maker_fee=2e-4)

    async def go():
        assert await venue.place_post_only(A, 1.0, 10.1) is None  # would cross: post-only rejects
        oid = await venue.place_post_only(A, 3.0, 9.9)  # joins the existing best bid (queue ahead)
        clock.ms += 1000
        feed._ingest_trades([{"symbol": A, "timestamp": clock.ms, "price": 9.9, "amount": 5.0, "id": 1}])
        st = await venue.order(oid)
        assert st.filled == 0.0  # trades AT our price may have filled the queue ahead of us
        feed._ingest_trades([{"symbol": A, "timestamp": clock.ms + 1, "price": 9.85, "amount": 2.0, "id": 2},
                             {"symbol": A, "timestamp": clock.ms + 1, "price": 9.85, "amount": 2.0, "id": 2}])
        st = await venue.order(oid)
        assert st.filled == pytest.approx(2.0) and st.open  # duplicate trade id counted once
        assert st.fee == pytest.approx(2.0 * 9.9 * 2e-4) and st.avg_price == 9.9

        oid2 = await venue.place_post_only(A, 1.0, 9.95)  # improves the bid: alone at the top
        feed._ingest_trades([{"symbol": A, "timestamp": clock.ms + 2, "price": 9.95, "amount": 1.0, "id": 3}])
        st2 = await venue.order(oid2)
        assert st2.filled == pytest.approx(1.0) and not st2.open

    asyncio.run(go())


def test_paper_venue_quote_rule_without_trade_stream():
    clock = Clock(T0)
    feed = _feed(clock)
    venue = PaperVenue(feed, 5e-4, 2e-4)

    async def go():
        oid = await venue.place_post_only(B, -2.0, 5.1)  # sell at the ask
        assert (await venue.order(oid)).filled == 0
        feed._ingest({B: {"bid": 5.1, "ask": 5.2}})  # buyers lifted through our level
        st = await venue.order(oid)
        assert st.filled == pytest.approx(2.0) and not st.open

    asyncio.run(go())


# ---------------------------------------------------------------- executor
class ScriptVenue(Venue):
    """Deterministic venue: the resting order fills by the scripted amounts, one per poll."""

    def __init__(self, script):
        self.quotes = {A: Quote(9.9, 10.1, 0), B: Quote(4.9, 5.1, 0)}
        self.script = iter(script)
        self.orders: dict[str, OrderState] = {}
        self.sizes: dict[str, float] = {}
        self.placed, self.markets = [], []
        self._ids = itertools.count(1)

    def quote(self, symbol):
        return self.quotes.get(symbol)

    async def market(self, symbol, qty, ref, allow_stale):
        q = self.quotes[symbol]
        px = q.ask if qty > 0 else q.bid
        self.markets.append((symbol, qty))
        return px, abs(qty) * px * 5e-4

    async def place_post_only(self, symbol, qty, price):
        oid = f"o{next(self._ids)}"
        self.placed.append((symbol, qty, price))
        self.orders[oid] = OrderState(open=True)
        self.sizes[oid] = abs(qty)
        return oid

    async def order(self, oid):
        st = self.orders[oid]
        if st.open:
            add = min(next(self.script, 0.0), self.sizes[oid] - st.filled)
            px = self.placed[int(oid[1:]) - 1][2]
            st = OrderState(st.filled + add, px, (st.filled + add) * px * 2e-4, st.filled + add < self.sizes[oid])
            self.orders[oid] = st
        return st

    async def cancel(self, oid):
        st = self.orders[oid]
        self.orders[oid] = OrderState(st.filled, st.avg_price, st.fee, False)
        return self.orders[oid]


def _executor(venue, clock, **kw):
    return PairExecutor(venue, maker_first=True, timeout_s=60, reprice_s=10, poll_s=2,
                        clock=lambda: clock.ms / 1000, sleep=clock.sleep, **kw)


def test_maker_first_hedges_every_fill_and_completes():
    clock = Clock(T0)
    venue = ScriptVenue([0.0, 4.0, 0.0, 6.0])  # the 10-unit maker order fills 4 then 6
    order = PairOrder("p", "open", A, B, 10.0, -15.0, 10.0, 5.0)  # A notional 100 > B 75: A is maker
    rep = asyncio.run(_executor(venue, clock).execute(order))
    assert rep.ok and rep.qty(A) == pytest.approx(10.0) and rep.qty(B) == pytest.approx(-15.0)
    assert venue.placed[0] == (A, 10.0, 9.9)  # rests at the bid
    assert venue.markets == [(B, -6.0), (B, -9.0)]  # each fill hedged pro rata, at once
    assert rep.price(A) == pytest.approx(9.9)  # bought at the bid instead of the 10.1 ask
    assert rep.stats["maker_share"] > 0.5 and rep.stats["fee_bps"] < 5.0


def test_maker_first_times_out_into_market_orders():
    clock = Clock(T0)
    venue = ScriptVenue([1.0])  # one unit fills, then nothing
    order = PairOrder("p", "close", A, B, -10.0, 15.0, 10.0, 5.0, reason="signal")
    rep = asyncio.run(_executor(venue, clock).execute(order))
    assert rep.ok and rep.qty(A) == pytest.approx(-10.0) and rep.qty(B) == pytest.approx(15.0)
    assert (A, -9.0) in venue.markets  # the rest of the maker leg taken after the timeout
    assert sum(q for s, q in venue.markets if s == B) == pytest.approx(15.0)
    assert rep.stats["seconds"] >= 60


def test_stops_and_missing_quotes():
    clock = Clock(T0)
    venue = ScriptVenue([])
    stop = PairOrder("p", "close", A, B, -10.0, 15.0, 10.0, 5.0, reason="stop_loss", urgent=True)
    rep = asyncio.run(_executor(venue, clock).execute(stop))
    assert rep.ok and venue.placed == [] and len(venue.markets) == 2  # urgent: market orders only
    assert rep.stats["seconds"] == 0

    venue.quotes.pop(B)
    rep = asyncio.run(_executor(venue, clock).execute(PairOrder("p", "open", A, B, 10.0, -15.0, 10.0, 5.0)))
    assert not rep.ok and len(venue.markets) == 2  # nothing new traded


def test_maker_order_follows_the_best_price():
    clock = Clock(T0)
    venue = ScriptVenue([0.0] * 5 + [10.0])
    ex = _executor(venue, clock)

    async def go():
        task = asyncio.create_task(ex.execute(PairOrder("p", "open", A, B, 10.0, -15.0, 10.0, 5.0)))
        await asyncio.sleep(0)
        venue.quotes[A] = Quote(9.95, 10.15, 0)  # the bid moves up
        return await task

    rep = asyncio.run(go())
    assert rep.ok and [p[2] for p in venue.placed] == [9.9, 9.95]  # repriced once (after reprice_s)


def test_tick_improvement_inside_a_wide_spread():
    clock = Clock(T0)
    venue = ScriptVenue([10.0])
    rep = asyncio.run(_executor(venue, clock, ticks={A: 0.01}).execute(
        PairOrder("p", "open", A, B, 10.0, -15.0, 10.0, 5.0)))
    assert rep.ok and venue.placed[0][2] == pytest.approx(9.91)  # one tick above the bid


# ---------------------------------------------------------------- backtest model
def test_backtest_maker_leg_lowers_costs_but_not_on_stops():
    ka, kb = make_pair(n=3000, seed=4)
    data = make_pair_data("AUSDT", "BUSDT", ka, kb)
    args = (data, PairParams(240, 1.5, 0.0), StrategyConfig())
    win = (int(data.ts[240]), int(data.ts[-1]), 10_000.0)
    taker = run_backtest(*args, CostConfig(half_spread_bps=5, slippage_bps=5), *win)
    maker = run_backtest(*args, CostConfig(half_spread_bps=5, slippage_bps=5, maker_legs=1), *win)
    assert maker.fees < taker.fees and maker.equity[-1] > taker.equity[-1]
    # A stop exit pays taker fees on both legs even in maker mode.
    stops = [t for t in maker.trades if t.get("reason") == "stop_loss"]
    if stops:
        t = stops[0]
        exit_fee = 5e-4 * (abs(t["qty_a"]) * t["exit_px_a"] + abs(t["qty_b"]) * t["exit_px_b"])
        entry_notional = abs(t["qty_a"]) * t["entry_px_a"] + abs(t["qty_b"]) * t["entry_px_b"]
        assert t["fees"] - exit_fee < 5e-4 * entry_notional  # the entry was cheaper (one maker leg)

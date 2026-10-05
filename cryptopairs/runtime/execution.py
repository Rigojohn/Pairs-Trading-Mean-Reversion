"""Order execution: the pair execution algorithm and the venues it trades on.

PairExecutor (the algorithm, the same for paper and live):
    taker:       both legs as market orders, at once.
    maker_first: the larger-notional leg rests as a post-only limit at the best price,
                 repriced while it waits. Every fill is hedged at once on the other leg
                 with a market order, so the pair is never unhedged by more than one
                 poll interval. Whatever is still open after the timeout is taken with
                 market orders. Urgent orders (stops) always use market orders.

Venue (where orders go):
    PaperVenue   simulated against live quotes and the public trade stream. A resting
                 order fills only when the market actually trades through its price,
                 or at its price if the order was alone at the top of the book.
    (LiveVenue   ccxt orders on the exchange: Phase 3b.)
"""

from __future__ import annotations

import abc
import asyncio
import itertools
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from cryptopairs.runtime.feed import DataFeed, Quote

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PairOrder:
    pair_id: str
    kind: str  # "open" | "close" | "reduce" (manual partial close)
    symbol_a: str
    symbol_b: str
    qty_a: float  # signed quantity to trade (+ buy, - sell)
    qty_b: float
    ref_a: float  # reference price (mid or last close) for fallbacks and cost measurement
    ref_b: float
    reason: str = ""
    urgent: bool = False  # stops: market orders only, no waiting


@dataclass(frozen=True)
class LegFill:
    symbol: str
    qty: float  # signed quantity actually traded
    price: float  # average price
    fee: float


@dataclass
class ExecutionReport:
    order: PairOrder
    ok: bool
    fills: list[LegFill] = field(default_factory=list)
    error: str = ""
    stats: dict = field(default_factory=dict)

    @property
    def fee(self) -> float:
        return sum(f.fee for f in self.fills)

    def price(self, symbol: str) -> float:
        return next(f.price for f in self.fills if f.symbol == symbol)

    def qty(self, symbol: str) -> float:
        return next(f.qty for f in self.fills if f.symbol == symbol)


class ExecutionEngine(abc.ABC):
    @abc.abstractmethod
    async def execute(self, order: PairOrder) -> ExecutionReport: ...

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass


# --------------------------------------------------------------------------- venues
@dataclass
class OrderState:
    filled: float = 0.0  # unsigned
    avg_price: float = 0.0
    fee: float = 0.0
    open: bool = True


class Venue(abc.ABC):
    """Minimal order interface the executor needs (paper or exchange)."""

    @abc.abstractmethod
    def quote(self, symbol: str) -> Quote | None: ...

    @abc.abstractmethod
    async def market(self, symbol: str, qty: float, ref: float, allow_stale: bool) -> tuple[float, float]:
        """Fill ``qty`` (signed) now. Returns (avg price, fee); raises RuntimeError if impossible."""

    @abc.abstractmethod
    async def place_post_only(self, symbol: str, qty: float, price: float) -> str | None:
        """Rest a maker order; None if it would cross the book (rejected by post-only)."""

    @abc.abstractmethod
    async def order(self, order_id: str) -> OrderState: ...

    @abc.abstractmethod
    async def cancel(self, order_id: str) -> OrderState: ...


@dataclass
class _PaperOrder:
    symbol: str
    qty: float  # signed
    price: float
    placed_ms: int
    alone: bool  # strictly better than the existing best price when placed
    seen_ms: int
    state: OrderState = field(default_factory=OrderState)


class PaperVenue(Venue):
    """Simulated fills on live market data; nothing is sent to the exchange.

    Market orders: best ask (buy) / bid (sell), worsened by ``slippage_bps``.
    Post-only orders fill from the public trade stream, conservatively:
      - volume that traded strictly through our price fills us;
      - volume at exactly our price fills us only if we improved the best price
        when placing (no queue ahead of us).
    Without a trade stream, a resting order fills when the opposite best price
    reaches it (the book moved through us).
    """

    def __init__(self, feed: DataFeed, taker_fee: float, maker_fee: float, slippage_bps: float = 0.0) -> None:
        self.feed = feed
        self.taker_fee, self.maker_fee = taker_fee, maker_fee
        self.slip = slippage_bps * 1e-4
        self._orders: dict[str, _PaperOrder] = {}
        self._ids = itertools.count(1)

    def quote(self, symbol: str) -> Quote | None:
        return self.feed.quote(symbol)

    async def market(self, symbol: str, qty: float, ref: float, allow_stale: bool) -> tuple[float, float]:
        q = self.feed.quote(symbol)
        if q is None:
            if not allow_stale:
                raise RuntimeError(f"no fresh quote for {symbol}")
            px = ref  # exits must not be blocked by a stale stream
        else:
            px = q.ask if qty > 0 else q.bid
        px = px * (1 + self.slip) if qty > 0 else px * (1 - self.slip)
        return px, abs(qty) * px * self.taker_fee

    async def place_post_only(self, symbol: str, qty: float, price: float) -> str | None:
        q = self.feed.quote(symbol)
        if q is None or (qty > 0 and price >= q.ask) or (qty < 0 and price <= q.bid):
            return None  # would take liquidity: post-only rejects it
        now = self.feed.now_ms()
        alone = price > q.bid if qty > 0 else price < q.ask
        oid = f"paper-{next(self._ids)}"
        self._orders[oid] = _PaperOrder(symbol, qty, price, now, alone, now)
        return oid

    def _match(self, o: _PaperOrder) -> None:
        st = o.state
        if not st.open:
            return
        remaining = abs(o.qty) - st.filled
        buy = o.qty > 0
        fill = 0.0
        if self.feed.has_trades(o.symbol):
            for ts, px, amount in self.feed.trades_since(o.symbol, o.seen_ms):
                o.seen_ms = max(o.seen_ms, ts)
                through = px < o.price if buy else px > o.price
                at = px == o.price and o.alone
                if through or at:
                    fill += amount
        else:
            q = self.feed.quote(o.symbol)
            if q is not None and ((buy and q.ask <= o.price) or (not buy and q.bid >= o.price)):
                fill = remaining
        fill = min(fill, remaining)
        if fill > 0:
            st.avg_price = o.price  # limit orders fill at their own price
            st.filled += fill
            st.fee += fill * o.price * self.maker_fee
            if st.filled >= abs(o.qty) - 1e-12:
                st.open = False

    async def order(self, order_id: str) -> OrderState:
        o = self._orders[order_id]
        self._match(o)
        return OrderState(o.state.filled, o.state.avg_price, o.state.fee, o.state.open)

    async def cancel(self, order_id: str) -> OrderState:
        o = self._orders.pop(order_id)
        self._match(o)
        o.state.open = False
        return OrderState(o.state.filled, o.state.avg_price, o.state.fee, False)


# ------------------------------------------------------------------------ executor
@dataclass
class _Leg:
    symbol: str
    target: float  # signed
    ref: float
    qty: float = 0.0
    notional: float = 0.0
    fee: float = 0.0
    maker_qty: float = 0.0

    def add(self, qty: float, price: float, fee: float, maker: bool = False) -> None:
        self.qty += qty
        self.notional += qty * price
        self.fee += fee
        if maker:
            self.maker_qty += abs(qty)

    @property
    def remaining(self) -> float:
        return self.target - self.qty

    @property
    def price(self) -> float:
        return self.notional / self.qty if self.qty else self.ref

    def fill(self) -> LegFill:
        return LegFill(self.symbol, self.qty, self.price, self.fee)


class PairExecutor(ExecutionEngine):
    def __init__(
        self,
        venue: Venue,
        *,
        maker_first: bool = False,
        timeout_s: float = 300.0,
        reprice_s: float = 10.0,
        poll_s: float = 2.0,
        ticks: dict[str, float] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.venue = venue
        self.maker_first = maker_first
        self.timeout_s, self.reprice_s, self.poll_s = timeout_s, reprice_s, poll_s
        self.ticks = ticks or {}
        self._clock, self._sleep = clock, sleep

    async def execute(self, order: PairOrder) -> ExecutionReport:
        start = self._clock()
        a = _Leg(order.symbol_a, order.qty_a, order.ref_a)
        b = _Leg(order.symbol_b, order.qty_b, order.ref_b)
        stale_ok = order.kind != "open"  # a close or reduction must go through even on an old quote
        if order.kind == "open" and (self.venue.quote(a.symbol) is None or self.venue.quote(b.symbol) is None):
            return ExecutionReport(order, ok=False, error="no fresh quotes")  # nothing traded
        try:
            if self.maker_first and not order.urgent:
                maker, hedge = (a, b) if abs(a.target) * a.ref >= abs(b.target) * b.ref else (b, a)
                await self._work_maker(maker, hedge, stale_ok)
            # Market orders for anything left (all of it in taker mode or for stops).
            for leg in (a, b):
                if abs(leg.remaining) > 1e-12:
                    px, fee = await self.venue.market(leg.symbol, leg.remaining, leg.ref, stale_ok)
                    leg.add(leg.remaining, px, fee)
        except Exception as exc:  # report what did trade, so the caller can see the imbalance
            log.error("%s: execution error: %s", order.pair_id, exc)
            return ExecutionReport(order, ok=False, fills=[a.fill(), b.fill()], error=str(exc))
        return ExecutionReport(order, ok=True, fills=[a.fill(), b.fill()],
                               stats=self._stats(order, a, b, self._clock() - start))

    async def _work_maker(self, maker: _Leg, hedge: _Leg, stale_ok: bool) -> None:
        ratio = hedge.target / maker.target
        tick = self.ticks.get(maker.symbol, 0.0)
        deadline = self._clock() + self.timeout_s
        oid: str | None = None
        seen = OrderState(open=False)
        price = None
        placed_at = -math.inf

        async def absorb(st: OrderState) -> None:
            nonlocal seen
            dq = st.filled - seen.filled
            if dq > 0:
                notional = st.filled * st.avg_price - seen.filled * seen.avg_price
                signed = math.copysign(dq, maker.target)
                maker.add(signed, notional / dq, st.fee - seen.fee, maker=True)
                hq = signed * ratio  # hedge the fill at once
                px, fee = await self.venue.market(hedge.symbol, hq, hedge.ref, stale_ok)
                hedge.add(hq, px, fee)
            seen = st

        while abs(maker.remaining) > 1e-12 and self._clock() < deadline:
            q = self.venue.quote(maker.symbol)
            if q is None:
                break
            want = self._passive_price(q, maker.remaining > 0, tick)
            now = self._clock()
            if oid is None or (want != price and now - placed_at >= self.reprice_s):
                if oid is not None:
                    await absorb(await self.venue.cancel(oid))
                    oid = None
                if abs(maker.remaining) <= 1e-12:
                    break
                oid = await self.venue.place_post_only(maker.symbol, maker.remaining, want)
                price, placed_at, seen = want, now, OrderState(open=True)
                if oid is None:  # the book moved under us; retry after a pause
                    await self._sleep(self.poll_s)
                    continue
            await self._sleep(self.poll_s)
            st = await self.venue.order(oid)
            await absorb(st)
            if not st.open:
                oid = None
        if oid is not None:
            await absorb(await self.venue.cancel(oid))

    @staticmethod
    def _passive_price(q: Quote, buy: bool, tick: float) -> float:
        """Best bid (buy) / ask (sell); one tick better when the spread leaves room."""
        if tick > 0 and q.ask - q.bid > 1.5 * tick:
            return q.bid + tick if buy else q.ask - tick
        return q.bid if buy else q.ask

    @staticmethod
    def _stats(order: PairOrder, a: _Leg, b: _Leg, seconds: float) -> dict:
        gross = abs(a.notional) + abs(b.notional)
        if gross <= 0:
            return {}
        # Cost versus the reference mids: paying above the mid on a buy (or below on a sell) is a cost.
        slip = sum((leg.price - leg.ref) * leg.qty for leg in (a, b))
        maker_notional = sum(leg.maker_qty * abs(leg.price) for leg in (a, b))
        return {
            "order_kind": order.kind, "urgent": order.urgent, "seconds": round(seconds, 1),
            "maker_share": round(maker_notional / gross, 4),
            "cost_bps": round(1e4 * (slip + a.fee + b.fee) / gross, 2),
            "fee_bps": round(1e4 * (a.fee + b.fee) / gross, 2),
        }


class PaperExecutionEngine(PairExecutor):
    """Taker-only paper execution (kept for simple setups and tests)."""

    def __init__(self, feed: DataFeed, taker_fee: float, slippage_bps: float = 0.0) -> None:
        super().__init__(PaperVenue(feed, taker_fee, taker_fee, slippage_bps), maker_first=False)

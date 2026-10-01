"""DataFeed: closed 1h candles over REST and best bid/ask over WebSocket.

Design:
- Candles are pulled over REST once per hour, right after the bar closes. Any
  missing hours are back-filled in the same call, so a disconnect or restart
  never leaves a silent gap. For an hourly strategy this costs a handful of
  light requests per symbol per hour.
- Quotes stream over one shared WebSocket (ccxt.pro ``watch_bids_asks``);
  errors reconnect with exponential backoff and jitter. The exchange pushes a
  quote only when it changes, so a quiet book can look old while still being
  current. A REST snapshot of every book every ``snapshot_s`` seconds (one
  light request) refreshes all quotes and covers silent stream failures.
- Every quote carries its receive time; ``quote()`` returns None once a quote
  is older than ``quote_stale_s``, and callers decide what to do (entries
  are refused, exits fall back to the last close).
"""

from __future__ import annotations

import asyncio
import collections
import logging
import math
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import ccxt
import numpy as np

from cryptopairs.config import HOUR_MS

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Quote:
    bid: float
    ask: float
    recv_ms: int

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)


class CandleStore:
    """Rolling closed-candle closes per symbol, kept in time order without duplicates."""

    def __init__(self, capacity: int, tf_ms: int = HOUR_MS) -> None:
        self.capacity = int(capacity)
        self.tf_ms = tf_ms
        self._ts: dict[str, list[int]] = {}
        self._close: dict[str, list[float]] = {}
        self.gaps: dict[str, int] = {}

    def add(self, symbol: str, rows: list[list[Any]]) -> int:
        """Append [ts, open, high, low, close, ...] rows. Returns how many were new."""
        ts_l = self._ts.setdefault(symbol, [])
        cl_l = self._close.setdefault(symbol, [])
        added = 0
        for row in sorted(rows, key=lambda r: r[0]):
            ts, close = int(row[0]), row[4]
            if close is None or not math.isfinite(float(close)) or float(close) <= 0:
                log.warning("%s: dropping invalid candle %s", symbol, row)
                continue
            if ts_l and ts <= ts_l[-1]:
                if ts == ts_l[-1]:
                    cl_l[-1] = float(close)  # a revised candle replaces the stored one
                continue
            if ts_l and ts - ts_l[-1] > self.tf_ms:
                missing = (ts - ts_l[-1]) // self.tf_ms - 1
                self.gaps[symbol] = self.gaps.get(symbol, 0) + int(missing)
                log.warning("%s: %d hour(s) missing before %d (exchange has no data)", symbol, missing, ts)
            ts_l.append(ts)
            cl_l.append(float(close))
            added += 1
        if len(ts_l) > self.capacity * 1.25:
            del ts_l[: len(ts_l) - self.capacity]
            del cl_l[: len(cl_l) - self.capacity]
        return added

    def last_ts(self, symbol: str) -> int | None:
        ts = self._ts.get(symbol)
        return ts[-1] if ts else None

    def last_close(self, symbol: str) -> float | None:
        cl = self._close.get(symbol)
        return cl[-1] if cl else None

    def aligned_logs(
        self, a: str, b: str, end_ts: int, n: int, min_n: int | None = None
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Log closes of the last ``n`` bars both symbols share, ending exactly at ``end_ts``.

        With ``min_n``, fewer bars (but at least ``min_n``) are accepted.
        """
        w = self.aligned_window(a, b, end_ts, n, min_n)
        return None if w is None else (w[1], w[2])

    def aligned_window(
        self, a: str, b: str, end_ts: int, n: int, min_n: int | None = None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Like ``aligned_logs`` but also returns the bar open times: (ts, log_a, log_b)."""
        if self.last_ts(a) != end_ts or self.last_ts(b) != end_ts:
            return None
        ta, tb = np.asarray(self._ts[a]), np.asarray(self._ts[b])
        common, ia, ib = np.intersect1d(ta, tb, assume_unique=True, return_indices=True)
        if common.shape[0] < (n if min_n is None else min_n):
            return None
        ca, cb = np.asarray(self._close[a])[ia[-n:]], np.asarray(self._close[b])[ib[-n:]]
        return common[-n:], np.log(ca), np.log(cb)


class DataFeed:
    def __init__(
        self,
        exchange: Any,
        symbols: list[str],
        history_bars: int,
        *,
        timeframe: str = "1h",
        tf_ms: int = HOUR_MS,
        grace_s: float = 5.0,
        quote_stale_s: float = 45.0,
        max_concurrency: int = 4,
        snapshot_s: float = 20.0,
        trade_stream: bool = False,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.ex = exchange
        self.symbols = sorted(set(symbols))
        self.history_bars = int(history_bars)
        self.timeframe, self.tf_ms = timeframe, tf_ms
        self.grace_s, self.quote_stale_ms = grace_s, int(quote_stale_s * 1000)
        self.snapshot_s = snapshot_s
        self.candles = CandleStore(capacity=self.history_bars + 48, tf_ms=tf_ms)
        self._quotes: dict[str, Quote] = {}
        self._sem = asyncio.Semaphore(max_concurrency)
        self._clock, self._sleep = clock, sleep
        self._tasks: list[asyncio.Task] = []
        # Public trade tape per symbol (time, price, amount); used to simulate maker fills.
        self.trade_stream = trade_stream
        self._trades: dict[str, collections.deque] = {}
        self._trade_ids: dict[str, collections.deque] = {}
        self._running = False

    # -------------------------------------------------------------- helpers
    def now_ms(self) -> int:
        return int(self._clock() * 1000)

    async def _call(self, fn: Callable[..., Awaitable[Any]], *args: Any, attempts: int = 5, **kwargs: Any) -> Any:
        """Call the exchange, retrying network/rate-limit errors with backoff."""
        delay = 1.0
        for attempt in range(1, attempts + 1):
            try:
                async with self._sem:
                    return await fn(*args, **kwargs)
            except (ccxt.NetworkError, asyncio.TimeoutError) as exc:  # incl. rate limits, timeouts
                if attempt == attempts:
                    raise
                log.warning("%s failed (%s), retry %d/%d in %.1fs", getattr(fn, "__name__", fn), exc,
                            attempt, attempts - 1, delay)
                await self._sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 30.0)

    async def _fetch_closed(self, symbol: str, since: int, last_closed: int) -> list[list[Any]]:
        """All closed candles with since <= ts <= last_closed (paginated)."""
        out: list[list[Any]] = []
        while since <= last_closed:
            rows = await self._call(self.ex.fetch_ohlcv, symbol, self.timeframe, since=since, limit=1500)
            rows = [r for r in rows or [] if since <= r[0] <= last_closed]
            if not rows:
                break
            out.extend(rows)
            since = int(rows[-1][0]) + self.tf_ms
        return out

    # ---------------------------------------------------------------- candles
    def last_closed_bar(self, now_ms: int | None = None) -> int:
        now = self.now_ms() if now_ms is None else now_ms
        return (now // self.tf_ms) * self.tf_ms - self.tf_ms

    async def backfill(self) -> None:
        last_closed = self.last_closed_bar()
        since = last_closed - (self.history_bars - 1) * self.tf_ms

        async def one(sym: str) -> None:
            rows = await self._fetch_closed(sym, since, last_closed)
            self.candles.add(sym, rows)
            log.info("backfilled %s: %d bars", sym, len(rows))

        await asyncio.gather(*(one(s) for s in self.symbols))

    async def refresh(self, bar_ts: int, attempts: int = 4) -> set[str]:
        """Fetch new closed candles up to ``bar_ts``. Returns symbols still missing it."""
        missing = set(self.symbols)
        for attempt in range(attempts):
            async def one(sym: str) -> None:
                last = self.candles.last_ts(sym)
                since = (last + self.tf_ms) if last is not None else bar_ts - (self.history_bars - 1) * self.tf_ms
                try:
                    rows = await self._fetch_closed(sym, since, bar_ts)
                except ccxt.BaseError as exc:
                    log.warning("refresh %s failed: %s", sym, exc)
                    return
                self.candles.add(sym, rows)

            await asyncio.gather(*(one(s) for s in sorted(missing)))
            missing = {s for s in missing if self.candles.last_ts(s) != bar_ts}
            if not missing:
                break
            await self._sleep(2.0 * (attempt + 1))  # the exchange may publish the bar late
        if missing:
            log.warning("bar %d still missing for %s; those pairs skip this bar", bar_ts, sorted(missing))
        return missing

    async def wait_for_bar_close(self) -> int:
        """Sleep until the current bar closes (+grace), refresh candles, return its open time."""
        now = self.now_ms()
        boundary = (now // self.tf_ms + 1) * self.tf_ms
        await self._sleep((boundary - now) / 1000.0 + self.grace_s)
        bar_ts = boundary - self.tf_ms
        await self.refresh(bar_ts)
        return bar_ts

    # ------------------------------------------------------------- trade tape
    def has_trades(self, symbol: str) -> bool:
        return symbol in self._trades

    def trades_since(self, symbol: str, since_ms: int) -> list[tuple[int, float, float]]:
        return [t for t in self._trades.get(symbol, ()) if t[0] > since_ms]

    def _ingest_trades(self, trades: list[dict]) -> None:
        for t in trades or []:
            sym, ts, px, amt = t.get("symbol"), t.get("timestamp"), t.get("price"), t.get("amount")
            if sym is None or ts is None or px is None or amt is None:
                continue
            ids = self._trade_ids.setdefault(sym, collections.deque(maxlen=5000))
            tid = t.get("id")
            if tid is not None and tid in ids:  # streams may resend; count each trade once
                continue
            if tid is not None:
                ids.append(tid)
            self._trades.setdefault(sym, collections.deque(maxlen=20000)).append((int(ts), float(px), float(amt)))

    async def _trades_loop(self) -> None:
        if not hasattr(self.ex, "watch_trades_for_symbols"):
            return
        delay = 1.0
        while self._running:
            try:
                self._ingest_trades(await self.ex.watch_trades_for_symbols(self.symbols))
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except ccxt.NotSupported:
                log.warning("exchange has no trade stream; maker fills use the quote-based rule")
                return
            except Exception as exc:
                log.warning("trade stream error: %s (reconnecting in %.0fs)", exc, delay)
                await self._sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 60.0)

    # ---------------------------------------------------------------- funding
    async def funding_events(self, symbol: str, since: int, until: int) -> list[tuple[int, float]]:
        """Funding settlements (time, rate) with since < time <= until; [] if unavailable."""
        try:
            rows = await self._call(self.ex.fetch_funding_rate_history, symbol, since=since, limit=100)
        except (ccxt.NotSupported, AttributeError):
            return []
        except ccxt.BaseError as exc:
            log.warning("funding history for %s failed: %s", symbol, exc)
            return []
        out = []
        for r in rows or []:
            ts, rate = r.get("timestamp"), r.get("fundingRate")
            if ts is not None and rate is not None and since < int(ts) <= until:
                out.append((int(ts), float(rate)))
        return sorted(out)

    # ----------------------------------------------------------------- quotes
    async def wait_for_quotes(self, timeout_s: float = 20.0) -> int:
        """Wait until every symbol has a fresh quote (or the timeout). Returns how many do."""
        deadline = self.now_ms() + int(timeout_s * 1000)
        while True:
            ready = sum(1 for s in self.symbols if self.quote(s) is not None)
            if ready == len(self.symbols) or self.now_ms() >= deadline:
                return ready
            await self._sleep(0.5)

    def quote(self, symbol: str) -> Quote | None:
        q = self._quotes.get(symbol)
        if q is None or self.now_ms() - q.recv_ms > self.quote_stale_ms:
            return None
        return q

    def _ingest(self, tickers: dict[str, dict]) -> None:
        now = self.now_ms()
        for sym, t in (tickers or {}).items():
            bid, ask = t.get("bid"), t.get("ask")
            if bid is None or ask is None:
                continue
            bid, ask = float(bid), float(ask)
            if math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask:
                self._quotes[sym] = Quote(bid, ask, now)

    async def _stream_loop(self) -> None:
        """WebSocket best bid/ask; reconnects forever with backoff."""
        if not hasattr(self.ex, "watch_bids_asks"):
            return  # REST snapshots only
        failures, delay = 0, 1.0
        while self._running:
            try:
                self._ingest(await self.ex.watch_bids_asks(self.symbols))
                failures, delay = 0, 1.0
            except asyncio.CancelledError:
                raise
            except ccxt.NotSupported:
                log.warning("exchange has no bid/ask stream; using REST snapshots only")
                return
            except Exception as exc:
                failures += 1
                log.warning("quote stream error #%d: %s (reconnecting in %.0fs)", failures, exc, delay)
                await self._sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 60.0)

    async def _snapshot_loop(self) -> None:
        """REST snapshot of every book: keeps quiet symbols fresh, survives stream outages."""
        while self._running:
            try:
                self._ingest(await self._call(self.ex.fetch_bids_asks, self.symbols))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("quote snapshot failed: %s", exc)
            await self._sleep(self.snapshot_s)

    # -------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._running = True
        await self.backfill()
        self._tasks = [
            asyncio.create_task(self._stream_loop(), name="quote-stream"),
            asyncio.create_task(self._snapshot_loop(), name="quote-snapshot"),
        ]
        if self.trade_stream:
            self._tasks.append(asyncio.create_task(self._trades_loop(), name="trades"))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

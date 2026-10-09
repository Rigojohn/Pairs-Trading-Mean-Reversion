"""Exchange data for the dashboard, fetched by the dashboard's server from Binance REST.

Instrument facts (24h statistics, open interest, funding, tick and lot sizes, sector tags),
order books for the order-ticket preview and candles for the charts. Everything comes from
memory and is refreshed in the background (ui/swr.py), so a click never waits for Binance, and
every function returns None instead of raising when Binance cannot be reached, so a page never
breaks on a network hiccup. ``CRYPTOPAIRS_OFFLINE=1`` (tests)
turns the network off. CoinGecko adds names and market caps when it answers.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import streamlit as st

from cryptopairs.ui import swr

log = logging.getLogger(__name__)
HOUR = 3_600_000
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": HOUR, "4h": 4 * HOUR, "1d": 24 * HOUR}
# Candles per timeframe on the Pair desk: 1 day of 1m, 4 of 5m, 10 of 15m, ~6 weeks of 1h, 3 months of 4h,
# 4 months of 1d. Spread Z reaches further back with the archive (ui/history.py).
CANDLES = {"1m": 1440, "5m": 1152, "15m": 960, "1h": 1000, "4h": 540, "1d": 120}
# Binance (through ccxt) returns at most this many candles per request; more come page by page.
PER_REQUEST = 1000
# With an open position, an intraday chart reaches back to its entry, at most this many bars:
# 3 days of 1m, 14 of 5m, 30 of 15m.
REACH_BACK = {"1m": 3 * 1440, "5m": 14 * 288, "15m": 30 * 96}


def offline() -> bool:
    return os.environ.get("CRYPTOPAIRS_OFFLINE") == "1"


@st.cache_resource(ttl=6 * 3600, show_spinner=False)
def _exchange():
    import ccxt

    ex = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 10_000})
    ex.load_markets()
    return ex


def exchange():
    if offline():
        return None
    try:
        return _exchange()
    except Exception as exc:  # no network, or Binance refused the call
        log.warning("Binance markets unavailable: %s", exc)
        return None


def digits(tick: float | None, default: int = 4) -> int:
    """Decimals that show one price tick (0.0001 -> 4)."""
    if not tick or tick <= 0:
        return default
    return max(0, min(10, int(round(-math.log10(tick))) if tick < 1 else 0))


def _funding_intervals() -> dict[str, float] | None:
    """Hours between funding settlements for the symbols that differ from Binance's 8 h."""
    ex = exchange()
    if ex is None:
        return None
    try:
        return {r["symbol"]: float(r["fundingIntervalHours"]) for r in ex.fapiPublicGetFundingInfo()}
    except Exception as exc:
        log.warning("funding intervals unavailable: %s", exc)
        return None


def _snapshot() -> dict | None:
    """24h tickers and funding of every USDⓈ-M symbol: two requests for the whole market."""
    ex = exchange()
    if ex is None:
        return None
    try:
        return {"tickers": ex.fetch_tickers(), "rates": ex.fetch_funding_rates()}
    except Exception as exc:
        log.warning("instrument data unavailable: %s", exc)
        return None


def _open_interest(sym: str) -> float | None:
    ex = exchange()
    if ex is None:
        return None
    try:
        return float(ex.fetch_open_interest(sym).get("openInterestAmount") or 0.0)
    except Exception as exc:
        log.warning("open interest of %s unavailable: %s", sym, exc)
        return None


INSTRUMENTS_TTL = 15  # s: prices on the cards; the book, tape and live Z are real time in the browser


def instruments(symbols: tuple[str, ...], with_oi: bool = True) -> dict[str, dict] | None:
    """Facts per exchange symbol ('BAND/USDT:USDT'): price, 24h change and volume, open
    interest, mark/index, funding (rate, next time, interval), tick, lot step, minimums.
    From the market snapshot (at most ~15 s old). Open interest is one request per symbol, fetched
    in the background (0 until it arrives); ``with_oi=False`` skips it (the Overview needs only funding)."""
    ex = exchange()
    if ex is None:
        return None
    snap = swr.get("instruments", INSTRUMENTS_TTL, _snapshot)
    if snap is None:
        return None
    intervals = swr.get("funding_intervals", 3600, _funding_intervals) or {}
    tickers, rates = snap["tickers"], snap["rates"]
    try:
        out = {}
        for sym in symbols:
            m, t, f = ex.market(sym), tickers.get(sym, {}), rates.get(sym, {})
            oi = swr.get(("oi", sym), 60, lambda s=sym: _open_interest(s), wait=False) if with_oi else None
            mark = f.get("markPrice") or t.get("last")
            info = m.get("info", {})
            onboard = info.get("onboardDate")
            out[sym] = {
                "id": m["id"], "base": m["base"],
                "last": t.get("last"), "change_pct": t.get("percentage"), "quote_volume": t.get("quoteVolume"),
                "high": t.get("high"), "low": t.get("low"),
                "mark": mark, "index": f.get("indexPrice"), "funding_rate": f.get("fundingRate"),
                "next_funding": f.get("fundingTimestamp"),
                "funding_hours": intervals.get(m["id"], 8.0),
                "open_interest": (oi or 0.0) * (mark or 0.0),
                "tick": m["precision"].get("price"), "step": m["precision"].get("amount"),
                "min_qty": (m["limits"].get("amount") or {}).get("min") or 0.0,
                "min_notional": (m["limits"].get("cost") or {}).get("min") or 0.0,
                "sectors": [x for x in (info.get("underlyingSubType") or []) if x],
                "listed": pd.Timestamp(int(onboard), unit="ms") if onboard else None,
            }
        return out
    except Exception as exc:
        log.warning("instrument data unavailable: %s", exc)
        return None


def _order_books(symbols: tuple[str, ...], limit: int) -> dict[str, dict] | None:
    ex = exchange()
    if ex is None:
        return None
    try:
        out = {}
        for sym in symbols:
            ob = ex.fetch_order_book(sym, limit=limit)
            out[sym] = {"bids": ob["bids"], "asks": ob["asks"], "ts": ob.get("timestamp")}
        return out
    except Exception as exc:
        log.warning("order books unavailable: %s", exc)
        return None


def order_books(symbols: tuple[str, ...], limit: int = 50) -> dict[str, dict] | None:
    """Order book snapshot per symbol: {'bids': [[px, qty], ...], 'asks': [...], 'ts': ms} (≤ ~3 s old)."""
    if exchange() is None:
        return None
    return swr.get(("books", tuple(symbols), limit), 3, lambda: _order_books(tuple(symbols), limit))


def _pages(ex, symbol: str, tf: str, since: int, count: int) -> list:
    """``count`` candles from ``since`` on, ``PER_REQUEST`` at a time (fewer when they reach now)."""
    rows: list = []
    while len(rows) < count:
        ask = min(PER_REQUEST, count - len(rows))
        page = ex.fetch_ohlcv(symbol, tf, since=int(since), limit=ask)
        rows += page or []
        if not page or len(page) < ask:
            break
        since = int(page[-1][0]) + TF_MS[tf]
    return rows


def _frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"])
    df["qv"] = df["v"] * df["c"]
    return df.astype({"ts": "int64"})


def _candles(symbol: str, tf: str, limit: int) -> pd.DataFrame | None:
    ex = exchange()
    if ex is None:
        return None
    try:
        if limit <= PER_REQUEST:
            rows = ex.fetch_ohlcv(symbol, tf, limit=int(limit))
        else:  # the last ``limit`` bars, the forming one included
            step = TF_MS[tf]
            rows = _pages(ex, symbol, tf, (ex.milliseconds() // step - int(limit) + 1) * step, int(limit))
    except Exception as exc:
        log.warning("candles for %s unavailable: %s", symbol, exc)
        return None
    return _frame(rows)


def _older(symbol: str, tf: str, start: int) -> pd.DataFrame | None:
    """Closed candles from ``start`` up to half of ``CANDLES[tf]`` before now (the recent part overlaps them)."""
    ex = exchange()
    if ex is None:
        return None
    step = TF_MS[tf]
    count = (ex.milliseconds() - start) // step - CANDLES[tf] // 2
    if count <= 0:
        return _frame([])
    try:
        return _frame(_pages(ex, symbol, tf, start, int(count)))
    except Exception as exc:
        log.warning("older candles for %s unavailable: %s", symbol, exc)
        return None


def _older_start(tf: str, start_ms: int) -> int:
    """``start_ms`` to its day, no earlier than ``REACH_BACK`` bars before today: a cache key that changes
    about once a day (the chart is sent only from ``start_ms`` on)."""
    day = 24 * HOUR
    floor = (time.time() * 1000 - REACH_BACK[tf] * TF_MS[tf]) // day * day
    return int(max(int(start_ms) // day * day, floor))


def _candles_key(symbol: str, tf: str, limit: int) -> tuple:
    return ("candles", symbol, tf, int(limit))


def _candles_ttl(tf: str) -> float:
    return 10.0 if tf == "1m" else 30.0  # the forming candle moves live in the browser anyway


def candles(symbol: str, tf: str, limit: int = 500) -> pd.DataFrame | None:
    """Recent candles (open time ms, o, h, l, c, quote volume); the last one is still forming."""
    if exchange() is None:
        return None
    return swr.get(_candles_key(symbol, tf, limit), _candles_ttl(tf), lambda: _candles(symbol, tf, limit))


def prefetch_candles(symbols: tuple[str, ...], tf: str, limit: int = 500) -> None:
    """Start fetching several symbols' candles at once (a pair's two legs load in parallel)."""
    if exchange() is None:
        return
    for sym in symbols:
        swr.warm(_candles_key(sym, tf, limit), lambda s=sym: _candles(s, tf, limit))


def candles_from(symbol: str, tf: str, start_ms: int) -> pd.DataFrame | None:
    """``candles(symbol, tf, CANDLES[tf])`` reaching back to ``start_ms`` (intraday, at most ``REACH_BACK``
    bars). The older part never changes, so it is fetched again only once an hour."""
    recent = candles(symbol, tf, CANDLES[tf])
    if tf not in REACH_BACK or recent is None or recent.empty or int(start_ms) >= int(recent["ts"].iloc[0]):
        return recent
    start = _older_start(tf, start_ms)
    old = swr.get(("older", symbol, tf, start), 3600, lambda: _older(symbol, tf, start))
    if old is None or old.empty:
        return recent
    out = pd.concat([old, recent], ignore_index=True).drop_duplicates("ts", keep="last")
    return out[out["ts"] >= int(start_ms) // TF_MS[tf] * TF_MS[tf]].sort_values("ts", ignore_index=True)


def prefetch_candles_from(symbols: tuple[str, ...], tf: str, start_ms: int) -> None:
    """Both legs' older candles at once (``candles_from``)."""
    if exchange() is None or tf not in REACH_BACK:
        return
    start = _older_start(tf, start_ms)
    for sym in symbols:
        swr.warm(("older", sym, tf, start), lambda s=sym: _older(s, tf, start))


# ---------------------------------------------------------------- older history, on demand
# The Pair desk's Spread Z loads its older history while the user scrolls left (js/chart.js asks for the
# bars before the earliest one it has). Candles come in fixed blocks of PER_REQUEST bars aligned in time,
# one request each, kept in memory: closed candles never change. The dashboard shares its IP with the
# bots, so these requests have a budget (OLDER_PER_MIN, about 5% of Binance's weight limit).
OLDER_PER_MIN = 20
OLDER_KEEP = 300  # blocks in memory (~60 kB each)
_older_lock = threading.Lock()
_older_blocks: OrderedDict = OrderedDict()  # (symbol, tf, start) -> DataFrame
_older_budget = {"tokens": float(OLDER_PER_MIN), "at": time.monotonic()}
_older_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="older")


def _older_token() -> bool:
    """One request from the budget (refilled continuously, OLDER_PER_MIN a minute)."""
    with _older_lock:
        now = time.monotonic()
        b = _older_budget
        b["tokens"] = min(float(OLDER_PER_MIN), b["tokens"] + (now - b["at"]) * OLDER_PER_MIN / 60)
        b["at"] = now
        if b["tokens"] < 1:
            return False
        b["tokens"] -= 1
        return True


def older_start(tf: str, end_ms: int) -> int:
    """Where the older candles before ``end_ms`` start: the block holding the bar before it, and the block
    before that too when this one would give fewer than a quarter of a block."""
    span = PER_REQUEST * TF_MS[tf]
    start = (int(end_ms) - 1) // span * span
    if (int(end_ms) - start) // TF_MS[tf] < PER_REQUEST // 4:
        start -= span
    return start


def _older_block(symbol: str, tf: str, start: int) -> pd.DataFrame | None:
    key = (symbol, tf, int(start))
    with _older_lock:
        if key in _older_blocks:
            _older_blocks.move_to_end(key)
            return _older_blocks[key]
    ex = exchange()
    if ex is None or not _older_token():
        return None
    step = TF_MS[tf]
    end = int(start) + PER_REQUEST * step
    try:
        rows = ex.fetch_ohlcv(symbol, tf, since=int(start), limit=PER_REQUEST)
    except Exception as exc:
        log.warning("older candles for %s unavailable: %s", symbol, exc)
        return None
    df = _frame(rows or [])
    df = df[(df["ts"] >= int(start)) & (df["ts"] < end)].reset_index(drop=True)
    if end <= ex.milliseconds() - step:  # every bar of the block has closed: keep it
        with _older_lock:
            _older_blocks[key] = df
            while len(_older_blocks) > OLDER_KEEP:
                _older_blocks.popitem(last=False)
    return df


def older_candles(symbol: str, tf: str, end_ms: int) -> pd.DataFrame | None:
    """Closed candles from ``older_start(tf, end_ms)`` up to ``end_ms`` (exclusive), oldest first; an empty
    frame before the symbol was listed, None when Binance did not answer or the budget is spent."""
    start = older_start(tf, end_ms)
    span = PER_REQUEST * TF_MS[tf]
    parts = []
    for b in range(start, int(end_ms), span):
        df = _older_block(symbol, tf, b)
        if df is None:
            return None
        parts.append(df)
    out = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    return out[out["ts"] < int(end_ms)].reset_index(drop=True)


def older_pair(symbols: tuple[str, str], tf: str, end_ms: int) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """``older_candles`` of both legs, fetched at once; None unless both answered."""
    if exchange() is None:
        return None
    a, b = (_older_pool.submit(older_candles, sym, tf, end_ms) for sym in symbols)
    ca, cb = a.result(), b.result()
    return None if ca is None or cb is None else (ca, cb)


def prefetch_books(symbols: tuple[str, ...], limit: int = 50) -> None:
    if exchange() is None:
        return
    swr.warm(("books", tuple(symbols), limit), lambda: _order_books(tuple(symbols), limit))


def guess_digits(px: float | None) -> int:
    """Price decimals from a price when Binance's tick size is not available."""
    px = float(px) if px is not None and px == px else 1.0
    return int(min(8, max(2, 4 - math.floor(math.log10(px))))) if px > 0 else 4


def leg_meta(sym: str, mark: float | None = None) -> tuple[str, int]:
    """Binance id ('BANDUSDT') and price decimals of an exchange symbol, from the loaded markets (no request)."""
    ex = exchange()
    m = None
    if ex is not None:
        try:
            m = ex.market(sym)
        except Exception:
            m = None
    base, rest = sym.split("/", 1)
    tick = ((m or {}).get("precision") or {}).get("price")
    return (m or {}).get("id") or base + rest.split(":")[0], digits(tick, guess_digits(mark))


@st.cache_data(ttl=3600, show_spinner=False)
def chains(data_dir: str) -> dict[str, dict]:
    """Archive symbol ('BANDUSDT') -> CoinGecko id and blockchains, from data/meta/chains.csv."""
    p = Path(data_dir) / "meta" / "chains.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p).fillna("")
    return {r.symbol: {"coingecko_id": r.coingecko_id, "chains": [c for c in str(r.chains).split(";") if c]}
            for r in df.itertuples()}


@st.cache_data(ttl=600, show_spinner=False)
def coingecko(ids: tuple[str, ...]) -> dict[str, dict]:
    """Name, market cap and rank per CoinGecko id ({} when CoinGecko does not answer)."""
    ids = tuple(i for i in ids if i)
    if offline() or not ids:
        return {}
    try:
        import requests

        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                         params={"vs_currency": "usd", "ids": ",".join(ids)}, timeout=8)
        r.raise_for_status()
        return {c["id"]: {"name": c.get("name"), "market_cap": c.get("market_cap"),
                          "rank": c.get("market_cap_rank"), "fdv": c.get("fully_diluted_valuation")}
                for c in r.json()}
    except Exception as exc:
        log.warning("CoinGecko unavailable: %s", exc)
        return {}

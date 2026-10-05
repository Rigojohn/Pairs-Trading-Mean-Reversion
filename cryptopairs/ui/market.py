"""Exchange data for the dashboard, fetched by the dashboard's server from Binance REST.

Instrument facts (24h statistics, open interest, funding, tick and lot sizes, sector tags),
order books for the order-ticket preview and candles for the charts. Everything is cached
with a short TTL, and every function returns None instead of raising when Binance cannot be
reached, so a page never breaks on a network hiccup. ``CRYPTOPAIRS_OFFLINE=1`` (tests)
turns the network off. CoinGecko adds names and market caps when it answers.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

import pandas as pd
import streamlit as st

log = logging.getLogger(__name__)
HOUR = 3_600_000
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": HOUR, "4h": 4 * HOUR}


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


@st.cache_data(ttl=3600, show_spinner=False)
def _funding_intervals() -> dict[str, float]:
    """Hours between funding settlements for the symbols that differ from Binance's 8 h."""
    ex = exchange()
    if ex is None:
        return {}
    try:
        return {r["symbol"]: float(r["fundingIntervalHours"]) for r in ex.fapiPublicGetFundingInfo()}
    except Exception as exc:
        log.warning("funding intervals unavailable: %s", exc)
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def instruments(symbols: tuple[str, ...], with_oi: bool = True) -> dict[str, dict] | None:
    """Facts per exchange symbol ('BAND/USDT:USDT'): price, 24h change and volume, open
    interest, mark/index, funding (rate, next time, interval), tick, lot step, minimums.
    Open interest is one request per symbol: ``with_oi=False`` skips it (the Overview needs only funding)."""
    ex = exchange()
    if ex is None:
        return None
    try:
        tickers = ex.fetch_tickers(list(symbols))
        rates = ex.fetch_funding_rates(list(symbols))
        intervals = _funding_intervals()
        out = {}
        for sym in symbols:
            m, t, f = ex.market(sym), tickers.get(sym, {}), rates.get(sym, {})
            oi = ex.fetch_open_interest(sym) if with_oi else {}
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
                "open_interest": (oi.get("openInterestAmount") or 0.0) * (mark or 0.0),
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


@st.cache_data(ttl=3, show_spinner=False)
def order_books(symbols: tuple[str, ...], limit: int = 50) -> dict[str, dict] | None:
    """Order book snapshot per symbol: {'bids': [[px, qty], ...], 'asks': [...], 'ts': ms}."""
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


@st.cache_data(ttl=30, show_spinner=False)
def candles(symbol: str, tf: str, limit: int = 500) -> pd.DataFrame | None:
    """Recent candles (open time ms, o, h, l, c, quote volume); the last one is still forming."""
    ex = exchange()
    if ex is None:
        return None
    try:
        rows = ex.fetch_ohlcv(symbol, tf, limit=min(int(limit), 1500))
    except Exception as exc:
        log.warning("candles for %s unavailable: %s", symbol, exc)
        return None
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"])
    df["qv"] = df["v"] * df["c"]
    return df.astype({"ts": "int64"})


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

"""Coin -> blockchain mapping from CoinGecko, for the paper's same-chain filter (Filter 2).

The result is cached in data/meta/chains.csv (symbol, coingecko_id, chains),
which you can edit by hand. A coin's chains are the platforms it is deployed
on, plus the chain it is native to (for example ETH -> ethereum).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

API = "https://api.coingecko.com/api/v3"
_MULTIPLIER = re.compile(r"^(1000000|100000|10000|1000|1M)(?=[A-Z])")


def base_asset(symbol: str, quote: str = "USDT") -> str:
    """BANDUSDT -> BAND, 1000PEPEUSDT -> PEPE."""
    base = symbol[: -len(quote)] if symbol.endswith(quote) else symbol
    return _MULTIPLIER.sub("", base)


def _cg_get(path: str, retries: int = 5) -> object:
    headers = {"User-Agent": "cryptopairs/0.2", "Accept": "application/json"}
    key = os.environ.get("COINGECKO_API_KEY")
    if key:
        headers["x-cg-demo-api-key"] = key
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{API}{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code != 429 and exc.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(15 * (attempt + 1))  # the keyless tier allows only a few calls per minute
    raise RuntimeError(f"CoinGecko {path} failed: {last}")


def fetch_chain_map(symbols: list[str], quote: str = "USDT", market_pages: int = 8) -> pd.DataFrame:
    """Resolve Binance symbols to CoinGecko coins and their chains."""
    coins = _cg_get("/coins/list?include_platform=true")
    time.sleep(3)
    platforms = _cg_get("/asset_platforms")
    native = {p["native_coin_id"]: p["id"] for p in platforms if p.get("native_coin_id")}
    rank: dict[str, int] = {}
    for page in range(1, market_pages + 1):
        time.sleep(3)
        rows = _cg_get(f"/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={page}")
        for r in rows:
            rank.setdefault(r["id"], len(rank))
        if len(rows) < 250:
            break

    by_symbol: dict[str, list[dict]] = {}
    for c in coins:
        by_symbol.setdefault(c["symbol"].upper(), []).append(c)

    out = []
    for sym in symbols:
        base = base_asset(sym, quote)
        candidates = by_symbol.get(base.upper(), [])
        # Tickers are not unique; take the largest coin by market cap.
        candidates = sorted(candidates, key=lambda c: rank.get(c["id"], 10**9))
        if not candidates or candidates[0]["id"] not in rank:
            out.append({"symbol": sym, "coingecko_id": "", "chains": ""})
            continue
        coin = candidates[0]
        chains = {p for p, addr in (coin.get("platforms") or {}).items() if p}
        if coin["id"] in native:
            chains.add(native[coin["id"]])
        out.append({"symbol": sym, "coingecko_id": coin["id"], "chains": ";".join(sorted(chains))})
    return pd.DataFrame(out)


def load_chain_map(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype=str).fillna("")
    return {r.symbol: {c for c in r.chains.split(";") if c} for r in df.itertuples()}

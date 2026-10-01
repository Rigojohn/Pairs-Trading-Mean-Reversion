"""Historical data: Binance USDM futures klines and funding rates.

Source: the public archive at data.binance.vision. The symbol universe comes
from the archive's bucket listing, which still includes delisted contracts, so
research is free of survivorship bias.

Storage: one pickle per symbol, data/klines/<interval>/<SYMBOL>.pkl with
columns ts (open time, epoch ms), open, high, low, close, quote_volume; and
data/funding/<SYMBOL>.pkl with columns ts (funding time floored to the hour),
rate.
"""

from __future__ import annotations

import io
import logging
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from cryptopairs.config import HOUR_MS

log = logging.getLogger(__name__)

ARCHIVE = "https://data.binance.vision"
BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
UM = "data/futures/um"
USER_AGENT = "cryptopairs/0.2 (+research downloader)"
KLINE_COLS = ["ts", "open", "high", "low", "close", "quote_volume"]


# --------------------------------------------------------------------------- http
def _get(url: str, timeout: float = 30.0, retries: int = 3) -> bytes | None:
    """GET with retries. Returns None on 404 (file not in the archive)."""
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(min(2.0 * attempt, 6.0))
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last}")


def _read_zip_csv(blob: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = zf.namelist()[0]
        raw = zf.read(name)
    df = pd.read_csv(io.BytesIO(raw), header=None)
    first = str(df.iat[0, 0])
    if not first.lstrip("-").isdigit():  # newer files carry a header row
        df = df.iloc[1:].reset_index(drop=True)
    return df


# ------------------------------------------------------------------------ universe
def list_archive_symbols(quote: str = "USDT") -> list[str]:
    """All USDM symbols that ever had monthly klines (delisted ones included)."""
    prefix = f"{UM}/monthly/klines/"
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    symbols: list[str] = []
    marker = ""
    while True:
        url = f"{BUCKET}?delimiter=/&prefix={prefix}&marker={marker}"
        blob = _get(url)
        if blob is None:
            raise RuntimeError("archive listing not found")
        root = ET.fromstring(blob)
        for cp in root.findall("s3:CommonPrefixes/s3:Prefix", ns):
            sym = cp.text[len(prefix) :].strip("/")
            if sym:
                symbols.append(sym)
        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=ns) == "true"
        if not truncated:
            break
        marker = root.findtext("s3:NextMarker", default="", namespaces=ns)
        if not marker:
            break
    pat = re.compile(rf"^[A-Z0-9]+{quote}$")
    return sorted(s for s in symbols if pat.match(s))


# -------------------------------------------------------------------------- months
def month_range(start_ms: int, end_ms: int) -> list[str]:
    start = pd.Timestamp(start_ms, unit="ms")
    end = pd.Timestamp(end_ms, unit="ms")
    months = pd.period_range(start.to_period("M"), end.to_period("M"), freq="M")
    return [str(p) for p in months]


def _month_days(month: str) -> list[str]:
    p = pd.Period(month, freq="M")
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    days = pd.date_range(p.start_time, min(p.end_time, today - pd.Timedelta(days=1)), freq="D")
    return [d.strftime("%Y-%m-%d") for d in days]


# --------------------------------------------------------------------------- klines
def _parse_klines(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "ts": pd.to_numeric(df[0], errors="coerce"),
            "open": pd.to_numeric(df[1], errors="coerce"),
            "high": pd.to_numeric(df[2], errors="coerce"),
            "low": pd.to_numeric(df[3], errors="coerce"),
            "close": pd.to_numeric(df[4], errors="coerce"),
            "quote_volume": pd.to_numeric(df[7], errors="coerce"),
        }
    ).dropna()
    ts = out["ts"].astype("int64")
    out["ts"] = np.where(ts > 10**14, ts // 1000, ts)  # some archives use microseconds
    return out


def _fetch_kline_month(symbol: str, interval: str, month: str) -> pd.DataFrame | None:
    url = f"{ARCHIVE}/{UM}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
    blob = _get(url)
    if blob is not None:
        return _parse_klines(_read_zip_csv(blob))
    # Recent months are only published as daily files until the month is complete.
    # Older 404s mean the contract was not listed, so skip the 30 daily requests.
    this_month = pd.Timestamp.now(tz="UTC").tz_localize(None).to_period("M")
    if pd.Period(month, freq="M") < this_month - 1:
        return None
    frames = []
    for day in _month_days(month):
        url = f"{ARCHIVE}/{UM}/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{day}.zip"
        blob = _get(url)
        if blob is not None:
            frames.append(_parse_klines(_read_zip_csv(blob)))
    return pd.concat(frames, ignore_index=True) if frames else None


def _fetch_funding_month(symbol: str, month: str) -> pd.DataFrame | None:
    url = f"{ARCHIVE}/{UM}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"
    blob = _get(url)
    if blob is None:
        return None
    df = _read_zip_csv(blob)
    out = pd.DataFrame(
        {"ts": pd.to_numeric(df[0], errors="coerce"), "rate": pd.to_numeric(df[2], errors="coerce")}
    ).dropna()
    out["ts"] = (out["ts"].astype("int64") // HOUR_MS) * HOUR_MS
    return out


@dataclass
class DataStore:
    root: Path
    interval: str = "1h"

    def kline_path(self, symbol: str) -> Path:
        return self.root / "klines" / self.interval / f"{symbol}.pkl"

    def funding_path(self, symbol: str) -> Path:
        return self.root / "funding" / f"{symbol}.pkl"

    def symbols(self) -> list[str]:
        d = self.root / "klines" / self.interval
        return sorted(p.stem for p in d.glob("*.pkl")) if d.exists() else []

    def load_klines(self, symbol: str) -> pd.DataFrame:
        path = self.kline_path(symbol)
        if not path.exists():
            return pd.DataFrame(columns=KLINE_COLS)
        return pd.read_pickle(path)

    def load_funding(self, symbol: str) -> pd.DataFrame:
        path = self.funding_path(symbol)
        if not path.exists():
            return pd.DataFrame(columns=["ts", "rate"])
        return pd.read_pickle(path)

    @staticmethod
    def _merge_save(path: Path, old: pd.DataFrame, new: list[pd.DataFrame]) -> pd.DataFrame:
        frames = [f for f in [old, *new] if f is not None and len(f)]
        if not frames:
            return old
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        df.to_pickle(tmp)
        tmp.replace(path)
        return df

    def _missing_months(self, df: pd.DataFrame, months: list[str], step_ms: int) -> list[str]:
        if df.empty:
            return months
        ts = df["ts"].to_numpy()
        present = set(pd.to_datetime(ts, unit="ms").to_period("M").astype(str))
        last_ts = int(ts.max())
        missing = [m for m in months if m not in present]
        last_month = str(pd.Timestamp(last_ts, unit="ms").to_period("M"))
        month_end = int(pd.Period(last_month, freq="M").end_time.floor("h").value // 1_000_000)
        if last_month in months and last_ts < month_end - step_ms + 1:
            missing.append(last_month)  # last month only partially downloaded
        return sorted(set(missing))

    def update_symbol(
        self, symbol: str, months: list[str], probe_month: str | None, funding: bool
    ) -> str:
        """Download missing months for one symbol. Returns a short status string."""
        old = self.load_klines(symbol)
        if old.empty and probe_month is not None:
            probe = _fetch_kline_month(symbol, self.interval, probe_month)
            if probe is None or probe.empty:
                return "skip (listed after probe month)"
            old = self._merge_save(self.kline_path(symbol), old, [probe])
        todo = self._missing_months(old, months, HOUR_MS)
        new = [_fetch_kline_month(symbol, self.interval, m) for m in todo]
        df = self._merge_save(self.kline_path(symbol), old, new)
        status = f"{len(df)} bars"
        if funding:
            fold = self.load_funding(symbol)
            fmonths = months if fold.empty else self._missing_months(fold, months, 8 * HOUR_MS)
            fnew = [_fetch_funding_month(symbol, m) for m in fmonths]
            fdf = self._merge_save(self.funding_path(symbol), fold, fnew)
            status += f", {len(fdf)} funding events"
        return status


def download(
    store: DataStore,
    symbols: list[str],
    start_ms: int,
    end_ms: int,
    *,
    probe_ms: int | None,
    funding: bool = True,
    threads: int = 8,
) -> dict[str, str]:
    """Download klines (and funding) for ``symbols`` over [start_ms, end_ms].

    ``probe_ms``: a symbol is skipped unless it has data in that month, which
    cheaply drops contracts listed too late to pass the history filter.
    """
    months = month_range(start_ms, end_ms)
    probe = month_range(probe_ms, probe_ms)[0] if probe_ms is not None else None
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        futs = {pool.submit(store.update_symbol, s, months, probe, funding): s for s in symbols}
        for i, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                results[sym] = fut.result()
            except Exception as exc:  # keep going; report at the end
                results[sym] = f"error: {exc}"
                log.warning("download %s failed: %s", sym, exc)
            if i % 25 == 0 or i == len(futs):
                log.info("downloaded %d/%d symbols", i, len(futs))
    return results


# ------------------------------------------------------------------------ pair data
@dataclass
class PairData:
    """Two legs aligned on common bars, plus funding charged during each bar."""

    a: str
    b: str
    ts: np.ndarray
    open_a: np.ndarray
    high_a: np.ndarray
    low_a: np.ndarray
    close_a: np.ndarray
    open_b: np.ndarray
    high_b: np.ndarray
    low_b: np.ndarray
    close_b: np.ndarray
    funding_a: np.ndarray
    funding_b: np.ndarray

    def __post_init__(self) -> None:
        self.log_a = np.log(self.close_a)
        self.log_b = np.log(self.close_b)

    def __len__(self) -> int:
        return int(self.ts.shape[0])

    def window(self, start_ms: int, end_ms: int) -> tuple[int, int]:
        """Inclusive index range of bars with start_ms <= ts <= end_ms (i1 < i0 if empty)."""
        i0 = int(np.searchsorted(self.ts, start_ms, side="left"))
        i1 = int(np.searchsorted(self.ts, end_ms, side="right")) - 1
        return i0, i1


def _funding_per_bar(ts: np.ndarray, funding: pd.DataFrame) -> np.ndarray:
    """Rate charged during each bar: an event at time F hits the bar closing at F."""
    out = np.zeros(ts.shape[0])
    if funding is None or funding.empty:
        return out
    bar_open = funding["ts"].to_numpy(dtype=np.int64) - HOUR_MS
    idx = np.searchsorted(ts, bar_open)
    ok = (idx < ts.shape[0]) & (ts[np.minimum(idx, ts.shape[0] - 1)] == bar_open)
    np.add.at(out, idx[ok], funding["rate"].to_numpy(dtype=np.float64)[ok])
    return out


def make_pair_data(
    a: str,
    b: str,
    kl_a: pd.DataFrame,
    kl_b: pd.DataFrame,
    fund_a: pd.DataFrame | None = None,
    fund_b: pd.DataFrame | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> PairData:
    m = kl_a.merge(kl_b, on="ts", suffixes=("_a", "_b"), how="inner").sort_values("ts")
    if start_ms is not None:
        m = m[m["ts"] >= start_ms]
    if end_ms is not None:
        m = m[m["ts"] <= end_ms]
    m = m[(m["close_a"] > 0) & (m["close_b"] > 0)]
    ts = m["ts"].to_numpy(dtype=np.int64)
    col = lambda c: m[c].to_numpy(dtype=np.float64)  # noqa: E731
    return PairData(
        a=a, b=b, ts=ts,
        open_a=col("open_a"), high_a=col("high_a"), low_a=col("low_a"), close_a=col("close_a"),
        open_b=col("open_b"), high_b=col("high_b"), low_b=col("low_b"), close_b=col("close_b"),
        funding_a=_funding_per_bar(ts, fund_a), funding_b=_funding_per_bar(ts, fund_b),
    )


def load_pair(store: DataStore, a: str, b: str, start_ms: int | None = None, end_ms: int | None = None) -> PairData:
    return make_pair_data(
        a, b,
        store.load_klines(a), store.load_klines(b),
        store.load_funding(a), store.load_funding(b),
        start_ms, end_ms,
    )

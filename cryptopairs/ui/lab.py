"""What-if analysis: the sidebar parameters run through the real strategy on real data.

The same backtest the research uses (``research.backtest.run_backtest``, with the
bot's profile costs) is run on the local archive (``data/``), extended up to the
last closed hour with bars fetched from Binance, so the lab always reaches "now".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.config import AppConfig
from cryptopairs.core.signals import estimate_fits
from cryptopairs.core.strategy import PairParams
from cryptopairs.research.backtest import BacktestResult, run_backtest
from cryptopairs.research.data import DataStore, PairData, make_pair_data
from cryptopairs.ui import data as D

HOUR = 3_600_000
DAY = 24 * HOUR
DELTAS = [1e-8, 3e-8, 1e-7, 3e-7, 1e-6, 3e-6, 1e-5, 3e-5, 1e-4]
LOOKBACKS = [168, 360, 720, 1440, 2160, 4320]
ZE_GRID = [0.8, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5]
ZX_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]


@dataclass(frozen=True)
class WhatIf:
    """The sidebar's parameter set for one pair."""

    z_entry: float
    z_exit: float
    lookback: int
    kalman_delta: float | None
    capital: float
    leverage: float

    def params(self) -> PairParams:
        return PairParams(int(self.lookback), float(self.z_entry), float(self.z_exit), self.kalman_delta)

    def same_model(self, other: "WhatIf") -> bool:
        return (self.lookback, self.kalman_delta) == (other.lookback, other.kalman_delta)


def calibrated(pair: dict, leverage: float, override: dict | None = None) -> WhatIf:
    """Parameters the pair was calibrated with (the base of an override, if there is one)."""
    p = (override or {}).get("base") or pair
    return WhatIf(float(p["z_entry"]), float(p["z_exit"]), int(p["lookback"]), p.get("kalman_delta"),
                  float(pair["capital"]), float(leverage))


def in_use(pair: dict, leverage: float) -> WhatIf:
    return WhatIf(pair["z_entry"], pair["z_exit"], pair["lookback"], pair["kalman_delta"], pair["capital"], leverage)


def memory_label(delta: float | None, lookback: int) -> str:
    """How far back the hedge ratio looks, in words."""
    if delta is None:
        return f"rolling OLS over {lookback} h ({lookback / 24:.0f} d)"
    return f"Kalman memory ~{1 / math.sqrt(delta) / 24:,.0f} d"


# ------------------------------------------------------------------ signals
def simulate_signals(t: pd.Series, z: np.ndarray, z_entry: float, z_exit: float) -> pd.DataFrame:
    """Entry/exit points the thresholds would give on a Z path (no costs, stops or filters).

    Long spread when Z < -z_entry, short when Z > z_entry; a long exits when Z crosses
    back up through -z_exit, a short when it crosses down through +z_exit (the strategy's rule).
    """
    out, side, prev = [], 0, math.nan
    for ti, zi in zip(t, z):
        if not np.isfinite(zi):
            continue
        if side == 0:
            if zi < -z_entry:
                side = 1
                out.append((ti, zi, "long"))
            elif zi > z_entry:
                side = -1
                out.append((ti, zi, "short"))
        elif (side > 0 and prev < -z_exit <= zi) or (side < 0 and prev > z_exit >= zi):
            side = 0
            out.append((ti, zi, "exit"))
        prev = zi
    return pd.DataFrame(out, columns=["time", "z", "kind"])


# --------------------------------------------------------------------- data
def local_symbol(exchange_symbol: str) -> str:
    """'BAND/USDT:USDT' -> 'BANDUSDT' (the archive's file name)."""
    base, rest = exchange_symbol.split("/", 1)
    return base + rest.split(":")[0]


@st.cache_data(ttl=300, show_spinner=False)
def recent_bars(symbol: str, since_ms: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Closed 1h bars and funding events from Binance after the local archive ends."""
    import ccxt

    ex = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 15_000})
    last_closed = (int(pd.Timestamp.now(tz="UTC").value // 1_000_000) // HOUR - 1) * HOUR
    rows, t = [], since_ms
    while t <= last_closed:
        batch = ex.fetch_ohlcv(symbol, "1h", since=t, limit=1500)
        if not batch:
            break
        rows += batch
        t = int(batch[-1][0]) + HOUR
        if len(batch) < 1500:
            break
    kl = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    kl = kl[kl["ts"] <= last_closed].astype({"ts": "int64"})
    kl["quote_volume"] = kl.pop("volume") * kl["close"]
    fr = ex.fetch_funding_rate_history(symbol, since=since_ms - 8 * HOUR, limit=1000) or []
    fund = pd.DataFrame({"ts": [int(f["timestamp"]) for f in fr], "rate": [float(f["fundingRate"]) for f in fr]})
    return kl, fund.astype({"ts": "int64"})


def _merge(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if new is None or new.empty:
        return old
    return pd.concat([old, new[old.columns]], ignore_index=True).drop_duplicates("ts", keep="last").sort_values("ts")


@st.cache_data(ttl=300, show_spinner=False, max_entries=6)
def pair_history(data_dir: str, sym_a: str, sym_b: str, extend: bool) -> tuple[PairData, dict]:
    """Both legs from the local archive, plus the bars since it ends (when ``extend``)."""
    store = DataStore(Path(data_dir))
    a, b = local_symbol(sym_a), local_symbol(sym_b)
    kla, klb, fa, fb = store.load_klines(a), store.load_klines(b), store.load_funding(a), store.load_funding(b)
    info = {"archive_end": None, "live_bars": 0, "error": None}
    if kla.empty or klb.empty:
        info["error"] = f"no local data for {a if kla.empty else b} (run: python -m cryptopairs download)"
    else:
        info["archive_end"] = int(min(kla["ts"].max(), klb["ts"].max()))
    if extend and info["archive_end"] is not None:
        since = info["archive_end"] + HOUR
        try:
            ka, fa_new = recent_bars(sym_a, since)
            kb, fb_new = recent_bars(sym_b, since)
            kla, klb = _merge(kla, ka), _merge(klb, kb)
            fa, fb = _merge(fa, fa_new), _merge(fb, fb_new)
            info["live_bars"] = int(min(len(ka), len(kb)))
        except Exception as exc:  # offline: the archive alone still works
            info["error"] = f"could not fetch recent bars from Binance ({exc.__class__.__name__}); archive only"
    return make_pair_data(a, b, kla, klb, fa, fb), info


@st.cache_data(show_spinner=False, max_entries=24)
def fits_for(_data: PairData, key: tuple, lookback: int, delta: float | None, outlier_z: float | None,
             max_skips: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-bar (alpha, beta, sigma) over the whole history. ``key`` identifies the data."""
    return estimate_fits(_data.log_a, _data.log_b, lookback, delta, outlier_z, max_skips)


def data_key(data: PairData) -> tuple:
    return data.a, data.b, len(data), int(data.ts[-1]) if len(data) else 0


# ----------------------------------------------------------------- backtest
def periods(cfg: AppConfig, data: PairData, first_session_ms: int | None) -> dict[str, tuple[int, int]]:
    end = int(data.ts[-1])
    oos = cfg.research.test_start_ms
    out = {
        "Out-of-sample (since calibration)": (oos, end),
        "Last 30 days": (end - 30 * DAY, end),
        "Last 90 days": (end - 90 * DAY, end),
        "Last 365 days": (end - 365 * DAY, end),
    }
    if first_session_ms:
        out["Paper-trading period"] = (first_session_ms // HOUR * HOUR, end)
    out["Calibration window (in-sample)"] = (cfg.research.calibration_start_ms, cfg.research.train_end_ms)
    out["Full history"] = (int(data.ts[0]), end)
    return out


def run(data: PairData, w: WhatIf, cfg: AppConfig, start: int, end: int, fits) -> BacktestResult:
    sc = replace(cfg.strategy, leverage=float(w.leverage))
    return run_backtest(data, w.params(), sc, cfg.costs, start, end, float(w.capital), fits=fits)


def z_path(data: PairData, fits, res: BacktestResult) -> tuple[pd.Series, np.ndarray]:
    """Z the strategy sees: the refitted model while flat, the frozen entry fit in a trade."""
    if res.ts.size == 0:
        return pd.Series(dtype="datetime64[ns]"), np.empty(0)
    i0 = int(np.searchsorted(data.ts, res.ts[0]))
    sl = slice(i0, i0 + res.ts.size)
    al, be, si = (f[sl] for f in fits)
    la, lb = data.log_a[sl], data.log_b[sl]
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (la - al - be * lb) / si
    for t in res.trades:
        m = (res.ts >= t["entry_ts"]) & (res.ts <= t.get("exit_ts", res.ts[-1]))
        if t.get("sigma"):
            z[m] = (la[m] - t["alpha"] - t["beta"] * lb[m]) / t["sigma"]
    return D.to_time(res.ts), z


def surface(data: PairData, fits, cfg: AppConfig, w: WhatIf, start: int, end: int) -> pd.DataFrame:
    """Sharpe over a (z_entry, z_exit) grid with the what-if model, leverage and capital."""
    sc = replace(cfg.strategy, leverage=float(w.leverage))
    rows = []
    for ze in ZE_GRID:
        for zx in ZX_GRID:
            if not zx < ze:
                continue
            p = PairParams(int(w.lookback), ze, zx, w.kalman_delta)
            m = run_backtest(data, p, sc, cfg.costs, start, end, float(w.capital), fits=fits,
                             record_trades=True).metrics()
            rows.append({"z_entry": ze, "z_exit": zx, "Sharpe": m["Sharpe"], "TotalRet": m["TotalRet"],
                         "Trades": m["Trades"]})
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False, max_entries=12)
def surface_cached(_data: PairData, _fits, _cfg: AppConfig, key: tuple, what_if: tuple, start: int, end: int) -> pd.DataFrame:
    """``surface`` cached by (data, profile) ``key``, the what-if values and the period."""
    return surface(_data, _fits, _cfg, WhatIf(*what_if), start, end)


def trades_frame(res: BacktestResult) -> pd.DataFrame:
    t = res.trades_frame()
    if t.empty:
        return t
    for col in ("exit_ts", "pnl", "fees", "funding", "reason"):
        if col not in t:
            t[col] = np.nan
    return pd.DataFrame({
        "side": t["side"].map({1: "LONG", -1: "SHORT"}),
        "entry": D.to_time(t["entry_ts"]),
        "exit": D.to_time(t["exit_ts"]),
        "hours": (t["exit_ts"] - t["entry_ts"]) / HOUR,
        "Z entry": t["z_entry"], "beta": t["beta"], "reason": t["reason"].fillna("open"),
        "PnL": t["pnl"], "PnL %": 100 * t["pnl"] / t["equity_before"], "fees": t["fees"], "funding": t["funding"],
    })

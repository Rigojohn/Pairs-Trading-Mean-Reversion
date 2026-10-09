"""The spread's whole history for the pair desk: the research archive before the bot's own records.

The bot records its model (``fit_history``) only from 30 days before its first start, and Binance
serves about 1000 candles per request, so the Spread Z chart used to stop a few weeks back. Here
the same model is run over the archive (data/klines/1h, from June 2023) plus the Binance bars after
it, and its rows are put in front of the bot's records: the bot's rows win wherever they exist,
the archive only fills the time before them. Each bot's own Kalman history is replayed
(``strategy.kalman_history``: None = the whole series, as bot B and the backtest; 8760 = bot A's
one-year window), so the join has no jump.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.core.signals import estimate_fits
from cryptopairs.ui import lab as L
from cryptopairs.ui import market as M
from cryptopairs.ui import tvcharts as TC

log = logging.getLogger(__name__)
# The warm-up calls cached functions from its own threads; Streamlit logs "missing ScriptRunContext" for
# every such call, which is expected here and would flood the log.
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)

FIT_COLS = ["bar_ts", "pair_id", "kf_alpha", "kf_beta", "kf_sigma", "used_alpha", "used_beta", "used_sigma",
            "spread_u", "z", "log_a", "log_b"]


@st.cache_data(ttl=6 * 3600, show_spinner="Loading the spread's history…", max_entries=64)
def model_history(data_dir: str, sym_a: str, sym_b: str, pair_id: str, lookback: int, delta: float | None,
                  history: int | None, outlier_z: float | None, max_skips: int) -> pd.DataFrame:
    """Model rows (same columns as ``fit_history``) for every bar of the archive + recent Binance bars.

    ``kf_*`` at bar t is the model before bar t, as the bot records it at that bar's close.
    No ``used_*``: no position was held by this bot then, so the fit in force is the model's.
    """
    data, _info = L.pair_history(data_dir, sym_a, sym_b, True)
    if len(data) == 0:
        return pd.DataFrame(columns=FIT_COLS)
    al, be, si = estimate_fits(data.log_a, data.log_b, int(lookback), delta, outlier_z, int(max_skips),
                               history=history if delta is not None else None)
    with np.errstate(invalid="ignore", divide="ignore"):
        u = data.log_a - al - be * data.log_b
        z = np.where(si > 0, u / si, np.nan)
    df = pd.DataFrame({"bar_ts": data.ts.astype(np.int64), "pair_id": pair_id, "kf_alpha": al, "kf_beta": be,
                       "kf_sigma": si, "used_alpha": np.nan, "used_beta": np.nan, "used_sigma": np.nan,
                       "spread_u": u, "z": z, "log_a": data.log_a, "log_b": data.log_b})
    return df[np.isfinite(df["z"])].reset_index(drop=True)


def extend(fit: pd.DataFrame | None, px: pd.DataFrame | None, hist: pd.DataFrame, tf: str
           ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The bot's model rows and closes, with the archive history in front of them.

    The archive only covers the time before the bot's first recorded bar (and, for prices,
    before the first Binance candle), so whatever the bot recorded is shown unchanged.
    """
    fit = fit if fit is not None else pd.DataFrame(columns=FIT_COLS)
    px = px if px is not None else pd.DataFrame(columns=["ts", "a", "b"])
    if hist is None or hist.empty:
        return fit, px
    first_fit = int(fit["bar_ts"].min()) if not fit.empty else None
    older = hist if first_fit is None else hist[hist["bar_ts"] < first_fit]
    if older.empty:
        return fit, px
    fit = pd.concat([older[FIT_COLS], fit[FIT_COLS]], ignore_index=True) if not fit.empty else older[FIT_COLS]
    if px.empty:  # no Binance candles: z_spec takes every close from the (now longer) model rows
        return fit, px
    old_px = TC.closes_from_fit(fit, tf)  # the bot's own rows too, should they reach back past the candles
    old_px = old_px[old_px["ts"] < int(px["ts"].min())]
    if not old_px.empty:
        px = pd.concat([old_px, px[["ts", "a", "b"]]], ignore_index=True)
    return fit, px.sort_values("ts", ignore_index=True)


_WARM: dict = {"sig": None, "at": 0.0, "busy": False}
WARM_EVERY_S = 3600


def warm(data_dir: str, jobs: list[tuple], candle_limit: int) -> None:
    """Load every pair's data in the background, so the first click on a pair finds it in memory.

    ``jobs``: (pair_id, sym_a, sym_b, model_args or None) in priority order (open positions first).
    Per pair: the 1h candles of both legs and the Binance bars after the archive (all pairs), and the
    whole Spread Z history (``model_history(data_dir, sym_a, sym_b, pair_id, *model_args)``) where
    ``model_args`` is given. Four pairs at a time, at most once an hour (mostly waiting for Binance)."""
    sig = (data_dir, tuple(j[0] for j in jobs), candle_limit)
    if _WARM["busy"] or (_WARM["sig"] == sig and time.time() - _WARM["at"] < WARM_EVERY_S):
        return
    _WARM.update(busy=True, sig=sig, at=time.time())

    def one(job: tuple) -> None:
        pid, sym_a, sym_b, model_args = job
        try:
            M.candles(sym_a, "1h", candle_limit)
            M.candles(sym_b, "1h", candle_limit)
            if model_args is not None:
                model_history(data_dir, sym_a, sym_b, pid, *model_args)
            else:
                L.pair_history(data_dir, sym_a, sym_b, True)
        except Exception as exc:
            log.warning("warm-up of %s failed: %s", pid, exc)

    def work() -> None:
        t0 = time.time()
        try:  # 4 at a time: most of it is waiting for Binance
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="warm-up") as pool:
                list(pool.map(one, jobs))
            log.info("warm-up of %d pairs done in %.0f s", len(jobs), time.time() - t0)
        finally:
            _WARM["busy"] = False

    threading.Thread(target=work, daemon=True, name="warm-up").start()


# ------------------------------------------------------------- older Spread Z, while the user scrolls left
# Bars per piece at 1h and 4h, cut from the history already in memory; 1m/5m/15m come from Binance
# in blocks of 1000 (market.older_candles).
OLDER_BARS = {"1h": 1500, "4h": 1500}


def older_piece(px: pd.DataFrame, fit: pd.DataFrame, trades: pd.DataFrame, tf: str, before_s: int,
                zkw: dict, data_starts: bool) -> dict:
    """The Spread Z of the bars before ``before_s`` (the close time, s, of the earliest point the chart
    has), drawn by the very ``z_spec`` that draws the chart, so a bar's Z is the same in both.

    ``px``: closes of those bars; ``fit``: the bot's model rows with the archive in front.
    ``data_starts``: ``px`` holds the first bars there are (the coin's listing, the archive's start).
    Returns, per series id, its data and markers, and ``done`` once nothing older is left.
    """
    px = px[px["ts"] + TC.TF_SECONDS[tf] * 1000 <= int(before_s) * 1000] if len(px) else px
    spec = TC.z_spec(px, fit, trades, tf, **zkw)
    data = {s["id"]: s["data"] for s in spec["series"]}
    markers = {s["id"]: s["markers"] for s in spec["series"] if s.get("markers")}
    z = data.get("z") or {"t": []}
    step_s = TC.TF_SECONDS[tf]
    first_close = (int(px["ts"].iloc[0]) // 1000 + step_s) if len(px) else None
    # nothing older: no bars, no Z (before the model's first fit), or the first fit or bar is inside this piece
    done = data_starts or not z["t"] or first_close is None or z["t"][0] > first_close
    return {"before": int(before_s), "series": data, "markers": markers, "done": bool(done),
            "first": int(z["t"][0]) if z["t"] else None}

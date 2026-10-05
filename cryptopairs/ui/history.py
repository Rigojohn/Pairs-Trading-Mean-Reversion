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

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.core.signals import estimate_fits
from cryptopairs.ui import lab as L
from cryptopairs.ui import tvcharts as TC

FIT_COLS = ["bar_ts", "pair_id", "kf_alpha", "kf_beta", "kf_sigma", "used_alpha", "used_beta", "used_sigma",
            "spread_u", "z", "log_a", "log_b"]


@st.cache_data(ttl=6 * 3600, show_spinner="Loading the spread's history…", max_entries=24)
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

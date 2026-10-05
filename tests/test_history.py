"""The pair desk's Spread Z history: the archive in front of the bot's own model records."""

import numpy as np
import pandas as pd

from cryptopairs.ui import history as HI
from cryptopairs.ui import tvcharts as TC

HOUR = 3_600_000
T0 = 1_780_000_000_000 // HOUR * HOUR


def _rows(start: int, n: int, z0: float, beta: float, source: str) -> pd.DataFrame:
    ts = start + HOUR * np.arange(n, dtype=np.int64)
    lb = np.log(2.0) + 0.001 * np.arange(n)
    la = 0.1 + beta * lb + 0.05 * z0
    return pd.DataFrame({"bar_ts": ts, "pair_id": "A_B", "kf_alpha": 0.1, "kf_beta": beta, "kf_sigma": 0.05,
                         "used_alpha": np.nan, "used_beta": np.nan, "used_sigma": np.nan,
                         "spread_u": 0.05 * z0, "z": z0, "log_a": la, "log_b": lb, "src": source})


def test_bot_rows_win_and_the_archive_only_fills_the_time_before_them():
    hist = _rows(T0, 200, 1.0, 0.9, "archive")
    bot = _rows(T0 + 150 * HOUR, 60, 2.0, 0.8, "bot")
    fit, px = HI.extend(bot.drop(columns="src"), None, hist.drop(columns="src"), "1h")
    assert fit["bar_ts"].is_monotonic_increasing and fit["bar_ts"].is_unique
    assert fit["bar_ts"].iloc[0] == T0 and len(fit) == 150 + 60
    assert (fit[fit["bar_ts"] >= T0 + 150 * HOUR]["kf_beta"] == 0.8).all()  # the bot's own rows, unchanged
    assert (fit[fit["bar_ts"] < T0 + 150 * HOUR]["kf_beta"] == 0.9).all()
    assert px.empty  # no Binance candles: z_spec reads every close from the model rows


def test_binance_candles_are_kept_and_older_closes_come_from_the_model_rows():
    hist = _rows(T0, 300, 1.0, 0.9, "archive").drop(columns="src")
    bot = _rows(T0 + 200 * HOUR, 100, 2.0, 0.8, "bot").drop(columns="src")
    candles = pd.DataFrame({"ts": T0 + HOUR * np.arange(250, 300, dtype=np.int64), "a": 9.0, "b": 9.0})
    fit, px = HI.extend(bot, candles, hist, "1h")
    assert px["ts"].is_monotonic_increasing and px["ts"].is_unique
    assert px["ts"].iloc[0] == T0 and px["ts"].iloc[-1] == T0 + 299 * HOUR
    assert (px[px["ts"] >= T0 + 250 * HOUR][["a", "b"]] == 9.0).all().all()  # Binance where it has candles
    # Before the candles: the closes recorded with the model (the bot's rows from 200 on, archive before).
    row = px[px["ts"] == T0 + 210 * HOUR].iloc[0]
    assert np.isclose(np.log(row["b"]), bot.loc[bot["bar_ts"] == T0 + 210 * HOUR, "log_b"].iloc[0])


def test_history_points_are_labelled_at_their_bar_close_like_the_bots():
    hist = _rows(T0, 100, 1.0, 0.9, "archive").drop(columns="src")
    fit, px = HI.extend(None, None, hist, "1h")
    now = T0 + 200 * HOUR
    h = TC.z_points(TC.closes_from_fit(fit, "1h"), TC.fits_in_force(fit, None, now), "1h", now)
    # Row bar_ts = T is the model at T's close (from_ms = T + 1h): its point sits at that close.
    assert h["t"].iloc[0] == (T0 + HOUR) // 1000
    assert np.allclose(h["z"], 1.0)

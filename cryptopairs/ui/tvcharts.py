"""Chart specs for TradingView Lightweight Charts (ui/tv.py renders them).

Pure functions: database frames in, a JSON-ready dict out (panes, series with data and a hash,
price lines, markers, live bindings to the browser's Binance stream). Kept free of Streamlit
so the tests can check them.

Lightweight Charts spaces bars evenly by index, so each chart uses one resolution only:
hourly model history, or the bot's 5-second marks bucketed to 1/5/15 minutes, or exchange
candles. Times are UTC seconds (the browser shows them in the dashboard's zone, ui/tv.py);
hourly model points are labelled at the bar's close.
"""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

from cryptopairs.ui.data import event_ms
from cryptopairs.ui.fund import underwater
from cryptopairs.ui.theme import AMBER, BORDER, CYAN, FAINT, GREEN, LEG_A, LEG_B, MUTED, RED, TEXT, VIOLET

HOUR = 3_600_000
SOLID, DOTTED, DASHED, LARGE_DASHED, SPARSE = 0, 1, 2, 3, 4
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
# Bars shown when a chart opens (the rest is a scroll away).
VISIBLE = {"1m": 360, "5m": 288, "15m": 192, "1h": 24 * 14, "4h": 180}
INTRADAY = ("1m", "5m", "15m")


def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def _clean(v) -> list:
    return [None if x is None or not np.isfinite(x) else round(float(x), 10) for x in np.asarray(v, dtype=float)]


def _dedupe(t: np.ndarray, *cols: np.ndarray) -> tuple:
    """Strictly increasing times (the last value of a repeated time wins), as the library needs."""
    t = np.asarray(t, dtype=np.int64)
    if t.size == 0:
        return (t, *cols)
    order = np.argsort(t, kind="stable")
    t = t[order]
    cols = [np.asarray(c)[order] for c in cols]
    keep = np.r_[t[1:] != t[:-1], True]
    return (t[keep], *[c[keep] for c in cols])


def series(sid: str, kind: str, data: dict, pane: int = 0, options: dict | None = None, lines: list | None = None,
           markers: list | None = None, live: dict | None = None, legend: str | None = None, digits: int = 2,
           role: str | None = None, margins: tuple[float, float] | None = None) -> dict:
    """One series. ``margins``: (top, bottom) share of the pane its price scale leaves empty."""
    s = {"id": sid, "type": kind, "pane": pane, "data": data, "options": options or {}, "digits": digits}
    if margins:
        s["margins"] = {"top": margins[0], "bottom": margins[1]}
    if lines:
        s["lines"] = lines
    if markers:
        s["markers"] = markers
    if live:
        s["live"] = live
    if legend:
        s["legend"] = legend
    if role:
        s["role"] = role
    s["hash"] = hashlib.md5(json.dumps(data, separators=(",", ":")).encode()).hexdigest()[:16]
    return s


def line_data(t_sec, v) -> dict:
    t, v = _dedupe(np.asarray(t_sec, dtype=np.int64), np.asarray(v, dtype=float))
    return {"t": [int(x) for x in t], "v": _clean(v)}


def price_line(price: float, color: str, title: str = "", style: int = DASHED, width: int = 1,
               axis: bool = True) -> dict:
    return {"price": float(price), "color": color, "title": title, "lineStyle": style, "lineWidth": width,
            "axisLabelVisible": axis}


def price_format(digits: int) -> dict:
    return {"type": "price", "precision": int(digits), "minMove": 10 ** -int(digits)}


# ------------------------------------------------------------- trade marks
def _snap(times: np.ndarray, t_sec: int) -> int | None:
    """The series time nearest to ``t_sec`` (markers must sit on a bar)."""
    if times.size == 0:
        return None
    i = int(np.searchsorted(times, t_sec))
    if i <= 0:
        return int(times[0])
    if i >= times.size:
        return int(times[-1])
    return int(times[i] if times[i] - t_sec <= t_sec - times[i - 1] else times[i - 1])


def _label(ms: int, tf_sec: int, label: str) -> int:
    """The bar an event belongs to: the first close at or after it ("end"), or the bar it fell in."""
    sec = ms / 1000
    return int(math.ceil(sec / tf_sec) * tf_sec) if label == "end" else int(sec // tf_sec * tf_sec)


def trade_markers(trades: pd.DataFrame, times: np.ndarray, tf_sec: int, label: str = "start",
                  reductions: list[int] | None = None) -> list[dict]:
    """Entry / close / stop markers of the bot's trades, placed on the bar where each happened.

    ``label``: "end" when a bar's time is its close (hourly model points), else its open.
    """
    times = np.asarray(times, dtype=np.int64)
    if times.size == 0:
        return []
    lo, hi = int(times[0]) - tf_sec, int(times[-1]) + tf_sec

    def bar(ms: int) -> int | None:
        b = _label(event_ms(ms), tf_sec, label)
        return _snap(times, b) if lo <= b <= hi else None

    out = []
    for t in (trades.itertuples() if trades is not None and len(trades) else ()):
        side = int(t.side)
        manual = getattr(t, "entry_reason", None) == "manual"
        tm = bar(t.entry_ts)
        if tm is not None:
            out.append({"time": tm, "position": "belowBar" if side > 0 else "aboveBar",
                        "shape": "arrowUp" if side > 0 else "arrowDown", "color": GREEN if side > 0 else RED,
                        "text": ("M " if manual else "") + ("BUY" if side > 0 else "SELL")})
        if pd.notna(t.exit_ts):
            tm = bar(int(t.exit_ts))
            if tm is not None:
                reason = str(t.reason or "")
                stop = reason.startswith(("stop", "liquidation"))
                pnl = float(t.pnl) if pd.notna(t.pnl) else math.nan
                out.append({"time": tm, "position": "aboveBar" if side > 0 else "belowBar",
                            "shape": "square" if stop else "circle", "color": AMBER if stop else TEXT,
                            "text": ("STOP" if stop else "CLOSE") + (f" {pnl:+.2f}" if math.isfinite(pnl) else "")})
    for ms in reductions or []:
        tm = bar(ms + 1 if int(ms) % HOUR == 0 else ms)  # always a real time
        if tm is not None:
            out.append({"time": tm, "position": "inBar", "shape": "circle", "color": MUTED, "text": "REDUCE",
                        "size": 0.7})
    return sorted(out, key=lambda m: m["time"])


def position_spans(trades: pd.DataFrame, now_ms: int) -> list[tuple[int, int, int]]:
    """(start ms, end ms, side) of every trade at the real times, the open one up to now."""
    spans = []
    for t in (trades.itertuples() if trades is not None and len(trades) else ()):
        spans.append((event_ms(t.entry_ts), event_ms(t.exit_ts) if pd.notna(t.exit_ts) else now_ms, int(t.side)))
    return spans


def shade(times: np.ndarray, spans: list[tuple[int, int, int]], tf_sec: int, label: str, pane: int = 0) -> dict | None:
    """Columns behind the bars held in a position (green long, red short)."""
    times = np.asarray(times, dtype=np.int64)
    if times.size == 0 or not spans:
        return None
    v = np.zeros(times.size)
    c: list = [None] * times.size
    for a, b, side in spans:
        if label == "end":  # an observation at the bar's close, from the first close after the entry to the exit
            m = (times * 1000 > a) & (times <= _label(b, tf_sec, "end"))
        else:  # a bar that overlaps the position
            m = ((times + tf_sec) * 1000 > a) & (times * 1000 <= b)
        v[m] = 1.0
        col = _rgba(GREEN if side > 0 else RED, 0.10)
        for i in np.flatnonzero(m):
            c[i] = col
    if not v.any():
        return None
    data = {"t": [int(x) for x in times], "v": [float(x) if x else None for x in v], "c": c}
    return series(f"shade{pane}", "Area", data, pane, role="shade", margins=(0.0, 0.0),
                  options={"lineVisible": False, "lineWidth": 1, "lineColor": "rgba(0,0,0,0)",
                           "topColor": _rgba(GREEN, 0.1), "bottomColor": _rgba(GREEN, 0.1),
                           "crosshairMarkerVisible": False})


# ------------------------------------------------------------- spread Z
def _intraday(live: pd.DataFrame, tf: str) -> pd.DataFrame:
    """The bot's live marks bucketed to ``tf`` (last mark of each bucket, labelled at its start)."""
    step = TF_SECONDS[tf] * 1000
    if live is None or live.empty:
        return pd.DataFrame(columns=["t", "z", "u", "sigma", "beta"])
    df = live.assign(b=(live["ts"] // step) * step).sort_values("ts").groupby("b").last()
    return pd.DataFrame({"t": (df.index.to_numpy() // 1000).astype(np.int64),
                         "z": ((df["z_buy"] + df["z_sell"]) / 2).to_numpy(dtype=float),
                         "u": df["spread_u"].to_numpy(dtype=float), "sigma": df["sigma"].to_numpy(dtype=float),
                         "beta": df["beta"].to_numpy(dtype=float)})


def _hourly(fit: pd.DataFrame) -> pd.DataFrame:
    """Hourly model history: the strategy's Z (fit in use when recorded, else the model's) and the model's."""
    if fit is None or fit.empty:
        return pd.DataFrame(columns=["t", "z", "z_model", "u", "sigma"])
    used = fit["used_sigma"].notna() & (fit["used_sigma"] > 0)
    u_used = fit["log_a"] - fit["used_alpha"] - fit["used_beta"] * fit["log_b"]
    u = np.where(used, u_used, fit["spread_u"])
    sigma = np.where(used, fit["used_sigma"], fit["kf_sigma"])
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(used, u_used / fit["used_sigma"], fit["z"])
    return pd.DataFrame({"t": ((fit["bar_ts"] + HOUR) // 1000).astype(np.int64), "z": z,
                         "z_model": fit["z"].to_numpy(dtype=float), "u": u, "sigma": sigma})


def z_spec(fit: pd.DataFrame, live: pd.DataFrame, trades: pd.DataFrame, tf: str, *, z_entry: float, z_exit: float,
           exit_plan: float | None = None, what_if: tuple[float, float] | None = None, max_entry_z: float | None = None,
           fit_in_use: dict | None = None, symbols: tuple[str, str] | None = None, now_ms: int,
           reductions: list[int] | None = None, height: int = 520) -> dict:
    """Spread Z with the thresholds, trades and the position shaded; the spread and its bands below."""
    tf_sec = TF_SECONDS[tf]
    if tf in INTRADAY:
        h, label = _intraday(live, tf), "start"
    else:
        h, label = _hourly(fit), "end"
    t = h["t"].to_numpy(dtype=np.int64)
    out: list[dict] = []
    sp = shade(t, position_spans(trades, now_ms), tf_sec, label)
    if sp:
        out.append(sp)
    lines = [price_line(z_entry, RED, "entry"), price_line(-z_entry, RED, "entry")]
    zx = z_exit if exit_plan is None else exit_plan
    ex_color, ex_title = (GREEN, "exit") if exit_plan is None else (AMBER, "exit · plan")
    if zx > 0:
        lines += [price_line(zx, ex_color, ex_title, DOTTED), price_line(-zx, ex_color, ex_title, DOTTED)]
    lines.append(price_line(0.0, BORDER, "", SOLID, 1, axis=False))
    if max_entry_z:
        lines += [price_line(max_entry_z, FAINT, "max", SPARSE, axis=False),
                  price_line(-max_entry_z, FAINT, "max", SPARSE, axis=False)]
    if what_if and tuple(what_if) != (z_entry, z_exit):
        wze, wzx = what_if
        lines += [price_line(wze, AMBER, "what-if", LARGE_DASHED), price_line(-wze, AMBER, "what-if", LARGE_DASHED)]
        if wzx > 0:
            lines += [price_line(wzx, AMBER, "", SPARSE, axis=False), price_line(-wzx, AMBER, "", SPARSE, axis=False)]
    fmt = price_format(2)
    if tf not in INTRADAY and "z_model" in h and len(h):
        out.append(series("z_model", "Line", line_data(t, h["z_model"]), 0, legend="model Z",
                          options={"color": _rgba(VIOLET, 0.55), "lineWidth": 1, "lineStyle": DOTTED,
                                   "priceFormat": fmt, "lastValueVisible": False, "priceLineVisible": False,
                                   "crosshairMarkerVisible": False}))
    out.append(series("z", "Line", line_data(t, h["z"]), 0, lines=lines, legend="Z · strategy",
                      markers=trade_markers(trades, t, tf_sec, label, reductions),
                      options={"color": VIOLET if tf not in INTRADAY else CYAN, "lineWidth": 2, "priceFormat": fmt,
                               "priceLineVisible": False}))
    if fit_in_use and symbols:
        live_fit = {k: float(fit_in_use[k]) for k in ("alpha", "beta", "sigma")}
        out.append(series("z_live", "Line", {"t": [], "v": []}, 0, legend="Z · Binance live",
                          live={"kind": "z", "a": symbols[0], "b": symbols[1], "fit": live_fit, "tf": tf_sec,
                                "label": label},
                          options={"color": CYAN, "lineWidth": 2, "priceFormat": fmt, "lineType": 1,
                                   "priceLineColor": CYAN}))
    # Pane 1: the spread with its bands (z x sigma of the fit the strategy uses at each bar).
    sig = h["sigma"].to_numpy(dtype=float) if len(h) else np.empty(0)
    sfmt = price_format(4)
    band_opts = lambda c, s: {"color": c, "lineWidth": 1, "lineStyle": s, "priceFormat": sfmt,  # noqa: E731
                              "lastValueVisible": False, "priceLineVisible": False, "crosshairMarkerVisible": False}
    for k, c, s, name in ((z_entry, RED, DASHED, "entry"), (zx, GREEN if exit_plan is None else AMBER, DOTTED, "exit")):
        if k <= 0:
            continue
        out.append(series(f"band_{name}_hi", "Line", line_data(t, k * sig), 1, options=band_opts(c, s)))
        out.append(series(f"band_{name}_lo", "Line", line_data(t, -k * sig), 1, options=band_opts(c, s)))
    out.append(series("spread", "Line", line_data(t, h["u"]), 1, legend="spread u", digits=4,
                      lines=[price_line(0.0, BORDER, "", SOLID, axis=False)],
                      options={"color": TEXT, "lineWidth": 1, "priceFormat": sfmt, "priceLineVisible": False}))
    if fit_in_use and symbols:
        out.append(series("spread_live", "Line", {"t": [], "v": []}, 1,
                          live={"kind": "spread", "a": symbols[0], "b": symbols[1],
                                "fit": {k: float(fit_in_use[k]) for k in ("alpha", "beta", "sigma")},
                                "tf": tf_sec, "label": label},
                          options={"color": CYAN, "lineWidth": 1, "priceFormat": sfmt}))
    return {"height": height, "panes": [3, 1.2], "series": out, "seconds": False,
            "visible_bars": VISIBLE.get(tf)}


# ---------------------------------------------------------------- legs
def _candle_data(df: pd.DataFrame) -> dict:
    t, o, hi, lo, c = _dedupe((df["ts"] // 1000).to_numpy(dtype=np.int64), df["o"].to_numpy(float),
                              df["h"].to_numpy(float), df["l"].to_numpy(float), df["c"].to_numpy(float))
    return {"t": [int(x) for x in t], "o": _clean(o), "h": _clean(hi), "l": _clean(lo), "c": _clean(c)}


def _volume_data(df: pd.DataFrame) -> dict:
    t, v, up = _dedupe((df["ts"] // 1000).to_numpy(dtype=np.int64), df["qv"].to_numpy(float),
                       (df["c"] >= df["o"]).to_numpy())
    return {"t": [int(x) for x in t], "v": _clean(v),
            "c": [_rgba(GREEN, 0.35) if u else _rgba(RED, 0.35) for u in up]}


def legs_spec(ca: pd.DataFrame | None, cb: pd.DataFrame | None, labels: tuple[str, str], ids: tuple[str, str],
              digits: tuple[int, int], tf: str, trades: pd.DataFrame, entry: tuple[float, float] | None,
              side_a: int = 0, height: int = 520, reductions: list[int] | None = None) -> dict:
    """Candles of both legs, one pane each, with volume, the entry prices and the trades."""
    tf_sec = TF_SECONDS[tf]
    out = []
    for pane, (df, label, sym, dg) in enumerate(zip((ca, cb), labels, ids, digits)):
        if df is None or df.empty:
            continue
        cd = _candle_data(df)
        lines = []
        if entry:
            leg_side = side_a if pane == 0 else -side_a
            lines.append(price_line(entry[pane], GREEN if leg_side > 0 else RED,
                                    f"entry {'long' if leg_side > 0 else 'short'}", DASHED, 1))
        out.append(series(f"vol{pane}", "Histogram", _volume_data(df), pane,
                          live={"kind": "volume", "symbol": sym, "interval": tf, "up": _rgba(GREEN, 0.35),
                                "down": _rgba(RED, 0.35)},
                          options={"priceScaleId": f"vol{pane}", "priceFormat": {"type": "volume"},
                                   "lastValueVisible": False, "priceLineVisible": False}, margins=(0.8, 0.0)))
        out.append(series(f"c{pane}", "Candlestick", cd, pane, lines=lines, legend=label, digits=dg,
                          markers=trade_markers(trades, np.asarray(cd["t"]), tf_sec, "start", reductions),
                          live={"kind": "kline", "symbol": sym, "interval": tf},
                          options={"upColor": GREEN, "downColor": RED, "borderVisible": False, "wickUpColor": GREEN,
                                   "wickDownColor": RED, "priceFormat": price_format(dg)}))
    return {"height": height, "panes": [1, 1], "series": out, "visible_bars": VISIBLE.get(tf)}


def relative_spec(ca: pd.DataFrame | None, cb: pd.DataFrame | None, labels: tuple[str, str], ids: tuple[str, str],
                  tf: str, fit_in_use: dict | None, z_entry: float, z_exit: float, trades: pd.DataFrame,
                  height: int = 520) -> dict:
    """Both legs rebased to 100, and the Z the candles give under the fit in use."""
    tf_sec = TF_SECONDS[tf]
    out: list[dict] = []
    if ca is None or cb is None or ca.empty or cb.empty:
        return {"height": height, "panes": [1, 1], "series": out}
    m = ca[["ts", "c"]].merge(cb[["ts", "c"]], on="ts", suffixes=("_a", "_b"))
    t = (m["ts"] // 1000).to_numpy(dtype=np.int64)
    base_a, base_b = float(m["c_a"].iloc[0]), float(m["c_b"].iloc[0])
    fmt = price_format(2)
    for pane_label, col, base, color, sym in ((labels[0], "c_a", base_a, LEG_A, ids[0]),
                                              (labels[1], "c_b", base_b, LEG_B, ids[1])):
        out.append(series(f"rb_{col}", "Line", line_data(t, 100 * m[col] / base), 0, legend=pane_label,
                          live={"kind": "rebased", "symbol": sym, "interval": tf, "base": base},
                          options={"color": color, "lineWidth": 2, "priceFormat": fmt}))
    if fit_in_use and fit_in_use.get("sigma"):
        a, b, s = (float(fit_in_use[k]) for k in ("alpha", "beta", "sigma"))
        z = (np.log(m["c_a"]) - a - b * np.log(m["c_b"])) / s
        lines = [price_line(z_entry, RED, "entry"), price_line(-z_entry, RED, "entry"),
                 price_line(0.0, BORDER, "", SOLID, axis=False)]
        if z_exit > 0:
            lines += [price_line(z_exit, GREEN, "exit", DOTTED), price_line(-z_exit, GREEN, "exit", DOTTED)]
        out.append(series("z_fit", "Line", line_data(t, z), 1, legend="Z · today's fit", lines=lines,
                          markers=trade_markers(trades, t, tf_sec, "start"),
                          live={"kind": "z", "a": ids[0], "b": ids[1], "fit": {"alpha": a, "beta": b, "sigma": s},
                                "tf": tf_sec, "label": "start"},
                          options={"color": VIOLET, "lineWidth": 2, "priceFormat": fmt}))
    return {"height": height, "panes": [1.4, 1], "series": out, "visible_bars": VISIBLE.get(tf)}


def model_spec(fit: pd.DataFrame, live: pd.DataFrame, height: int = 520) -> dict:
    """Hedge ratio and sigma: what the estimator says now vs the fit the strategy trades on."""
    out = []
    if fit is not None and not fit.empty:
        t = ((fit["bar_ts"] + HOUR) // 1000).to_numpy(dtype=np.int64)
        f4 = price_format(4)
        out.append(series("beta_model", "Line", line_data(t, fit["kf_beta"]), 0, legend="β model", digits=4,
                          options={"color": LEG_A, "lineWidth": 2, "priceFormat": f4}))
        out.append(series("beta_used", "Line", line_data(t, fit["used_beta"]), 0, legend="β in use", digits=4,
                          options={"color": AMBER, "lineWidth": 2, "lineType": 1, "priceFormat": f4}))
        out.append(series("sigma_model", "Line", line_data(t, fit["kf_sigma"]), 1, legend="σ model", digits=4,
                          options={"color": LEG_A, "lineWidth": 2, "priceFormat": f4}))
        out.append(series("sigma_used", "Line", line_data(t, fit["used_sigma"]), 1, legend="σ in use", digits=4,
                          options={"color": AMBER, "lineWidth": 2, "lineType": 1, "priceFormat": f4}))
    return {"height": height, "panes": [1, 1], "series": out}


# ------------------------------------------------------------------ fund NAV
def performance_spec(grid: pd.DataFrame, *, capital: float, peak_before: float, kill_level: float | None = None,
                     trades: pd.DataFrame | None = None, labels: dict | None = None, max_dd: float | None = None,
                     height: int = 600) -> dict:
    """The fund's NAV over a range and its drawdown from the high-water mark ("underwater").

    ``grid``: ``fund.nav_grid`` (even bars, NaN where the bot was down). The NAV is shaded green
    above and red below its value at the start of the range; the dotted line is the high-water
    mark. Closed trades sit on the line with their PnL. The lower pane is the drawdown in %, with
    the range's deepest point marked: ``max_dd`` (from every mark, not only the plotted bars) when given.
    The high-water mark never stretches the scale, so a short range is not squashed by an old peak.
    """
    t = (grid["t"].to_numpy(dtype=np.int64) // 1000) if len(grid) else np.empty(0, np.int64)
    v = grid["nav"].to_numpy(dtype=float) if len(grid) else np.empty(0)
    fin = v[np.isfinite(v)]
    base = float(fin[0]) if fin.size else float(capital)
    if "hwm" in grid and "dd" in grid and len(grid):  # from every observation (fund.nav_grid)
        hwm, dd = grid["hwm"].to_numpy(dtype=float), grid["dd"].to_numpy(dtype=float)
    else:
        hwm, dd = underwater(v, peak_before)
    fmt = price_format(2)
    lines = [price_line(capital, MUTED, "capital", DOTTED)]
    if kill_level:
        lines.append(price_line(kill_level, RED, "kill switch", SPARSE))
    marks = []
    if trades is not None and len(trades) and t.size:
        closed = trades[trades["exit_ts"].notna()]
        for r in closed.itertuples():
            ms = event_ms(int(r.exit_ts))
            if not (t[0] * 1000 <= ms <= t[-1] * 1000 + 60_000):
                continue
            pnl = float(r.pnl) if pd.notna(r.pnl) else 0.0
            name = (labels or {}).get(r.pair_id, r.pair_id)
            marks.append({"time": _snap(t, int(math.ceil(ms / 1000))), "position": "aboveBar" if pnl > 0 else "belowBar",
                          "shape": "circle", "color": GREEN if pnl > 0 else RED, "size": 0.8,
                          "text": f"{name} {pnl:+.2f}"})
    nav = series("nav", "Baseline", line_data(t, v), 0, lines=lines, legend="NAV", markers=sorted(marks, key=lambda m: m["time"]),
                 options={"baseValue": {"type": "price", "price": base}, "lineWidth": 2, "lineType": 0,
                          "topLineColor": GREEN, "topFillColor1": _rgba(GREEN, 0.30), "topFillColor2": _rgba(GREEN, 0.03),
                          "bottomLineColor": RED, "bottomFillColor1": _rgba(RED, 0.03), "bottomFillColor2": _rgba(RED, 0.30),
                          "priceFormat": fmt, "color": GREEN, "priceLineVisible": True, "crosshairMarkerRadius": 4})
    hw = series("hwm", "Line", line_data(t, np.where(np.isfinite(v), hwm, np.nan)), 0, legend="high-water mark",
                role="guide",
                options={"color": _rgba(AMBER, 0.75), "lineWidth": 1, "lineStyle": DOTTED, "lineType": 1,
                         "priceFormat": fmt, "lastValueVisible": False, "priceLineVisible": False,
                         "crosshairMarkerVisible": False})
    dd_lines = [price_line(0.0, BORDER, "", SOLID, axis=False)]
    worst = max_dd if max_dd is not None else (float(np.nanmin(dd)) if np.isfinite(dd).any() else 0.0)
    if worst < 0:
        dd_lines.append(price_line(worst, RED, f"max {worst:.2f}%", DASHED))
    draw = series("dd", "Area", line_data(t, dd), 1, legend="drawdown %", lines=dd_lines,
                  options={"lineColor": RED, "topColor": _rgba(RED, 0.04), "bottomColor": _rgba(RED, 0.40), "lineWidth": 1,
                           "priceFormat": fmt, "invertFilledArea": True, "color": RED, "priceLineVisible": False},
                  margins=(0.08, 0.02))
    return {"height": height, "panes": [3, 1.25], "series": [nav, hw, draw], "right_offset": 1,
            "seconds": bool(len(grid) > 1 and int(grid["t"].iloc[-1] - grid["t"].iloc[0]) <= 6 * HOUR)}

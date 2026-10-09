"""Chart specs for TradingView Lightweight Charts (ui/tv.py renders them).

Pure functions: database frames in, a JSON-ready dict out (panes, series with data and a hash,
price lines, markers, live bindings to the browser's Binance stream). Kept free of Streamlit
so the tests can check them.

Lightweight Charts spaces bars evenly by index, so each chart uses one resolution only.
Times are UTC seconds (the browser shows them in the dashboard's zone, ui/tv.py). Candles are
labelled at their open, as on an exchange; a Z point is labelled at the close it was measured
at, so the same moment shows the same Z on every timeframe.
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
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
# Bars shown when a chart opens (the rest is a scroll away). 1d: the whole history at once.
VISIBLE = {"1m": 360, "5m": 288, "15m": 192, "1h": 24 * 14, "4h": 180, "1d": 1300}
# The NAV chart and the Spread Z open with one point every OPENING_PX pixels in every timeframe, as TradingView
# does: a finer timeframe shows less time (1m is stretched), and a history too short to fill the width shows
# whole. The Spread Z then keeps the width the user zoomed to when the timeframe or the pair changes.
OPENING_PX = 6
RIGHT_PX = 64  # room after the NAV's last point, so the latest closed trade's label is not cut off
STACK = 4  # closed trades drawn on one NAV bar and side at most: beyond, the latest STACK - 1 and "+n more"
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
           role: str | None = None, margins: tuple[float, float] | None = None,
           include: list[float] | None = None, boxes: list[dict] | None = None) -> dict:
    """One series. ``margins``: (top, bottom) share of the pane its price scale leaves empty;
    ``include``: values its price scale always shows (levels drawn as price lines);
    ``boxes``: spans of time shaded behind it (``position_boxes``)."""
    s = {"id": sid, "type": kind, "pane": pane, "data": data, "options": options or {}, "digits": digits}
    if include:
        s["include"] = [float(x) for x in include]
    if margins:
        s["margins"] = {"top": margins[0], "bottom": margins[1]}
    if lines:
        s["lines"] = lines
    if markers:
        s["markers"] = markers
    if boxes:
        s["boxes"] = boxes
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


def fill_z(t, px_a, px_b) -> float | None:
    """The Z of fills at ``px_a``/``px_b`` under the trade's fit (frozen for its whole life)."""
    try:
        a, b, s, pa, pb = (float(x) for x in (t.alpha, t.beta, t.sigma, px_a, px_b))
    except (AttributeError, TypeError, ValueError):
        return None
    if not (s > 0 and pa > 0 and pb > 0 and math.isfinite(a) and math.isfinite(b)):
        return None
    return (math.log(pa) - a - b * math.log(pb)) / s


def event_bar(ms: int, tf_sec: int, label: str) -> int:
    """The bar (its time, s) a trade event is drawn on: its markers and its position box share it."""
    real = event_ms(ms)
    if label == "end" and tf_sec * 1000 < HOUR and int(ms) % HOUR == 0:
        real += 1  # an hourly decision runs seconds after the close: the next intraday bar
    return _label(real, tf_sec, label)


def trade_markers(trades: pd.DataFrame, times: np.ndarray, tf_sec: int, label: str = "start",
                  reductions: list | None = None, at_fill_z: bool = False) -> list[dict]:
    """Entry / close / stop markers of the bot's trades, placed on the bar where each happened.

    ``label``: "end" when a bar's time is its close (Z points), else its open. ``at_fill_z``
    (Z charts): each marker sits at the Z its fills got, which is what the PnL is measured from,
    rather than on the line at the bar's close (the order may fill seconds or minutes later).
    """
    times = np.asarray(times, dtype=np.int64)
    if times.size == 0:
        return []
    lo, hi = int(times[0]) - tf_sec, int(times[-1]) + tf_sec

    def bar(ms: int) -> int | None:
        b = event_bar(ms, tf_sec, label)
        return _snap(times, b) if lo <= b <= hi else None

    out = []
    for t in (trades.itertuples() if trades is not None and len(trades) else ()):
        side = int(t.side)
        manual = getattr(t, "entry_reason", None) == "manual"
        tm = bar(t.entry_ts)
        if tm is not None:
            m = {"time": tm, "position": "belowBar" if side > 0 else "aboveBar",
                 "shape": "arrowUp" if side > 0 else "arrowDown", "color": GREEN if side > 0 else RED,
                 "text": ("M " if manual else "") + ("BUY" if side > 0 else "SELL")}
            z = fill_z(t, getattr(t, "entry_px_a", None), getattr(t, "entry_px_b", None)) if at_fill_z else None
            if z is not None:
                m.update(position="atPriceBottom" if side > 0 else "atPriceTop", price=z, text=f"{m['text']} {z:+.2f}")
            out.append(m)
        if pd.notna(t.exit_ts):
            tm = bar(int(t.exit_ts))
            if tm is not None:
                reason = str(t.reason or "")
                stop = reason.startswith(("stop", "liquidation"))
                pnl = float(t.pnl) if pd.notna(t.pnl) else math.nan
                m = {"time": tm, "position": "aboveBar" if side > 0 else "belowBar",
                     "shape": "square" if stop else "circle", "color": AMBER if stop else TEXT,
                     "text": ("STOP" if stop else "CLOSE") + (f" {pnl:+.2f}" if math.isfinite(pnl) else "")}
                z = fill_z(t, getattr(t, "exit_px_a", None), getattr(t, "exit_px_b", None)) if at_fill_z else None
                if z is not None:
                    m.update(position="atPriceMiddle", price=z, text=f"{m['text']} @ {z:+.2f}")
                out.append(m)
    for r in reductions or []:  # manual reductions / additions: a time, or (time, "REDUCE" | "ADD")
        ms, text = (r, "REDUCE") if isinstance(r, (int, np.integer)) else r
        tm = bar(ms + 1 if int(ms) % HOUR == 0 else ms)  # always a real time
        if tm is not None:
            out.append({"time": tm, "position": "inBar", "shape": "circle", "color": MUTED, "text": text,
                        "size": 0.7})
    return sorted(out, key=lambda m: m["time"])


def last_entry_ms(trades: pd.DataFrame | None) -> int | None:
    """When the latest trade was entered (its real time), or None without trades."""
    if trades is None or not len(trades) or "entry_ts" not in trades:
        return None
    return event_ms(int(trades["entry_ts"].max()))


def position_boxes(trades: pd.DataFrame | None, tf_sec: int, label: str = "end") -> list[dict]:
    """One box per trade behind the line (green long, red short; drawn by js/chart.js): from the bar of
    its entry marker to the bar of its exit marker, ``to`` None while open (up to the latest point).
    Every trade, also those before the bars sent: older history loaded later shows them."""
    out = []
    for t in (trades.itertuples() if trades is not None and len(trades) else ()):
        color = GREEN if int(t.side) > 0 else RED
        out.append({"from": event_bar(t.entry_ts, tf_sec, label),
                    "to": event_bar(int(t.exit_ts), tf_sec, label) if pd.notna(t.exit_ts) else None,
                    "fill": _rgba(color, 0.10), "edge": _rgba(color, 0.45)})
    return sorted(out, key=lambda b: b["from"])


# ------------------------------------------------------------- spread Z
def fits_in_force(fit: pd.DataFrame, trades: pd.DataFrame | None, now_ms: int) -> pd.DataFrame:
    """The fit (alpha, beta, sigma) the strategy traded on from each hourly decision on.

    Model-history row ``bar_ts`` is decided at that bar's close (``from_ms`` = bar_ts + 1 h)
    and holds until the next decision. Its fit is the one the bot recorded as in use; where
    none was recorded (history back-filled on a restart, hours the bot was down) it is the fit
    frozen at the entry of a position held then, else the model's estimate at that bar, which
    is what a flat strategy refits to every bar. ``model_z``: the Z under the model's estimate.
    """
    cols = ["from_ms", "alpha", "beta", "sigma", "model_z"]
    if fit is None or fit.empty:
        return pd.DataFrame(columns=cols)
    f = fit.sort_values("bar_ts")
    start = f["bar_ts"].to_numpy(dtype=np.int64) + HOUR
    al, be, si = (f[f"kf_{k}"].to_numpy(dtype=float).copy() for k in ("alpha", "beta", "sigma"))
    held = trades.sort_values("entry_ts") if trades is not None and len(trades) and "sigma" in trades else ()
    for t in (held.itertuples() if len(held) else ()):
        if not (pd.notna(t.alpha) and pd.notna(t.beta) and pd.notna(t.sigma) and t.sigma > 0):
            continue
        a = event_ms(t.entry_ts)
        b = event_ms(t.exit_ts) if pd.notna(t.exit_ts) else now_ms
        m = (start + HOUR > a) & (start <= b)  # still frozen after an exit, until the next bar refits
        al[m], be[m], si[m] = float(t.alpha), float(t.beta), float(t.sigma)
    used = (f["used_sigma"] > 0).to_numpy()
    al[used], be[used], si[used] = (f[f"used_{k}"].to_numpy(dtype=float)[used] for k in ("alpha", "beta", "sigma"))
    return pd.DataFrame({"from_ms": start, "alpha": al, "beta": be, "sigma": si,
                         "model_z": f["z"].to_numpy(dtype=float)})


def closes(ca: pd.DataFrame | None, cb: pd.DataFrame | None) -> pd.DataFrame:
    """Close prices of both legs on the bars they share (``ts`` = open time, ms)."""
    if ca is None or cb is None or ca.empty or cb.empty:
        return pd.DataFrame(columns=["ts", "a", "b"])
    return ca[["ts", "c"]].merge(cb[["ts", "c"]], on="ts", suffixes=("_a", "_b")).rename(
        columns={"c_a": "a", "c_b": "b"}).sort_values("ts", ignore_index=True)


def closes_from_fit(fit: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Hourly or 4-hour closes from the bot's model history (when Binance does not answer)."""
    step = TF_SECONDS[tf] * 1000
    if fit is None or fit.empty or step < HOUR:
        return pd.DataFrame(columns=["ts", "a", "b"])
    f = fit[(fit["bar_ts"] + HOUR) % step == 0]
    return pd.DataFrame({"ts": (f["bar_ts"] + HOUR - step).to_numpy(dtype=np.int64),
                         "a": np.exp(f["log_a"].to_numpy(dtype=float)), "b": np.exp(f["log_b"].to_numpy(dtype=float))})


def z_points(px: pd.DataFrame, forces: pd.DataFrame, tf: str, now_ms: int, fit_now: dict | None = None) -> pd.DataFrame:
    """Z and the spread at each bar's close, under the fit in force at that moment.

    One definition for every timeframe: a 1-minute bar closing on the hour shows the same Z as
    the hourly bar closing then. Points are labelled at the close (``t``, seconds); the forming
    bar's close is now, and from the last decision on the fit is ``fit_now`` (the bot's).
    """
    out = pd.DataFrame(columns=["t", "z", "u", "sigma"])
    if px is None or px.empty or forces is None or forces.empty:
        return out
    step = TF_SECONDS[tf] * 1000
    ts = px["ts"].to_numpy(dtype=np.int64)
    moment = np.minimum(ts + step, now_ms)
    start = forces["from_ms"].to_numpy(dtype=np.int64)
    i = np.searchsorted(start, moment, side="right") - 1
    known = i >= 0
    pick = lambda k: np.where(known, forces[k].to_numpy(dtype=float)[np.clip(i, 0, None)], np.nan)  # noqa: E731
    al, be, si = pick("alpha"), pick("beta"), pick("sigma")
    if fit_now and fit_now.get("sigma"):
        cur = moment >= start[-1]
        al[cur], be[cur], si[cur] = float(fit_now["alpha"]), float(fit_now["beta"]), float(fit_now["sigma"])
    with np.errstate(invalid="ignore", divide="ignore"):
        u = np.log(px["a"].to_numpy(dtype=float)) - al - be * np.log(px["b"].to_numpy(dtype=float))
        z = np.where(si > 0, u / si, np.nan)
    ok = np.flatnonzero(np.isfinite(z))
    if ok.size == 0:
        return out
    k = slice(ok[0], None)  # nothing before the first fit
    return pd.DataFrame({"t": ((ts + step) // 1000)[k], "z": z[k], "u": u[k], "sigma": si[k]})


def z_levels(side: int, z_entry: float, z_exit: float, exit_plan: float | None = None,
             entered: float | None = None) -> list[tuple[float, str, str]]:
    """The Z levels that matter now, as (Z, "entry" | "exit", label).

    Flat: the thresholds, a long entry below -z_entry and a short entry above +z_entry, with their
    exits. In a position only its own two: where it entered (``entered``, the Z its fills got;
    the threshold when unknown) and its exit. A long exits when Z rises back to -z_exit, a short when
    it falls back to +z_exit: on the side it came from, never across zero. ``exit_plan`` replaces
    z_exit in a position.
    """
    if side:
        zx = z_exit if exit_plan is None else exit_plan
        z_in = -side * z_entry if entered is None or not math.isfinite(entered) else float(entered)
        return [(z_in, "entry", "entry"), (-side * zx, "exit", "exit · plan" if exit_plan is not None else "exit")]
    out = []
    for sd, name in ((1, "long"), (-1, "short")):
        out.append((-sd * z_entry, "entry", f"{name} entry"))
        if z_exit > 0:
            out.append((-sd * z_exit, "exit", f"{name} exit"))
    if z_exit <= 0:
        out.append((0.0, "exit", "exit"))
    return out


def _level_style(z: float, role: str, label: str, side: int = 0) -> tuple[str, int]:
    """Colour and line style of a level: long green, short red, exits cyan, a manual plan amber."""
    if label.endswith("plan"):
        return AMBER, DOTTED
    if role == "exit":
        return CYAN, DOTTED
    long = side > 0 if side else z < 0
    return (GREEN if long else RED), DASHED


def z_spec(px: pd.DataFrame, fit: pd.DataFrame, trades: pd.DataFrame, tf: str, *, z_entry: float, z_exit: float,
           exit_plan: float | None = None, what_if: tuple[float, float] | None = None, max_entry_z: float | None = None,
           fit_now: dict | None = None, symbols: tuple[str, str] | None = None, now_ms: int, side: int = 0,
           entered: float | None = None, reductions: list | None = None, height: int = 560) -> dict:
    """The strategy's Z at every moment, with the levels it acts on (``z_levels``: both directions
    while flat, the open position's own while ``side`` != 0), the trades and a box per position;
    the spread (% from fair value) with the same levels as bands below. ``px``: ``closes(...)`` at ``tf``
    (hourly and 4-hour fall back to the bot's model history). The last point follows Binance live."""
    tf_sec = TF_SECONDS[tf]
    if (px is None or px.empty) and tf not in INTRADAY:
        px = closes_from_fit(fit, tf)
    h = z_points(px, fits_in_force(fit, trades, now_ms), tf, now_ms, fit_now)
    t = h["t"].to_numpy(dtype=np.int64)
    out: list[dict] = []
    levels = z_levels(side, z_entry, z_exit, exit_plan, entered)
    width = 2 if side else 1
    lines = []
    for z, role, label in levels:
        color, style = _level_style(z, role, label, side)
        lines.append(price_line(z, color, label, style, width))
    if not any(z == 0 for z, _, _ in levels):
        lines.append(price_line(0.0, BORDER, "", SOLID, 1, axis=False))
    if max_entry_z and not side:
        lines += [price_line(max_entry_z, FAINT, "no entry beyond", SPARSE, axis=False),
                  price_line(-max_entry_z, FAINT, "no entry beyond", SPARSE, axis=False)]
    if what_if and tuple(what_if) != (z_entry, z_exit):
        for z, role, _label in z_levels(side, *what_if, entered=entered):
            lines.append(price_line(z, AMBER, "what-if" if role == "entry" else "", LARGE_DASHED if role == "entry"
                                    else SPARSE, axis=role == "entry"))
    live = None
    if fit_now and fit_now.get("sigma") and symbols:
        live = {"a": symbols[0], "b": symbols[1], "tf": tf_sec, "label": "end",
                "fit": {k: float(fit_now[k]) for k in ("alpha", "beta", "sigma")}}
    out.append(series("z", "Line", line_data(t, h["z"]), 0, lines=lines, legend="Z", include=[z for z, _, _ in levels],
                      markers=trade_markers(trades, t, tf_sec, "end", reductions, at_fill_z=True),
                      boxes=position_boxes(trades, tf_sec), live=dict(live, kind="z") if live else None,
                      options={"color": VIOLET, "lineWidth": 2, "priceFormat": price_format(2), "priceLineVisible": True,
                               "priceLineColor": VIOLET, "priceLineStyle": DOTTED}))
    # Pane 1: the spread in % (log A - alpha - beta log B) and its bands, z x sigma of the fit in force.
    sig = 100 * h["sigma"].to_numpy(dtype=float)
    pfmt = price_format(2)
    band_opts = lambda c, s: {"color": c, "lineWidth": 1, "lineStyle": s, "priceFormat": pfmt,  # noqa: E731
                              "lastValueVisible": False, "priceLineVisible": False, "crosshairMarkerVisible": False}
    zero = [price_line(0.0, BORDER, "", SOLID, axis=False)]
    keep: list[float] = []
    sig_now = 100 * float(fit_now["sigma"]) if side and fit_now and fit_now.get("sigma") else None
    for z, role, label in levels:
        color, style = _level_style(z, role, label, side)
        if sig_now is not None:  # in a position the fit is frozen: fixed levels
            zero.append(price_line(z * sig_now, color, label, style, width))
            keep.append(z * sig_now)
        elif z == 0:
            zero = [price_line(0.0, color, label, style, width)]
            keep.append(0.0)
        else:
            out.append(series("band_" + label.replace(" ", "_"), "Line", line_data(t, z * sig), 1,
                              options=band_opts(color, style)))
    out.append(series("spread", "Line", line_data(t, 100 * h["u"].to_numpy(dtype=float)), 1, legend="spread %",
                      lines=zero, include=keep,
                      live=dict(live, kind="spread", scale=100.0) if live else None,
                      options={"color": TEXT, "lineWidth": 1, "priceFormat": pfmt, "priceLineVisible": False}))
    return {"height": height, "panes": [3, 1.3], "series": out, "seconds": False,
            "bar_px": OPENING_PX, "keep_zoom": True}


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
              side_a: int = 0, height: int = 520, reductions: list | None = None) -> dict:
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
                  tf: str, trades: pd.DataFrame, height: int = 520) -> dict:
    """Both legs rebased to 100 at the left edge, with the trades (the Z is in the Spread Z view)."""
    tf_sec = TF_SECONDS[tf]
    out: list[dict] = []
    if ca is None or cb is None or ca.empty or cb.empty:
        return {"height": height, "panes": [1], "series": out}
    m = ca[["ts", "c"]].merge(cb[["ts", "c"]], on="ts", suffixes=("_a", "_b"))
    t = (m["ts"] // 1000).to_numpy(dtype=np.int64)
    base_a, base_b = float(m["c_a"].iloc[0]), float(m["c_b"].iloc[0])
    fmt = price_format(2)
    for pane_label, col, base, color, sym in ((labels[0], "c_a", base_a, LEG_A, ids[0]),
                                              (labels[1], "c_b", base_b, LEG_B, ids[1])):
        out.append(series(f"rb_{col}", "Line", line_data(t, 100 * m[col] / base), 0, legend=pane_label,
                          markers=trade_markers(trades, t, tf_sec, "start") if col == "c_a" else None,
                          live={"kind": "rebased", "symbol": sym, "interval": tf, "base": base},
                          options={"color": color, "lineWidth": 2, "priceFormat": fmt}))
    return {"height": height, "panes": [1], "series": out, "visible_bars": VISIBLE.get(tf)}


def model_spec(fit: pd.DataFrame, trades: pd.DataFrame, now_ms: int, *, z_entry: float | None = None,
               height: int = 600) -> dict:
    """The estimator against the fit the strategy trades on, hour by hour: hedge ratio, sigma, and
    the Z each gives. They differ only while a position holds its entry fit (or an override)."""
    out = []
    if fit is None or fit.empty:
        return {"height": height, "panes": [1, 1, 1.2], "series": out}
    fit = fit.sort_values("bar_ts")
    forces = fits_in_force(fit, trades, now_ms)
    t = ((fit["bar_ts"] + HOUR) // 1000).to_numpy(dtype=np.int64)
    f4, f2 = price_format(4), price_format(2)
    model = {"color": LEG_A, "lineWidth": 2, "priceFormat": f4}
    used = lambda fmt: {"color": AMBER, "lineWidth": 2, "lineType": 1, "priceFormat": fmt}  # noqa: E731
    out.append(series("beta_model", "Line", line_data(t, fit["kf_beta"]), 0, legend="β model", digits=4,
                      options=model))
    out.append(series("beta_used", "Line", line_data(t, forces["beta"]), 0, legend="β in use", digits=4,
                      options=used(f4)))
    out.append(series("sigma_model", "Line", line_data(t, fit["kf_sigma"]), 1, legend="σ model", digits=4,
                      options=model))
    out.append(series("sigma_used", "Line", line_data(t, forces["sigma"]), 1, legend="σ in use", digits=4,
                      options=used(f4)))
    with np.errstate(invalid="ignore", divide="ignore"):
        z_used = (fit["log_a"].to_numpy(float) - forces["alpha"].to_numpy(float)
                  - forces["beta"].to_numpy(float) * fit["log_b"].to_numpy(float)) / forces["sigma"].to_numpy(float)
    lines = [price_line(0.0, BORDER, "", SOLID, axis=False)]
    if z_entry:
        lines += [price_line(z_entry, RED, "entry"), price_line(-z_entry, RED, "entry")]
    out.append(series("z_model", "Line", line_data(t, forces["model_z"]), 2, legend="Z if refit now (model)",
                      options={"color": LEG_A, "lineWidth": 1, "priceFormat": f2}))
    out.append(series("z_used", "Line", line_data(t, z_used), 2, legend="Z in use", lines=lines,
                      options={"color": AMBER, "lineWidth": 2, "priceFormat": f2}))
    return {"height": height, "panes": [1, 1, 1.2], "series": out}


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
    stacks: dict[tuple[int, str], list[tuple[int, float, str]]] = {}  # (bar, side) -> (exit ms, pnl, label)
    if trades is not None and len(trades) and t.size:
        closed = trades[trades["exit_ts"].notna()]
        first_open = int(t[0] - (t[1] - t[0] if t.size > 1 else 0)) * 1000  # the first bar starts a bar before its label
        for r in closed.itertuples():
            ms = event_ms(int(r.exit_ts))
            if not (first_open < ms <= t[-1] * 1000 + 60_000):
                continue
            pnl = float(r.pnl) if pd.notna(r.pnl) else 0.0
            name = (labels or {}).get(r.pair_id, r.pair_id)
            i = min(int(np.searchsorted(t, math.ceil(ms / 1000))), t.size - 1)  # the bar it closed in (labelled at its end)
            stacks.setdefault((int(t[i]), "aboveBar" if pnl > 0 else "belowBar"), []).append((ms, pnl, f"{name} {pnl:+.2f}"))
    marks = []
    for (tm, pos), rows in stacks.items():
        rows.sort(key=lambda x: x[0])
        if len(rows) > STACK:  # the latest ones, and the rest summed up (a finer timeframe shows each)
            rest, rows = rows[:len(rows) - STACK + 1], rows[len(rows) - STACK + 1:]
            rows.append((0, sum(p for _, p, _ in rest), f"+{len(rest)} more {sum(p for _, p, _ in rest):+.2f}"))
        marks += [{"time": tm, "position": pos, "shape": "circle", "color": GREEN if pos == "aboveBar" else RED,
                   "size": 0.8, "text": text} for _, _, text in rows]
    nav = series("nav", "Baseline", line_data(t, v), 0, lines=lines, legend="NAV", markers=sorted(marks, key=lambda m: m["time"]),
                 options={"baseValue": {"type": "price", "price": base}, "lineWidth": 2, "lineType": 0,
                          "topLineColor": GREEN, "topFillColor1": _rgba(GREEN, 0.30), "topFillColor2": _rgba(GREEN, 0.03),
                          "bottomLineColor": RED, "bottomFillColor1": _rgba(RED, 0.03), "bottomFillColor2": _rgba(RED, 0.30),
                          "priceFormat": fmt, "color": GREEN, "priceLineVisible": True, "crosshairMarkerRadius": 4})
    nav["marker_room"] = True  # pairs often close in the same bar, at a high: room for the whole stack (js/chart.js)
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
            "right_px": RIGHT_PX, "bar_px": OPENING_PX, "fit": True,
            "seconds": bool(len(grid) > 1 and int(grid["t"].iloc[-1] - grid["t"].iloc[0]) <= 6 * HOUR)}

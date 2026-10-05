"""Plotly figures for the dashboard (dark "desk" template from ui/theme.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from cryptopairs.ui import data as D
from cryptopairs.ui.theme import AMBER, BLUE, BORDER, GREEN, LEG_A, LEG_B, MUTED, RED, TEXT, VIOLET

HOUR = 3_600_000

# How each signal is drawn: (symbol, colour, legend name). What-if markers use the open variant.
MARKERS = {
    "long": ("triangle-up", GREEN, "BUY spread"),  # long A / short B
    "short": ("triangle-down", RED, "SELL spread"),  # short A / long B
    "exit": ("circle", TEXT, "CLOSE"),
    "stop": ("x", AMBER, "STOP"),
}


def _t(ms) -> pd.Series:
    return D.to_time(ms)


def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def _markers(fig: go.Figure, m: pd.DataFrame, row: int | None = None, what_if: bool = False, size: int = 12) -> None:
    if m is None or m.empty:
        return
    for kind, (symbol, color, name) in MARKERS.items():
        part = m[m["kind"] == kind]
        if part.empty:
            continue
        tr = go.Scatter(
            x=part["time"], y=part["z"], mode="markers",
            name=("what-if: " if what_if else "") + name, legendgroup=("wi-" if what_if else "") + kind,
            marker=dict(symbol=symbol + ("-open" if what_if else ""), size=size - (2 if what_if else 0), color=color,
                        line=dict(width=1.5 if what_if else 1, color=color if what_if else "#0b0e14")),
            text=part.get("text"), hovertemplate="%{text}<extra></extra>" if "text" in part else None,
        )
        fig.add_trace(tr, row=row, col=1) if row else fig.add_trace(tr)


# ------------------------------------------------------------------ portfolio
def attribution_waterfall(att: pd.DataFrame, capital: float, height: int = 330) -> go.Figure:
    """Capital -> spread PnL (closed, open) -> fees -> funding -> NAV."""
    parts = [("Capital", capital, "absolute"), ("Spread · closed", att["spread closed"].sum(), "relative"),
             ("Spread · open", att["spread open"].sum(), "relative"), ("Fees", att["fees"].sum(), "relative"),
             ("Funding", att["funding"].sum(), "relative"), ("NAV", 0.0, "total")]
    fig = go.Figure(go.Waterfall(
        x=[p[0] for p in parts], y=[p[1] for p in parts], measure=[p[2] for p in parts],
        text=[f"{capital:,.2f}"] + [f"{p[1]:+,.2f}" for p in parts[1:-1]] + [f"{capital + att['total'].sum():,.2f}"],
        textposition="outside", connector=dict(line=dict(color=BORDER, width=1)),
        increasing=dict(marker=dict(color=GREEN)), decreasing=dict(marker=dict(color=RED)),
        totals=dict(marker=dict(color=BLUE)), hovertemplate="%{x}: %{y:+,.4f} USDT<extra></extra>"))
    lo = min(capital, capital + att["total"].sum(), capital + att[["spread closed", "spread open"]].sum().sum())
    hi = max(capital, capital + att["total"].sum(), capital + att[["spread closed", "spread open"]].sum().sum())
    pad = max((hi - lo) * 0.6, capital * 0.002)
    fig.update_yaxes(range=[lo - pad, hi + pad], title_text="USDT")
    fig.update_layout(height=height, showlegend=False, hovermode="closest", margin=dict(l=10, r=10, t=20, b=10))
    return fig


def attribution_bars(att: pd.DataFrame, height: int = 380) -> go.Figure:
    """Per pair: spread PnL (closed / open), fees and funding, with the net total marked."""
    t = att.sort_values("total")
    labels = t["pair"].str.replace("USDT", "").str.replace("_", " / ")
    fig = go.Figure()
    for col, pos, neg, name in (("spread closed", GREEN, RED, "spread · closed"),
                                ("spread open", _rgba(GREEN, 0.45), _rgba(RED, 0.45), "spread · open"),
                                ("fees", MUTED, MUTED, "fees"), ("funding", AMBER, AMBER, "funding")):
        fig.add_trace(go.Bar(y=labels, x=t[col], orientation="h", name=name,
                             marker_color=[pos if v >= 0 else neg for v in t[col]],
                             hovertemplate="%{y}: %{x:+,.4f}<extra>" + name + "</extra>"))
    fig.add_trace(go.Scatter(y=labels, x=t["total"], mode="markers", name="net", hovertemplate="%{y}: net %{x:+,.4f}<extra></extra>",
                             marker=dict(symbol="diamond", size=9, color=TEXT, line=dict(width=1, color="#0b0e14"))))
    fig.add_vline(x=0, line=dict(color=BORDER, width=1))
    fig.update_layout(barmode="relative", height=height, hovermode="closest",
                      legend=dict(y=1.02, yanchor="bottom", x=0, xanchor="left"), margin=dict(l=10, r=10, t=30, b=10))
    return fig


def pnl_calendar(daily: pd.DataFrame, height: int = 260) -> go.Figure:
    """Daily PnL as a calendar: one row per week (Monday first), one column per weekday."""
    df = daily.copy()
    df["day"] = pd.to_datetime(df["day"])
    df["week"] = df["day"] - pd.to_timedelta(df["day"].dt.weekday, unit="D")
    df["dow"] = df["day"].dt.weekday
    weeks = sorted(df["week"].unique())
    z = np.full((len(weeks), 7), np.nan)
    text = np.full((len(weeks), 7), "", dtype=object)
    for r in df.itertuples():
        i = weeks.index(r.week)
        z[i, r.dow] = r.PnL
        text[i, r.dow] = f"{r.day:%d/%m}<br>{r.PnL:+,.2f}"
    lim = float(np.nanmax(np.abs(z))) if np.isfinite(z).any() else 1.0
    lim = lim or 1.0
    fig = go.Figure(go.Heatmap(
        z=z, x=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], y=[f"{pd.Timestamp(w):%d %b}" for w in weeks],
        text=text, texttemplate="%{text}", textfont=dict(size=11), xgap=4, ygap=4, zmid=0, zmin=-lim, zmax=lim,
        colorscale=[[0, RED], [0.5, "#161c28"], [1, GREEN]], showscale=False,
        hovertemplate="%{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False, side="top")
    fig.update_layout(height=max(140, min(height, 70 + 46 * len(weeks))), hovermode="closest",
                      margin=dict(l=10, r=10, t=30, b=10))
    return fig


def corr_heatmap(corr: pd.DataFrame, height: int = 360) -> go.Figure:
    labels = [c.replace("USDT", "").replace("_", "/") for c in corr.columns]
    fig = go.Figure(go.Heatmap(z=corr.to_numpy(), x=labels, y=labels, zmin=-1, zmax=1, zmid=0,
                               colorscale=[[0, RED], [0.5, "#161c28"], [1, GREEN]],
                               text=np.round(corr.to_numpy(), 2), texttemplate="%{text}", textfont=dict(size=10),
                               hovertemplate="%{y} vs %{x}: %{z:.2f}<extra></extra>", colorbar=dict(title="ρ")))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=height, hovermode="closest", margin=dict(l=10, r=10, t=10, b=10))
    return fig


def exposure_chart(ex: pd.DataFrame, height: int = 300) -> go.Figure:
    fig = go.Figure(go.Bar(x=ex.index, y=ex["net"], marker_color=[GREEN if v >= 0 else RED for v in ex["net"]],
                           hovertemplate="%{x}: %{y:+,.2f} USDT<extra></extra>"))
    fig.update_layout(height=height, yaxis_title="USDT (+long / -short)", hovermode="closest",
                      margin=dict(l=10, r=10, t=10, b=10))
    return fig


def exec_chart(ex: pd.DataFrame, height: int = 320) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=ex["time"], y=ex["cost bps"], mode="markers", text=ex["pair"],
        marker=dict(size=10, color=ex["maker %"], colorscale=[[0, RED], [0.5, AMBER], [1, GREEN]], cmin=0, cmax=100,
                    colorbar=dict(title="maker %"), line=dict(width=0)),
        hovertemplate="%{text}<br>cost %{y:.1f} bps<br>maker %{marker.color:.0f}%<extra></extra>"))
    fig.update_layout(height=height, yaxis_title="cost (bps)", hovermode="closest", margin=dict(l=10, r=10, t=10, b=10))
    return fig


# ---------------------------------------------------------------------- lab
def bt_equity(curves: dict[str, tuple[pd.Series, np.ndarray]], colors: dict[str, str], height: int = 380) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    for name, (t, eq) in curves.items():
        if len(eq) == 0:
            continue
        ret = 100 * (eq / eq[0] - 1)
        dd = 100 * (eq / np.maximum.accumulate(eq) - 1)
        c = colors.get(name, BLUE)
        fig.add_trace(go.Scatter(x=t, y=ret, name=name, line=dict(color=c, width=1.8),
                                 hovertemplate="%{y:+.2f}%<extra>" + name + "</extra>"), 1, 1)
        fig.add_trace(go.Scatter(x=t, y=dd, name=name + " DD", line=dict(color=c, width=1), showlegend=False,
                                 hovertemplate="%{y:.2f}%<extra>" + name + " DD</extra>"), 2, 1)
    fig.add_hline(y=0, line=dict(color=BORDER, width=1), row=1, col=1)
    fig.update_yaxes(title_text="return %", row=1, col=1)
    fig.update_yaxes(title_text="DD %", row=2, col=1)
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10))
    return fig


def bt_signals(t: pd.Series, z: np.ndarray, prices: tuple[pd.Series, np.ndarray, np.ndarray, str, str],
               markers: pd.DataFrame, ze: float, zx: float, height: int = 560) -> go.Figure:
    """Prices (rebased) on top, the Z the strategy saw below, with every entry and exit."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.38, 0.62], vertical_spacing=0.05)
    pt, pa, pb, a, b = prices
    fig.add_trace(go.Scatter(x=pt, y=100 * pa / pa[0], name=a, line=dict(color=LEG_A, width=1.4)), 1, 1)
    fig.add_trace(go.Scatter(x=pt, y=100 * pb / pb[0], name=b, line=dict(color=LEG_B, width=1.4)), 1, 1)
    fig.add_trace(go.Scatter(x=t, y=z, name="Z (strategy view)", line=dict(color=VIOLET, width=1.2)), 2, 1)
    for sgn in (1, -1):
        fig.add_hline(y=sgn * ze, line=dict(color=RED, dash="dash", width=1.1), row=2, col=1)
        fig.add_hline(y=sgn * zx, line=dict(color=GREEN, dash="dot", width=1.1), row=2, col=1)
    _markers(fig, markers, row=2, size=10)
    fig.update_yaxes(title_text="rebased", row=1, col=1)
    fig.update_yaxes(title_text="Z", row=2, col=1)
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10))
    return fig


def surface_heatmap(df: pd.DataFrame, marks: dict[str, tuple[float, float]], height: int = 360) -> go.Figure:
    piv = df.pivot(index="z_exit", columns="z_entry", values="Sharpe")
    lim = float(np.nanmax(np.abs(piv.to_numpy()))) if np.isfinite(piv.to_numpy()).any() else 1.0
    fig = go.Figure(go.Heatmap(
        z=piv.to_numpy(), x=[f"{v:g}" for v in piv.columns], y=[f"{v:g}" for v in piv.index],
        colorscale=[[0, RED], [0.5, "#161c28"], [1, GREEN]], zmid=0, zmin=-lim, zmax=lim,
        colorbar=dict(title="Sharpe"), hovertemplate="z_entry %{x} / z_exit %{y}<br>Sharpe %{z:.2f}<extra></extra>",
        text=np.round(piv.to_numpy(), 2), texttemplate="%{text}", textfont=dict(size=10)))
    for name, (ze, zx) in marks.items():
        if f"{ze:g}" in fig.data[0].x and f"{zx:g}" in fig.data[0].y:
            fig.add_trace(go.Scatter(x=[f"{ze:g}"], y=[f"{zx:g}"], mode="markers", name=name,
                                     marker=dict(symbol="square-open", size=26, line=dict(width=2.5),
                                                 color=AMBER if name.startswith("what") else TEXT)))
    fig.update_layout(height=height, xaxis_title="z_entry", yaxis_title="z_exit", hovermode="closest",
                      margin=dict(l=10, r=10, t=30, b=10))
    return fig


# --------------------------------------------------------------- live (intraday)
def _with_gaps(df: pd.DataFrame, max_gap_ms: int = 30_000) -> pd.DataFrame:
    """Insert an empty row where marks are missing (bot down), so lines break instead of bridging it."""
    if len(df) < 2:
        return df
    gaps = df["ts"].diff() > max_gap_ms
    if not gaps.any():
        return df
    blank = pd.DataFrame({"ts": df.loc[gaps, "ts"] - 1})  # just before the first mark after the gap
    return pd.concat([df, blank]).sort_values("ts", kind="stable", ignore_index=True)  # other columns: NaN

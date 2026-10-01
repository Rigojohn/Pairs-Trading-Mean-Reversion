"""Plotly figures for the dashboard (dark "desk" template from ui/theme.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from cryptopairs.ui.theme import AMBER, BLUE, BORDER, CYAN, FAINT, GREEN, LEG_A, LEG_B, MUTED, RED, TEXT, VIOLET

HOUR = 3_600_000

# How each signal is drawn: (symbol, colour, legend name). What-if markers use the open variant.
MARKERS = {
    "long": ("triangle-up", GREEN, "BUY spread (long A / short B)"),
    "short": ("triangle-down", RED, "SELL spread (short A / long B)"),
    "exit": ("circle", TEXT, "CLOSE"),
    "stop": ("x", AMBER, "STOP"),
}


def _t(ms) -> pd.Series:
    return pd.to_datetime(ms, unit="ms")


def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def _shade_positions(fig: go.Figure, spans: list[tuple], rows: str | int = "all") -> None:
    for x0, x1, side in spans:
        fig.add_vrect(x0=x0, x1=x1, fillcolor=GREEN if side > 0 else RED, opacity=0.06, line_width=0, row=rows, col=1)


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
def equity_chart(curve: pd.Series, capital: float, height: int = 380) -> go.Figure:
    dd = 100 * (curve / curve.cummax() - 1)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.74, 0.26], vertical_spacing=0.04)
    up = curve.iloc[-1] >= capital
    fig.add_trace(go.Scatter(x=curve.index, y=curve, name="equity", line=dict(color=GREEN if up else RED, width=2),
                             hovertemplate="%{y:,.2f}<extra>equity</extra>"), 1, 1)
    fig.add_hline(y=capital, line=dict(color=MUTED, dash="dot", width=1), row=1, col=1,
                  annotation_text="capital", annotation_font_color=MUTED, annotation_position="bottom left")
    fig.add_trace(go.Scatter(x=dd.index, y=dd, name="drawdown %", fill="tozeroy", fillcolor=_rgba(RED, 0.18),
                             line=dict(color=RED, width=1), hovertemplate="%{y:.2f}%<extra>drawdown</extra>"), 2, 1)
    fig.update_yaxes(title_text="USDT", row=1, col=1)
    fig.update_yaxes(title_text="DD %", row=2, col=1)
    fig.update_layout(height=height, showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
    return fig


def pnl_by_pair(rows: pd.DataFrame, height: int = 380) -> go.Figure:
    t = rows.sort_values("PnL")
    open_pnl = t["unrealized"]
    realized = t["PnL"] - open_pnl  # closed trades plus fees/funding already paid on the open one
    fig = go.Figure()
    fig.add_trace(go.Bar(y=t["pair"], x=realized, orientation="h", name="realized (incl. fees, funding)",
                         marker_color=[_rgba(GREEN if v >= 0 else RED, 0.85) for v in realized]))
    fig.add_trace(go.Bar(y=t["pair"], x=open_pnl, orientation="h", name="open (unrealized)",
                         marker_color=[_rgba(GREEN if v >= 0 else RED, 0.35) for v in open_pnl],
                         marker_line=dict(color=[GREEN if v >= 0 else RED for v in open_pnl], width=1)))
    fig.update_layout(barmode="relative", height=height, hovermode="y unified", xaxis_title="USDT",
                      margin=dict(l=10, r=10, t=30, b=10))
    return fig


def exposure_chart(ex: pd.DataFrame, height: int = 300) -> go.Figure:
    fig = go.Figure(go.Bar(x=ex.index, y=ex["net"], marker_color=[GREEN if v >= 0 else RED for v in ex["net"]],
                           hovertemplate="%{x}: %{y:+,.2f} USDT<extra></extra>"))
    fig.update_layout(height=height, yaxis_title="USDT (+long / -short)", hovermode="closest",
                      margin=dict(l=10, r=10, t=10, b=10))
    return fig


# ----------------------------------------------------------------- one pair
def prices_chart(fit: pd.DataFrame, live: pd.DataFrame, a: str, b: str, spans: list[tuple],
                 height: int = 330) -> go.Figure:
    """Chart A: both legs rebased to 100 at the start of the window (hourly closes + live mids)."""
    fig = go.Figure()
    if not fit.empty:
        t = _t(fit["bar_ts"] + HOUR)
        base_a, base_b = np.exp(fit["log_a"].iloc[0]), np.exp(fit["log_b"].iloc[0])
        fig.add_trace(go.Scatter(x=t, y=100 * np.exp(fit["log_a"]) / base_a, name=a, line=dict(color=LEG_A, width=1.8)))
        fig.add_trace(go.Scatter(x=t, y=100 * np.exp(fit["log_b"]) / base_b, name=b, line=dict(color=LEG_B, width=1.8)))
        if not live.empty:
            tl = _t(live["ts"])
            fig.add_trace(go.Scatter(x=tl, y=100 * live["mid_a"] / base_a, name=f"{a} live", mode="lines",
                                     line=dict(color=LEG_A, width=1, dash="dot"), showlegend=False))
            fig.add_trace(go.Scatter(x=tl, y=100 * live["mid_b"] / base_b, name=f"{b} live", mode="lines",
                                     line=dict(color=LEG_B, width=1, dash="dot"), showlegend=False))
    _shade_positions(fig, spans, rows=None)
    fig.update_layout(height=height, yaxis_title="rebased = 100", margin=dict(l=10, r=10, t=30, b=10))
    return fig


def z_chart(fit: pd.DataFrame, live: pd.DataFrame, spans: list[tuple], trades: pd.DataFrame,
            in_use: tuple[float, float], what_if: tuple[float, float] | None, sim: pd.DataFrame | None,
            max_entry_z: float | None, height: int = 420) -> go.Figure:
    """Chart B: Z-score with entry/exit bands, the bot's trades and the what-if signals."""
    fig = go.Figure()
    if not fit.empty:
        fig.add_trace(go.Scatter(x=_t(fit["bar_ts"] + HOUR), y=fit["z"], name="Z (hourly model)",
                                 line=dict(color=VIOLET, width=1.6)))
    if not live.empty:
        fig.add_trace(go.Scatter(x=_t(live["ts"]), y=(live["z_buy"] + live["z_sell"]) / 2, name="Z live (fit in use)",
                                 mode="lines", line=dict(color=CYAN, width=1)))
    ze, zx = in_use
    for sgn in (1, -1):
        fig.add_hline(y=sgn * ze, line=dict(color=RED, dash="dash", width=1.2))
        fig.add_hline(y=sgn * zx, line=dict(color=GREEN, dash="dot", width=1.2))
    fig.add_annotation(xref="paper", x=1, y=ze, text=f"entry ±{ze:g}", showarrow=False, xanchor="right",
                       yanchor="bottom", font=dict(color=RED, size=10))
    fig.add_annotation(xref="paper", x=1, y=-zx, text=f"exit ±{zx:g}", showarrow=False, xanchor="right",
                       yanchor="top", font=dict(color=GREEN, size=10))
    if what_if and tuple(what_if) != tuple(in_use):
        wze, wzx = what_if
        for sgn in (1, -1):
            fig.add_hline(y=sgn * wze, line=dict(color=AMBER, dash="longdash", width=1.4))
            fig.add_hline(y=sgn * wzx, line=dict(color=AMBER, dash="dot", width=1))
        fig.add_annotation(xref="paper", x=0, y=wze, text=f"what-if entry ±{wze:g} / exit ±{wzx:g}", showarrow=False,
                           xanchor="left", yanchor="bottom", font=dict(color=AMBER, size=10))
    if max_entry_z:
        for sgn in (1, -1):
            fig.add_hline(y=sgn * max_entry_z, line=dict(color=FAINT, dash="dashdot", width=1))
    fig.add_hline(y=0, line=dict(color=BORDER, width=1))
    _shade_positions(fig, spans, rows=None)
    _markers(fig, trades)
    _markers(fig, sim, what_if=True)
    zmax = np.nanmax(np.abs(fit["z"])) if not fit.empty and fit["z"].notna().any() else 3.0
    lim = max(ze, (what_if or in_use)[0]) * 1.25
    fig.update_yaxes(range=[-max(zmax, lim) * 1.08, max(zmax, lim) * 1.08], title_text="Z")
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10))
    return fig


def model_chart(fit: pd.DataFrame, live: pd.DataFrame, z_entry: float, z_exit: float, spans: list[tuple],
                height: int = 560) -> go.Figure:
    """Spread with its bands, and the hedge ratio and sigma: model now vs the fit in use."""
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.42, 0.29, 0.29],
                        subplot_titles=("Spread u = log A - α - β·log B, with entry/exit bands",
                                        "Hedge ratio β: model now vs in use", "σ of the spread"))
    if not fit.empty:
        t = _t(fit["bar_ts"] + HOUR)
        sig = fit["kf_sigma"]
        for k, color, dash in ((z_entry, RED, "dash"), (z_exit, GREEN, "dot")):
            for sgn in (1, -1):
                fig.add_trace(go.Scatter(x=t, y=sgn * k * sig, mode="lines", line=dict(color=color, dash=dash, width=1),
                                         hoverinfo="skip", showlegend=False), 1, 1)
        fig.add_trace(go.Scatter(x=t, y=fit["spread_u"], name="spread (model)", line=dict(color=VIOLET)), 1, 1)
        fig.add_trace(go.Scatter(x=t, y=fit["kf_beta"], name="β model", line=dict(color=BLUE)), 2, 1)
        fig.add_trace(go.Scatter(x=t, y=fit["kf_sigma"], name="σ model", line=dict(color=BLUE)), 3, 1)
    if not live.empty:
        tl = _t(live["ts"])
        fig.add_trace(go.Scatter(x=tl, y=live["spread_u"], mode="lines", name="spread live (fit in use)",
                                 line=dict(color=CYAN, width=1)), 1, 1)
        fig.add_trace(go.Scatter(x=tl, y=live["beta"], mode="lines", name="β in use", line=dict(color=AMBER, shape="hv")), 2, 1)
        fig.add_trace(go.Scatter(x=tl, y=live["sigma"], mode="lines", name="σ in use", line=dict(color=AMBER, shape="hv")), 3, 1)
    fig.add_hline(y=0, line=dict(color=BORDER, width=1), row=1, col=1)
    _shade_positions(fig, spans)
    fig.update_annotations(font=dict(color=MUTED, size=11))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10))
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

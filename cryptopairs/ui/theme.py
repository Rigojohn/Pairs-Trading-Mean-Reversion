"""Colours, the Plotly template and the CSS of the dark dashboard theme."""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

BG, PANEL, PANEL_2, BORDER = "#0b0e14", "#131925", "#171e2c", "#232b3a"
TEXT, MUTED, FAINT = "#d7dce5", "#8b93a5", "#4b5468"
GREEN, RED, BLUE, AMBER, VIOLET, CYAN = "#2ebd85", "#f6465d", "#5b9cff", "#f0b429", "#a78bfa", "#22d3ee"
GRID = "#1a2130"
LEG_A, LEG_B = BLUE, AMBER  # the two assets of a pair, everywhere

pio.templates["desk"] = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT, size=12),
        colorway=[BLUE, AMBER, GREEN, RED, VIOLET, CYAN, "#f472b6", "#94a3b8"],
        xaxis=dict(gridcolor=GRID, zerolinecolor=BORDER, linecolor=BORDER, showspikes=True, automargin=True,
                   spikecolor=MUTED, spikethickness=1, spikedash="dot", spikemode="across"),
        yaxis=dict(gridcolor=GRID, zerolinecolor=BORDER, linecolor=BORDER, automargin=True, title=dict(standoff=8)),
        hoverlabel=dict(bgcolor=PANEL_2, bordercolor=BORDER, font=dict(color=TEXT)),
        legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.02, yanchor="bottom", x=0, xanchor="left",
                    font=dict(color=MUTED, size=11)),
        modebar=dict(bgcolor="rgba(0,0,0,0)", color=MUTED, activecolor=AMBER),
        margin=dict(l=10, r=10, t=36, b=10),
        hovermode="x unified",
    )
)
pio.templates.default = "desk"

CSS = f"""
<style>
.block-container {{ padding-top: 4.6rem; padding-bottom: 2rem; max-width: 100%; }}
[data-testid="stSkillsNudge"] {{ display: none !important; }}
/* KPI cards: an even grid (4 per row, 8 on wide screens) instead of squeezed columns */
[class*="st-key-kpi"] {{ display: grid !important; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: .6rem !important; }}
[class*="st-key-kpi"] > [data-testid="stElementContainer"] {{ width: auto !important; min-width: 0 !important; }}
[class*="st-key-kpi"] > [data-testid="stElementContainer"] > div,
[class*="st-key-kpi"] [data-testid="stMetric"] {{ height: 100%; }}
.st-key-kpi_overview, .st-key-kpi_pair, .st-key-kpi_fund, .st-key-kpi_book {{
  grid-template-columns: repeat(4, minmax(0, 1fr)) !important; }}
@media (min-width: 1400px) {{
  .st-key-kpi_fund, .st-key-kpi_book {{ grid-template-columns: repeat(8, minmax(0, 1fr)) !important; }}
  .st-key-kpi_fund [data-testid="stMetric"], .st-key-kpi_book [data-testid="stMetric"] {{ min-height: 92px; }}
  .st-key-kpi_fund [data-testid="stMetricValue"], .st-key-kpi_book [data-testid="stMetricValue"] {{ font-size: 1.25rem; }}
}}
@media (min-width: 1820px) {{
  .st-key-kpi_overview, .st-key-kpi_pair {{ grid-template-columns: repeat(8, minmax(0, 1fr)) !important; }}
}}
/* The fund row reads as one band: NAV numbers first, the book's risk second */
.st-key-kpi_book {{ margin-top: -.15rem; }}
[data-testid="stMetric"] {{ min-height: 104px; }}
[data-testid="stMetricLabel"] p {{ white-space: normal; line-height: 1.25; }}
[data-testid="stMetricDelta"], [data-testid="stMetricDelta"] div {{ white-space: normal !important;
  overflow: visible !important; text-overflow: clip !important; }}
.down {{ background: rgba(246,70,93,.12); border: 1px solid rgba(246,70,93,.55); border-radius: 8px;
         padding: 12px 16px; margin-bottom: 12px; color: {TEXT}; font-size: .92rem; line-height: 1.5; }}
.down b {{ color: {RED}; }}
.down code {{ background: rgba(0,0,0,.35); padding: 1px 6px; border-radius: 4px; font-size: .84rem; }}
/* Live updates without the "reloading" look: Streamlit fades elements being recomputed
   (opacity .33 after 0.5 s) and shows a running badge; a live screen should just tick. */
[data-stale="true"] {{ opacity: 1 !important; transition: none !important; }}
[data-testid="stStatusWidgetRunningIcon"], [data-testid="stStatusWidgetRunningManIcon"],
[data-testid="stStatusWidget"] button {{ display: none !important; }}
#cp-watchdog {{ position: fixed; top: 16px; right: 64px; z-index: 999990; font-size: .76rem; font-weight: 600;
  letter-spacing: .03em; padding: 4px 11px; border-radius: 999px; border: 1px solid {BORDER};
  background: {PANEL}; font-variant-numeric: tabular-nums; pointer-events: none; }}
#cp-watchdog.ok {{ color: {GREEN}; border-color: rgba(46,189,133,.45); }}
#cp-watchdog.warn {{ color: {AMBER}; border-color: rgba(240,180,41,.55); }}
#cp-watchdog.bad {{ color: #fff; background: {RED}; border-color: {RED}; }}
#cp-watchdog.mute {{ color: {MUTED}; }}
[data-testid="stSkeleton"] {{ display: none; }}
[data-testid="stMetric"] {{
  background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 14px 8px 14px;
}}
[data-testid="stMetricLabel"] p {{ color: {MUTED}; font-size: 0.72rem; letter-spacing: .06em; text-transform: uppercase; }}
[data-testid="stMetricValue"] {{ font-size: 1.45rem; font-variant-numeric: tabular-nums; }}
[data-testid="stMetricDelta"] {{ font-variant-numeric: tabular-nums; }}
[data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
.desk-title {{ font-size: 1.35rem; font-weight: 650; letter-spacing: .01em; margin: 0; }}
.desk-sub {{ color: {MUTED}; font-size: .82rem; margin: 2px 0 10px 0; }}
.desk-h {{ color: {MUTED}; font-size: .74rem; letter-spacing: .08em; text-transform: uppercase;
          border-bottom: 1px solid {BORDER}; padding-bottom: 4px; margin: 14px 0 8px 0; }}
.pill {{ display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600;
        letter-spacing: .04em; margin-right: 6px; border: 1px solid {BORDER}; }}
.pill-ok {{ color: {GREEN}; background: rgba(46,189,133,.10); border-color: rgba(46,189,133,.35); }}
.pill-bad {{ color: {RED}; background: rgba(246,70,93,.10); border-color: rgba(246,70,93,.35); }}
.pill-warn {{ color: {AMBER}; background: rgba(240,180,41,.10); border-color: rgba(240,180,41,.35); }}
.pill-info {{ color: {BLUE}; background: rgba(91,156,255,.10); border-color: rgba(91,156,255,.35); }}
.pill-mute {{ color: {MUTED}; }}
.strip {{ display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: center; background: {PANEL};
         border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px; margin-bottom: 12px;
         font-variant-numeric: tabular-nums; font-size: .86rem; }}
.strip b {{ color: {TEXT}; font-weight: 600; }}
.strip span.k {{ color: {MUTED}; margin-right: 6px; font-size: .74rem; text-transform: uppercase; letter-spacing: .05em; }}
.pos {{ color: {GREEN}; }} .neg {{ color: {RED}; }}
.params td, .params th {{ padding: 4px 12px; border-bottom: 1px solid {BORDER}; font-variant-numeric: tabular-nums; }}
.params th {{ color: {MUTED}; font-weight: 500; text-align: left; font-size: .78rem; }}
.params {{ border-collapse: collapse; width: 100%; font-size: .88rem; }}
.params td.diff {{ color: {AMBER}; font-weight: 600; }}
/* Overview: the performance line above the NAV chart */
.perf-head {{ display: grid; grid-template-columns: 1.6fr repeat(5, 1fr); gap: 0; background: {PANEL};
  border: 1px solid {BORDER}; border-radius: 10px; margin: 2px 0 10px 0; font-variant-numeric: tabular-nums; }}
.perf-head .ph {{ display: flex; flex-direction: column; gap: 2px; padding: 12px 16px; border-left: 1px solid {BORDER}; }}
.perf-head .ph:first-child {{ border-left: none; }}
.perf-head .k {{ color: {MUTED}; font-size: .7rem; letter-spacing: .07em; text-transform: uppercase; }}
.perf-head b {{ font-size: 1.3rem; font-weight: 650; color: {TEXT}; line-height: 1.25; }}
.perf-head .ph.main b {{ font-size: 1.9rem; letter-spacing: -.01em; }}
.perf-head .s {{ color: {MUTED}; font-size: .78rem; }}
.perf-head .pos {{ color: {GREEN}; }} .perf-head .neg {{ color: {RED}; }}
@media (max-width: 1300px) {{ .perf-head {{ grid-template-columns: repeat(3, 1fr); }}
  .perf-head .ph:nth-child(4) {{ border-left: none; }} .perf-head .ph {{ border-top: 1px solid {BORDER}; }}
  .perf-head .ph:nth-child(-n+3) {{ border-top: none; }} }}
/* Position card: equity on a scale from the catastrophe stop to above the entry equity */
.riskbar {{ position: relative; height: 24px; background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px;
  margin: 12px 0 26px 0; }}
.rb-fill {{ position: absolute; left: 0; top: 0; bottom: 0; opacity: .28; border-radius: 6px 0 0 6px; }}
.rb-tick {{ position: absolute; top: -5px; bottom: -5px; border-left: 2px solid; }}
.rb-tick em {{ position: absolute; top: 100%; left: 0; transform: translateX(-50%); font-size: .68rem; font-style: normal;
  white-space: nowrap; margin-top: 3px; letter-spacing: .04em; text-transform: uppercase; }}
.rb-now {{ position: absolute; top: 50%; transform: translate(-50%, -50%); font-size: .74rem; font-weight: 700;
  background: {BG}; color: {TEXT}; padding: 0 6px; border-radius: 4px; border: 1px solid {BORDER};
  font-variant-numeric: tabular-nums; z-index: 2; }}
</style>
"""


def apply() -> None:
    st.html(CSS)


def signed(v: float, fmt: str = "{:+,.2f}") -> str:
    """HTML number coloured by sign."""
    if v is None or v != v:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{fmt.format(v)}</span>'


def pill(text: str, kind: str = "mute") -> str:
    return f'<span class="pill pill-{kind}">{text}</span>'


def heading(text: str) -> None:
    st.html(f'<div class="desk-h">{text}</div>')


def page_title(title: str, subtitle: str = "") -> None:
    st.html(f'<p class="desk-title">{title}</p>' + (f'<p class="desk-sub">{subtitle}</p>' if subtitle else ""))

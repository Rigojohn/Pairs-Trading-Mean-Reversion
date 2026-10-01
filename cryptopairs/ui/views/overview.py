"""Overview: the fund at a glance. KPIs, equity and drawdown, open book, signal monitor, exposure."""

import numpy as np
import streamlit as st

from cryptopairs.ui import charts as C
from cryptopairs.ui import data as D
from cryptopairs.ui import live as LIVE
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import AMBER, GREEN, RED, heading, page_title

ctx = W.context()
page_title("Portfolio overview", "Live book of the paper/live bot · every number comes from its state database")


def _color_sign(v):
    if isinstance(v, (int, float)) and np.isfinite(v):
        return f"color: {GREEN}" if v > 0 else (f"color: {RED}" if v < 0 else "")
    return ""


def _color_stop(v):
    if isinstance(v, (int, float)) and np.isfinite(v):
        return f"color: {RED}; font-weight: 600" if v < 5 else (f"color: {AMBER}" if v < 10 else "")
    return ""


@st.fragment(run_every=ctx.refresh or None)
def live() -> None:
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if d["pairs"].empty:
        st.info("The bot has not saved any pair yet.")
        return
    cfg = D.session_cfg(d)
    capital = float(d["pairs"]["capital"].sum())
    rows = D.pair_rows(d, cfg)
    curve = D.equity_series(d)
    W.status_strip(d, ctx, curve, rows, capital)
    W.kpi_row(d, ctx, curve, rows, capital, cfg)
    view = rows[rows["pair"].isin(ctx.view_pairs)]

    left, right = st.columns([3, 2], gap="medium")
    with left:
        heading(f"Equity & drawdown · {ctx.window}")
        if len(curve) > 1:
            LIVE.chart(C.equity_chart(curve, capital), key="ov_equity", rev=f"equity-{ctx.window}")
        else:
            st.info("The curve appears a few seconds after the bot starts.")
    with right:
        heading("PnL by pair · realized + open")
        LIVE.chart(C.pnl_by_pair(view), key="ov_pnl")

    heading("Open positions · live marks")
    book = view[view["side"] != 0]
    if book.empty:
        st.caption("No open positions.")
    else:
        cols = ["pair", "position", "since", "hours", "qty A", "entry A", "mark A", "qty B", "entry B", "mark B",
                "notional", "beta", "spread entry", "spread now", "Z at entry", "Z now", "to exit", "unrealized",
                "unrealized %", "to stop %", "to catastrophe %", "funding", "status"]
        fmt = {"hours": "{:.1f}", "qty A": "{:,.4g}", "qty B": "{:,.4g}", "entry A": "{:.6g}", "mark A": "{:.6g}",
               "entry B": "{:.6g}", "mark B": "{:.6g}", "notional": "{:,.2f}", "beta": "{:.4f}",
               "spread entry": "{:+.4f}", "spread now": "{:+.4f}", "Z at entry": "{:+.2f}", "Z now": "{:+.2f}",
               "to exit": "{:+.2f}", "unrealized": "{:+,.4f}", "unrealized %": "{:+.2f}%", "to stop %": "{:.1f}%",
               "to catastrophe %": "{:.1f}%", "funding": "{:+.4f}"}
        st.dataframe(
            book[cols].style.format(fmt, na_rep="")
            .map(_color_sign, subset=["unrealized", "unrealized %", "funding"])
            .map(_color_stop, subset=["to stop %", "to catastrophe %"]),
            width="stretch", hide_index=True, key="ov_book",
            column_config={"since": st.column_config.DatetimeColumn("since (UTC)", format="MM-DD HH:mm"),
                           "position": st.column_config.TextColumn("side")})
        st.caption("Spread = log A − α − β·log B with the fit frozen at entry. 'to exit': Z distance still to travel "
                   "to the exit band. 'to stop %': equity headroom before the stop-loss, as % of entry equity.")

    a, b = st.columns([3, 2], gap="medium")
    with a:
        heading("Signal monitor · all pairs")
        cols = ["pair", "position", "Z now", "Z entry", "Z exit", "to entry", "equity", "PnL", "PnL %", "trades",
                "wins", "status"]
        st.dataframe(
            view[cols].style.format({"Z now": "{:+.2f}", "Z entry": "{:.2f}", "Z exit": "{:.2f}", "to entry": "{:+.2f}",
                                     "equity": "{:,.2f}", "PnL": "{:+,.2f}", "PnL %": "{:+.2f}%"}, na_rep="")
            .map(_color_sign, subset=["PnL", "PnL %"])
            .map(lambda v: f"color: {AMBER}; font-weight: 600" if isinstance(v, float) and v <= 0.25 else "",
                 subset=["to entry"]),
            width="stretch", hide_index=True, height=min(38 + 35 * len(view), 640), key="ov_signals")
        st.caption("'to entry' ≤ 0 = an entry signal now (taken at the next hourly close if the filters agree); "
                   "amber = within 0.25 of a signal.")
    with b:
        heading("Net exposure by coin")
        ex = D.exposure(view)
        if ex.empty:
            st.caption("No open positions.")
        else:
            LIVE.chart(C.exposure_chart(ex), key="ov_exposure")
            st.caption(f"Gross {ex['gross'].sum():,.2f} USDT · net {ex['net'].sum():+,.2f} USDT "
                       f"(pairs are near dollar-neutral only when β ≈ 1).")


live()

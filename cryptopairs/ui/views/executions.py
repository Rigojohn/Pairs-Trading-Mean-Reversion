"""Execution quality: maker share, cost and time per execution (maker-first vs all-taker)."""

import streamlit as st

from cryptopairs.ui import charts as C
from cryptopairs.ui import data as D
from cryptopairs.ui import live as LIVE
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import heading, page_title

ctx = W.context()
page_title("Execution quality", "Every open/close the bot executed: how much was filled as maker, what it cost vs mid")


@st.fragment(run_every=ctx.refresh or None)
def live() -> None:
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if d["pairs"].empty:
        st.info("The bot has not saved any pair yet.")
        return
    cfg = D.session_cfg(d)
    rows = D.pair_rows(d, cfg)
    W.status_strip(d, ctx, D.equity_series(d), rows, float(d["pairs"]["capital"].sum()))
    ex = D.executions(d["events"])
    if not ex.empty:
        ex = ex[ex["pair"].isin(ctx.view_pairs)]
    if ex.empty:
        st.caption("No executions yet.")
        return
    calm = ex[~ex["urgent"]]
    c = st.columns(6)
    c[0].metric("Executions", f"{len(ex)}", f"{int(ex['urgent'].sum())} urgent (stops)", delta_color="off")
    c[1].metric("Maker share", f"{calm['maker %'].mean():.0f}%" if len(calm) else "—",
                help="share of non-urgent notional filled as maker (post-only)")
    c[2].metric("Avg cost", f"{ex['cost bps'].mean():.1f} bps", help="fees + price vs mid; all-taker is ~10-15 bps")
    c[3].metric("Avg fees", f"{ex['fee bps'].mean():.1f} bps", help="taker 5 bps, maker 2 bps")
    c[4].metric("Median time", f"{ex['seconds'].median():.0f} s", f"max {ex['seconds'].max():.0f} s", delta_color="off")
    c[5].metric("Cost saved vs taker", f"{max(0.0, 12.5 - ex['cost bps'].mean()):.1f} bps",
                help="vs ~12.5 bps for two market orders (5 bps fee + half spread + slippage); indicative")
    left, right = st.columns([3, 2], gap="medium")
    with left:
        heading("Cost per execution (colour = maker share)")
        LIVE.chart(C.exec_chart(ex), key="ex_cost")
    with right:
        heading("By pair")
        per = ex.groupby("pair")[["maker %", "cost bps", "fee bps", "seconds"]].mean().round(1)
        per["executions"] = ex.groupby("pair").size()
        st.dataframe(per.sort_values("cost bps"), width="stretch", key="ex_by_pair")
    heading("All executions")
    st.dataframe(ex.iloc[::-1].style.format({"maker %": "{:.0f}%", "cost bps": "{:.1f}", "fee bps": "{:.1f}",
                                             "seconds": "{:.0f}"}),
                 width="stretch", hide_index=True, key="ex_all",
                 column_config={"time": st.column_config.DatetimeColumn("time (UTC)", format="MM-DD HH:mm:ss")})


live()

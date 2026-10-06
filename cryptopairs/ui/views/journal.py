"""Trade journal: every trade of the selected bot, open and closed, with filters."""

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.ui import data as D
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import GREEN, RED, page_title

ctx = W.context()
page_title("Trade journal", f"Every trade of the bot, newest first · fees and funding: + received, − paid · times "
                            f"{D.TZ_LABEL}")

PERIODS = {"All": None, "Today": 0, "7 days": 7, "30 days": 30}


def _filtered(j: pd.DataFrame, labels: dict) -> pd.DataFrame:
    f = st.container(horizontal=True, gap="medium", vertical_alignment="bottom")
    pairs = f.multiselect("Pair", sorted(j["pair"].unique()), key="jr_pairs", format_func=lambda p: labels.get(p, p),
                          placeholder="all pairs", width=260)
    side = f.segmented_control("Side", ["All", "LONG", "SHORT"], default="All", key="jr_side", required=True)
    status = f.segmented_control("Status", ["All", "Open", "Closed"], default="All", key="jr_status", required=True)
    result = f.segmented_control("Result", ["All", "Win", "Loss"], default="All", key="jr_result", required=True,
                                 help="by PnL: closed trades by their result, open ones by their PnL now")
    exits = f.multiselect("Exit reason", sorted(j["exit reason"].unique()), key="jr_exit", placeholder="any",
                          width=230)
    origin = f.segmented_control("Origin", ["All", "signal", "manual"], default="All", key="jr_origin", required=True,
                                 help="who opened it: the strategy's signal or you from the order ticket")
    period = f.segmented_control("Entered", list(PERIODS), default="All", key="jr_period", required=True)
    if pairs:
        j = j[j["pair"].isin(pairs)]
    if side and side != "All":
        j = j[j["side"] == side]
    if status and status != "All":
        j = j[j["status"] == status.lower()]
    if result == "Win":
        j = j[j["PnL"] > 0]
    elif result == "Loss":
        j = j[j["PnL"] < 0]
    if exits:
        j = j[j["exit reason"].isin(exits)]
    if origin and origin != "All":
        j = j[j["origin"] == origin]
    days = PERIODS.get(period)
    if days is not None:
        start = (D.to_time(D.day_start_ms()) - pd.Timedelta(days=days)).date()
        j = j[j["entry_day"] >= start]
    return j


def _colour(v):
    if isinstance(v, (int, float)) and np.isfinite(v) and v != 0:
        return f"color: {GREEN}" if v > 0 else f"color: {RED}"
    return ""


@st.fragment(run_every=W.every(ctx.refresh))
def live() -> None:
    W.pulse(ctx.db_path, ctx.refresh, "pulse_journal_live")
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if d["pairs"].empty:
        st.info("The bot has not saved any pair yet.")
        return
    rows = D.pair_rows(d, D.session_cfg(d))
    realtime = D.query(str(ctx.db_path), "SELECT ts, pair_id FROM events WHERE kind='realtime_exit'")
    j = D.journal(d, rows, realtime)
    j = j[j["pair"].isin(ctx.view_pairs)]
    if j.empty:
        st.caption("No trades yet.")
        return
    labels = {r["pair"]: f"{r['a']}/{r['b']}" for _, r in rows.iterrows()}
    j = _filtered(j, labels)
    closed = j[j["status"] == "closed"]
    won, lost = int((closed["PnL"] > 0).sum()), int((closed["PnL"] < 0).sum())
    tot = lambda x: round(float(x.sum()), 2) + 0.0  # noqa: E731 - never "-0.00"
    st.caption(f"**{len(j)}** trades · {int((j['status'] == 'open').sum())} open · closed: {won} won, {lost} lost · "
               f"realized PnL **{tot(closed['PnL']):+,.2f}** USDT (fees {tot(closed['fees']):+,.2f}, "
               f"funding {tot(closed['funding']):+,.2f}) · open PnL now "
               f"{tot(j.loc[j['status'] == 'open', 'PnL']):+,.2f}")
    if j.empty:
        st.caption("No trade matches these filters.")
        return
    view = j.drop(columns=["entry_day"]).assign(pair=j["pair"].map(lambda p: labels.get(p, p)))
    st.dataframe(
        view.style.format({"hours": "{:.1f}", "Z entry": "{:+.2f}", "Z exit / now": "{:+.2f}", "PnL": "{:+,.4f}",
                           "PnL %": "{:+.2f}%", "fees": "{:+.4f}", "funding": "{:+.4f}"}, na_rep="—")
        .map(_colour, subset=["PnL", "PnL %", "funding"])
        .map(lambda v: "color: #f0b429" if v == "manual" else "", subset=["origin"])
        .map(lambda v: "color: #5b9cff; font-weight: 600" if v == "open" else "", subset=["status"]),
        width="stretch", hide_index=True, placeholder="", key="jr_table", height=min(38 + 35 * len(view), 720),
        column_config={
            "id": st.column_config.NumberColumn("#", width="small"),
            "entry": st.column_config.DatetimeColumn("entry", format="DD/MM HH:mm", width=95),
            "exit": st.column_config.DatetimeColumn("exit", format="DD/MM HH:mm", width=95),
            "hours": st.column_config.NumberColumn("hours", help="how long it was (or has been) open"),
            "Z exit / now": st.column_config.NumberColumn(
                "Z exit / now", help="closed: the Z at the exit prices under the position's own fit; open: the Z now"),
            "PnL": st.column_config.NumberColumn("PnL", help="USDT. Closed: after fees and funding. Open: unrealized now"),
            "PnL %": st.column_config.NumberColumn("PnL %", help="of the pair's equity at the entry"),
            "exit reason": st.column_config.TextColumn(
                "exit reason", width=165, help="exit signal · hourly = at a bar close; · instant = within seconds of the crossing "
                                    "(realtime exits, bot B); stop-loss; manual = from the order ticket"),
        })


live()

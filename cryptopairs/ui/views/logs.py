"""System logs: the bot's log file (live tail with filters) and its structured events."""

import json

import pandas as pd
import streamlit as st

from cryptopairs.ui import data as D
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import AMBER, MUTED, RED, page_title

ctx = W.context()
page_title("System logs", f"Live tail of `{ctx.log_path}` and the event journal of the state database")

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
f = st.columns([2.2, 2.2, 3, 1.2, 1.2])
levels = f[0].multiselect("Level", LEVELS, default=["INFO", "WARNING", "ERROR", "CRITICAL"], key="log_levels")
sources = f[1].multiselect("Source", ["runtime.app", "runtime.engine", "runtime.feed", "runtime.execution",
                                      "runtime.commands", "runtime.risk", "runtime.store"], key="log_sources",
                           placeholder="all sources")
needle = f[2].text_input("Search", key="log_search", placeholder="text or pair, e.g. CLOSE, SFPUSDT, error")
n_lines = f[3].selectbox("Lines", [200, 500, 1000, 5000], index=1, key="log_lines")
newest_first = f[4].toggle("Newest first", value=True, key="log_order")


def _level_color(v: str) -> str:
    return {"ERROR": f"color: {RED}; font-weight: 600", "CRITICAL": f"color: {RED}; font-weight: 700",
            "WARNING": f"color: {AMBER}", "DEBUG": f"color: {MUTED}"}.get(v, "")


@st.fragment(run_every=ctx.refresh or None)
def live() -> None:
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if not d["pairs"].empty:
        rows = D.pair_rows(d, D.session_cfg(d))
        W.status_strip(d, ctx, D.equity_series(d), rows, float(d["pairs"]["capital"].sum()))
    tab_log, tab_events = st.tabs(["Log file", "Event journal"])
    with tab_log:
        p = ctx.log_path
        log = D.read_log(str(p), p.stat().st_mtime if p.exists() else 0.0)
        if log.empty:
            st.info(f"No log at `{p}` yet.")
        else:
            view = log[log["level"].isin(levels)] if levels else log
            if sources:
                view = view[view["source"].isin(sources)]
            if needle:
                view = view[view["message"].str.contains(needle, case=False, regex=False)]
            view = view.tail(int(n_lines))
            if newest_first:
                view = view.iloc[::-1]
            k = W.kpis("logs")
            k.metric("Lines shown", f"{len(view):,}", f"of {len(log):,} in the tail", delta_color="off", delta_arrow="off")
            k.metric("Warnings", f"{int((log['level'] == 'WARNING').sum())}")
            k.metric("Errors", f"{int(log['level'].isin(['ERROR', 'CRITICAL']).sum())}")
            last = log["time"].iloc[-1]
            k.metric("Last line", f"{last:%H:%M:%S}" if pd.notna(last) else "—", f"{D.TZ_LABEL} time", delta_color="off", delta_arrow="off")
            st.dataframe(view.style.map(_level_color, subset=["level"]), width="stretch", hide_index=True, placeholder="", height=560,
                         key="lg_tail",
                         column_config={"time": st.column_config.DatetimeColumn("time", format="DD/MM HH:mm:ss"),
                                        "message": st.column_config.TextColumn(width="large")})
            st.download_button("Download this view", view.to_csv(index=False).encode(), f"{p.stem}_log.csv", "text/csv",
                               on_click="ignore")
    with tab_events:
        ev = d["events"]
        if ev.empty:
            st.caption("No events yet.")
        else:
            kinds = sorted(ev["kind"].unique())
            pick = st.multiselect("Event kinds", kinds, default=[], key="ev_kinds", placeholder="all kinds")
            e = ev[ev["kind"].isin(pick)] if pick else ev
            st.dataframe(pd.DataFrame({
                "time": D.to_time(e["ts"]), "event": e["kind"], "pair": e["pair_id"].fillna(""),
                "details": e["payload"].fillna("").map(lambda s: "" if s in ("", "{}") else ", ".join(
                    f"{k}={v}" for k, v in json.loads(s).items())),
            }), width="stretch", hide_index=True, placeholder="", height=560, key="lg_events",
                column_config={"time": st.column_config.DatetimeColumn(format="DD/MM HH:mm:ss"),
                               "details": st.column_config.TextColumn(width="large")})


live()

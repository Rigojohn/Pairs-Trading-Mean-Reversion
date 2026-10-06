"""Risk & controls: global switches, per-pair controls, parameter overrides, command history."""

import json

import pandas as pd
import streamlit as st

from cryptopairs.ui import data as D
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import heading, page_title, pill

ctx = W.context()
page_title("Risk & controls", "The dashboard never trades: buttons queue a command, the bot checks it and runs it "
                              "within ~2 s, and writes the result below")


@st.fragment(run_every=W.every(ctx.refresh))
def live() -> None:
    W.pulse(ctx.db_path, ctx.refresh, "pulse_controls_live")
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if d["pairs"].empty:
        st.info("The bot has not saved any pair yet.")
        return
    cfg = D.session_cfg(d)
    rows = D.pair_rows(d, cfg)
    W.status_strip(d, ctx, D.equity_series(d), rows, float(d["pairs"]["capital"].sum()))
    kv = D.kv_dict(d)
    risk = cfg.get("risk", {}) or {}
    strat = cfg.get("strategy", {}) or {}

    heading("Global switches")
    a, b, c = st.columns(3, gap="medium")
    with a:
        st.markdown("**New entries**")
        if kv.get("entries_paused"):
            st.html(pill("PAUSED", "warn"))
            if st.button("Resume entries (all pairs)", key="g_resume"):
                W.send(ctx.db_path, "resume_entries")
        else:
            st.html(pill("ALLOWED", "ok"))
            if st.button("Pause entries (all pairs)", key="g_pause"):
                W.send(ctx.db_path, "pause_entries")
        st.caption("Exits and stops keep running while paused.")
    with b:
        st.markdown("**Kill switch**")
        if kv.get("risk.kill_switch"):
            st.html(pill("TRIGGERED", "bad"))
            sure = st.checkbox("confirm reset", key="conf_kill")
            if st.button("Reset kill switch", disabled=not sure, key="g_kill"):
                W.send(ctx.db_path, "reset_kill_switch")
        else:
            st.html(pill("ARMED", "ok"))
        st.caption(f"Stops new entries at a {risk.get('portfolio_max_drawdown', 0.25):.0%} drawdown from the peak "
                   f"equity ({(kv.get('risk.peak_equity') or 0):,.2f}).")
    with c:
        st.markdown("**Flatten**")
        n_open = int((rows["side"] != 0).sum())
        sure = st.checkbox(f"I want to close ALL {n_open} position(s) at market", key="conf_flat", disabled=n_open == 0)
        if st.button("Flatten all", disabled=not sure, type="primary", key="g_flat"):
            W.send(ctx.db_path, "flatten_all")

    heading("Risk limits in force")
    lim = W.kpis("limits")
    lim.metric("Leverage", f"{strat.get('leverage', '—')}x")
    lim.metric("Stop-loss", f"{(strat.get('stop_loss_pct') or 0):.0%}", "bar close", delta_color="off", delta_arrow="off")
    cat = strat.get("catastrophe_stop_pct")
    lim.metric("Catastrophe stop", f"{cat:.0%}" if cat else "off", "checked every ~5 s", delta_color="off", delta_arrow="off")
    lim.metric("Max |Z| at entry", f"{strat.get('max_entry_z') or '—'}")
    lim.metric("Max open pairs", f"{risk.get('max_open_pairs', '—')}")
    lim.metric("Max gross / coin", f"{risk.get('max_symbol_gross_leverage', '—')}x equity")

    heading("Per-pair controls")
    view = rows[rows["pair"].isin(ctx.view_pairs)]
    st.dataframe(view[["pair", "position", "equity", "PnL", "Z now", "Z entry", "Z exit", "lookback", "kalman_delta",
                       "status"]].rename(columns={"kalman_delta": "Kalman δ"}).style.format({"equity": "{:,.2f}", "PnL": "{:+,.2f}", "Z now": "{:+.2f}",
                                                "Kalman δ": "{:.0e}"}, na_rep=""),
                 width="stretch", hide_index=True, placeholder="", key="ct_pairs")
    pc = st.columns([3, 2, 2, 2])
    pid = pc[0].selectbox("Pair", view["pair"].tolist(), key="ctl_pair")
    r = view[view["pair"] == pid].iloc[0] if pid else None
    if r is not None:
        with pc[1]:
            if r["paused"]:
                if st.button("Resume entries", key="ctl_resume", width="stretch"):
                    W.send(ctx.db_path, "resume_pair", pid)
            elif st.button("Pause entries", key="ctl_pause", width="stretch"):
                W.send(ctx.db_path, "pause_pair", pid)
        with pc[2]:
            sure = st.checkbox("confirm close", key="ctl_conf_close", disabled=r["side"] == 0)
            if st.button("Close at market", key="ctl_close", disabled=not sure or r["side"] == 0, width="stretch"):
                W.send(ctx.db_path, "close_pair", pid)
        with pc[3]:
            if st.button("Revert parameters", key="ctl_revert", disabled=not r["override"], width="stretch",
                         help="back to the calibrated parameters of the pairs file"):
                W.send(ctx.db_path, "clear_params", pid)

    heading("Parameter overrides")
    ov = kv.get("param_overrides") or {}
    if not ov:
        st.caption("None: every pair trades on its calibrated parameters. Stage a change on the Pair monitor page.")
    else:
        st.dataframe(pd.DataFrame([{
            "pair": k, "since": D.to_time(v.get("ts", 0)),
            "Z entry": f"{v['base']['z_entry']:g} → {v['params']['z_entry']:g}",
            "Z exit": f"{v['base']['z_exit']:g} → {v['params']['z_exit']:g}",
            "lookback": f"{v['base']['lookback']} → {v['params']['lookback']}",
            "δ": f"{v['base'].get('kalman_delta')} → {v['params'].get('kalman_delta')}",
        } for k, v in ov.items()]), width="stretch", hide_index=True, placeholder="", key="ct_overrides")

    heading("Command history")
    cmds = d["commands"]
    if cmds.empty:
        st.caption("No commands yet.")
    else:
        st.dataframe(pd.DataFrame({
            "#": cmds["id"], "sent": D.to_time(cmds["ts"]), "command": cmds["kind"],
            "pair": cmds["pair_id"].fillna(""),
            "details": cmds["payload"].fillna("").map(lambda s: "" if s in ("", "{}") else ", ".join(
                f"{k}={v}" for k, v in json.loads(s).items())),
            "status": cmds["status"], "result": cmds["result"].fillna(""),
            "took (s)": (cmds["done_ts"] - cmds["ts"]) / 1000,
        }).style.format({"took (s)": "{:.1f}"}, na_rep="")
            .map(lambda v: "color: #2ebd85" if v == "done" else ("color: #f6465d" if v == "rejected" else
                                                                 ("color: #f0b429" if v == "pending" else "")),
                 subset=["status"]),
            width="stretch", hide_index=True, placeholder="", key="ct_commands",
            column_config={"sent": st.column_config.DatetimeColumn(format="DD/MM HH:mm:ss")})


live()

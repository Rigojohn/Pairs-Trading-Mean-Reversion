"""Pair monitor: one pair in depth. Prices, Z with bands and signals, model, position, controls."""

import json

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.ui import charts as C
from cryptopairs.ui import data as D
from cryptopairs.ui import lab as L
from cryptopairs.ui import live as LIVE
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import heading, page_title, pill

ctx = W.context()
pid = ctx.focus
pair = ctx.pair(pid)
a, b = pair["a"].split("/")[0], pair["b"].split("/")[0]
page_title(f"{a} / {b}", f"Pair monitor · {pid} · change the pair in the sidebar")

opts = st.columns([1.3, 1.3, 1.3, 4])
show_sim = opts[0].toggle("What-if signals", value=True, help="signals the sidebar thresholds would give on the "
                          "hourly model Z (no costs, stops or filters)")
show_live = opts[1].toggle("Live ticks", value=True, help="15-second snapshots between the hourly bars")
show_model = opts[2].toggle("Model diagnostics", value=False, help="spread with bands, β and σ: model now vs in use")


@st.fragment(run_every=ctx.refresh or None)
def live() -> None:
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if d["pairs"].empty or pid not in set(d["pairs"]["pair_id"]):
        st.info("This pair is not in the selected bot.")
        return
    cfg = D.session_cfg(d)
    capital = float(d["pairs"]["capital"].sum())
    rows = D.pair_rows(d, cfg)
    W.status_strip(d, ctx, D.equity_series(d), rows, capital)
    row = rows[rows["pair"] == pid].iloc[0]
    last = d["commands"][d["commands"]["pair_id"] == pid].head(1) if not d["commands"].empty else pd.DataFrame()
    if len(last) and D.now_ms() - int(last.iloc[0]["ts"]) < 15 * 60_000:  # feedback for a recent button press
        r = last.iloc[0]
        st.html(pill(f"{r['kind']} → {r['status']}", {"done": "ok", "rejected": "bad"}.get(r["status"], "warn"))
                + f'<span style="color:#8b93a5;font-size:.8rem">{r["result"] or "waiting for the bot…"}</span>')
    pr = d["pairs"][d["pairs"]["pair_id"] == pid].iloc[0]
    st_ = json.loads(pr["strategy"])
    p = D.load_pair(str(ctx.db_path), D.version(ctx.db_path), pid, ctx.since_ms)
    fit, lv = p["fit"], (p["live"] if show_live else p["live"].iloc[0:0])
    live_last = p["live"].iloc[-1] if not p["live"].empty else None
    fit_last = fit.iloc[-1] if not fit.empty else None

    c = st.columns(8)
    c[0].metric("Position", row["position"], row["status"] or "active", delta_color="off")
    c[1].metric("Unrealized", f"{row['unrealized']:+,.4f}",
                f"{row['unrealized %']:+.2f}% of entry" if np.isfinite(row["unrealized %"]) else None)
    zs = p["live"]["z_buy"].add(p["live"]["z_sell"]).div(2).tail(240).round(3).tolist() if not p["live"].empty else None
    c[2].metric("Z now", f"{row['Z now']:+.2f}" if np.isfinite(row["Z now"]) else "—",
                f"entry ±{row['Z entry']:g} · exit ±{row['Z exit']:g}", delta_color="off", chart_data=zs)
    if row["side"]:
        c[3].metric("To exit (Z)", f"{row['to exit']:+.2f}" if np.isfinite(row["to exit"]) else "—",
                    f"stop in {row['to stop %']:.1f}%" if np.isfinite(row["to stop %"]) else None, delta_color="off")
    else:
        c[3].metric("To entry (Z)", f"{row['to entry']:+.2f}" if np.isfinite(row["to entry"]) else "—",
                    "signal" if np.isfinite(row["to entry"]) and row["to entry"] <= 0 else "waiting", delta_color="off")
    c[4].metric("β in use", f"{st_['beta']:.4f}" if st_.get("beta") is not None else "—",
                f"model now {fit_last['kf_beta']:.4f}" if fit_last is not None and pd.notna(fit_last["kf_beta"]) else None,
                delta_color="off")
    c[5].metric("σ in use", f"{st_['sigma']:.4f}" if st_.get("sigma") else "—",
                f"model now {fit_last['kf_sigma']:.4f}" if fit_last is not None and pd.notna(fit_last["kf_sigma"]) else None,
                delta_color="off")
    c[6].metric("Bid-ask A / B", f"{live_last['hs_bps_a'] * 2:.1f} / {live_last['hs_bps_b'] * 2:.1f}"
                if live_last is not None and pd.notna(live_last["hs_bps_a"]) else "—", "bps", delta_color="off")
    c[7].metric("Realized", f"{row['realized']:+,.4f}", f"{int(row['trades'])} trades · {int(row['wins'])} won",
                delta_color="off")

    trades = d["trades"][d["trades"]["pair_id"] == pid] if not d["trades"].empty else pd.DataFrame()
    marks, spans = W.trade_markers(trades, fit, p["live"]) if len(trades) else (pd.DataFrame(), [])
    if fit.empty and lv.empty:
        st.info("No model history yet for this pair: it appears after the bot's first start with schema v3.")
        return

    wi, use = ctx.what_if_for(pid), ctx.live_params(pid)
    sim = None
    if show_sim and not fit.empty:
        sim = L.simulate_signals(D.to_time(fit["bar_ts"] + D.HOUR), fit["z"].to_numpy(dtype=float), wi.z_entry, wi.z_exit)
        if len(sim):
            sim["text"] = sim["kind"].map({"long": "what-if BUY spread", "short": "what-if SELL spread",
                                           "exit": "what-if CLOSE"}) + sim["z"].map(lambda z: f" · Z {z:+.2f}")
    heading("Chart A · normalised price action")
    rev = f"{pid}-{ctx.window}"  # a new pair or window resets the zoom; live ticks keep it
    LIVE.chart(C.prices_chart(fit, lv, a, b, spans), key="pm_prices", rev=rev)
    heading("Chart B · spread Z-score, thresholds and signals")
    max_z = (cfg.get("strategy", {}) or {}).get("max_entry_z")
    LIVE.chart(C.z_chart(fit, lv, spans, marks, (use.z_entry, use.z_exit), (wi.z_entry, wi.z_exit), sim, max_z),
               key="pm_z", rev=rev)
    note = ("Filled markers = the bot's trades (▲ buy spread, ▼ sell spread, ● close, ✕ stop); hollow = what-if. "
            "Red/green = thresholds in use; amber = sidebar what-if. Shaded = in position (fit frozen).")
    if not wi.same_model(use):
        note += " The what-if changes the model (δ / lookback): its Z path differs. See the Backtest lab for it."
    st.caption(note)
    if show_model:
        heading("Model diagnostics")
        LIVE.chart(C.model_chart(fit, lv, use.z_entry, use.z_exit, spans), key="pm_model", rev=rev)

    if row["side"] and live_last is not None:
        heading("Position legs")
        book = json.loads(pr["book"])
        legs = []
        for sym, qty, entry_px, mark in ((pr["symbol_a"], book["qty_a"], book["entry_px_a"], live_last["mid_a"]),
                                         (pr["symbol_b"], book["qty_b"], book["entry_px_b"], live_last["mid_b"])):
            legs.append({"leg": sym, "side": "long" if qty > 0 else "short", "qty": qty, "entry": entry_px,
                         "mark": mark, "move %": 100 * (mark / entry_px - 1) if entry_px else np.nan,
                         "notional": abs(qty) * mark, "leg PnL": qty * (mark - entry_px)})
        lg = pd.DataFrame(legs)
        st.dataframe(lg.style.format({"qty": "{:,.4f}", "entry": "{:.6g}", "mark": "{:.6g}", "move %": "{:+.2f}%",
                                      "notional": "{:,.2f}", "leg PnL": "{:+,.4f}"}), width="stretch", hide_index=True,
                     key="pm_legs")
        ratio = legs[1]["notional"] / legs[0]["notional"] if legs[0]["notional"] else np.nan
        st.caption(f"Hedge ratio now {ratio:.3f} vs β at entry {st_.get('beta', np.nan):.3f} · entry equity "
                   f"{st_.get('entry_equity') or np.nan:,.4f} · funding so far {book.get('funding_paid', 0.0):+.4f}")

    if len(trades):
        heading("Trades of this pair")
        tv = D.trade_view(trades.iloc[::-1])
        st.dataframe(tv.style.format({"hours": "{:.1f}", "Z entry": "{:+.2f}", "beta": "{:.4f}", "PnL": "{:+,.4f}",
                                      "PnL %": "{:+.2f}%", "fees": "{:.4f}", "funding": "{:+.4f}"}, na_rep=""),
                     width="stretch", hide_index=True, key="pm_trades")


live()

# ----------------------------------------------------------- parameters
@st.fragment(run_every=ctx.refresh or None)
def manage() -> None:
    """Parameters and controls; refreshed so overrides and pauses show as soon as the bot applies them."""
    brief = D.load_brief(str(ctx.db_path), D.version(ctx.db_path))
    p = next((x for x in brief["pairs"] if x["id"] == pid), None)
    if p is None:
        return
    ov = (brief.get("overrides") or {}).get(pid)
    cal, use = L.calibrated(p, ctx.leverage, ov), L.in_use(p, ctx.leverage)
    wi = ctx.what_if.get(pid) or use
    heading("Parameters · calibrated vs in use vs what-if")
    fmt_d = lambda w: "OLS" if w.kalman_delta is None else f"{w.kalman_delta:.0e}"  # noqa: E731
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.html(W.params_table([
            ("Z entry", [f"{w.z_entry:g}" for w in (cal, use, wi)]),
            ("Z exit", [f"{w.z_exit:g}" for w in (cal, use, wi)]),
            ("lookback (bars)", [f"{w.lookback}" for w in (cal, use, wi)]),
            ("Kalman δ", [fmt_d(w) for w in (cal, use, wi)]),
            ("hedge memory", [L.memory_label(w.kalman_delta, w.lookback) for w in (cal, use, wi)]),
            ("capital (USDT)", [f"{w.capital:,.0f}" for w in (cal, use, wi)]),
            ("leverage", [f"{w.leverage:g}x" for w in (cal, use, wi)]),
        ], ["Calibrated (pairs file)", "In use (bot)", "What-if (sidebar)"]))
        if ov:
            pending = (ov["params"]["z_entry"], ov["params"]["z_exit"], ov["params"]["lookback"],
                       ov["params"]["kalman_delta"]) != (use.z_entry, use.z_exit, use.lookback, use.kalman_delta)
            st.html(pill("MANUAL OVERRIDE" + (" · PENDING UNTIL THE POSITION CLOSES" if pending else " IN FORCE"), "warn")
                    + '<span style="color:#8b93a5;font-size:.8rem">until reverted or the pairs file is recalibrated</span>')
    with right:
        changed = (wi.z_entry, wi.z_exit, wi.lookback, wi.kalman_delta) != (use.z_entry, use.z_exit, use.lookback,
                                                                             use.kalman_delta)
        st.markdown("**Apply to the live bot**")
        if not changed:
            st.caption("Move the sidebar sliders and press **Recalculate** to stage new thresholds / model settings. "
                       "Capital and leverage are what-if only.")
        else:
            st.caption(("The pair is in a position: the bot keeps its current parameters until it closes, then "
                        "switches. " if p["side"] else
                        "The pair is flat: the bot switches at once (refitting if δ / lookback changed). ")
                       + "Check the Backtest lab first; every change is logged as an event.")
        sure = st.checkbox("I have checked the backtest and want this change live", key=f"conf_apply_{pid}",
                           disabled=not changed)
        if st.button("Apply what-if to live bot", type="primary", disabled=not (changed and sure), key=f"apply_{pid}"):
            W.send(ctx.db_path, "set_params", pid, lookback=int(wi.lookback), z_entry=float(wi.z_entry),
                   z_exit=float(wi.z_exit), kalman_delta=wi.kalman_delta)
        if ov and st.button("Revert to calibrated parameters", key=f"revert_{pid}"):
            W.send(ctx.db_path, "clear_params", pid)

    heading("Controls")
    kv = D.kv_dict({"kv": D.query(str(ctx.db_path), "SELECT * FROM kv")})
    paused = pid in set(kv.get("paused_pairs", []) or [])
    k = st.columns([2, 2, 3])
    with k[0]:
        if p["side"] != 0:
            sure = st.checkbox("confirm close", key=f"conf_close_{pid}")
            if st.button("Close position now (market)", key=f"close_{pid}", disabled=not sure, type="primary"):
                W.send(ctx.db_path, "close_pair", pid)
        else:
            st.caption("No position to close.")
    with k[1]:
        if paused:
            if st.button("Resume entries for this pair", key=f"resume_{pid}"):
                W.send(ctx.db_path, "resume_pair", pid)
        elif st.button("Pause entries for this pair", key=f"pause_{pid}"):
            W.send(ctx.db_path, "pause_pair", pid)
    with k[2]:
        st.caption("Exits and stops always keep running, even when entries are paused. The result of each button "
                   "appears above the charts within ~2 s.")


manage()

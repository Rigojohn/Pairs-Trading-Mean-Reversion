"""Pair desk: one pair the way a trading desk watches it.

Top: the live ticker (Z and PnL from Binance in the browser) and the bot's numbers.
Middle: TradingView charts (spread Z, legs, relative value, model) next to the live order
books and the trade tape. Then the order ticket (open, close or scale out, exit plan) and the
position card, the pair's profile (instruments, relationship statistics, track record) and
the strategy parameters (calibrated / in use / what-if).
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from cryptopairs.ui import data as D
from cryptopairs.ui import desk as K
from cryptopairs.ui import history as HI
from cryptopairs.ui import lab as L
from cryptopairs.ui import live as LIVE
from cryptopairs.ui import market as M
from cryptopairs.ui import tv as TV
from cryptopairs.ui import tvcharts as TC
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import AMBER, GREEN, LEG_A, MUTED, RED, VIOLET, heading, page_title, pill

ctx = W.context()
pid = ctx.focus
pair = ctx.pair(pid)
a, b = pair["a"].split("/")[0], pair["b"].split("/")[0]
page_title(f"{a} / {b}", f"Pair desk · {pid} · pick another pair in the watchlist · times {D.TZ_LABEL}")

VIEWS = {"Spread Z": ["1m", "5m", "15m", "1h", "4h", "1d"], "Legs": ["5m", "15m", "1h", "4h"],
         "Relative": ["5m", "15m", "1h", "4h"], "Model": ["1h"]}
# Candles per timeframe (one request each): 1 day of 1m, 4 of 5m, 10 of 15m, ~6 weeks of 1h, 3 months of 4h,
# 4 months of 1d. Spread Z reaches further back with the archive (ui/history.py).
CANDLES = {"1m": 1440, "5m": 1152, "15m": 960, "1h": 1000, "4h": 540, "1d": 120}
# Spread Z points sent per refresh (the chart reruns every few seconds): about what one screen holds
# at 1h and 4h; 1d carries the whole history since 2023.
HISTORY_BARS = {"1h": 1500, "4h": 1500, "1d": 2000}

bar = st.container(horizontal=True, gap="large", vertical_alignment="bottom")
view = bar.segmented_control("Chart", list(VIEWS), default="Spread Z", key="pd_view", required=True) or "Spread Z"
tfs = VIEWS[view]
tf = bar.segmented_control("Timeframe", tfs, default="1h" if "1h" in tfs else tfs[0], key=f"pd_tf_{view}",
                           required=True) or ("1h" if "1h" in tfs else tfs[0])


def _state(d: dict) -> tuple[K.PairState | None, dict]:
    p = D.load_pair(str(ctx.db_path), D.version(ctx.db_path), pid, ctx.since_ms)
    return K.load_state(d, ctx, pid, p["pending"]), p


def _reductions(p: dict) -> list[int]:
    r = p.get("reductions")
    return [] if r is None or r.empty else [int(x) for x in r["ts"]]


def _chart(s: K.PairState, p: dict, trades: pd.DataFrame) -> dict:
    use, wi = ctx.live_params(pid), ctx.what_if_for(pid)
    max_z = s.scfg.get("max_entry_z")
    if view == "Model":
        return TC.model_spec(p["fit"], trades, D.now_ms(), z_entry=use.z_entry)
    ca, cb = M.candles(s.sym_a, tf, CANDLES[tf]), M.candles(s.sym_b, tf, CANDLES[tf])
    if view == "Spread Z":
        px, fit = TC.closes(ca, cb), p["fit"]
        if tf not in TC.INTRADAY:  # the whole history: the archive (from 6/2023) before the bot's own records
            fit = D.load_pair(str(ctx.db_path), D.version(ctx.db_path), pid, 0)["fit"]
            hist = HI.model_history(str(ctx.data_dir), s.sym_a, s.sym_b, pid, int(use.lookback), use.kalman_delta,
                                    s.scfg.get("kalman_history", 8760), s.scfg.get("kalman_outlier_z", 4.0),
                                    int(s.scfg.get("kalman_max_skips", 48)))
            fit, px = HI.extend(fit, px, hist, tf)
            if px.empty:  # Binance did not answer: every close from the model rows
                px = TC.closes_from_fit(fit, tf)
            px = px.tail(HISTORY_BARS[tf])
        return TC.z_spec(px, fit, trades, tf, z_entry=use.z_entry, z_exit=use.z_exit,
                         exit_plan=s.strat.get("z_exit_override"), what_if=(wi.z_entry, wi.z_exit), max_entry_z=max_z,
                         fit_now=s.fit, symbols=(s.id_a, s.id_b), now_ms=D.now_ms(), side=s.side,
                         entered=s.row["Z at entry"], reductions=_reductions(p))
    if view == "Legs":
        entry = (float(s.book["entry_px_a"]), float(s.book["entry_px_b"])) if s.side else None
        return TC.legs_spec(ca, cb, (s.a, s.b), (s.id_a, s.id_b), (s.digits_a, s.digits_b), tf, trades, entry,
                            side_a=s.side, reductions=_reductions(p))
    return TC.relative_spec(ca, cb, (s.a, s.b), (s.id_a, s.id_b), tf, trades)


@st.fragment(run_every=ctx.refresh or None)
def desk() -> None:
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if d["pairs"].empty or pid not in set(d["pairs"]["pair_id"]):
        st.info("This pair is not in the selected bot.")
        return
    cfg = D.session_cfg(d)
    rows = D.pair_rows(d, cfg)
    W.status_strip(d, ctx, D.equity_series(d), rows, float(d["pairs"]["capital"].sum()))
    s, p = _state(d)
    K.feedback(s)
    TV.market("ticker", key=f"pd_ticker_{pid}", **s.market_data())
    r = s.row
    info = dict(delta_color="off", delta_arrow="off")
    g = W.kpis("pair")
    g.metric("Position", K.SIDE_NAME[s.side], r["status"] or "active", **info)
    g.metric("Unrealized", f"{r['unrealized']:+,.4f}", f"{r['unrealized %']:+.2f}%" if np.isfinite(r["unrealized %"]) else None,
             help="USDT, and % of the equity at entry (bot's 5-second marks)")
    g.metric("Z now", f"{r['Z now']:+.2f}" if np.isfinite(r["Z now"]) else "—",
             K.rule_text(s.side, r["Z entry"], r["exit Z"]), **info,
             help="the bot's last live mark; entries are checked at each hourly close, exits too, or within "
                  "seconds when the bot runs realtime exits (bot B)")
    if s.side:
        g.metric("To exit (Z)", f"{r['to exit']:+.2f}" if np.isfinite(r["to exit"]) else "—",
                 f"stop {r['to stop %']:.1f}% away" if np.isfinite(r["to stop %"]) else None, **info)
    else:
        g.metric("To entry (Z)", f"{r['to entry']:+.2f}" if np.isfinite(r["to entry"]) else "—",
                 "signal" if np.isfinite(r["to entry"]) and r["to entry"] <= 0 else "waiting", **info)
    fit_last = p["fit"].iloc[-1] if not p["fit"].empty else None
    g.metric("β in use", f"{s.strat['beta']:.4f}" if s.strat.get("beta") is not None else "—",
             f"model {fit_last['kf_beta']:.4f}" if fit_last is not None and pd.notna(fit_last["kf_beta"]) else None,
             **info, help="hedge ratio the position trades on vs the estimate now")
    g.metric("σ in use", f"{s.strat['sigma']:.4f}" if s.strat.get("sigma") else "—",
             f"model {fit_last['kf_sigma']:.4f}" if fit_last is not None and pd.notna(fit_last["kf_sigma"]) else None, **info)
    ia, ib = ((s.insts or {}).get(s.sym_a) or {}), ((s.insts or {}).get(s.sym_b) or {})
    fr = [x.get("funding_rate") for x in (ia, ib)]
    g.metric("Funding", " / ".join("—" if x is None else f"{100 * x:+.3f}%" for x in fr), f"{a} / {b} · next settlement",
             **info, help="current funding rates of the legs (longs pay a positive rate)")
    g.metric("Realized", f"{r['realized']:+,.4f}", f"{int(r['trades'])} trades · {int(r['wins'])} won", **info)

    trades = d["trades"][d["trades"]["pair_id"] == pid] if not d["trades"].empty else pd.DataFrame()
    left, right = st.columns([8, 4], gap="medium")
    with left:
        spec = _chart(s, p, trades)
        if not spec["series"]:
            st.info("No data for this chart yet (Binance unreachable, or no model history before the bot's first bar).")
        TV.chart(spec, key="pd_chart", rev=f"{pid}-{view}-{tf}-{ctx.window}")
        st.caption({
            "Spread Z": "One Z on every timeframe: the Z the strategy saw at that moment, i.e. Binance prices under "
                        "the fit (α, β, σ) in force then (refitted every hour while flat, frozen at the entry while in "
                        "a position). Each point is the Z at the bar's close, so 15:00 shows the same Z on 1m and 1h; "
                        "the last point moves live with Binance and matches the ticker. Lines: while flat, the "
                        "thresholds: LONG entry (green, below zero) and SHORT entry (red, above zero) with their exits "
                        "(cyan). In a position only its own two: entry = the Z the fills got, which the PnL is measured "
                        "from (a SHORT gains while Z is below it, a LONG while above), and its exit: a LONG exits when "
                        "Z rises back to it, a SHORT when Z falls back to it. BUY/SELL/CLOSE sit at their fills' Z. The "
                        "bot checks entries at each hourly close (the 1h points) and exits there too, or within seconds "
                        "of the crossing when it runs realtime exits (bot B); between closes the stops act. 1h, 4h "
                        "and 1d reach back to 2023: before the bot's own records the same model is run over the "
                        "research archive (1d shows it all at once). Amber = the position's exit plan or the what-if below. Shaded = in a position. "
                        "Lower pane: the spread, how far A is from its fair price against B in %, with the same levels "
                        "(Z × σ). The model's own Z is in the Model view.",
            "Legs": "Candles from Binance; the forming candle updates live. Dashed lines = entry prices of the open position.",
            "Relative": "Both legs rebased to 100 at the left edge; the forming candle updates live.",
            "Model": "Each hour: the Kalman estimate (blue) vs the fit the strategy traded on (amber), for β, σ and "
                     "the Z they give. They split only while a position keeps its entry fit: blue Z = the Z if the "
                     "position were closed and the model refitted now.",
        }[view])
    with right:
        TV.market("book", key=f"pd_book_{pid}", **s.market_data())
        TV.market("tape", key=f"pd_tape_{pid}", rows=18, **s.market_data())


desk()


@st.fragment(run_every=max(ctx.refresh, 3) if ctx.refresh else None)
def trade() -> None:
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if d["pairs"].empty or pid not in set(d["pairs"]["pair_id"]):
        return
    s, _ = _state(d)
    left, right = st.columns([5, 7], gap="large")
    with left:
        heading("Order ticket · manual")
        K.ticket(s, ctx)
        kv = D.kv_dict(d)
        paused = pid in set(kv.get("paused_pairs", []) or [])
        c1, c2 = st.columns([2, 3])
        if paused:
            if c1.button("Resume automatic entries", key=f"resume_{pid}", width="stretch"):
                W.send(ctx.db_path, "resume_pair", pid)
        elif c1.button("Pause automatic entries", key=f"pause_{pid}", width="stretch"):
            W.send(ctx.db_path, "pause_pair", pid)
        c2.caption("A pause stops the bot's own entries for this pair; exits, stops and your manual orders still work.")
    with right:
        heading("Position")
        K.position_card(s)


trade()


@st.fragment(run_every=60 if ctx.refresh else None)
def profile() -> None:
    d = D.load_core(str(ctx.db_path), D.version(ctx.db_path), ctx.since_ms)
    if d["pairs"].empty or pid not in set(d["pairs"]["pair_id"]):
        return
    s, _ = _state(d)
    heading("Pair profile")
    left, right = st.columns([5, 7], gap="large")
    with left:
        st.markdown("**Instruments**")
        if s.insts is None:
            st.caption("Binance is not reachable from the dashboard right now.")
        st.dataframe(K.instruments_table(s, str(ctx.data_dir)), width="stretch", key="pd_inst")
    with right:
        st.markdown("**Relationship** · hourly closes")
        stats, roll, carry = K.relationship(str(ctx.data_dir), pair["a"], pair["b"], not M.offline())
        if not stats:
            st.caption(f"No local price history for this pair ({carry.get('error')}).")
        else:
            st.dataframe(K.stats_table(stats, s.strat.get("beta")), width="stretch", key="pd_stats")
            st.caption("Diagnostics only: the strategy has no cointegration filter (as in the paper) and trades the "
                       "Kalman spread, not this OLS one. Engle-Granger critical values: −3.90 (1%), −3.34 (5%), −3.04 (10%).")
            if np.isfinite(carry.get("long", np.nan)):
                st.caption(f"Funding carry over the last 30 days, per 100 USDT gross: long spread "
                           f"{-100 * carry['long']:+.3f} USDT/day, short spread {-100 * carry['short']:+.3f} USDT/day "
                           "(+ = received).")
    if len(roll):
        t = (roll["ts"] // 1000).to_numpy()
        spec = {"height": 230, "panes": [1], "series": [
            TC.series("corr_ret", "Line", TC.line_data(t, roll["corr_ret"]), legend="corr · returns 30d", digits=3,
                      options={"color": LEG_A, "lineWidth": 2, "priceFormat": TC.price_format(2)}),
            TC.series("corr_price", "Line", TC.line_data(t, roll["corr_price"]), legend="corr · prices 30d", digits=3,
                      options={"color": VIOLET, "lineWidth": 1, "priceFormat": TC.price_format(2)},
                      lines=[TC.price_line(0.5, MUTED, "0.5", TC.DOTTED), TC.price_line(0.9, MUTED, "0.9", TC.DOTTED)]),
        ]}
        heading("Rolling 30-day correlation · last year (the paper's funnel asks > 0.9 for prices and > 0.5 for returns)")
        TV.chart(spec, key="pd_corr", rev=f"corr-{pid}")
    trades = d["trades"][d["trades"]["pair_id"] == pid] if not d["trades"].empty else pd.DataFrame()
    a1, a2 = st.columns([5, 7], gap="large")
    with a1:
        heading("Track record")
        rec = K.track_record(s, trades, K.calibration_record(ctx.brief.get("profile"), pid))
        if rec.empty:
            st.caption("No closed trades and no calibration record yet.")
        else:
            st.dataframe(rec, width="stretch", key="pd_record")
    with a2:
        closed = trades[trades["exit_ts"].notna()] if len(trades) else trades
        heading("PnL per trade")
        if len(closed):
            pnl = closed["pnl"].astype(float)
            fig = go.Figure(go.Bar(x=D.to_time(closed["exit_ts"].astype("int64")), y=pnl,
                                   marker_color=[GREEN if v > 0 else RED for v in pnl],
                                   hovertemplate="%{x|%m-%d %H:%M}<br>%{y:+.4f} USDT<extra></extra>"))
            fig.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10), hovermode="closest", yaxis_title="USDT")
            LIVE.chart(fig, key="pd_pnl")
        else:
            st.caption("No closed trades yet.")
    if len(trades):
        heading("Trades of this pair")
        tv = D.trade_view(trades.iloc[::-1])
        st.dataframe(tv.style.format({"hours": "{:.1f}", "Z entry": "{:+.2f}", "beta": "{:.4f}", "PnL": "{:+,.4f}",
                                      "PnL %": "{:+.2f}%", "fees": "{:.4f}", "funding": "{:+.4f}"}, na_rep="")
                     .map(lambda v: f"color: {AMBER}" if v == "manual" else "", subset=["origin"]),
                     width="stretch", hide_index=True, placeholder="", key="pd_trades")


profile()


# ----------------------------------------------------------- parameters
@st.fragment(run_every=ctx.refresh or None)
def manage() -> None:
    """Parameters for future trades; refreshed so overrides show as soon as the bot applies them."""
    brief = D.load_brief(str(ctx.db_path), D.version(ctx.db_path))
    p = next((x for x in brief["pairs"] if x["id"] == pid), None)
    if p is None:
        return
    ov = (brief.get("overrides") or {}).get(pid)
    cal, use = L.calibrated(p, ctx.leverage, ov), L.in_use(p, ctx.leverage)
    wi = ctx.what_if.get(pid) or use
    fmt_d = lambda w: "OLS" if w.kalman_delta is None else f"{w.kalman_delta:.0e}"  # noqa: E731
    left, right = st.container(), st.container()
    with left:
        st.html(W.params_table([
            ("Z entry", [f"{w.z_entry:g}" for w in (cal, use, wi)]),
            ("Z exit", [f"{w.z_exit:g}" for w in (cal, use, wi)]),
            ("lookback (bars)", [f"{w.lookback}" for w in (cal, use, wi)]),
            ("Kalman δ", [fmt_d(w) for w in (cal, use, wi)]),
            ("hedge memory", [L.memory_label(w.kalman_delta, w.lookback) for w in (cal, use, wi)]),
            ("capital (USDT)", [f"{w.capital:,.0f}" for w in (cal, use, wi)]),
            ("leverage", [f"{w.leverage:g}x" for w in (cal, use, wi)]),
        ], ["Calibrated (pairs file)", "In use (bot)", "What-if"]))
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
            st.caption("Move the what-if sliders and press **Recalculate** to stage new thresholds / model settings. "
                       "Capital and leverage are what-if only. For the open position use the ticket's exit plan.")
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


heading("Strategy parameters · calibrated vs in use vs what-if (future trades)")
p_left, p_right = st.columns([5, 4], gap="large")
with p_left:
    manage()
with p_right:
    st.markdown("**What-if** · the charts above and the Backtest lab follow these")
    W.what_if_panel(ctx, pid, columns=2)

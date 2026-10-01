"""Backtest lab: the sidebar what-if vs the calibrated parameters, on real data up to the last hour."""

from dataclasses import astuple
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.config import AppConfig, load_config
from cryptopairs.ui import charts as C
from cryptopairs.ui import lab as L
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import AMBER, GREEN, MUTED, RED, TEXT, heading, page_title, pill

ctx = W.context()
pid = ctx.focus
pair = ctx.pair(pid)
a, b = pair["a"].split("/")[0], pair["b"].split("/")[0]
page_title(f"Backtest lab · {a} / {b}",
           "The research backtest (same strategy code, the bot profile's costs: fees, spread, slippage, funding, "
           "next-open fills, stops) on the local archive + the latest Binance bars")


@st.cache_resource(show_spinner=False)
def _config(path: str, profile: str | None) -> AppConfig:
    return load_config(path if Path(path).exists() else None, profile)


try:
    cfg = _config(str(ctx.config_path), ctx.brief.get("profile"))
except Exception as exc:
    st.error(f"Could not load the config for profile {ctx.brief.get('profile')!r}: {exc}")
    st.stop()

top = st.columns([2.2, 2.2, 1.4, 1.6])
extend = top[2].toggle("Up to the last hour", value=True, key="lab_extend",
                      help="add the bars after the local archive from Binance")
with st.spinner("Loading price history…"):
    data, info = L.pair_history(str(ctx.data_dir), pair["a"], pair["b"], extend)
if info["error"] and len(data) == 0:
    st.error(info["error"])
    st.stop()
presets = L.periods(cfg, data, ctx.brief.get("first_start"))
choice = top[0].selectbox("Period", [*presets, "Custom range"], index=0, key="lab_period")
if choice == "Custom range":
    lo, hi = pd.to_datetime(data.ts[0], unit="ms").date(), pd.to_datetime(data.ts[-1], unit="ms").date()
    rng = top[1].date_input("Range (UTC)", (max(lo, hi - pd.Timedelta(days=180)), hi), min_value=lo, max_value=hi,
                            key="lab_range")
    if not isinstance(rng, tuple) or len(rng) != 2:
        st.stop()
    start = int(pd.Timestamp(rng[0]).value // 1_000_000)
    end = int((pd.Timestamp(rng[1]) + pd.Timedelta(hours=23)).value // 1_000_000)
else:
    start, end = presets[choice]
    top[1].text_input("Range (UTC)", f"{pd.to_datetime(start, unit='ms'):%Y-%m-%d} → {pd.to_datetime(end, unit='ms'):%Y-%m-%d %H:%M}",
                      disabled=True)
want_surface = top[3].toggle("Sensitivity map", value=True, help="Sharpe over a z_entry × z_exit grid (~1-3 s)")

in_sample = start < cfg.research.test_start_ms
badges = [pill("IN-SAMPLE" if in_sample else "OUT-OF-SAMPLE", "bad" if in_sample else "ok"),
          pill(f"archive to {pd.to_datetime(info['archive_end'], unit='ms'):%Y-%m-%d %H:%M}", "mute")
          if info["archive_end"] else "",
          pill(f"+{info['live_bars']} live bars", "info") if info["live_bars"] else "",
          pill(f"profile {cfg.profile}", "mute")]
st.html("".join(badges))
if info["error"]:
    st.caption(f"⚠ {info['error']}")
if in_sample:
    st.warning("This period overlaps the data the pairs were calibrated on (before "
               f"{cfg.research.test_start}). Results here are optimistic by construction. Judge changes on the "
               "out-of-sample period.")

cal, use, wi = ctx.calibrated(pid), ctx.live_params(pid), ctx.what_if_for(pid)
wi_cal = L.WhatIf(cal.z_entry, cal.z_exit, cal.lookback, cal.kalman_delta, wi.capital, wi.leverage)
runs = {"calibrated": wi_cal}
if (use.z_entry, use.z_exit, use.lookback, use.kalman_delta) != (cal.z_entry, cal.z_exit, cal.lookback, cal.kalman_delta):
    runs["in use (override)"] = L.WhatIf(use.z_entry, use.z_exit, use.lookback, use.kalman_delta, wi.capital, wi.leverage)
if wi not in runs.values():
    runs["what-if"] = wi
colors = {"calibrated": TEXT, "in use (override)": GREEN, "what-if": AMBER}
sc = cfg.strategy
key = L.data_key(data)

results, fits = {}, {}
with st.spinner("Running backtests…"):
    for name, w in runs.items():
        fits[name] = L.fits_for(data, key, int(w.lookback), w.kalman_delta, sc.kalman_outlier_z, sc.kalman_max_skips)
        results[name] = L.run(data, w, cfg, start, end, fits[name])

# ------------------------------------------------------------------ metrics
heading("Performance")
names = list(results)
metrics = {n: results[n].metrics() for n in names}
spec = [("Total return", "TotalRet", "{:+.2%}"), ("Annual return", "AnnRet", "{:+.2%}"), ("Sharpe", "Sharpe", "{:.2f}"),
        ("Annual vol", "AnnVol", "{:.2%}"), ("Max drawdown", "MaxDD", "{:.2%}"), ("Trades", "Trades", "{:.0f}"),
        ("Win rate", "WinRate", "{:.0%}"), ("Stop-losses", "StopLosses", "{:.0f}"), ("Fees (USDT)", "Fees", "{:,.2f}"),
        ("Funding (USDT)", "Funding", "{:+,.2f}"), ("Final equity", "FinalEquity", "{:,.2f}")]
focus = names[-1]
kc = st.columns(6)
for col, (label, k, f) in zip(kc, [spec[0], spec[2], spec[4], spec[5], spec[6], spec[9]]):
    v, ref = metrics[focus][k], metrics["calibrated"][k]
    delta = None
    if focus != "calibrated" and np.isfinite(v) and np.isfinite(ref):
        delta = (f.format(v - ref) if k not in ("Trades",) else f"{v - ref:+.0f}") + " vs calibrated"
    col.metric(f"{label} · {focus}", f.format(v) if np.isfinite(v) else "—", delta,
               delta_color="inverse" if k == "Funding" else "normal")
table = pd.DataFrame({n: [f.format(metrics[n][k]) if np.isfinite(metrics[n][k]) else "—" for _, k, f in spec]
                      for n in names}, index=[s[0] for s in spec])
left, right = st.columns([2, 3], gap="large")
with left:
    st.dataframe(table, width="stretch")
    st.caption(f"Capital {wi.capital:,.0f} USDT · leverage {wi.leverage:g}x · Sharpe annualised with √8760 on hourly "
               "returns · results include every modelled cost.")
with right:
    curves = {n: (pd.Series(pd.to_datetime(r.ts, unit="ms")), r.equity) for n, r in results.items()}
    st.plotly_chart(C.bt_equity(curves, colors), width="stretch", theme=None, config={"displaylogo": False})

# ------------------------------------------------------------------ signals
heading(f"Signals · {focus}")
res = results[focus]
t, z = L.z_path(data, fits[focus], res)
i0 = int(np.searchsorted(data.ts, res.ts[0])) if res.ts.size else 0
sl = slice(i0, i0 + res.ts.size)
prices = (pd.Series(pd.to_datetime(data.ts[sl], unit="ms")), data.close_a[sl], data.close_b[sl], a, b)
mk = []
for tr in res.trades:
    s = int(tr["side"])
    mk.append({"time": pd.to_datetime(tr["entry_ts"], unit="ms"), "z": tr["z_entry"], "kind": "long" if s > 0 else "short",
               "text": f"{'BUY' if s > 0 else 'SELL'} spread · Z {tr['z_entry']:+.2f}"})
    if "exit_ts" in tr:
        j = int(np.searchsorted(res.ts, tr["exit_ts"]))
        reason = str(tr.get("reason", ""))
        mk.append({"time": pd.to_datetime(tr["exit_ts"], unit="ms"), "z": float(z[min(j, len(z) - 1)]) if len(z) else np.nan,
                   "kind": "stop" if reason.startswith(("stop", "liquidation")) else "exit",
                   "text": f"CLOSE ({reason}) · PnL {tr.get('pnl', np.nan):+.2f}"})
w = runs[focus]
if res.ts.size:
    st.plotly_chart(C.bt_signals(t, z, prices, pd.DataFrame(mk, columns=["time", "z", "kind", "text"]), w.z_entry, w.z_exit),
                    width="stretch", theme=None, config={"displaylogo": False})
    st.caption("Z as the strategy saw it: refitted every bar while flat, frozen at the entry fit while in a trade. "
               "Fills happen at the next bar's open.")
else:
    st.info("Not enough history in this period for the lookback.")

# ----------------------------------------------------------------- surface
if want_surface:
    heading("Sensitivity · Sharpe over z_entry × z_exit (what-if model, capital and leverage)")
    with st.spinner("Scanning the grid…"):
        surf = L.surface_cached(data, fits[focus], cfg, (*key, cfg.profile), astuple(w), start, end)
    marks = {"calibrated": (cal.z_entry, cal.z_exit), "what-if": (w.z_entry, w.z_exit)}
    st.plotly_chart(C.surface_heatmap(surf, marks), width="stretch", theme=None, config={"displaylogo": False})
    st.caption("A robust choice sits on a broad plateau, not on an isolated bright cell. White box = calibrated, "
               "amber box = what-if (shown when it lies on the grid).")

# ------------------------------------------------------------------- trades
heading(f"Trades · {focus}")
tf = L.trades_frame(res)
if tf.empty:
    st.caption("No trades in this period.")
else:
    st.dataframe(tf.iloc[::-1].style.format({"hours": "{:.0f}", "Z entry": "{:+.2f}", "beta": "{:.4f}", "PnL": "{:+,.2f}",
                                             "PnL %": "{:+.2f}%", "fees": "{:,.2f}", "funding": "{:+,.2f}"}, na_rep="")
                 .map(lambda v: f"color: {GREEN}" if isinstance(v, float) and v > 0 else
                      (f"color: {RED}" if isinstance(v, float) and v < 0 else ""), subset=["PnL", "PnL %"]),
                 width="stretch", hide_index=True, height=360)
    st.download_button("Download trades (CSV)", tf.to_csv(index=False).encode(), f"{pid}_{focus}_trades.csv", "text/csv",
                       on_click="ignore")
st.html(f'<div style="color:{MUTED};font-size:.78rem;margin-top:10px">Use this page to understand a change, not to '
        f'pick the best-looking one: tuning on the same data you judge on overfits (see the validation protocol in '
        f'<code>python -m cryptopairs experiments</code>).</div>')

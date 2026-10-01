"""Pieces shared by every page: the sidebar (context and what-if panel), KPI row, status strip."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.runtime.store import submit_command
from cryptopairs.ui import data as D
from cryptopairs.ui.lab import DELTAS, LOOKBACKS, WhatIf, calibrated, in_use
from cryptopairs.ui.theme import pill, signed

WINDOWS = {"24h": 1, "7d": 7, "30d": 30, "All": None}
REFRESH = [0, 1, 2, 5, 10]


@dataclass
class Ctx:
    """Everything a page needs, decided by the sidebar on each run."""

    state_dir: Path
    db_path: Path
    log_path: Path
    config_path: Path
    data_dir: Path
    brief: dict
    focus: str | None
    view_pairs: list[str]
    window: str
    since_ms: int
    refresh: int
    leverage: float
    what_if: dict[str, WhatIf] = field(default_factory=dict)

    def pair(self, pair_id: str | None) -> dict | None:
        return next((p for p in self.brief["pairs"] if p["id"] == pair_id), None)

    def override(self, pair_id: str | None) -> dict | None:
        return (self.brief.get("overrides") or {}).get(pair_id)

    def live_params(self, pair_id: str) -> WhatIf:
        return in_use(self.pair(pair_id), self.leverage)

    def calibrated(self, pair_id: str) -> WhatIf:
        return calibrated(self.pair(pair_id), self.leverage, self.override(pair_id))

    def what_if_for(self, pair_id: str) -> WhatIf:
        """The sidebar's applied parameters, or the live ones when nothing was recalculated."""
        return self.what_if.get(pair_id) or self.live_params(pair_id)


def context() -> Ctx:
    ctx = st.session_state.get("ctx")
    if ctx is None:
        st.stop()
    return ctx


def send(db_path: Path, kind: str, pair_id: str | None = None, **payload) -> None:
    cid = submit_command(db_path, kind, pair_id, **payload)
    st.toast(f"Sent to the bot: **{kind}** {pair_id or ''} (#{cid}). It runs within ~2 s.", icon="📨")


# ------------------------------------------------------------------ sidebar
def _pair_label(p: dict, overrides: dict) -> str:
    a, b = p["a"].split("/")[0], p["b"].split("/")[0]
    tag = {1: "▲ LONG ", -1: "▼ SHORT ", 0: ""}[p["side"]]
    return f"{tag}{a} / {b}" + ("  ✎" if p["id"] in overrides else "")


def sidebar(state_dir: Path, log_dir: Path, config_path: Path, data_dir: Path, default_db: str | None) -> Ctx | None:
    dbs = sorted(p.name for p in state_dir.glob("*.db"))
    with st.sidebar:
        st.html('<div style="font-size:1.15rem;font-weight:700;letter-spacing:.02em">CryptoPairs '
                '<span style="color:#f0b429">Desk</span></div>'
                '<div style="color:#8b93a5;font-size:.78rem;margin-bottom:6px">Stat-arb · Binance USDⓈ-M perps</div>')
        if not dbs:
            st.warning(f"No bot database in `{state_dir}/` yet. Start the bot first.")
            return None
        idx = dbs.index(default_db) if default_db in dbs else (dbs.index("paper.db") if "paper.db" in dbs else 0)
        db_name = st.selectbox("Bot", dbs, index=idx, help="state database of a running (or stopped) bot")
        db_path = state_dir / db_name
        brief = D.load_brief(str(db_path), D.version(db_path))
        if not brief["pairs"]:
            st.info("The bot has not saved any pair yet.")
            return None
        overrides = brief.get("overrides") or {}
        ids = [p["id"] for p in brief["pairs"]]
        ordered = sorted(brief["pairs"], key=lambda p: (p["side"] == 0, p["id"]))
        labels = {p["id"]: _pair_label(p, overrides) for p in brief["pairs"]}
        focus = st.selectbox("Focus pair", [p["id"] for p in ordered], format_func=labels.get, key="focus",
                             help="drives the Z KPI, the Pair monitor, the Backtest lab and the what-if panel")
        view_pairs = st.multiselect("Pairs in view", ids, default=[], format_func=labels.get, key="view_pairs",
                                    placeholder="all pairs", help="filter for the portfolio tables and charts")
        c1, c2 = st.columns(2)
        window = c1.radio("History", list(WINDOWS), index=1, key="window")
        refresh = c2.radio("Live update", REFRESH, index=2, key="refresh",
                           format_func=lambda s: "paused" if s == 0 else f"every {s} s",
                           help="numbers and charts update in place; the bot writes live marks every ~5 s")
        leverage = float((brief.get("config", {}).get("strategy") or {}).get("leverage") or 1.0)

        st.divider()
        what_if = _what_if_panel(brief, focus, leverage)
        st.caption(f"Profile **{brief.get('profile') or '?'}** · decisions on each hourly close · "
                   "live marks every ~5 s · times UTC")
    days = WINDOWS[window]
    return Ctx(
        state_dir=state_dir, db_path=db_path, log_path=log_dir / f"{db_path.stem}.log", config_path=config_path,
        data_dir=data_dir, brief=brief, focus=focus, view_pairs=view_pairs or ids, window=window,
        since_ms=(D.now_ms() - days * 24 * D.HOUR) // 60_000 * 60_000 if days else 0, refresh=int(refresh), leverage=leverage,
        what_if=what_if,
    )


def _what_if_panel(brief: dict, pid: str, leverage: float) -> dict[str, WhatIf]:
    """Sliders for the focus pair. 'Recalculate' applies them to the charts and the lab."""
    store: dict[str, WhatIf] = st.session_state.setdefault("what_if", {})
    pair = next(p for p in brief["pairs"] if p["id"] == pid)
    live = in_use(pair, leverage)
    cur = store.get(pid, live)
    kalman = pair["kalman_delta"] is not None
    st.markdown("**Strategy parameters** · what-if")
    with st.form(f"what_if_{pid}", border=False):
        ze = st.slider("Z entry threshold", 0.5, 4.0, float(cur.z_entry), 0.05, key=f"wi_ze_{pid}",
                       help="enter when |Z| exceeds this (long the spread below −z, short above +z)")
        zx = st.slider("Z exit threshold", 0.0, 2.0, float(cur.z_exit), 0.05, key=f"wi_zx_{pid}",
                       help="exit when Z crosses back through ±this (0 = the mean)")
        lookbacks = sorted({*LOOKBACKS, int(cur.lookback), int(live.lookback)})  # never snap the current value
        if kalman:
            deltas = sorted({*DELTAS, cur.kalman_delta, live.kalman_delta} - {None})
            delta = st.select_slider("Hedge-ratio adaptivity (Kalman δ)", deltas, value=cur.kalman_delta,
                                     key=f"wi_kd_{pid}", format_func=lambda d: f"{d:.0e} · ~{1 / math.sqrt(d) / 24:,.0f} d memory",
                                     help="higher δ = β and the mean adapt faster (shorter memory)")
            lookback = st.select_slider("σ window / warm-up (bars)", lookbacks, value=int(cur.lookback),
                                        key=f"wi_lb_{pid}", format_func=lambda n: f"{n} h · {n / 24:.0f} d")
        else:
            delta = None
            lookback = st.select_slider("Lookback window (rolling mean / β)", lookbacks,
                                        value=int(cur.lookback), key=f"wi_lb_{pid}",
                                        format_func=lambda n: f"{n} h · {n / 24:.0f} d")
        capital = st.number_input("Capital allocation (USDT)", 10.0, 1_000_000.0, float(cur.capital), 50.0,
                                  key=f"wi_cap_{pid}", help="what-if only: sizes the backtest")
        lev = st.slider("Leverage", 1.0, 5.0, float(cur.leverage), 0.5, key=f"wi_lev_{pid}",
                        help="what-if only: notional = leverage × pair equity")
        b1, b2 = st.columns(2)
        go = b1.form_submit_button("Recalculate", type="primary", width="stretch")
        reset = b2.form_submit_button("Reset", width="stretch")
    if go:
        if not zx < ze:
            st.error("Z exit must be below Z entry.")
        else:
            store[pid] = WhatIf(round(ze, 4), round(zx, 4), int(lookback), delta, float(capital), float(lev))
            st.session_state["what_if_applied"] = D.now_ms()
    if reset:
        store.pop(pid, None)
        for k in [k for k in st.session_state if str(k).startswith("wi_") and str(k).endswith(pid)]:
            del st.session_state[k]
        st.rerun()
    applied = store.get(pid)
    if applied and applied != live:
        diff = [n for n, a, b in (("Z entry", applied.z_entry, live.z_entry), ("Z exit", applied.z_exit, live.z_exit),
                                  ("lookback", applied.lookback, live.lookback),
                                  ("δ", applied.kalman_delta, live.kalman_delta),
                                  ("capital", applied.capital, live.capital), ("leverage", applied.leverage, live.leverage))
                if a != b]
        st.html(pill("WHAT-IF ACTIVE", "warn") + f'<span style="color:#8b93a5;font-size:.78rem">{", ".join(diff)}</span>')
    else:
        st.html(pill("LIVE PARAMETERS", "ok"))
    return store


# --------------------------------------------------------------- live strip
def status_strip(d: dict, ctx: Ctx, curve: pd.Series, rows: pd.DataFrame, capital: float) -> None:
    """One line on top of every page: bot heartbeat, mode, equity, today, open positions, next bar."""
    kv = D.kv_dict(d)
    age = D.heartbeat_age(d)
    if age is None:
        bot = pill("NO LIVE DATA", "mute")
    elif age < 60:
        bot = pill(f"● LIVE · {age:.0f}s", "ok")
    else:
        bot = pill(f"STALE · {age / 60:.0f} min", "bad")
    if kv.get("risk.kill_switch"):
        mode = pill("KILL SWITCH", "bad")
    elif kv.get("entries_paused"):
        mode = pill("ENTRIES PAUSED", "warn")
    else:
        mode = pill("TRADING", "info")
    total = float(curve.iloc[-1]) if len(curve) else float(rows["equity"].sum())
    day0 = float(d["day_start"]["equity"].iloc[0]) if not d["day_start"].empty else total
    now = D.now_ms()
    nxt = (now // D.HOUR + 1) * D.HOUR - now
    n_open = int((rows["side"] != 0).sum())
    st.html(
        f'<div class="strip">{bot}{mode}'
        f'<span><span class="k">Equity</span><b>{total:,.2f}</b> {signed(100 * (total / capital - 1), "{:+.2f}%")}</span>'
        f'<span><span class="k">Today</span>{signed(total - day0)}</span>'
        f'<span><span class="k">Open</span><b>{n_open}</b> / {len(rows)}</span>'
        f'<span><span class="k">Next bar</span><b>{nxt // 60000:02d}:{nxt // 1000 % 60:02d}</b></span>'
        f'<span><span class="k">Profile</span>{ctx.brief.get("profile") or "?"}</span>'
        f'<span><span class="k">Bot</span>{ctx.db_path.stem}</span></div>')


def kpi_row(d: dict, ctx: Ctx, curve: pd.Series, rows: pd.DataFrame, capital: float, cfg: dict) -> None:
    kv = D.kv_dict(d)
    age = D.heartbeat_age(d)
    perf = D.performance(d, curve)
    total = float(curve.iloc[-1]) if len(curve) else float(rows["equity"].sum())
    day0 = float(d["day_start"]["equity"].iloc[0]) if not d["day_start"].empty else total
    peak = kv.get("risk.peak_equity") or total
    kill_dd = (cfg.get("risk", {}) or {}).get("portfolio_max_drawdown", 0.25)
    open_rows = rows[rows["side"] != 0]
    c = st.columns(8)
    if age is None:
        c[0].metric("Bot", "no data", help="restart the bot to enable live snapshots")
    else:
        c[0].metric("Bot", "LIVE" if age < 60 else "STALE", f"{age:.0f}s ago", delta_color="off" if age < 60 else "inverse")
    spark = curve.iloc[:: max(1, len(curve) // 120)].round(2).tolist() if len(curve) > 2 else None
    c[1].metric("Equity", f"{total:,.2f}", f"{100 * (total / capital - 1):+.2f}% on {capital:,.0f}",
                chart_data=spark, chart_type="area")
    c[2].metric("Daily PnL", f"{total - day0:+,.2f}", f"{100 * (total / day0 - 1):+.2f}%" if day0 else None,
                help="equity now vs the first snapshot after 00:00 UTC")
    c[3].metric("Open", f"{len(open_rows)} / {len(rows)}",
                f"gross {open_rows['notional'].sum():,.0f} USDT" if len(open_rows) else "flat", delta_color="off",
                help=f"unrealized {open_rows['unrealized'].sum():+,.4f} USDT")
    sh = perf["sharpe"]
    c[4].metric(f"Sharpe ({ctx.window})", f"{sh:.2f}" if np.isfinite(sh) else "—",
                f"{perf['hours']} h of bars", delta_color="off",
                help="annualised (√8760) from hourly bar-close equity; needs ≥ 24 bars and means little "
                     "before a few weeks of trading")
    mdd = perf["max_dd"]
    head = 100 * (total / (peak * (1 - kill_dd)) - 1)
    c[5].metric(f"Max DD ({ctx.window})", f"{100 * mdd:.2f}%" if np.isfinite(mdd) else "—",
                f"kill switch {head:.1f}% away", delta_color="off",
                help=f"kill switch at {kill_dd:.0%} below the peak equity {peak:,.2f}")
    fr = rows[rows["pair"] == ctx.focus]
    if len(fr):
        r = fr.iloc[0]
        c[6].metric(f"Z · {r['a']}/{r['b']}", f"{r['Z now']:+.2f}" if np.isfinite(r["Z now"]) else "—",
                    f"entry ±{r['Z entry']:g} · {r['position']}", delta_color="off",
                    help="live Z of the focus pair (mid of the executable buy/sell Z)")
    status = "KILL SWITCH" if kv.get("risk.kill_switch") else ("PAUSED" if kv.get("entries_paused") else "TRADING")
    c[7].metric("Status", status, f"{int(rows['paused'].sum())} paused · {int(rows['override'].sum())} override",
                delta_color="off")


# ------------------------------------------------------------ trade marks
def trade_markers(trades: pd.DataFrame, fit: pd.DataFrame, live: pd.DataFrame) -> tuple[pd.DataFrame, list[tuple]]:
    """Entry/close markers of the bot's trades on the Z chart, and the in-position spans."""
    rows, spans = [], []
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    for t in trades.itertuples():
        side = int(t.side)
        t0 = D.to_time(pd.Series([t.entry_ts]))[0]
        rows.append({"time": t0, "z": t.z_entry, "kind": "long" if side > 0 else "short",
                     "text": f"{'BUY' if side > 0 else 'SELL'} spread · Z {t.z_entry:+.2f} · β {t.beta:.3f}"})
        if pd.notna(t.exit_ts):
            t1 = D.to_time(pd.Series([int(t.exit_ts)]))[0]
            reason = str(t.reason or "")
            kind = "stop" if reason.startswith(("stop", "liquidation")) else "exit"
            pnl = float(t.pnl) if pd.notna(t.pnl) else math.nan
            rows.append({"time": t1, "z": D.z_at(int(t.exit_ts), fit, live), "kind": kind,
                         "text": f"CLOSE ({reason}) · PnL {pnl:+.4f}"})
        else:
            t1 = now
        spans.append((t0, t1, side))
    return pd.DataFrame(rows, columns=["time", "z", "kind", "text"]), spans


def params_table(rows: list[tuple[str, list]], headers: list[str]) -> str:
    """Small HTML table; a cell that differs from the first value column is highlighted."""
    head = "".join(f"<th>{h}</th>" for h in ["", *headers])
    body = []
    for name, vals in rows:
        ref = vals[0]
        cells = "".join(f'<td class="{"diff" if v != ref and v != "—" else ""}">{v}</td>' for v in vals)
        body.append(f"<tr><th>{name}</th>{cells}</tr>")
    return f'<table class="params"><tr>{head}</tr>{"".join(body)}</table>'

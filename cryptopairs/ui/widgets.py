"""Pieces shared by every page: the sidebar (context, view menu, watchlist), the what-if panel,
KPI rows and the status strip."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.runtime.store import submit_command
from cryptopairs.ui import data as D
from cryptopairs.ui import fund as F
from cryptopairs.ui import live as LIVE
from cryptopairs.ui import tv as TV
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
        """The what-if panel's applied parameters, or the live ones when nothing was recalculated."""
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
    """The bot, a compact view menu and the live watchlist (a click picks the focus pair)."""
    dbs = sorted(p.name for p in state_dir.glob("*.db"))
    with st.sidebar:
        st.html('<div style="font-size:1.15rem;font-weight:700;letter-spacing:.02em">CryptoPairs '
                '<span style="color:#f0b429">Desk</span></div>'
                '<div style="color:#8b93a5;font-size:.78rem;margin-bottom:2px">Stat-arb · Binance USDⓈ-M perps</div>')
        if not dbs:
            st.warning(f"No bot database in `{state_dir}/` yet. Start the bot first.")
            return None
        idx = dbs.index(default_db) if default_db in dbs else (dbs.index("paper.db") if "paper.db" in dbs else 0)
        db_name = st.selectbox("Bot", dbs, index=idx, help="state database of a running (or stopped) bot",
                               label_visibility="collapsed" if len(dbs) == 1 else "visible")
        db_path = state_dir / db_name
        brief = D.load_brief(str(db_path), D.version(db_path))
        if not brief["pairs"]:
            st.info("The bot has not saved any pair yet.")
            return None
        overrides = brief.get("overrides") or {}
        ids = [p["id"] for p in brief["pairs"]]
        ordered = sorted(brief["pairs"], key=lambda p: (p["side"] == 0, p["id"]))
        labels = {p["id"]: _pair_label(p, overrides) for p in brief["pairs"]}
        pick = st.session_state.pop("_focus_pick", None)  # a click in the watchlist (previous run)
        if pick in ids:
            st.session_state["focus"] = pick
        elif st.session_state.get("focus") not in ids:
            st.session_state["focus"] = ordered[0]["id"]
        with st.popover("View · pair · refresh", icon=":material/tune:", width="stretch"):
            focus = st.selectbox("Focus pair", [p["id"] for p in ordered], format_func=labels.get, key="focus",
                                 help="or click a pair in the watchlist")
            window = st.segmented_control("History", list(WINDOWS), default="7d", key="window", required=True) or "7d"
            refresh = st.segmented_control("Live update", REFRESH, default=2, key="refresh", required=True,
                                           format_func=lambda x: "off" if x == 0 else f"{x}s",
                                           help="numbers and charts update in place; the bot writes live marks "
                                                "every ~5 s")
            refresh = 2 if refresh is None else refresh
            view_pairs = st.multiselect("Pairs in view", ids, default=[], format_func=labels.get, key="view_pairs",
                                        placeholder="all pairs", help="filter for the portfolio tables and charts")
        leverage = float((brief.get("config", {}).get("strategy") or {}).get("leverage") or 1.0)
        days = WINDOWS[window]
        since_ms = (D.now_ms() - days * 24 * D.HOUR) // 60_000 * 60_000 if days else 0
        _watchlist(db_path, since_ms, int(refresh), focus)
        st.caption(f"Profile **{brief.get('profile') or '?'}** · decisions on each hourly close · live marks every "
                   f"~5 s · times {D.TZ_LABEL}")
    return Ctx(
        state_dir=state_dir, db_path=db_path, log_path=log_dir / f"{db_path.stem}.log", config_path=config_path,
        data_dir=data_dir, brief=brief, focus=focus, view_pairs=view_pairs or ids, window=window,
        since_ms=since_ms, refresh=int(refresh), leverage=leverage,
        what_if=st.session_state.setdefault("what_if", {}),
    )


def watchlist_rows(rows: pd.DataFrame, pending: set[str]) -> list[dict]:
    """Rows for the sidebar watchlist: open positions by PnL, then flat pairs nearest to a signal."""
    num = lambda v: float(v) if v is not None and np.isfinite(v) else None  # noqa: E731
    out = []
    for r in rows.to_dict("records"):
        out.append({"id": r["pair"], "a": r["a"], "b": r["b"], "side": int(r["side"]), "z": num(r["Z now"]),
                    "ze": float(r["Z entry"]), "to_entry": num(r.get("to entry")), "pnl": num(r["PnL"]),
                    "upnl": num(r["unrealized"]), "status": r["status"], "paused": bool(r["paused"]),
                    "override": bool(r["override"]), "plan": bool(r.get("exit plan")), "busy": r["pair"] in pending})
    key = lambda x: ((0, -(x["upnl"] or 0.0)) if x["side"] else  # noqa: E731
                     (1, x["to_entry"] if x["to_entry"] is not None else math.inf))
    return sorted(out, key=key)


def _watchlist(db_path: Path, since_ms: int, refresh: int, focus: str) -> None:
    @st.fragment(run_every=refresh or None)
    def watch() -> None:
        d = D.load_core(str(db_path), D.version(db_path), since_ms)
        if d["pairs"].empty:
            return
        rows = D.pair_rows(d, D.session_cfg(d))
        pending = set(D.query(str(db_path), "SELECT DISTINCT pair_id FROM orders WHERE status='pending'")
                      .get("pair_id", pd.Series(dtype=str)))
        pick = TV.watchlist(watchlist_rows(rows, pending), focus)
        if pick and pick != focus:
            st.session_state["_focus_pick"] = pick
            st.rerun()

    watch()


def what_if_panel(ctx: "Ctx", pid: str, columns: int = 1) -> None:
    """What-if parameters for one pair. 'Recalculate' feeds them to the charts and the Backtest lab."""
    store: dict[str, WhatIf] = st.session_state.setdefault("what_if", {})
    pair = ctx.pair(pid)
    live = in_use(pair, ctx.leverage)
    cur = store.get(pid, live)
    kalman = pair["kalman_delta"] is not None
    with st.form(f"what_if_{pid}", border=False):
        cols = st.columns(columns) if columns > 1 else [st.container()]
        c = lambda i: cols[i % len(cols)]  # noqa: E731
        ze = c(0).slider("Z entry threshold", 0.5, 4.0, float(cur.z_entry), 0.05, key=f"wi_ze_{pid}",
                         help="enter when |Z| exceeds this (long the spread below −z, short above +z)")
        zx = c(1).slider("Z exit threshold", 0.0, 2.0, float(cur.z_exit), 0.05, key=f"wi_zx_{pid}",
                         help="exit when Z crosses back through ±this (0 = the mean)")
        lookbacks = sorted({*LOOKBACKS, int(cur.lookback), int(live.lookback)})  # never snap the current value
        if kalman:
            deltas = sorted({*DELTAS, cur.kalman_delta, live.kalman_delta} - {None})
            delta = c(2).select_slider("Hedge-ratio adaptivity (Kalman δ)", deltas, value=cur.kalman_delta,
                                       key=f"wi_kd_{pid}", format_func=lambda d: f"{d:.0e} ({1 / math.sqrt(d) / 24:,.0f}d)",
                                       help="higher δ = β and the mean adapt faster (shorter memory)")
            lookback = c(3).select_slider("σ window / warm-up (bars)", lookbacks, value=int(cur.lookback),
                                          key=f"wi_lb_{pid}", format_func=lambda n: f"{n} h · {n / 24:.0f} d")
        else:
            delta = None
            lookback = c(2).select_slider("Lookback window (rolling mean / β)", lookbacks, value=int(cur.lookback),
                                          key=f"wi_lb_{pid}", format_func=lambda n: f"{n} h · {n / 24:.0f} d")
        capital = c(4).number_input("Capital allocation (USDT)", 10.0, 1_000_000.0, float(cur.capital), 50.0,
                                    key=f"wi_cap_{pid}", help="what-if only: sizes the backtest")
        lev = c(5).slider("Leverage", 1.0, 5.0, float(cur.leverage), 0.5, key=f"wi_lev_{pid}",
                          help="what-if only: notional = leverage × pair equity")
        b1, b2, _ = st.columns([1, 1, 2] if columns > 1 else [1, 1, 0.01])
        go = b1.form_submit_button("Recalculate", type="primary", width="stretch")
        reset = b2.form_submit_button("Reset", width="stretch")
    if go:
        if not zx < ze:
            st.error("Z exit must be below Z entry.")
        else:
            store[pid] = WhatIf(round(ze, 4), round(zx, 4), int(lookback), delta, float(capital), float(lev))
            st.session_state["what_if_applied"] = D.now_ms()
            st.rerun()  # every chart and table of the page follows
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
        st.html(pill("LIVE PARAMETERS", "ok") + '<span style="color:#8b93a5;font-size:.78rem">move the sliders and '
                "press Recalculate to compare</span>")


# --------------------------------------------------------------- live strip
def status_strip(d: dict, ctx: Ctx, curve: pd.Series, rows: pd.DataFrame, capital: float) -> None:
    """One line on top of every page: bot heartbeat, mode, equity, today, open positions, next bar."""
    kv = D.kv_dict(d)
    age = D.heartbeat_age(d)
    hb = d["heartbeat"]["ts"].iloc[0] if not d["heartbeat"].empty else None
    LIVE.watchdog(int(hb) if hb is not None and pd.notna(hb) else None, ctx.refresh, D.now_ms())
    if age is None:
        bot = pill("NO LIVE DATA", "mute")
    elif age < 60:
        bot = pill(f"● LIVE · {age:.0f}s", "ok")
    else:
        bot = pill(f"BOT DOWN · {ago(age).replace(' ago', '')}", "bad")
    if kv.get("risk.kill_switch"):
        mode = pill("KILL SWITCH", "bad")
    elif kv.get("entries_paused"):
        mode = pill("ENTRIES PAUSED", "warn")
    else:
        mode = pill("TRADING", "info")
    if age is not None and age >= 60:
        hb = D.to_time(int(D.now_ms() - age * 1000))
        st.html(f'<div class="down"><b>● The bot is not running</b>: no live data for {ago(age).replace(" ago", "")} '
                f'(last snapshot {hb:%H:%M:%S} {D.TZ_LABEL} time). Prices, PnL and Z below are frozen at that time, and nothing '
                f'is traded or protected by stops. Start it in a terminal (no <code>--fresh</code>; positions are kept): '
                f'<code>python -m cryptopairs --profile {ctx.brief.get("profile") or "kalman"} paper</code></div>')
    total = float(curve.iloc[-1]) if len(curve) else float(rows["equity"].sum())
    day0 = F.value_before(curve, D.local_now().normalize(), capital)  # the same "today" as the Overview
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


def kpis(key: str):
    """A row of KPI cards that wraps onto more rows when the screen is narrow (see theme.py)."""
    return st.container(horizontal=True, gap="small", key=f"kpi_{key}")


def ago(seconds: float) -> str:
    return f"{seconds:.0f}s ago" if seconds < 90 else (f"{seconds / 60:.0f} min ago" if seconds < 5400
                                                     else f"{seconds / 3600:.1f} h ago")


def kpi_row(d: dict, ctx: Ctx, curve: pd.Series, rows: pd.DataFrame, capital: float, cfg: dict) -> None:
    kv = D.kv_dict(d)
    age = D.heartbeat_age(d)
    perf = D.performance(d, curve)
    total = float(curve.iloc[-1]) if len(curve) else float(rows["equity"].sum())
    day0 = float(d["day_start"]["equity"].iloc[0]) if not d["day_start"].empty else total
    peak = kv.get("risk.peak_equity") or total
    kill_dd = (cfg.get("risk", {}) or {}).get("portfolio_max_drawdown", 0.25)
    open_rows = rows[rows["side"] != 0]
    info = dict(delta_color="off", delta_arrow="off")  # a caption under the value, not a change
    g = kpis("overview")
    if age is None:
        g.metric("Bot", "no data", help="restart the bot to enable live snapshots")
    else:
        g.metric("Bot", "LIVE" if age < 60 else "DOWN", ago(age), delta_color="off" if age < 60 else "inverse",
                 delta_arrow="off", help="time since the bot's last live snapshot (it writes one every ~5 s)")
    g.metric("Equity", f"{total:,.2f}", f"{100 * (total / capital - 1):+.2f}%", help=f"capital {capital:,.2f} USDT")
    g.metric("Daily PnL", f"{total - day0:+,.2f}", f"{100 * (total / day0 - 1):+.2f}%" if day0 else None,
             help=f"equity now vs the first snapshot after midnight ({D.TZ_LABEL} time)")
    g.metric("Open pairs", f"{len(open_rows)} / {len(rows)}",
             f"{open_rows['notional'].sum():,.0f} USDT gross" if len(open_rows) else "flat", **info,
             help=f"unrealized {open_rows['unrealized'].sum():+,.4f} USDT")
    sh = perf["sharpe"]
    g.metric(f"Sharpe {ctx.window}", f"{sh:.2f}" if np.isfinite(sh) else "—", f"{perf['hours']} h of data", **info,
             help="annualised (√8760) from hourly bar-close equity; needs ≥ 24 bars and means little "
                  "before a few weeks of trading")
    mdd = perf["max_dd"]
    head = 100 * (total / (peak * (1 - kill_dd)) - 1)
    g.metric(f"Max DD {ctx.window}", f"{100 * mdd:.2f}%" if np.isfinite(mdd) else "—", f"kill switch {head:.0f}% away",
             **info, help=f"kill switch at {kill_dd:.0%} below the peak equity {peak:,.2f}")
    fr = rows[rows["pair"] == ctx.focus]
    if len(fr):
        r = fr.iloc[0]
        g.metric(f"Z {r['a']}/{r['b']}", f"{r['Z now']:+.2f}" if np.isfinite(r["Z now"]) else "—",
                 f"{r['position'] if r['position'] != '-' else 'flat'} · entry ±{r['Z entry']:g}", **info,
                 help="live Z of the focus pair (mid of the executable buy/sell Z)")
    status = "KILL SWITCH" if kv.get("risk.kill_switch") else ("PAUSED" if kv.get("entries_paused") else "TRADING")
    g.metric("Status", status, f"{int(rows['paused'].sum())} paused · {int(rows['override'].sum())} override", **info)


def params_table(rows: list[tuple[str, list]], headers: list[str]) -> str:
    """Small HTML table; a cell that differs from the first value column is highlighted."""
    head = "".join(f"<th>{h}</th>" for h in ["", *headers])
    body = []
    for name, vals in rows:
        ref = vals[0]
        cells = "".join(f'<td class="{"diff" if v != ref and v != "—" else ""}">{v}</td>' for v in vals)
        body.append(f"<tr><th>{name}</th>{cells}</tr>")
    return f'<table class="params"><tr>{head}</tr>{"".join(body)}</table>'

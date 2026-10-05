"""Overview: the fund at a glance.

NAV and its returns by period, risk ratios, the open book (exposure, funding carry, market
beta), the blotter with quick actions, PnL attribution (spread / fees / funding), daily PnL,
the pairs nearest to a signal and the correlation of the pairs' PnL. Every number comes
from the bot's state database; funding rates and BTC history come from Binance.
"""

import math

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.ui import charts as C
from cryptopairs.ui import data as D
from cryptopairs.ui import fund as F
from cryptopairs.ui import lab as L
from cryptopairs.ui import live as LIVE
from cryptopairs.ui import market as M
from cryptopairs.ui import tv as TV
from cryptopairs.ui import tvcharts as TC
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import AMBER, GREEN, RED, heading, page_title, pill

CARRY_ALERT = 0.5  # % of a position's notional per day: funding that eats a typical spread move in days

ctx = W.context()
page_title("Portfolio overview", f"NAV in USDT · every number comes from the bot's state database · times {D.TZ_LABEL}")


def _sign(v):
    if isinstance(v, (int, float)) and np.isfinite(v):
        return f"color: {GREEN}" if v > 0 else (f"color: {RED}" if v < 0 else "")
    return ""


def _stop(v):
    if isinstance(v, (int, float)) and np.isfinite(v):
        return f"color: {RED}; font-weight: 600" if v < 5 else (f"color: {AMBER}" if v < 10 else "")
    return ""


def _pct(v, d=2, plus=True):
    return "—" if v is None or not np.isfinite(v) else (f"{v:+.{d}f}%" if plus else f"{v:.{d}f}%")


@st.cache_data(ttl=900, show_spinner=False)
def coin_betas(data_dir: str, coins: tuple[str, ...], extend: bool) -> dict[str, float]:
    """Beta of each coin's hourly returns to BTC over the last 30 days (archive + recent Binance bars)."""
    out = {}
    for c in coins:
        if c == "BTC":
            out[c] = 1.0
            continue
        try:
            data, _ = L.pair_history(data_dir, f"{c}/USDT:USDT", "BTC/USDT:USDT", extend)
        except Exception:
            continue
        if len(data) < 200:
            continue
        out[c] = F.beta_to(np.diff(data.log_a[-721:]), np.diff(data.log_b[-721:]))
    return out


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
    kv = D.kv_dict(d)
    nav = float(curve.iloc[-1]) if len(curve) else float(rows["equity"].sum())
    view = rows[rows["pair"].isin(ctx.view_pairs)]
    # Full history for returns by period (the window only limits the charts).
    full = D.equity_series(D.load_core(str(ctx.db_path), D.version(ctx.db_path), 0)) if ctx.since_ms else curve
    rets = F.period_returns(full, capital, D.local_now())
    ret = {r["period"]: r for r in rets.to_dict("records")}
    risk = F.risk_stats(d["equity"]["equity"].to_numpy(dtype=float) if not d["equity"].empty else np.empty(0), curve)
    trades = d["trades"]
    ts = F.trade_stats(trades)
    book = F.book_summary(rows, nav)
    open_rows = rows[rows["side"] != 0]
    insts = M.instruments(tuple(sorted(set(open_rows["sym_a"]) | set(open_rows["sym_b"])))) if len(open_rows) else {}
    carry = F.funding_carry(rows, insts)
    ex = D.exposure(rows)
    betas = coin_betas(str(ctx.data_dir), tuple(sorted(ex.index)), not M.offline()) if len(ex) else {}
    beta_usd, cover = F.market_beta(ex["net"] if len(ex) else pd.Series(dtype=float), betas)
    peak = kv.get("risk.peak_equity") or nav
    kill_dd = (cfg.get("risk", {}) or {}).get("portfolio_max_drawdown", 0.25)
    info = dict(delta_color="off", delta_arrow="off")

    def money(r):
        return (f"{r['PnL']:+,.2f}", _pct(r["return %"])) if r else ("—", None)

    att = F.attribution(rows)
    _performance(d, capital, nav, peak * (1 - kill_dd), trades, rets, att)

    heading("Risk & book")
    g = W.kpis("fund")
    g.metric("NAV", f"{nav:,.2f}", _pct(100 * (nav / capital - 1)), help=f"starting capital {capital:,.2f} USDT")
    for name, label in (("Today", "Today"), ("Month to date", "Month to date"), ("Since inception", "Since inception")):
        v, dlt = money(ret.get(name))
        g.metric(label, v, dlt, help=f"PnL in USDT since {ret[name]['from']:%d/%m %H:%M}" if name in ret else None)
    g.metric(f"Sharpe {ctx.window}", f"{risk['sharpe']:.2f}" if np.isfinite(risk["sharpe"]) else "—",
             f"Sortino {risk['sortino']:.2f}" if np.isfinite(risk["sortino"]) else f"{risk['hours']} h of data", **info,
             help="annualised (√8760) from hourly bar-close NAV; needs ≥ 24 bars and means little before a few weeks")
    g.metric("Volatility", _pct(100 * risk["vol"], 1, plus=False) if np.isfinite(risk["vol"]) else "—", "annualised",
             **info)
    g.metric(f"Max DD {ctx.window}", _pct(100 * risk["max_dd"]) if np.isfinite(risk["max_dd"]) else "—",
             f"now {_pct(100 * risk['dd_now'])}" if np.isfinite(risk["dd_now"]) else None, **info)
    head = 100 * (nav / (peak * (1 - kill_dd)) - 1)
    g.metric("Kill switch", "TRIGGERED" if kv.get("risk.kill_switch") else f"{head:.1f}% away",
             f"at {peak * (1 - kill_dd):,.2f} ({kill_dd:.0%} below peak)", **info)

    k = W.kpis("book")
    k.metric("Gross exposure", f"{book['gross']:,.0f}", f"{book['gross_x']:.2f}x NAV", **info,
             help="sum of both legs' notional over the open pairs, at live prices")
    k.metric("Net exposure", f"{book['net']:+,.0f}", f"{book['net_x']:+.2f}x NAV", **info,
             help="long minus short notional; pairs are dollar-neutral only when β ≈ 1")
    k.metric("BTC-equivalent", f"{beta_usd:+,.1f}", f"USDT of BTC · {cover:.0%} of the book covered", **info,
             help="Σ net exposure per coin × the coin's 30-day beta to BTC: the book moves like this much BTC, "
                  f"so a 1% BTC move is worth about {beta_usd / 100:+,.3f} USDT. Near 0 = market-neutral.")
    total_carry = float(carry.sum()) if len(carry) and carry.notna().all() else math.nan
    k.metric("Funding carry", f"{total_carry:+,.3f}" if np.isfinite(total_carry) else "—", "USDT per day at current rates",
             delta_color="off", delta_arrow="off", help="received (+) or paid (−) by the open legs at the current rates")
    k.metric("Open positions", f"{book['open']} / {book['pairs']}",
             f"{int(rows['paused'].sum())} paused · {int(rows['override'].sum())} override", **info)
    k.metric("Hit rate", f"{ts['hit']:.0%}" if np.isfinite(ts["hit"]) else "—",
             f"{ts['n']} closed trades" + (f" · {ts['manual']} manual" if ts["manual"] else ""), **info)
    num = lambda v: "—" if not np.isfinite(v) else f"{v:+.2f}"  # noqa: E731
    k.metric("Profit factor", f"{ts['profit_factor']:.2f}" if np.isfinite(ts["profit_factor"]) else "—",
             f"avg win {num(ts['avg_win'])} / loss {num(ts['avg_loss'])}" if ts["n"] else "no closed trades", **info,
             help="gross wins / gross losses of the closed trades")
    k.metric("Avg holding", f"{ts['avg_hold_h']:.1f} h" if np.isfinite(ts["avg_hold_h"]) else "—",
             f"best {num(ts['best'])} · worst {num(ts['worst'])}" if ts["n"] else None, **info)

    heading("Blotter · open positions (select a row for actions)")
    blot = view[view["side"] != 0].copy()
    if blot.empty:
        st.caption("No open positions.")
    else:
        blot["carry/day"] = blot["pair"].map(carry)
        blot["carry %"] = 100 * blot["carry/day"] / blot["notional"].where(blot["notional"] > 0)
        blot["costs"] = -(blot["fees"] + blot["funding"])  # as PnL: + received, − paid
        hot = blot[blot["carry %"] <= -CARRY_ALERT]
        if len(hot):
            st.warning("Funding alert: " + "; ".join(
                f"**{r['a']}/{r['b']}** pays {-r['carry/day']:,.2f} USDT a day ({-r['carry %']:.2f}% of its notional) at "
                "the current rates" for r in hot.to_dict("records"))
                + ". The strategy does not price funding in its exits; the Pair desk shows each leg's rate and the "
                  "next settlement.", icon=":material/warning:")
        cols = ["pair", "position", "since", "hours", "notional", "unrealized", "unrealized %", "Z at entry", "Z now",
                "to exit", "to stop %", "carry/day", "carry %", "costs", "beta", "status"]
        fmt = {"hours": "{:.1f}", "notional": "{:,.2f}", "unrealized": "{:+,.4f}", "unrealized %": "{:+.2f}%",
               "Z at entry": "{:+.2f}", "Z now": "{:+.2f}", "to exit": "{:+.2f}", "to stop %": "{:.1f}%",
               "carry/day": "{:+.4f}", "carry %": "{:+.2f}%", "costs": "{:+.4f}", "beta": "{:.3f}"}
        sel = st.dataframe(
            blot[cols].style.format(fmt, na_rep="—").map(_sign, subset=["unrealized", "unrealized %", "carry/day", "costs"])
            .map(_stop, subset=["to stop %"])
            .map(lambda v: f"color: {RED}; font-weight: 700" if isinstance(v, float) and v <= -CARRY_ALERT else "",
                 subset=["carry %"]),
            width="stretch", hide_index=True, placeholder="", key="ov_book", on_select="rerun",
            selection_mode="single-row",
            column_config={"since": st.column_config.DatetimeColumn("since", format="DD/MM HH:mm"),
                           "position": st.column_config.TextColumn("side"),
                           "costs": st.column_config.NumberColumn("fees + funding", help="so far, as PnL: + received, − paid "
                                                                   "(fees are always a cost)"),
                           "carry %": st.column_config.NumberColumn("carry %/day", help="funding per day as % of the "
                                                                    "position's gross notional")})
        picked = sel.selection.rows if sel is not None and hasattr(sel, "selection") else []
        if picked:
            _actions(blot.iloc[picked[0]], d)
        else:
            st.caption("'to exit': Z still to travel to the exit band. 'to stop %': equity headroom before the "
                       "stop-loss, as % of the entry equity. 'carry/day': funding per day at the current rates. Funding "
                       "and fees: + received, − paid.")

    a, b = st.columns([3, 2], gap="medium")
    with a:
        heading("PnL attribution by pair · spread vs costs")
        moved = att[att["pair"].isin(view["pair"]) & (att.drop(columns="pair").abs().sum(axis=1) > 1e-9)]
        if moved.empty:
            st.caption("No PnL yet.")
        else:
            LIVE.chart(C.attribution_bars(moved, height=max(240, 30 * len(moved) + 110)), key="ov_attr")
            st.caption(f"{len(att) - len(moved)} pair(s) without any trade yet are left out.")
    with b:
        heading(f"Daily PnL · {D.TZ_LABEL} days")
        daily = F.daily_pnl(full, capital)
        if daily.empty:
            st.caption("No NAV history yet.")
        else:
            LIVE.chart(C.pnl_calendar(daily), key="ov_calendar")
        heading("Net exposure by coin")
        exv = D.exposure(view)
        if exv.empty:
            st.caption("No open positions.")
        else:
            LIVE.chart(C.exposure_chart(exv, height=260), key="ov_exposure")

    c1, c2 = st.columns([3, 2], gap="medium")
    with c1:
        heading("Signal radar · flat pairs nearest to an entry")
        flat = view[view["side"] == 0].sort_values("to entry")
        cols = ["pair", "Z now", "Z entry", "Z exit", "to entry", "equity", "PnL", "trades", "wins", "status"]
        st.dataframe(
            flat[cols].style.format({"Z now": "{:+.2f}", "Z entry": "{:.2f}", "Z exit": "{:.2f}", "to entry": "{:+.2f}",
                                     "equity": "{:,.2f}", "PnL": "{:+,.2f}"}, na_rep="—")
            .map(_sign, subset=["PnL"])
            .map(lambda v: f"color: {AMBER}; font-weight: 600" if isinstance(v, float) and v <= 0.25 else "",
                 subset=["to entry"]),
            width="stretch", hide_index=True, placeholder="", height=min(38 + 35 * len(flat), 460), key="ov_signals")
        st.caption("'to entry' ≤ 0 = a signal now (taken at the next hourly close if the filters agree); amber = within "
                   "0.25 of a signal.")
    with c2:
        heading("Correlation of hourly PnL between pairs")
        corr = F.pnl_correlation(d["marks"][d["marks"]["pair_id"].isin(view["pair"])] if not d["marks"].empty
                                 else d["marks"])
        if corr.empty:
            st.caption("Needs at least a day of bars with two or more pairs in a position. A fund watches this to "
                       "avoid several 'independent' pairs that are really one bet.")
        else:
            LIVE.chart(C.corr_heatmap(corr), key="ov_corr")


def _hero(pts: pd.DataFrame, nav: float, capital: float, rets: pd.DataFrame, episodes: pd.DataFrame) -> None:
    """One line with the numbers a fund reads first: NAV, today, the high-water mark, drawdowns."""
    cls = lambda v: "pos" if v > 0 else ("neg" if v < 0 else "")  # noqa: E731
    pnl = nav - capital
    pct = 100 * (nav / capital - 1) if capital else math.nan
    today = rets.set_index("period").loc["Today"] if len(rets) and "Today" in set(rets["period"]) else None
    r_pnl = float(today["PnL"]) if today is not None else 0.0
    r_pct = float(today["return %"]) if today is not None else math.nan
    hwm = max(capital, float(pts["nav"].max())) if len(pts) else capital
    at_high = pts[pts["nav"] >= hwm] if len(pts) else pts
    hwm_ms = int(at_high["ts"].iloc[-1]) if len(at_high) else None  # the last time the NAV stood at its high
    dd_now = 100 * (nav / hwm - 1) if hwm else math.nan
    under = (D.now_ms() - hwm_ms) / D.HOUR if hwm_ms is not None and dd_now < -1e-9 else None
    worst = episodes.iloc[0] if len(episodes) else None

    def cell(k: str, v: str, sub: str, c: str = "") -> str:
        return f'<div class="ph"><span class="k">{k}</span><b class="{c}">{v}</b><span class="s">{sub}</span></div>'

    cells = [
        f'<div class="ph main"><span class="k">NAV · USDT</span><b>{nav:,.2f}</b>'
        f'<span class="s {cls(pnl)}">{pnl:+,.2f} ({pct:+.2f}%) since inception</span></div>',
        cell(f"Today · {D.TZ_LABEL}", f"{r_pnl:+,.2f}", f"{r_pct:+.2f}% since midnight" if np.isfinite(r_pct) else "—",
             cls(r_pnl)),
        cell("High-water mark", f"{hwm:,.2f}",
             f"{D.to_time(hwm_ms):%d/%m %H:%M}" if hwm_ms is not None else "the starting capital"),
        cell("Drawdown now", f"{dd_now:+.2f}%" if np.isfinite(dd_now) else "—",
             f"{under:.1f} h under water" if under else "at the high", cls(dd_now)),
        cell("Max drawdown", f"{worst['depth %']:.2f}%" if worst is not None else "0.00%",
             f"trough {D.to_time(int(worst['trough'])):%d/%m %H:%M}" if worst is not None else "none yet",
             "neg" if worst is not None else ""),
        cell("To recover", f"{100 * (hwm / nav - 1):+.2f}%" if 0 < nav < hwm else "—",
             "back to the high-water mark" if nav < hwm else "at a new high"),
    ]
    st.html('<div class="perf-head">' + "".join(cells) + "</div>")


def _performance(d: dict, capital: float, nav: float, kill_level: float, trades: pd.DataFrame,
                 rets: pd.DataFrame, att: pd.DataFrame) -> None:
    """NAV and drawdown over a chosen range, the deepest drawdowns, returns and PnL attribution."""
    nd = D.load_nav(str(ctx.db_path), D.version(ctx.db_path))
    pts = F.nav_points(nd["hourly"], nd["live"])
    now = D.now_ms()
    auto = F.default_timeframe(pts, now)
    tf = st.session_state.get("ov_tf") or auto
    start, end, step = F.timeframe_window(pts, tf, now)
    if not d["sessions"].empty:  # the paper's first session: earlier bar closes only restate the starting capital
        start = max(start, int(d["sessions"]["started_at"].min()))
    offset = int(pd.Timestamp.now(tz=D.TZ).utcoffset().total_seconds() * 1000)
    grid = F.nav_grid(pts, start, end, step, offset, capital)
    episodes = F.drawdown_episodes(pts, capital)
    _hero(pts, nav, capital, rets, episodes)
    bar = st.container(horizontal=True, gap="medium", vertical_alignment="center")
    bar.segmented_control("Timeframe", list(F.TIMEFRAMES), default=auto, key="ov_tf", required=True,
                          label_visibility="collapsed",
                          help="how much time one point of the curve covers; every timeframe shows the whole history "
                               "from the start of paper trading")
    show = bar.toggle("Closed trades on the chart", value=True, key="ov_show_trades")
    coarse = (f" (coarsened to {step // 60_000} min: the history is long)" if step > F.TIMEFRAMES[tf] else "")
    bar.caption(f"from the start of paper trading, {D.to_time(start):%d/%m %H:%M}, to now · one point = {tf}{coarse} · "
                "scroll to zoom, double-click to see everything · a gap = the bot was not running · "
                f"dotted = high-water mark · times {D.TZ_LABEL}")
    labels = {r.pair_id: f"{r.symbol_a.split('/')[0]}/{r.symbol_b.split('/')[0]}" for r in d["pairs"].itertuples()}
    peak_before = F.high_water(pts, start, capital)
    spec = TC.performance_spec(grid, capital=capital, peak_before=peak_before, kill_level=kill_level,
                               trades=trades if show else None, labels=labels,
                               max_dd=F.range_max_drawdown(pts, start, end, peak_before))
    if grid["nav"].notna().sum() < 2:
        st.info("The NAV curve appears a few seconds after the bot starts.")
    spec["fit"] = True  # the whole history stays in view as points are added, until the user zooms
    TV.chart(spec, key="ov_perf", rev=f"perf-{tf}")

    a, b, c = st.columns([5, 4, 4], gap="medium")
    with a:
        heading("Drawdowns · deepest first")
        if episodes.empty:
            st.caption("No drawdown deeper than 0.1% yet.")
        else:
            ep = episodes.head(6)
            t = lambda col: ep[col].map(lambda x: D.to_time(int(x)) if pd.notna(x) else pd.NaT)  # noqa: E731
            st.dataframe(pd.DataFrame({"from peak": t("peak"), "trough": t("trough"), "depth": ep["depth %"],
                                       "recovered": t("recovered"), "fall (h)": ep["fall h"],
                                       "length (h)": ep["length h"]})
                         .style.format({"depth": "{:.2f}%", "fall (h)": "{:.1f}", "length (h)": "{:.1f}"})
                         .map(lambda v: f"color: {RED}" if isinstance(v, float) and v < 0 else "", subset=["depth"]),
                         hide_index=True, width="stretch", placeholder="under water", key="ov_dd",
                         column_config={k: st.column_config.DatetimeColumn(k, format="DD/MM HH:mm")
                                        for k in ("from peak", "trough", "recovered")})
            st.caption("From a high-water mark to the lowest NAV before it was regained. 'length' runs to the "
                       "recovery, or to now while still under water.")
    with b:
        heading("Returns")
        if rets.empty:
            st.caption("No NAV history yet.")
        else:
            st.dataframe(rets.style.format({"PnL": "{:+,.2f}", "return %": "{:+.2f}%"}, na_rep="—")
                         .map(_sign, subset=["PnL", "return %"]),
                         hide_index=True, width="stretch", placeholder="", key="ov_rets",
                         column_config={"from": st.column_config.DatetimeColumn("from", format="DD/MM HH:mm")})
    with c:
        heading("Where the PnL comes from")
        LIVE.chart(C.attribution_waterfall(att, capital, height=250), key="ov_waterfall")


def _actions(r: pd.Series, d: dict) -> None:
    """Quick actions on the selected position; the Pair desk has the full ticket."""
    pid = r["pair"]
    bot_up = (D.heartbeat_age(d) or 1e9) < 60
    st.html(pill(f"{r['position']} {r['a']}/{r['b']}", "ok" if r["side"] > 0 else "bad")
            + f'<span style="color:#8b93a5;font-size:.84rem">uPnL {r["unrealized"]:+,.4f} · Z {r["Z now"]:+.2f} · '
              f'{r["hours"]:.1f} h</span>')
    c = st.columns([1.3, 1.1, 1.1, 1.3, 3])
    if c[0].button("Open in Pair desk", key=f"ov_desk_{pid}", icon=":material/candlestick_chart:", width="stretch"):
        st.session_state["_focus_pick"] = pid
        st.switch_page("ui/views/pair.py")
    sure = c[4].checkbox("Confirm a market close", key=f"ov_conf_{pid}", disabled=not bot_up,
                         help="closes at market now; for maker-first or other sizes use the Pair desk ticket")
    if c[1].button("Close 50%", key=f"ov_half_{pid}", disabled=not (sure and bot_up), width="stretch"):
        W.send(ctx.db_path, "close_pair", pid, fraction=0.5, execution="market")
    if c[2].button("Close 100%", key=f"ov_full_{pid}", disabled=not (sure and bot_up), type="primary", width="stretch"):
        W.send(ctx.db_path, "close_pair", pid, fraction=1.0, execution="market")
    if r["paused"]:
        if c[3].button("Resume entries", key=f"ov_resume_{pid}", width="stretch"):
            W.send(ctx.db_path, "resume_pair", pid)
    elif c[3].button("Pause entries", key=f"ov_pause_{pid}", width="stretch"):
        W.send(ctx.db_path, "pause_pair", pid)
    if not bot_up:
        st.caption("The bot is not running: closing is disabled.")


live()

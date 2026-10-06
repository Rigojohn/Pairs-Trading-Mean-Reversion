"""Pieces of the Pair desk page: the pair's live state, the order ticket, the position card
and the pair profile. The page (ui/views/pair.py) lays them out.

Buttons only queue commands (runtime/commands.py); the bot checks every order again with
its own quotes and risk rules, and the result appears on the page within a couple of seconds.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from cryptopairs.config import AppConfig, load_config
from cryptopairs.research import pairstats as PS
from cryptopairs.ui import data as D
from cryptopairs.ui import lab as L
from cryptopairs.ui import market as M
from cryptopairs.ui import ticket as T
from cryptopairs.ui import widgets as W
from cryptopairs.ui.theme import AMBER, GREEN, MUTED, RED, pill

SIDE_NAME = {1: "LONG", -1: "SHORT", 0: "FLAT"}


def _z(x: float) -> str:
    return "0" if x == 0 else f"{x:+g}"


def rule_text(side: int, z_entry: float, z_exit: float) -> str:
    """The levels in words. A long exits when Z rises back to -z_exit, a short when it falls back
    to +z_exit: on its own side of zero, never across it (checked at each hourly close)."""
    if side > 0:
        return f"exit when Z rises to {_z(-z_exit)}"
    if side < 0:
        return f"exit when Z falls to {_z(z_exit)}"
    exits = "exit 0" if z_exit == 0 else f"exits {_z(-z_exit)} / {_z(z_exit)}"
    return f"long < {_z(-z_entry)} · short > {_z(z_entry)} · {exits}"


@st.cache_resource(show_spinner=False)
def app_config(path: str, profile: str | None) -> AppConfig:
    """The bot profile's full config (fees, execution mode) for previews."""
    try:
        return load_config(path if Path(path).exists() else None, profile)
    except Exception:
        return AppConfig()


@dataclass
class PairState:
    """Everything the desk shows about one pair, read once per refresh."""

    pid: str
    sym_a: str  # exchange symbols, e.g. "BAND/USDT:USDT"
    sym_b: str
    a: str  # labels, e.g. "BAND"
    b: str
    id_a: str  # Binance ids, e.g. "BANDUSDT"
    id_b: str
    digits_a: int
    digits_b: int
    row: pd.Series  # D.pair_rows
    strat: dict  # strategy snapshot
    book: dict  # PairBook
    capital: float
    params: dict  # z_entry, z_exit, lookback, kalman_delta (in use)
    cfg: dict  # session config: strategy, risk
    insts: dict | None
    pending: pd.DataFrame
    bot_age: float | None
    kill_switch: bool
    paused: bool
    commands: pd.DataFrame

    @property
    def side(self) -> int:
        return int(self.strat.get("side", 0))

    @property
    def fit(self) -> dict | None:
        return D.fit_of(self.strat)

    @property
    def bot_up(self) -> bool:
        return self.bot_age is not None and self.bot_age < 60

    @property
    def busy(self) -> bool:
        return not self.pending.empty

    @property
    def scfg(self) -> dict:
        return self.cfg.get("strategy", {}) or {}

    def position(self) -> dict | None:
        """What the browser needs for the live PnL ticker."""
        return D.position_of(self.strat, self.book)

    def market_data(self) -> dict:
        """The live panels' data (js/market.js); the watchlist sends the same per pair (widgets.watchlist_rows)."""
        return {"pid": self.pid, "a": self.id_a, "b": self.id_b, "label_a": self.a, "label_b": self.b, "digits_a": self.digits_a,
                "digits_b": self.digits_b, "fit": self.fit, "position": self.position(),
                "z_entry": self.params["z_entry"]}


def load_state(d: dict, ctx, pid: str, pending: pd.DataFrame) -> PairState | None:
    pr = d["pairs"][d["pairs"]["pair_id"] == pid]
    if pr.empty:
        return None
    pr = pr.iloc[0]
    cfg = _merged_cfg(D.session_cfg(d), app_config(str(ctx.config_path), ctx.brief.get("profile")))
    rows = D.pair_rows(d, cfg)
    insts = M.instruments((pr["symbol_a"], pr["symbol_b"]))
    ia, ib = (insts or {}).get(pr["symbol_a"], {}), (insts or {}).get(pr["symbol_b"], {})
    kv = D.kv_dict(d)
    delta = pr["kalman_delta"] if "kalman_delta" in pr and pd.notna(pr["kalman_delta"]) else None
    return PairState(
        pid=pid, sym_a=pr["symbol_a"], sym_b=pr["symbol_b"], a=pr["symbol_a"].split("/")[0], b=pr["symbol_b"].split("/")[0],
        id_a=ia.get("id") or L.local_symbol(pr["symbol_a"]), id_b=ib.get("id") or L.local_symbol(pr["symbol_b"]),
        digits_a=M.digits(ia.get("tick"), _guess_digits(rows, pid, "mark A")),
        digits_b=M.digits(ib.get("tick"), _guess_digits(rows, pid, "mark B")),
        row=rows[rows["pair"] == pid].iloc[0], strat=json.loads(pr["strategy"]), book=json.loads(pr["book"]),
        capital=float(pr["capital"]),
        params={"z_entry": float(pr["z_entry"]), "z_exit": float(pr["z_exit"]), "lookback": int(pr["lookback"]),
                "kalman_delta": delta},
        cfg=cfg, insts=insts, pending=pending, bot_age=D.heartbeat_age(d),
        kill_switch=bool(kv.get("risk.kill_switch")), commands=d["commands"],
        paused=pid in set(kv.get("paused_pairs", []) or []) or bool(kv.get("entries_paused")),
    )


def warm(ctx) -> None:
    """Start the background warm-up of every pair of the bot (ui/history.py ``warm``): open positions
    first with their whole Spread Z history, then the others' candles and recent bars."""
    if M.offline() or not ctx.brief.get("pairs"):
        return
    from cryptopairs.ui import history as HI

    scfg = _merged_cfg(ctx.brief.get("config") or {}, app_config(str(ctx.config_path), ctx.brief.get("profile")))["strategy"]
    hist = (scfg.get("kalman_history", 8760), scfg.get("kalman_outlier_z", 4.0), int(scfg.get("kalman_max_skips", 48)))
    jobs = [(p["id"], p["a"], p["b"], (int(p["lookback"]), p["kalman_delta"], *hist) if p["side"] else None)
            for p in sorted(ctx.brief["pairs"], key=lambda p: p["side"] == 0)]
    HI.warm(str(ctx.data_dir), jobs, M.CANDLES["1h"])


def _merged_cfg(session: dict, app: AppConfig) -> dict:
    """The running session's strategy and risk settings, completed by the profile's defaults."""
    return {"strategy": {**asdict(app.strategy), **(session.get("strategy") or {})},
            "risk": {**asdict(app.risk), **(session.get("risk") or {})}}


def _guess_digits(rows: pd.DataFrame, pid: str, col: str) -> int:
    """Price decimals from the last mark when Binance's tick size is not available."""
    r = rows[rows["pair"] == pid]
    return M.guess_digits(r[col].iloc[0] if len(r) and pd.notna(r[col].iloc[0]) else None)


# --------------------------------------------------------------- feedback
def feedback(s: PairState) -> None:
    """The last button press for this pair and any order being worked right now."""
    parts = []
    if s.busy:
        o = s.pending.iloc[0]
        age = (D.now_ms() - int(o["created_at"])) / 1000
        parts.append(pill(f"EXECUTING · {o['kind']} · {o['reason']}", "warn")
                     + f'<span style="color:{MUTED};font-size:.8rem">{age:.0f} s · maker orders can take up to the '
                       "timeout, then go at market</span>")
    cmds = s.commands[s.commands["pair_id"] == s.pid] if not s.commands.empty else s.commands
    if len(cmds) and D.now_ms() - int(cmds.iloc[0]["ts"]) < 15 * 60_000:
        r = cmds.iloc[0]
        kind = {"done": "ok", "rejected": "bad"}.get(r["status"], "warn")
        parts.append(pill(f"{r['kind']} → {r['status']}", kind)
                     + f'<span style="color:{MUTED};font-size:.8rem">{r["result"] or "waiting for the bot…"}</span>')
    if parts:
        st.html("<div style='display:flex;flex-direction:column;gap:4px;margin:2px 0 8px 0'>"
                + "".join(f"<div>{p}</div>" for p in parts) + "</div>")


# ----------------------------------------------------------------- ticket
def _legs_table(p: T.Preview, s: PairState) -> None:
    rows = [{"leg": l.label, "order": ("BUY" if l.qty > 0 else "SELL") + (" · maker" if l.maker else ""),
             "qty": abs(l.qty), "est. price": l.price, "vs mid bps": l.slip_bps,
             "notional": l.notional, "fee": l.fee} for l in p.legs]
    df = pd.DataFrame(rows)
    st.dataframe(df.style.format({"qty": "{:,.6g}", "est. price": "{:.6g}", "vs mid bps": "{:+.1f}",
                                  "notional": "{:,.2f}", "fee": "{:.4f}"}, na_rep="—")
                 .map(lambda v: f"color: {GREEN}" if str(v).startswith("BUY") else (f"color: {RED}" if str(v).startswith("SELL") else ""),
                      subset=["order"]),
                 hide_index=True, width="stretch", placeholder="")


def _blocked(s: PairState, opening: bool) -> str | None:
    if not s.bot_up:
        return "The bot is not running: nothing can be sent."
    if s.busy:
        return "An order for this pair is being executed; wait for it to finish."
    if opening and s.kill_switch:
        return "The kill switch is engaged: no new positions (reset it on Risk & controls)."
    return None


def ticket(s: PairState, ctx) -> None:
    """Order ticket: open (flat pair) or close / scale out / exit plan (open position)."""
    cfg = app_config(str(ctx.config_path), ctx.brief.get("profile"))
    taker, maker = cfg.costs.taker_fee, cfg.costs.maker_fee
    maker_default = cfg.runtime.execution == "maker_first"
    books = M.order_books((s.sym_a, s.sym_b))
    tabs = st.tabs(["Open", "Close", "Exit plan"] if not s.side else ["Close", "Exit plan", "Open"])
    by = dict(zip(["Open", "Close", "Exit plan"] if not s.side else ["Close", "Exit plan", "Open"], tabs))
    with by["Open"]:
        _open_tab(s, ctx, books, taker, maker, maker_default)
    with by["Close"]:
        _close_tab(s, ctx, books, taker, maker)
    with by["Exit plan"]:
        _exit_tab(s, ctx)


def _confirm_key(name: str, s: PairState) -> str:
    n = st.session_state.get(f"tk_sent_{s.pid}", 0)  # a new key after each send unticks the box
    return f"tk_{name}_{s.pid}_{n}"


def _sent(s: PairState) -> None:
    st.session_state[f"tk_sent_{s.pid}"] = st.session_state.get(f"tk_sent_{s.pid}", 0) + 1


def _open_tab(s: PairState, ctx, books, taker: float, maker: float, maker_default: bool) -> None:
    if s.side:
        st.caption("The pair already holds a position. Close it first (adding to a position is not supported).")
        return
    if s.strat.get("deactivated"):
        st.warning("This pair is deactivated (its equity fell to the floor): the bot refuses new positions.")
        return
    lev_cfg = float(s.scfg.get("leverage") or 1.0)
    cap = float((s.cfg.get("risk", {}) or {}).get("manual_max_leverage") or lev_cfg)
    row = s.row
    z = row["Z now"]
    hint = 1 if (np.isfinite(z) and z < 0) else -1  # the side the model would favour
    c1, c2 = st.columns(2)
    side_lbl = c1.segmented_control("Side", ["Long spread", "Short spread"], key=f"tk_side_{s.pid}", required=True,
                                    default="Long spread" if hint > 0 else "Short spread",
                                    help=f"Long spread = buy {s.a} / sell {s.b}; short spread = sell {s.a} / buy {s.b}")
    side = 1 if (side_lbl or ("Long spread" if hint > 0 else "Short spread")) == "Long spread" else -1
    ex_lbl = c2.segmented_control("Execution", ["Market", "Maker-first"], key=f"tk_ex_open_{s.pid}", required=True,
                                  default="Maker-first" if maker_default else "Market",
                                  help="Maker-first: the larger leg rests post-only at the best price, each fill is "
                                       "hedged at market; after the timeout the rest goes at market")
    execution = "maker" if ex_lbl == "Maker-first" else "market"
    steps = sorted({*np.round(np.arange(0.25, cap + 1e-9, 0.25), 2).tolist(), lev_cfg, cap})
    lev = st.select_slider("Leverage (gross notional / pair equity)", steps, value=lev_cfg if lev_cfg in steps else steps[-1],
                           key=f"tk_lev_{s.pid}", format_func=lambda x: f"{x:g}x",
                           help=f"the strategy trades at {lev_cfg:g}x; manual orders are capped at {cap:g}x "
                                "(risk.manual_max_leverage)")
    fit = s.fit or {}
    pair = {"symbol_a": s.sym_a, "symbol_b": s.sym_b, "label_a": s.a, "label_b": s.b,
            "equity": float(s.book["cash"]), **{k: fit.get(k) for k in ("alpha", "beta", "sigma")},
            "z_entry": s.params["z_entry"], "z_exit": s.params["z_exit"]}
    p = T.preview_open(pair, side, float(lev), execution, books, s.insts, taker, maker,
                       float(s.scfg.get("stop_loss_pct") or 0.0), s.scfg.get("catastrophe_stop_pct"))
    if p.error:
        st.error(p.error)
        return
    if p.warning:
        st.warning(p.warning)
    else:
        _open_preview(p, s, fit)
    why = _blocked(s, opening=True)
    if why:
        st.warning(why)
    sure = st.checkbox(f"Confirm: {'LONG' if side > 0 else 'SHORT'} {s.a}/{s.b} at {lev:g}x, {ex_lbl or 'Market'}",
                       key=_confirm_key("open", s), disabled=why is not None)
    if st.button("Send order to the bot", type="primary", key=f"tk_send_open_{s.pid}",
                 disabled=not sure or why is not None, width="stretch"):
        W.send(ctx.db_path, "open_pair", s.pid, side=side, leverage=float(lev), execution=execution)
        _sent(s)


def _open_preview(p: T.Preview, s: PairState, fit: dict) -> None:
    _legs_table(p, s)
    k = W.kpis(f"tk_open_{s.pid}")
    k.metric("Z at entry", f"{p.z:+.2f}", "signal" if p.signal else "no signal", delta_color="off", delta_arrow="off",
             help="executable Z for this side (buy Z for long, sell Z for short) under the fit in use")
    k.metric("Gross notional", f"{p.gross:,.2f}", f"hedge {p.hedge_ratio:.3f} vs β {fit.get('beta', math.nan):.3f}",
             delta_color="off", delta_arrow="off")
    k.metric("Est. cost", f"{p.cost:,.4f}", f"{p.cost_bps:.1f} bps of gross", delta_color="off", delta_arrow="off",
             help="fees plus the price paid away from mid, from the live order book")
    k.metric("Stop / catastrophe", f"{p.stop_level:,.2f}",
             f"catastrophe {p.catastrophe_level:,.2f}" if np.isfinite(p.catastrophe_level) else "no catastrophe stop",
             delta_color="off", delta_arrow="off", help="pair equity (USDT) at which the bot closes the position")
    if np.isfinite(p.funding_day):
        k.metric("Funding", f"{-p.funding_next:+,.4f}", f"{-p.funding_day:+,.4f} / day", delta_color="normal",
                 delta_arrow="off", help="USDT received (+) or paid (−) at each leg's next settlement, and per day, "
                                         "at the current rates (legs can settle every 4 h or 8 h)")
    for n in p.notes:
        st.caption("• " + n)


def _close_tab(s: PairState, ctx, books, taker: float, maker: float) -> None:
    if not s.side:
        st.caption("No open position.")
        return
    c1, c2 = st.columns(2)
    frac_lbl = c1.segmented_control("Size", ["25%", "50%", "75%", "100%"], default="100%", key=f"tk_frac_{s.pid}",
                                    required=True)
    fraction = int((frac_lbl or "100%").rstrip("%")) / 100
    ex_lbl = c2.segmented_control("Execution", ["Market", "Maker-first"], default="Market", key=f"tk_ex_close_{s.pid}",
                                  required=True,
                                  help="Market closes in a second. Maker-first is cheaper but can take up to the timeout.")
    execution = "maker" if ex_lbl == "Maker-first" else "market"
    b = s.book
    pair = {"symbol_a": s.sym_a, "symbol_b": s.sym_b, "label_a": s.a, "label_b": s.b, "qty_a": float(b["qty_a"]),
            "qty_b": float(b["qty_b"]), "entry_a": float(b["entry_px_a"]), "entry_b": float(b["entry_px_b"]),
            "cash": float(b["cash"])}
    p = T.preview_close(pair, fraction, execution, books, s.insts, taker, maker)
    if p.error:
        st.error(p.error)
        return
    if p.warning:
        st.warning(p.warning)
    else:
        _legs_table(p, s)
        k = W.kpis(f"tk_close_{s.pid}")
        k.metric("PnL realized", f"{p.realized:+,.4f}", "after this order's fees", delta_color="off", delta_arrow="off")
        k.metric("Est. cost", f"{p.cost:,.4f}", f"{p.cost_bps:.1f} bps of gross", delta_color="off", delta_arrow="off")
        if fraction < 0.999:
            k.metric("Left open", f"{p.remaining[0]:,.6g} / {p.remaining[1]:,.6g}", f"{s.a} / {s.b}",
                     delta_color="off", delta_arrow="off")
    for n in p.notes:
        st.caption("• " + n)
    why = _blocked(s, opening=False)
    if why:
        st.warning(why)
    sure = st.checkbox(f"Confirm: close {fraction:.0%} of {s.a}/{s.b}, {ex_lbl or 'Market'}", key=_confirm_key("close", s),
                       disabled=why is not None)
    if st.button("Send order to the bot", type="primary", key=f"tk_send_close_{s.pid}",
                 disabled=not sure or why is not None, width="stretch"):
        W.send(ctx.db_path, "close_pair", s.pid, fraction=fraction, execution=execution)
        _sent(s)


def _exit_tab(s: PairState, ctx) -> None:
    if not s.side:
        st.caption("The exit plan applies to an open position. For future trades change the parameters below.")
        return
    ze = s.params["z_entry"]
    cat = s.scfg.get("catastrophe_stop_pct")
    stop_cfg = float(s.scfg.get("stop_loss_pct") or 0.2)
    zo, so = s.strat.get("z_exit_override"), s.strat.get("stop_pct_override")
    cur_z = s.params["z_exit"] if zo is None else float(zo)
    cur_stop = stop_cfg if so is None else float(so)
    st.caption(f"For this position only, until it closes. The pair's rule: {rule_text(s.side, ze, s.params['z_exit'])}, "
               f"stop-loss {stop_cfg:.0%}. The catastrophe stop ({cat:.0%}) is a hard limit and stays."
               if cat else "For this position only, until it closes.")
    c1, c2 = st.columns(2)
    z_exit = c1.number_input("Exit at |Z| ≤", 0.0, max(0.05, ze - 0.05), float(min(cur_z, ze - 0.05)), 0.05,
                             key=f"tk_zx_{s.pid}", help="0 = when the spread is back at its mean")
    top = (cat if cat else 1.0) * 100 - 1
    stop = c2.number_input("Stop-loss (% of entry equity)", 1.0, float(top), float(min(cur_stop * 100, top)), 1.0,
                           key=f"tk_sl_{s.pid}")
    entry_eq = float(s.strat.get("entry_equity") or 0.0)
    eq = float(s.row["equity"])
    level = (1 - stop / 100) * entry_eq
    st.html(pill("EXIT PLAN IN FORCE" if (zo is not None or so is not None) else "STRATEGY RULE", "warn" if (zo is not None or so is not None) else "ok")
            + f'<span style="color:{MUTED};font-size:.82rem">new stop at {level:,.2f} USDT · equity now {eq:,.2f}'
            + (" · <b style='color:#f6465d'>below it: closes at the next check</b>" if eq <= level else "") + "</span>")
    why = _blocked(s, opening=False)
    b1, b2 = st.columns(2)
    if b1.button("Apply exit plan", type="primary", key=f"tk_send_exit_{s.pid}", disabled=why is not None,
                 width="stretch"):
        W.send(ctx.db_path, "set_exit_plan", s.pid, z_exit=float(z_exit), stop_loss_pct=float(stop) / 100)
    if b2.button("Back to the strategy's rule", key=f"tk_clear_exit_{s.pid}", width="stretch",
                 disabled=why is not None or (zo is None and so is None)):
        W.send(ctx.db_path, "set_exit_plan", s.pid, z_exit=None, stop_loss_pct=None)


# ---------------------------------------------------------------- position
def _risk_bar(s: PairState) -> str:
    """Equity now on a scale from the catastrophe stop to above the entry equity."""
    r, entry = s.row, float(s.strat.get("entry_equity") or 0.0)
    if not entry:
        return ""
    stop_pct = float(r["stop %"] or 0.0)
    cat = s.scfg.get("catastrophe_stop_pct")
    lo = (1 - (cat or stop_pct * 1.5 or 0.3)) * entry
    hi = entry * (1 + max(0.1, abs(float(r["equity"]) / entry - 1) * 1.3))
    pos = lambda v: max(0.0, min(100.0, 100 * (v - lo) / (hi - lo)))  # noqa: E731
    eq = float(r["equity"])
    marks = [(pos((1 - stop_pct) * entry), "stop", AMBER), (pos(entry), "entry", MUTED)]
    if cat:
        marks.insert(0, (pos((1 - cat) * entry), "catastrophe", RED))
    ticks = "".join(f'<span class="rb-tick" style="left:{x:.1f}%;border-color:{c}"><em style="color:{c}">{n}</em></span>'
                    for x, n, c in marks)
    color = GREEN if eq >= entry else (AMBER if eq > (1 - stop_pct) * entry else RED)
    return (f'<div class="riskbar"><div class="rb-fill" style="width:{pos(eq):.1f}%;background:{color}"></div>{ticks}'
            f'<span class="rb-now" style="left:{pos(eq):.1f}%">{eq:,.2f}</span></div>')


def position_card(s: PairState) -> None:
    r = s.row
    if not s.side:
        st.html(pill("FLAT", "mute") + f'<span style="color:{MUTED};font-size:.84rem">{r["status"] or "active"}</span>')
        cd = s.strat.get("cooldown_until") or -1
        lines = []
        if cd > D.now_ms():
            lines.append(f"Cooldown after a stop until {D.clock(cd, '%d/%m %H:%M')} (automatic entries only).")
        if np.isfinite(r["to entry"]):
            lines.append(f"Z must move {max(0.0, r['to entry']):.2f} more for an automatic entry "
                         f"(Z now {r['Z now']:+.2f}, threshold ±{s.params['z_entry']:g}).")
        lines.append(f"Equity {r['equity']:,.2f} USDT · realized {r['realized']:+,.4f} over {int(r['trades'])} trades.")
        for x in lines:
            st.caption(x)
        return
    b = s.book
    held = (D.now_ms() - D.event_ms(s.strat.get("entry_ts") or 0)) / D.HOUR
    st.html(pill(SIDE_NAME[s.side] + " SPREAD", "ok" if s.side > 0 else "bad")
            + (pill("EXIT PLAN", "warn") if r["exit plan"] else "")
            + f'<span style="color:{MUTED};font-size:.84rem">since {D.clock(D.event_ms(s.strat["entry_ts"]), "%d/%m %H:%M")}'
              f' · {held:.1f} h · Z at entry {r["Z at entry"]:+.2f} · {rule_text(s.side, 0.0, r["exit Z"])}</span>')
    st.html(_risk_bar(s))
    legs = []
    for label, qty, entry, mark in ((s.a, b["qty_a"], b["entry_px_a"], r["mark A"]), (s.b, b["qty_b"], b["entry_px_b"], r["mark B"])):
        legs.append({"leg": label, "side": "long" if qty > 0 else "short", "qty": qty, "entry": entry, "mark": mark,
                     "move %": 100 * (mark / entry - 1) if entry else np.nan, "notional": abs(qty) * mark,
                     "PnL": qty * (mark - entry)})
    lg = pd.DataFrame(legs)
    st.dataframe(lg.style.format({"qty": "{:,.6g}", "entry": "{:.8g}", "mark": "{:.8g}", "move %": "{:+.2f}%",
                                  "notional": "{:,.2f}", "PnL": "{:+,.4f}"}, na_rep="—")
                 .map(lambda v: f"color: {GREEN}" if v == "long" else (f"color: {RED}" if v == "short" else ""), subset=["side"]),
                 hide_index=True, width="stretch", placeholder="", key=f"pd_legs_{s.pid}")
    ratio = legs[1]["notional"] / legs[0]["notional"] if legs[0]["notional"] else np.nan
    k = W.kpis(f"pos_{s.pid}")
    k.metric("Unrealized", f"{r['unrealized']:+,.4f}", f"{r['unrealized %']:+.2f}%" if np.isfinite(r["unrealized %"]) else None,
             help="at the bot's last live mark (every ~5 s)")
    k.metric("To stop", f"{r['to stop %']:.1f}%" if np.isfinite(r["to stop %"]) else "—",
             f"catastrophe {r['to catastrophe %']:.1f}%" if np.isfinite(r["to catastrophe %"]) else None,
             delta_color="off", delta_arrow="off", help="equity headroom as % of the entry equity")
    k.metric("Hedge drift", f"{ratio:.3f}", f"β at entry {float(s.strat.get('beta') or np.nan):.3f}", delta_color="off",
             delta_arrow="off", help="notional B / notional A now vs the hedge ratio the position was sized with")
    k.metric("Funding · fees", f"{-float(b.get('funding_paid', 0.0)):+,.4f}", f"fees {-float(b.get('fees_paid', 0.0)):+,.4f}",
             delta_color="off", delta_arrow="off",
             help="funding received (+) or paid (−) so far by this pair, and its fees (always a cost, −)")


# ----------------------------------------------------------------- profile
def _fmt_pct(v, d=2):
    return "—" if v is None or not np.isfinite(v) else f"{v:+.{d}f}%"


def instruments_table(s: PairState, data_dir: str) -> pd.DataFrame:
    meta = M.chains(data_dir)
    ids = tuple((meta.get(x) or {}).get("coingecko_id", "") for x in (s.id_a, s.id_b))
    cg = M.coingecko(ids)
    rows = {}
    for sym, sid, cid, leg_side in ((s.sym_a, s.id_a, ids[0], s.side), (s.sym_b, s.id_b, ids[1], -s.side)):
        i = (s.insts or {}).get(sym) or {}
        c = cg.get(cid) or {}
        chains = ", ".join((meta.get(sid) or {}).get("chains", [])[:3]) or "—"
        fr = i.get("funding_rate")
        rows[sym.split("/")[0]] = {
            "Name": c.get("name") or "—",
            "Sector (Binance)": ", ".join(i.get("sectors") or []) or "—",
            "Blockchains": chains,
            "Market cap": f"${c['market_cap'] / 1e6:,.0f}M · #{c.get('rank') or '—'}" if c.get("market_cap") else "—",
            "Listed on Binance": f"{i['listed']:%Y-%m-%d}" if i.get("listed") is not None else "—",
            "Price": f"{i['last']:.8g}" if i.get("last") else "—",
            "24h change": _fmt_pct(i.get("change_pct")),
            "24h volume": f"${i['quote_volume'] / 1e6:,.2f}M" if i.get("quote_volume") else "—",
            "Open interest": f"${i['open_interest'] / 1e6:,.2f}M" if i.get("open_interest") else "—",
            "Funding": (f"{100 * fr:+.4f}% / {i.get('funding_hours', 8):g}h {D.funding_word(fr, leg_side)}".rstrip()
                        if fr is not None else "—"),
            "Next funding": D.clock(i["next_funding"]) if i.get("next_funding") else "—",
            "Tick · lot · min notional": (f"{i['tick']:g} · {i['step']:g} · {i['min_notional']:g} USDT"
                                          if i.get("tick") else "—"),
        }
    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False, max_entries=8)
def relationship(data_dir: str, sym_a: str, sym_b: str, extend: bool) -> tuple[dict, pd.DataFrame, dict]:
    """Relationship statistics, a year of rolling correlations and the average funding carry."""
    data, info = L.pair_history(data_dir, sym_a, sym_b, extend)
    if len(data) < 48:
        return {}, pd.DataFrame(), {"error": info.get("error") or "not enough history"}
    stats = PS.relationship(data.log_a, data.log_b)
    n = min(len(data), 365 * 24 + 720)
    la, lb, ts = data.log_a[-n:], data.log_b[-n:], data.ts[-n:]
    ra, rb = np.diff(la, prepend=np.nan), np.diff(lb, prepend=np.nan)
    roll = pd.DataFrame({"ts": ts, "corr_ret": PS.rolling_corr(np.nan_to_num(ra), np.nan_to_num(rb), 720),
                         "corr_price": PS.rolling_corr(la, lb, 720)}).iloc[720:]
    beta = (stats.get("30d") or stats.get("all") or {}).get("beta_ols", math.nan)
    carry = {"long": PS.funding_carry(data.funding_a, data.funding_b, beta, 1),
             "short": PS.funding_carry(data.funding_a, data.funding_b, beta, -1),
             "beta": beta, "error": info.get("error"), "end": int(data.ts[-1])}
    return stats, roll, carry


def stats_table(stats: dict, kalman_beta: float | None) -> pd.DataFrame:
    labels = [("corr_price", "Correlation · log prices", "{:.3f}"), ("corr_ret", "Correlation · hourly returns", "{:.3f}"),
              ("beta_ols", "Hedge ratio β · OLS", "{:.4f}"), ("half_life_h", "Half-life of the OLS spread (h)", "{:,.0f}"),
              ("hurst", "Hurst exponent (< 0.5 reverts)", "{:.3f}"), ("adf_t", "Engle-Granger ADF t", "{:.2f}"),
              ("spread_sd", "Spread σ (log)", "{:.4f}"), ("vol_a", "Annual vol · A", "{:.0%}"),
              ("vol_b", "Annual vol · B", "{:.0%}")]
    out = {}
    for w, st_ in stats.items():
        col = {}
        for k, name, f in labels:
            v = st_.get(k)
            col[name] = "—" if v is None or not np.isfinite(v) else f.format(v)
        if st_.get("adf_t") is not None and np.isfinite(st_.get("adf_t", np.nan)):
            col["Engle-Granger ADF t"] += f" ({'cointegrated at ' + st_['adf_level'] if st_.get('adf_level') else 'not cointegrated'})"
        out[w] = col
    df = pd.DataFrame(out)
    if kalman_beta is not None:
        df.loc["Hedge ratio β · Kalman in use"] = [f"{kalman_beta:.4f}"] + [""] * (df.shape[1] - 1)
    return df


@st.cache_data(ttl=600, show_spinner=False)
def calibration_record(profile: str | None, pid: str) -> dict | None:
    """The pair's row in runs/<profile>/test_pairs.csv (out-of-sample backtest at calibration)."""
    p = Path("runs") / (profile or "latest") / "test_pairs.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    key = pid.replace("_", "-")
    r = df[(df["pair"] == key) | (df["pair"] == pid)]
    return r.iloc[0].to_dict() if len(r) else None


def track_record(s: PairState, trades: pd.DataFrame, cal: dict | None) -> pd.DataFrame:
    closed = trades[trades["exit_ts"].notna()] if len(trades) else trades
    live = {}
    if len(closed):
        hold = (closed["exit_ts"] - closed["entry_ts"]) / D.HOUR
        manual = int((closed.get("entry_reason", pd.Series(dtype=object)) == "manual").sum()) if "entry_reason" in closed else 0
        live = {"Trades": f"{len(closed)}" + (f" ({manual} manual)" if manual else ""),
                "Win rate": f"{(closed['pnl'] > 0).mean():.0%}",
                "Total PnL (USDT)": f"{closed['pnl'].sum():+,.4f}",
                "Return on capital": f"{closed['pnl'].sum() / s.capital:+.2%}",
                "Avg PnL / trade": f"{closed['pnl'].mean():+,.4f}",
                "Avg holding (h)": f"{hold.mean():.1f}",
                "Stops": f"{closed['reason'].fillna('').str.startswith('stop').sum()}",
                "Fees · funding": f"{-closed['fees'].sum():+,.4f} · {-closed['funding'].fillna(0).sum():+,.4f}"}
    calc = {}
    if cal:
        calc = {"Trades": f"{cal.get('test_Trades', math.nan):.0f}", "Win rate": f"{cal.get('test_WinRate', math.nan):.0%}",
                "Return on capital": f"{cal.get('test_TotalRet', math.nan):+.2%}",
                "Sharpe (train → test)": f"{cal.get('train_sharpe', math.nan):.2f} → {cal.get('test_Sharpe', math.nan):.2f}",
                "Max drawdown": f"{cal.get('test_MaxDD', math.nan):.1%}", "Stops": f"{cal.get('test_StopLosses', math.nan):.0f}"}
    idx = ["Trades", "Win rate", "Total PnL (USDT)", "Return on capital", "Avg PnL / trade", "Avg holding (h)",
           "Sharpe (train → test)", "Max drawdown", "Stops", "Fees · funding"]
    df = pd.DataFrame({"Backtest at calibration (test window)": [calc.get(i, "") for i in idx],
                       "This bot (paper)": [live.get(i, "") for i in idx]}, index=idx)
    return df[(df != "").any(axis=1)]

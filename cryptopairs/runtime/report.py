"""Read-only views of a StateStore: the ``status`` summary and CSV export."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from cryptopairs.runtime.store import StateStore

SIDE = {1: "LONG", -1: "SHORT", 0: "-"}


def _ts(ms) -> str:
    if ms is None or pd.isna(ms):
        return ""
    return pd.Timestamp(int(ms), unit="ms").strftime("%Y-%m-%d %H:%M")


def status_text(store: StateStore, last_trades: int = 10) -> str:
    out: list[str] = [f"State database: {store.path}"]
    sessions = store.query("SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1")
    if sessions:
        s = sessions[0]
        state = "stopped " + _ts(s["ended_at"]) if s["ended_at"] else "running (or stopped abruptly)"
        out.append(f"Last session: {s['id']} | profile {s['profile']} | started {_ts(s['started_at'])} UTC | {state}")

    pairs = store.load_pairs()
    capital = sum(p.capital for p in pairs.values())
    eq = store.query("SELECT * FROM equity ORDER BY bar_ts DESC LIMIT 1")
    if eq:
        e = eq[0]
        ret = (e["equity"] / capital - 1) * 100 if capital else float("nan")
        out.append(f"Equity at {_ts(e['bar_ts'])} UTC: {e['equity']:.2f} (start {capital:.2f}, {ret:+.2f}%)")
    kill = store.kv_get("risk.kill_switch", False)
    out.append(f"Kill switch: {'ENGAGED - no new entries' if kill else 'off'}")

    open_rows = []
    for p in pairs.values():
        st = p.strategy
        if st.get("side", 0) != 0:
            t = p.open_trade or {}
            open_rows.append({
                "pair": p.pair_id, "side": SIDE[st["side"]], "since (UTC)": _ts(st.get("entry_ts")),
                "z_entry": t.get("z_entry"), "beta": st.get("beta"),
                "qty_a": p.book["qty_a"], "qty_b": p.book["qty_b"], "entry_equity": st.get("entry_equity"),
            })
    out.append(f"\nOpen positions: {len(open_rows)} of {len(pairs)} pairs")
    if open_rows:
        out.append(pd.DataFrame(open_rows).to_string(index=False, float_format=lambda v: f"{v:.4g}"))
    flags = [p.pair_id for p in pairs.values() if p.strategy.get("deactivated")]
    if flags:
        out.append(f"Deactivated pairs: {', '.join(flags)}")

    closed = store.query("SELECT * FROM trades WHERE exit_ts IS NOT NULL ORDER BY exit_ts")
    if closed:
        df = pd.DataFrame(closed)
        wins = (df["pnl"] > 0).mean() * 100
        out.append(f"\nClosed trades: {len(df)} | win rate {wins:.0f}% | total PnL {df['pnl'].sum():+.4f}"
                   f" | fees {df['fees'].sum():.4f} | stop-losses {(df['reason'].str.startswith('stop')).sum()}")
        tail = df.tail(last_trades)
        view = pd.DataFrame({
            "pair": tail["pair_id"], "side": tail["side"].map(SIDE), "entry (UTC)": tail["entry_ts"].map(_ts),
            "exit (UTC)": tail["exit_ts"].map(_ts), "reason": tail["reason"], "pnl": tail["pnl"].round(4),
        })
        out.append(f"Last {len(view)} closed trades:")
        out.append(view.to_string(index=False))
    else:
        out.append("\nClosed trades: none yet")

    problems = store.query(
        "SELECT * FROM events WHERE kind IN ('entry_failed','exit_failed','kill_switch','recovered_pending_orders')"
        " ORDER BY id DESC LIMIT 5"
    )
    if problems:
        out.append("\nRecent problems:")
        for ev in problems:
            out.append(f"  {_ts(ev['ts'])} {ev['kind']} {ev['pair_id'] or ''} {json.loads(ev['payload'] or '{}')}")
    return "\n".join(out)


def export_csv(store: StateStore, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table, order in (("trades", "id"), ("equity", "bar_ts"), ("orders", "id"), ("fills", "id"),
                         ("events", "id"), ("sessions", "started_at")):
        df = pd.DataFrame(store.query(f"SELECT * FROM {table} ORDER BY {order}"))
        for col in ("entry_ts", "exit_ts", "bar_ts", "ts", "created_at", "updated_at", "started_at", "ended_at"):
            if col in df.columns:
                df[col + "_utc"] = df[col].map(_ts)
        path = out_dir / f"{table}.csv"
        df.to_csv(path, index=False)
        written.append(path)
    return written

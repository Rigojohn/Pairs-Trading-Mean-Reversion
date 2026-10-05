"""Read-only access to the bot's state database and the derived tables the pages show.

Everything here reads; nothing writes. Buttons queue commands through
runtime.store.submit_command and the bot executes them (runtime/commands.py).
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st

HOUR = 3_600_000
SIDE = {1: "LONG", -1: "SHORT", 0: "-"}
RAW_RECENT = 2 * HOUR  # live snapshots newer than this are kept at full resolution ...
THIN_BUCKET = 60_000  # ... older ones are thinned to the last one per minute (light charts)
HOURS_PER_YEAR = 24 * 365
# The dashboard shows wall-clock time in this zone (DST included); the bot and its database use UTC.
TZ_NAME = os.environ.get("CRYPTOPAIRS_TZ", "Europe/Athens")
TZ = ZoneInfo(TZ_NAME)
TZ_LABEL = "Athens" if TZ_NAME == "Europe/Athens" else TZ_NAME.split("/")[-1].replace("_", " ")


def event_ms(ts) -> int:
    """When a trade event really happened. Decisions at an hourly close are stamped with the
    open time of that bar (exactly on the hour); stops between bars and manual orders carry
    their real time."""
    ts = int(ts)
    return ts + HOUR if ts % HOUR == 0 else ts


def now_ms() -> int:
    return int(time.time() * 1000)


def to_time(ms):
    """Epoch milliseconds -> naive wall-clock time in the dashboard's zone (a Series stays a Series)."""
    if isinstance(ms, pd.Series):
        return pd.to_datetime(ms, unit="ms", utc=True).dt.tz_convert(TZ).dt.tz_localize(None)
    if np.ndim(ms) == 0:
        if ms is None or (isinstance(ms, float) and math.isnan(ms)):
            return pd.NaT
        return pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert(TZ).tz_localize(None)
    return pd.Series(pd.to_datetime(np.asarray(ms), unit="ms", utc=True).tz_convert(TZ).tz_localize(None))


def local_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=TZ).tz_localize(None)


def day_start_ms(ms: int | None = None) -> int:
    """Epoch ms of the local midnight that starts the day containing ``ms`` (default: now)."""
    t = pd.Timestamp(now_ms() if ms is None else int(ms), unit="ms", tz="UTC").tz_convert(TZ).normalize()
    return int(t.value // 1_000_000)


def clock(ms, fmt: str = "%H:%M") -> str:
    """Epoch ms as local wall-clock text, '—' when missing."""
    t = to_time(ms) if ms is not None else pd.NaT
    return "—" if pd.isna(t) else t.strftime(fmt)


def version(path: Path) -> float:
    """Changes whenever the bot writes (cache key)."""
    return max((p.stat().st_mtime for p in (path, Path(str(path) + "-wal")) if p.exists()), default=0.0)


def query(db_path: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        return pd.read_sql_query(sql, con, params=params)
    except Exception:  # an older database may not have every table yet
        return pd.DataFrame()
    finally:
        con.close()


def thinned(db_path: str, table: str, where: str, params: tuple, since_ms: int) -> pd.DataFrame:
    """Rows of a live-snapshot table since ``since_ms``: full detail for the last two hours,
    one row per minute before that (the last of each minute)."""
    recent = now_ms() - RAW_RECENT
    old = query(db_path, f"SELECT *, MAX(ts) AS _last FROM {table} WHERE {where} AND ts >= ? AND ts < ? "
                         f"GROUP BY ts / {THIN_BUCKET}", (*params, since_ms, recent))
    new = query(db_path, f"SELECT * FROM {table} WHERE {where} AND ts >= ? ORDER BY ts", (*params, max(since_ms, recent)))
    if old.empty:
        return new
    old = old.drop(columns="_last")
    return pd.concat([old, new], ignore_index=True).sort_values("ts", ignore_index=True)


# ------------------------------------------------------------------ loading
@st.cache_data(ttl=5, show_spinner=False, max_entries=8)
def load_core(db_path: str, version: float, since_ms: int) -> dict[str, pd.DataFrame]:
    q = lambda sql, p=(): query(db_path, sql, p)  # noqa: E731
    return {
        "sessions": q("SELECT * FROM sessions ORDER BY started_at"),
        "pairs": q("SELECT * FROM pair_state ORDER BY pair_id"),
        "equity": q("SELECT * FROM equity WHERE bar_ts >= ? ORDER BY bar_ts", (since_ms - HOUR,)),
        "equity_live": thinned(db_path, "equity_live", "1=1", (), since_ms),
        "day_start": q("SELECT equity FROM equity_live WHERE ts >= ? ORDER BY ts LIMIT 1", (day_start_ms(),)),
        "marks": q("SELECT * FROM pair_marks WHERE bar_ts >= ? ORDER BY bar_ts", (since_ms - HOUR,)),
        "last_marks": q("SELECT p.* FROM pair_marks p JOIN (SELECT pair_id, MAX(bar_ts) AS ts FROM pair_marks "
                        "GROUP BY pair_id) m ON p.pair_id = m.pair_id AND p.bar_ts = m.ts"),
        "live": q("SELECT p.* FROM pair_live p JOIN (SELECT pair_id, MAX(ts) AS ts FROM pair_live GROUP BY pair_id) m "
                  "ON p.pair_id = m.pair_id AND p.ts = m.ts"),
        "trades": q("SELECT * FROM trades ORDER BY id"),
        "events": q("SELECT * FROM events ORDER BY id DESC LIMIT 500"),
        "kv": q("SELECT * FROM kv"),
        "commands": q("SELECT * FROM commands ORDER BY id DESC LIMIT 200"),
        "heartbeat": q("SELECT MAX(ts) AS ts FROM equity_live"),
    }


@st.cache_data(ttl=5, show_spinner=False, max_entries=4)
def load_nav(db_path: str, version: float) -> dict[str, pd.DataFrame]:
    """The whole NAV history for the performance chart: bar-close NAV and the live marks
    (the last two hours in full, one per minute before that)."""
    live = thinned(db_path, "equity_live", "1=1", (), 0)
    return {"hourly": query(db_path, "SELECT bar_ts, equity FROM equity ORDER BY bar_ts"),
            "live": live[["ts", "equity"]] if not live.empty else pd.DataFrame(columns=["ts", "equity"])}


@st.cache_data(ttl=5, show_spinner=False, max_entries=8)
def load_pair(db_path: str, version: float, pair_id: str, since_ms: int) -> dict[str, pd.DataFrame]:
    q = lambda sql, p=(): query(db_path, sql, p)  # noqa: E731
    return {
        "fit": q("SELECT * FROM fit_history WHERE pair_id=? AND bar_ts >= ? ORDER BY bar_ts", (pair_id, since_ms - HOUR)),
        "live": thinned(db_path, "pair_live", "pair_id=?", (pair_id,), since_ms),
        # The last day at full detail for the intraday charts (1-5-15 min buckets).
        "live_day": q("SELECT * FROM pair_live WHERE pair_id=? AND ts >= ? ORDER BY ts", (pair_id, now_ms() - 24 * HOUR)),
        "pending": q("SELECT * FROM orders WHERE pair_id=? AND status='pending' ORDER BY id", (pair_id,)),
        "reductions": q("SELECT ts, payload FROM events WHERE pair_id=? AND kind='position_reduced' ORDER BY ts",
                        (pair_id,)),
    }


@st.cache_data(ttl=10, show_spinner=False, max_entries=4)
def load_brief(db_path: str, version: float) -> dict:
    """What the sidebar needs: pairs with their side and parameters, profile, overrides."""
    pairs = query(db_path, "SELECT pair_id, symbol_a, symbol_b, lookback, z_entry, z_exit, kalman_delta, capital, "
                            "strategy FROM pair_state ORDER BY pair_id")
    sessions = query(db_path, "SELECT profile, config, started_at FROM sessions ORDER BY started_at")
    kv = query(db_path, "SELECT * FROM kv")
    out = {"pairs": [], "profile": None, "config": {}, "overrides": {}, "first_start": None}
    for r in pairs.itertuples():
        side = int(json.loads(r.strategy).get("side", 0))
        delta = None if r.kalman_delta is None or pd.isna(r.kalman_delta) else float(r.kalman_delta)
        out["pairs"].append({"id": r.pair_id, "a": r.symbol_a, "b": r.symbol_b, "side": side,
                             "lookback": int(r.lookback), "z_entry": float(r.z_entry), "z_exit": float(r.z_exit),
                             "kalman_delta": delta, "capital": float(r.capital)})
    if not sessions.empty:
        out["profile"] = sessions["profile"].iloc[-1]
        out["first_start"] = int(sessions["started_at"].iloc[0])
        try:
            out["config"] = json.loads(sessions["config"].iloc[-1] or "{}")
        except (TypeError, ValueError):
            pass
    out["overrides"] = kv_dict({"kv": kv}).get("param_overrides") or {}
    return out


def kv_dict(d: dict) -> dict:
    kv = d["kv"]
    return {r.key: json.loads(r.value) for r in kv.itertuples()} if not kv.empty else {}


def session_cfg(d: dict) -> dict:
    s = d["sessions"]
    if s.empty or not s["config"].iloc[-1]:
        return {}
    try:
        return json.loads(s["config"].iloc[-1])
    except (TypeError, ValueError):
        return {}


def heartbeat_age(d: dict) -> float | None:
    hb = d["heartbeat"]["ts"].iloc[0] if not d["heartbeat"].empty else None
    return (now_ms() - int(hb)) / 1000 if hb is not None and pd.notna(hb) else None


# --------------------------------------------------------------- portfolio
def equity_series(d: dict) -> pd.Series:
    """Hourly bar-close equity plus live snapshots, by time."""
    parts = []
    if not d["equity"].empty:  # recorded at the bar's close = open time + 1h
        idx = pd.DatetimeIndex(to_time(d["equity"]["bar_ts"] + HOUR))
        parts.append(pd.Series(d["equity"]["equity"].to_numpy(), index=idx))
    if not d["equity_live"].empty:
        idx = pd.DatetimeIndex(to_time(d["equity_live"]["ts"]))
        parts.append(pd.Series(d["equity_live"]["equity"].to_numpy(), index=idx))
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts).sort_index()
    return s[~s.index.duplicated(keep="last")]


def performance(d: dict, curve: pd.Series) -> dict:
    """Sharpe on hourly bar closes (annualised with sqrt(8760), as in the research) and drawdowns."""
    out = {"sharpe": math.nan, "hours": 0, "max_dd": math.nan, "dd_now": math.nan, "vol": math.nan}
    eq = d["equity"]["equity"].to_numpy(dtype=float) if not d["equity"].empty else np.empty(0)
    if eq.size > 2:
        r = np.diff(eq) / eq[:-1]
        r = r[np.isfinite(r)]
        out["hours"] = int(r.size)
        sd = r.std(ddof=1) if r.size > 1 else 0.0
        if sd > 0:
            out["vol"] = float(sd * math.sqrt(HOURS_PER_YEAR))
            if r.size >= 24:  # below a day of bars the number means nothing
                out["sharpe"] = float(r.mean() / sd * math.sqrt(HOURS_PER_YEAR))
    if len(curve) > 1:
        dd = curve / curve.cummax() - 1
        out["max_dd"], out["dd_now"] = float(dd.min()), float(dd.iloc[-1])
    return out


def pair_rows(d: dict, cfg: dict) -> pd.DataFrame:
    """One row per pair: position, legs, live PnL, Z, distances to exit and stops."""
    strat_cfg = cfg.get("strategy", {}) or {}
    stop_cfg = strat_cfg.get("stop_loss_pct") or 0.0
    cat_pct = strat_cfg.get("catastrophe_stop_pct")
    live = d["live"].set_index("pair_id") if not d["live"].empty else pd.DataFrame()
    marks = d["last_marks"].set_index("pair_id") if not d["last_marks"].empty else pd.DataFrame()
    trades = d["trades"]
    kv = kv_dict(d)
    paused = set(kv.get("paused_pairs", []) or [])
    overrides = kv.get("param_overrides") or {}
    now = now_ms()
    rows = []
    for r in d["pairs"].itertuples():
        st_, book = json.loads(r.strategy), json.loads(r.book)
        # The manual exit plan of an open position (dashboard ticket) replaces the pair's rule.
        stop_pct = stop_cfg if st_.get("stop_pct_override") is None else float(st_["stop_pct_override"])
        z_exit = float(r.z_exit) if st_.get("z_exit_override") is None else float(st_["z_exit_override"])
        src = live.loc[r.pair_id] if r.pair_id in live.index else (
            marks.loc[r.pair_id] if r.pair_id in marks.index else None)
        side = int(st_.get("side", 0))
        equity = float(src["equity"]) if src is not None else float(book["cash"])
        val = lambda k: float(src[k]) if src is not None and k in src and pd.notna(src[k]) else np.nan  # noqa: E731
        z_buy, z_sell = val("z_buy"), val("z_sell")
        z = (z_buy + z_sell) / 2
        mark_a, mark_b = val("mid_a"), val("mid_b")
        entry_eq = st_.get("entry_equity")
        t = trades[trades["pair_id"] == r.pair_id] if not trades.empty else trades
        closed = t[t["exit_ts"].notna()] if len(t) else t
        qa, qb = float(book["qty_a"]), float(book["qty_b"])
        alpha, beta, sigma = st_.get("alpha"), st_.get("beta"), st_.get("sigma")
        spread_now = val("spread_u")
        spread_entry = (math.log(book["entry_px_a"]) - alpha - beta * math.log(book["entry_px_b"])
                        if side and alpha is not None and book["entry_px_a"] > 0 and book["entry_px_b"] > 0 else np.nan)
        delta = None if r.kalman_delta is None or pd.isna(r.kalman_delta) else float(r.kalman_delta)
        row = {
            "pair": r.pair_id, "a": r.symbol_a.split("/")[0], "b": r.symbol_b.split("/")[0],
            "sym_a": r.symbol_a, "sym_b": r.symbol_b,
            "position": SIDE[side], "side": side, "equity": equity, "capital": float(r.capital),
            "PnL": equity - r.capital, "PnL %": 100 * (equity / r.capital - 1) if r.capital else np.nan,
            "unrealized": equity - float(book["cash"]) if side else 0.0,
            "unrealized %": 100 * (equity - float(book["cash"])) / entry_eq if side and entry_eq else np.nan,
            "Z now": z, "z_buy": z_buy, "z_sell": z_sell, "Z entry": float(r.z_entry), "Z exit": float(r.z_exit),
            "exit plan": st_.get("z_exit_override") is not None or st_.get("stop_pct_override") is not None,
            "stop %": stop_pct, "exit Z": z_exit,
            "lookback": int(r.lookback), "kalman_delta": delta,
            "qty A": qa, "qty B": qb, "entry A": book["entry_px_a"] if side else np.nan,
            "entry B": book["entry_px_b"] if side else np.nan, "mark A": mark_a, "mark B": mark_b,
            "notional": abs(qa) * mark_a + abs(qb) * mark_b if side and np.isfinite(mark_a + mark_b) else 0.0,
            "beta": beta, "sigma": sigma, "spread entry": spread_entry, "spread now": spread_now,
            "trades": len(closed), "wins": int((closed["pnl"] > 0).sum()) if len(closed) else 0,
            "realized": float(closed["pnl"].sum()) if len(closed) else 0.0,
            "funding": float(book.get("funding_paid", 0.0)), "fees": float(book.get("fees_paid", 0.0)),
            "override": r.pair_id in overrides, "paused": r.pair_id in paused,
            "status": ", ".join(x for x in (
                "paused" if r.pair_id in paused else "",
                "deactivated" if st_.get("deactivated") else "",
                "cooldown" if (st_.get("cooldown_until") or -1) > now else "",
                "override" if r.pair_id in overrides else "",
                "exit plan" if st_.get("z_exit_override") is not None or st_.get("stop_pct_override") is not None
                else "") if x),
            "since": to_time(pd.Series([event_ms(st_["entry_ts"])]))[0] if side and st_.get("entry_ts", -1) > 0 else pd.NaT,
            "hours": (now - event_ms(st_["entry_ts"])) / HOUR if side and st_.get("entry_ts", -1) > 0 else np.nan,
            "Z at entry": np.nan,
        }
        if side and len(t) and t["exit_ts"].isna().any():
            row["Z at entry"] = float(t[t["exit_ts"].isna()]["z_entry"].iloc[-1])
        if side == 0:  # distance (in Z) still to travel before an entry signal
            row["to entry"] = r.z_entry - max(-z_buy, z_sell) if np.isfinite(z) else np.nan
            row["to exit"] = np.nan
        else:  # long exits when Z rises to -z_exit; short when it falls to +z_exit
            row["to entry"] = np.nan
            row["to exit"] = (-z_exit - z_sell) if side > 0 else (z_buy - z_exit)
        row["to stop %"] = (100 * (equity - (1 - stop_pct) * entry_eq) / entry_eq
                            if side and entry_eq and stop_pct else np.nan)
        row["to catastrophe %"] = (100 * (equity - (1 - cat_pct) * entry_eq) / entry_eq
                                   if side and entry_eq and cat_pct else np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


def exposure(rows: pd.DataFrame) -> pd.DataFrame:
    """Net and gross USDT exposure per coin over all open pairs, at live prices."""
    out: dict[str, float] = {}
    for r in rows[rows["side"] != 0].to_dict("records"):
        for coin, qty, px in ((r["a"], r["qty A"], r["mark A"]), (r["b"], r["qty B"], r["mark B"])):
            if pd.notna(px):
                out[coin] = out.get(coin, 0.0) + qty * px
    s = pd.Series(out, dtype=float)
    return pd.DataFrame({"net": s, "gross": s.abs()}).sort_values("net") if len(s) else pd.DataFrame()


def executions(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows = []
    for r in events[events["kind"] == "execution"].itertuples():
        p = json.loads(r.payload or "{}")
        rows.append({"time": to_time(pd.Series([r.ts]))[0], "pair": r.pair_id, "type": p.get("order_kind", ""),
                     "reason": p.get("reason", ""), "urgent": bool(p.get("urgent", False)),
                     "maker %": 100 * float(p.get("maker_share", 0.0)), "cost bps": float(p.get("cost_bps", 0.0)),
                     "fee bps": float(p.get("fee_bps", 0.0)), "seconds": float(p.get("seconds", 0.0))})
    return pd.DataFrame(rows)


def trade_view(t: pd.DataFrame) -> pd.DataFrame:
    t = t.copy()
    for col in ("exit_ts", "entry_ts", "pnl", "fees", "funding", "z_entry", "beta", "equity_before"):
        t[col] = pd.to_numeric(t[col], errors="coerce")
    origin = t["entry_reason"].fillna("signal") if "entry_reason" in t else pd.Series("signal", index=t.index)
    entry = t["entry_ts"].map(lambda x: event_ms(x) if pd.notna(x) else np.nan)
    exit_ = t["exit_ts"].map(lambda x: event_ms(x) if pd.notna(x) else np.nan)
    return pd.DataFrame({
        "pair": t["pair_id"], "side": t["side"].map(SIDE), "origin": origin,
        "entry": to_time(entry), "exit": to_time(exit_),
        "hours": ((exit_ - entry) / HOUR).round(1),
        "Z entry": t["z_entry"], "beta": t["beta"], "reason": t["reason"].fillna("open"),
        "PnL": t["pnl"], "PnL %": 100 * t["pnl"] / t["equity_before"], "fees": t["fees"], "funding": t["funding"],
    })


def z_at(ts_ms: int, fit: pd.DataFrame, live: pd.DataFrame) -> float:
    """Z closest to a time: the live snapshot if there is one nearby, else the hourly model."""
    if not live.empty:
        j = (live["ts"] - ts_ms).abs().idxmin()
        if abs(live.loc[j, "ts"] - ts_ms) < 5 * 60_000:
            return float((live.loc[j, "z_buy"] + live.loc[j, "z_sell"]) / 2)
    if not fit.empty:
        j = ((fit["bar_ts"] + HOUR) - ts_ms).abs().idxmin()
        return float(fit.loc[j, "z"]) if pd.notna(fit.loc[j, "z"]) else math.nan
    return math.nan


# --------------------------------------------------------------------- logs
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+\s+(\w+)\s+([\w.]+):\s?(.*)$")


@st.cache_data(ttl=3, show_spinner=False, max_entries=4)
def read_log(path: str, mtime: float, max_bytes: int = 2_000_000) -> pd.DataFrame:
    """Tail of a log file as (time, level, source, message); continuation lines join the entry above."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=["time", "level", "source", "message"])
    with p.open("rb") as fh:
        size = fh.seek(0, 2)
        fh.seek(max(0, size - max_bytes))
        text = fh.read().decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # the first line is probably cut
    rows: list[list] = []
    for line in lines:
        m = LOG_LINE.match(line)
        if m:
            rows.append([m.group(1), m.group(2), m.group(3).replace("cryptopairs.", ""), m.group(4)])
        elif rows and line.strip():
            rows[-1][3] += "\n" + line
    df = pd.DataFrame(rows, columns=["time", "level", "source", "message"])
    # The bot logs in UTC (cli._setup_logging); shown in the dashboard's zone.
    df["time"] = (pd.to_datetime(df["time"], errors="coerce").dt.tz_localize("UTC").dt.tz_convert(TZ)
                  .dt.tz_localize(None))
    return df

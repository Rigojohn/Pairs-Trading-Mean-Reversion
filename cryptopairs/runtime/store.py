"""StateStore: SQLite persistence for the trading runtime (one file per mode).

What is stored:
    pair_state  current state of every pair: strategy snapshot, book, open trade
    orders      every order, written as "pending" BEFORE it is executed
    fills       one row per executed leg
    trades      one row per round trip (exit fields empty while open)
    equity      total equity at every bar close
    equity_live total equity every few minutes at live prices (dashboard curve between bars)
    pair_marks  per-pair equity, Z-scores and prices at every bar close (for the dashboard)
    events      notable events (rejections, failures, kill switch, restarts)
    kv          small runtime values (risk peak equity, kill switch)

Crash safety: SQLite in WAL mode with synchronous=FULL. An order is
committed as "pending" before execution. Its fills, the trade row and the
pair state are then committed together in one transaction, so after a crash
the database is either before or after the fill, never in between. On restart, a
"pending" order means the process died mid-execution. In paper mode nothing
real happened, so it is marked "abandoned". Live mode will reconcile it with
the exchange by its client id.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 4
# v1 -> v2: pair_state.kalman_delta (Kalman-filter pairs).
# v2 -> v3: pair_live, fit_history and commands tables (created by the DDL below).
# v3 -> v4: trades.entry_reason ("signal" or "manual": opened from the dashboard).
# Applied in place on open, so the bot restarts on an existing database without --fresh.
MIGRATIONS = {2: ["ALTER TABLE pair_state ADD COLUMN kalman_delta REAL"], 3: [],
              4: ["ALTER TABLE trades ADD COLUMN entry_reason TEXT"]}

LIVE_COLUMNS = ("ts", "pair_id", "side", "equity", "unrealized", "z_buy", "z_sell", "spread_u",
                "alpha", "beta", "sigma", "mid_a", "mid_b", "hs_bps_a", "hs_bps_b")
FIT_COLUMNS = ("bar_ts", "pair_id", "kf_alpha", "kf_beta", "kf_sigma", "used_alpha", "used_beta",
               "used_sigma", "spread_u", "z", "log_a", "log_b")
COMMAND_KINDS = ("close_pair", "pause_pair", "resume_pair", "pause_entries", "resume_entries",
                 "reset_kill_switch", "flatten_all", "set_params", "clear_params", "open_pair", "set_exit_plan",
                 "add_pair", "pause_coin", "resume_coin")

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, mode TEXT NOT NULL, profile TEXT, started_at INTEGER NOT NULL,
    ended_at INTEGER, config TEXT
);
CREATE TABLE IF NOT EXISTS pair_state (
    pair_id TEXT PRIMARY KEY,
    symbol_a TEXT NOT NULL, symbol_b TEXT NOT NULL,
    lookback INTEGER NOT NULL, z_entry REAL NOT NULL, z_exit REAL NOT NULL,
    capital REAL NOT NULL,
    kalman_delta REAL,
    strategy TEXT NOT NULL,
    book TEXT NOT NULL,
    open_trade_id INTEGER,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL UNIQUE,
    session_id TEXT, pair_id TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT,
    symbol_a TEXT NOT NULL, symbol_b TEXT NOT NULL,
    qty_a REAL NOT NULL, qty_b REAL NOT NULL, ref_a REAL, ref_b REAL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'filled', 'failed', 'abandoned')),
    error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    symbol TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, fee REAL NOT NULL, ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id TEXT NOT NULL, session_id TEXT, side INTEGER NOT NULL,
    entry_ts INTEGER NOT NULL, exit_ts INTEGER, reason TEXT,
    z_entry REAL, alpha REAL, beta REAL, sigma REAL, expected_ret REAL,
    qty_a REAL, qty_b REAL, entry_px_a REAL, entry_px_b REAL, exit_px_a REAL, exit_px_b REAL,
    equity_before REAL, equity_after REAL, pnl REAL, fees REAL, funding REAL, entry_reason TEXT
);
CREATE INDEX IF NOT EXISTS trades_pair ON trades(pair_id, entry_ts);
CREATE TABLE IF NOT EXISTS equity (
    bar_ts INTEGER PRIMARY KEY, session_id TEXT, equity REAL NOT NULL,
    open_pairs INTEGER NOT NULL, kill_switch INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS equity_live (
    ts INTEGER PRIMARY KEY, session_id TEXT, equity REAL NOT NULL, open_pairs INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pair_marks (
    bar_ts INTEGER NOT NULL, pair_id TEXT NOT NULL, side INTEGER NOT NULL, equity REAL NOT NULL,
    z_buy REAL, z_sell REAL, mid_a REAL, mid_b REAL, beta REAL,
    PRIMARY KEY (bar_ts, pair_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, session_id TEXT,
    kind TEXT NOT NULL, pair_id TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pair_live (
    ts INTEGER NOT NULL, pair_id TEXT NOT NULL, side INTEGER NOT NULL, equity REAL NOT NULL,
    unrealized REAL, z_buy REAL, z_sell REAL, spread_u REAL, alpha REAL, beta REAL, sigma REAL,
    mid_a REAL, mid_b REAL, hs_bps_a REAL, hs_bps_b REAL,
    PRIMARY KEY (pair_id, ts)
);
CREATE INDEX IF NOT EXISTS pair_live_ts ON pair_live(ts);
CREATE TABLE IF NOT EXISTS fit_history (
    bar_ts INTEGER NOT NULL, pair_id TEXT NOT NULL,
    kf_alpha REAL, kf_beta REAL, kf_sigma REAL, used_alpha REAL, used_beta REAL, used_sigma REAL,
    spread_u REAL, z REAL, log_a REAL, log_b REAL,
    PRIMARY KEY (pair_id, bar_ts)
);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL, pair_id TEXT,
    payload TEXT, status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'rejected')),
    result TEXT, done_ts INTEGER
);
CREATE INDEX IF NOT EXISTS commands_status ON commands(status);
"""

TRADE_COLUMNS = (
    "pair_id", "session_id", "side", "entry_ts", "exit_ts", "reason", "z_entry", "alpha", "beta",
    "sigma", "expected_ret", "qty_a", "qty_b", "entry_px_a", "entry_px_b", "exit_px_a", "exit_px_b",
    "equity_before", "equity_after", "pnl", "fees", "funding", "entry_reason",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _finite(v):
    return v if v is not None and isinstance(v, (int, float)) and math.isfinite(v) else None


def _json(obj) -> str:
    def clean(v):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        return v

    return json.dumps(clean(obj), default=str, allow_nan=False)


@dataclass
class StoredPair:
    pair_id: str
    symbol_a: str
    symbol_b: str
    lookback: int
    z_entry: float
    z_exit: float
    capital: float
    strategy: dict
    book: dict
    open_trade: dict | None
    kalman_delta: float | None = None

    @property
    def params(self):
        from cryptopairs.core.strategy import PairParams

        return PairParams(self.lookback, self.z_entry, self.z_exit, self.kalman_delta)


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # timeout: the dashboard may hold a short write lock while it submits a command
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)  # explicit transactions
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.tx():
            for stmt in DDL.strip().split(";"):
                if stmt.strip():
                    self.conn.execute(stmt)
            row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                self.conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            else:
                version = int(row["value"])
                if version > SCHEMA_VERSION:
                    raise RuntimeError(f"{self.path}: schema v{version} is newer than this code (v{SCHEMA_VERSION})")
                for v in range(version + 1, SCHEMA_VERSION + 1):
                    for stmt in MIGRATIONS.get(v, []):
                        if not self._column_exists(stmt):  # a table the DDL just created has it already
                            self.conn.execute(stmt)
                self.conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        self.session_id: str | None = None

    def _column_exists(self, stmt: str) -> bool:
        m = re.match(r"\s*ALTER TABLE (\w+) ADD COLUMN (\w+)", stmt, re.I)
        if not m:
            return False
        return any(r["name"] == m.group(2) for r in self.conn.execute(f"PRAGMA table_info({m.group(1)})"))

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One atomic transaction: everything inside commits together or not at all."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- sessions
    def begin_session(self, mode: str, profile: str, config: dict | None = None) -> str:
        self.session_id = time.strftime(f"{mode}_%Y%m%d_%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:6]
        with self.tx() as c:
            c.execute("INSERT INTO sessions (id, mode, profile, started_at, config) VALUES (?,?,?,?,?)",
                      (self.session_id, mode, profile, _now_ms(), _json(config or {})))
        return self.session_id

    def end_session(self) -> None:
        if self.session_id:
            with self.tx() as c:
                c.execute("UPDATE sessions SET ended_at=? WHERE id=?", (_now_ms(), self.session_id))

    # ----------------------------------------------------------- pair state
    @staticmethod
    def _pair_row(pr) -> tuple:
        s, p = pr.spec, pr.spec.params
        return (s.id, s.symbol_a, s.symbol_b, p.lookback, p.z_entry, p.z_exit, s.capital, p.kalman_delta,
                _json(pr.strat.snapshot()), _json(asdict(pr.book)), (pr.trade or {}).get("id"), _now_ms())

    def _upsert_pair(self, c: sqlite3.Connection, pr) -> None:
        c.execute(
            """INSERT INTO pair_state (pair_id, symbol_a, symbol_b, lookback, z_entry, z_exit, capital,
                                       kalman_delta, strategy, book, open_trade_id, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(pair_id) DO UPDATE SET
                 symbol_a=excluded.symbol_a, symbol_b=excluded.symbol_b, lookback=excluded.lookback,
                 z_entry=excluded.z_entry, z_exit=excluded.z_exit, capital=excluded.capital,
                 kalman_delta=excluded.kalman_delta,
                 strategy=excluded.strategy, book=excluded.book, open_trade_id=excluded.open_trade_id,
                 updated_at=excluded.updated_at""",
            self._pair_row(pr),
        )

    def save_pairs(self, pairs) -> None:
        with self.tx() as c:
            for pr in pairs:
                self._upsert_pair(c, pr)
                trade = pr.trade or {}
                if trade.get("id") is not None:  # keep the open trade's accrued funding current
                    c.execute("UPDATE trades SET funding=? WHERE id=?", (trade.get("funding", 0.0), trade["id"]))

    # --------------------------------------------------------- live views
    def record_live(self, rows: list[dict]) -> None:
        """Per-pair snapshot at live prices (every few seconds) for the dashboard."""
        with self.tx() as c:
            c.executemany(
                f"INSERT OR REPLACE INTO pair_live VALUES ({','.join('?' * len(LIVE_COLUMNS))})",
                [tuple(_finite(r.get(k)) if k not in ("pair_id",) else r[k] for k in LIVE_COLUMNS) for r in rows],
            )

    def record_fit_history(self, rows: list[dict]) -> None:
        """Hourly model state per pair (filter estimate and the fit actually used)."""
        if not rows:
            return
        with self.tx() as c:
            c.executemany(
                f"INSERT OR REPLACE INTO fit_history VALUES ({','.join('?' * len(FIT_COLUMNS))})",
                [tuple(_finite(r.get(k)) if k != "pair_id" else r[k] for k in FIT_COLUMNS) for r in rows],
            )

    def prune_live(self, older_than_ms: int, equity_older_than_ms: int | None = None) -> int:
        """Delete per-pair live snapshots before ``older_than_ms``. Returns how many.

        Total equity is one small row per mark and is the NAV curve of the dashboard, so it is
        kept longer: before ``older_than_ms`` it is thinned to the last mark of each minute, and
        only marks before ``equity_older_than_ms`` are deleted.
        """
        with self.tx() as c:
            n = c.execute("DELETE FROM pair_live WHERE ts < ?", (older_than_ms,)).rowcount
            c.execute("DELETE FROM equity_live WHERE ts < ? AND ts NOT IN "
                      "(SELECT MAX(ts) FROM equity_live WHERE ts < ? GROUP BY ts / 60000)", (older_than_ms, older_than_ms))
            if equity_older_than_ms is not None:
                c.execute("DELETE FROM equity_live WHERE ts < ?", (equity_older_than_ms,))
        return n

    # ------------------------------------------------------------ commands
    def pending_commands(self) -> list[dict]:
        return self.query("SELECT * FROM commands WHERE status='pending' ORDER BY id")

    def finish_command(self, command_id: int, ok: bool, result: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE commands SET status=?, result=?, done_ts=? WHERE id=?",
                      ("done" if ok else "rejected", result, _now_ms(), command_id))

    def record_live_equity(self, ts: int, equity: float, open_pairs: int) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO equity_live VALUES (?,?,?,?)", (ts, self.session_id, equity, open_pairs))

    def record_marks(self, bar_ts: int, rows: list[dict]) -> None:
        with self.tx() as c:
            for r in rows:
                c.execute(
                    "INSERT OR REPLACE INTO pair_marks VALUES (?,?,?,?,?,?,?,?,?)",
                    (bar_ts, r["pair_id"], r["side"], r["equity"], _finite(r.get("z_buy")),
                     _finite(r.get("z_sell")), r.get("mid_a"), r.get("mid_b"), _finite(r.get("beta"))),
                )

    def load_pairs(self) -> dict[str, StoredPair]:
        out = {}
        for r in self.conn.execute("SELECT * FROM pair_state"):
            trade = None
            if r["open_trade_id"] is not None:
                t = self.conn.execute("SELECT * FROM trades WHERE id=?", (r["open_trade_id"],)).fetchone()
                trade = dict(t) if t is not None else None
            out[r["pair_id"]] = StoredPair(
                r["pair_id"], r["symbol_a"], r["symbol_b"], r["lookback"], r["z_entry"], r["z_exit"],
                r["capital"], json.loads(r["strategy"]), json.loads(r["book"]), trade, r["kalman_delta"],
            )
        return out

    # --------------------------------------------------------------- orders
    def order_pending(self, order) -> tuple[int, str]:
        """Write-ahead: record the order before it is sent. Returns (order id, client id)."""
        client_id = f"cp{uuid.uuid4().hex[:24]}"  # also used as the exchange clientOrderId prefix
        now = _now_ms()
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO orders (client_id, session_id, pair_id, kind, reason, symbol_a, symbol_b,
                                       qty_a, qty_b, ref_a, ref_b, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pending', ?, ?)""",
                (client_id, self.session_id, order.pair_id, order.kind, order.reason, order.symbol_a,
                 order.symbol_b, order.qty_a, order.qty_b, order.ref_a, order.ref_b, now, now),
            )
        return int(cur.lastrowid), client_id

    def order_failed(self, order_id: int, error: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE orders SET status='failed', error=?, updated_at=? WHERE id=?",
                      (error, _now_ms(), order_id))

    def pending_orders(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id")]

    def abandon_pending(self, note: str) -> int:
        with self.tx() as c:
            cur = c.execute("UPDATE orders SET status='abandoned', error=?, updated_at=? WHERE status='pending'",
                            (note, _now_ms()))
        return cur.rowcount

    def _fills(self, c: sqlite3.Connection, order_id: int, report) -> None:
        now = _now_ms()
        c.execute("UPDATE orders SET status='filled', updated_at=? WHERE id=?", (now, order_id))
        for f in report.fills:
            c.execute("INSERT INTO fills (order_id, symbol, qty, price, fee, ts) VALUES (?,?,?,?,?,?)",
                      (order_id, f.symbol, f.qty, f.price, f.fee, now))

    def commit_open(self, order_id: int, report, pr) -> int:
        """Fills + new trade row + pair state, atomically. Sets pr.trade['id']."""
        with self.tx() as c:
            self._fills(c, order_id, report)
            row = {k: pr.trade.get(k) for k in TRADE_COLUMNS}
            row["pair_id"], row["session_id"] = pr.spec.id, self.session_id
            cols = ",".join(TRADE_COLUMNS)
            cur = c.execute(f"INSERT INTO trades ({cols}) VALUES ({','.join('?' * len(TRADE_COLUMNS))})",
                            [row[k] for k in TRADE_COLUMNS])
            pr.trade["id"] = int(cur.lastrowid)
            self._upsert_pair(c, pr)
        return pr.trade["id"]

    def commit_close(self, order_id: int, report, pr, trade: dict) -> None:
        """Fills + completed trade row + flat pair state, atomically."""
        with self.tx() as c:
            self._fills(c, order_id, report)
            if trade.get("id") is not None:
                c.execute(
                    """UPDATE trades SET exit_ts=?, reason=?, exit_px_a=?, exit_px_b=?, equity_after=?,
                                         pnl=?, fees=?, funding=? WHERE id=?""",
                    (trade["exit_ts"], trade["reason"], trade["exit_px_a"], trade["exit_px_b"],
                     trade["equity_after"], trade["pnl"], trade["fees"], trade.get("funding", 0.0), trade["id"]),
                )
            self._upsert_pair(c, pr)

    def commit_reduce(self, order_id: int, report, pr) -> None:
        """Fills of a partial close or an addition + the open trade's fees + pair state, atomically."""
        with self.tx() as c:
            self._fills(c, order_id, report)
            trade = pr.trade or {}
            if trade.get("id") is not None:
                c.execute("UPDATE trades SET fees=?, funding=? WHERE id=?",
                          (trade.get("fees") or 0.0, trade.get("funding") or 0.0, trade["id"]))
            self._upsert_pair(c, pr)

    # ------------------------------------------------------- equity, events
    def record_equity(self, bar_ts: int, equity: float, open_pairs: int, kill_switch: bool) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?,?,?)",
                      (bar_ts, self.session_id, equity, open_pairs, int(kill_switch)))

    def event(self, kind: str, pair_id: str | None = None, **payload) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO events (ts, session_id, kind, pair_id, payload) VALUES (?,?,?,?,?)",
                      (_now_ms(), self.session_id, kind, pair_id, _json(payload)))

    def kv_get(self, key: str, default=None):
        r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def kv_set(self, key: str, value) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, _json(value)))

    # -------------------------------------------------------------- reports
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params)]


def submit_command(db_path: str | Path, kind: str, pair_id: str | None = None, **payload) -> int:
    """Queue a command for the running bot (used by the dashboard). The bot validates and runs it."""
    if kind not in COMMAND_KINDS:
        raise ValueError(f"unknown command {kind!r}")
    con = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        with con:
            cur = con.execute("INSERT INTO commands (ts, kind, pair_id, payload) VALUES (?,?,?,?)",
                              (_now_ms(), kind, pair_id, _json(payload)))
        return int(cur.lastrowid)
    finally:
        con.close()

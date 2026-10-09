"""Telegram alerts for the trader: a coin shock, a burst of stops on one coin, a bot that stopped.

A separate service (``python -m cryptopairs alerts``, docker service ``alerts``) next to the bots.
It only reads their state databases (read-only) and Binance's public prices, and never trades:
the alert tells the trader to look, and the decision (pause the coin, close, nothing) stays theirs.

Why (2026-10-08): OGN rose 53% in 3.5 hours on news. The bots took stops on every OGN pair and then
shorted OGN again in the other OGN pairs, twice. No automatic entry rule passed the historical check,
so the trader decides, with the "Pause by coin" button of Risk & controls.

Alerts, at most one per coin (or bot) per cooldown, all of one check in a single message:
  * shock: a coin traded by a bot moved >= ``move_pct`` over the last ``window_min`` minutes
    (about 18 messages a month over the 48 coins of the bots, 2025-10 .. 2026-09);
  * stops: ``stops_n`` or more stops caused by the same coin in one bot within ``stops_window_h``;
  * down / up: a bot wrote no live equity for ``down_after_min`` minutes, and when it is back;
  * funding: an open pair pays >= ``carry_pct`` of its notional a day at the current rates (OGN, 2026-10:
    about 1.8% a day on the shorts), again after ``carry_cooldown_h``;
  * drawdown: a bot lost ``day_loss_pct`` since midnight (Athens), again at each further step of it,
    and when it nears (``kill_near_pct``) or reaches its kill switch;
  * summary: every day at ``summary_hour`` (Athens): equity, the day's PnL, trades, fees and funding;
  * news: a headline (news sites' RSS, Binance announcements: ``runtime/news.py``) that names a coin of
    the bots' pairs, held or not, checked every ``news_every_s``.

Telegram: ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` in the environment (the server's .env).
Without a token the messages are only logged.
"""

from __future__ import annotations

import html
import json
import logging
import sqlite3
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptopairs.core.strategy import STOP_REASONS
from cryptopairs.runtime.news import NewsItem, coins_in

log = logging.getLogger(__name__)

MINUTE_MS, HOUR_MS = 60_000, 3_600_000
FAPI = "https://fapi.binance.com/fapi/v1"
BOT_NAMES = {"paper": "A", "kalman_b": "B"}  # state/<name>.db -> the name the trader uses
TZ = ZoneInfo("Europe/Athens")  # the trader's day: the dashboard's "Today" starts at the same midnight


@dataclass
class AlertConfig:
    move_pct: float = 0.10          # shock: |price move| over the window
    window_min: int = 60
    cooldown_h: float = 4.0         # per coin (shock) and per bot and coin (stops)
    stops_n: int = 2
    stops_window_h: float = 6.0
    down_after_min: float = 10.0
    check_every_s: float = 120.0
    carry_pct: float = 0.005        # funding paid per day / notional of the pair
    carry_cooldown_h: float = 24.0
    day_loss_pct: float = 0.03      # equity lost since midnight; alerts again at 6%, 9%, ...
    kill_near_pct: float = 0.05     # drawdown within 5 points of the kill switch
    summary_hour: int = 23          # local hour of the daily summary
    news_every_s: float = 300.0
    news_max_age_h: float = 3.0     # older headlines are not news any more (and not after a restart)
    news_max: int = 8               # headlines per message
    dashboard_url: str = "https://cryptobot.tail5610d8.ts.net"


@dataclass
class Move:
    pct: float                      # price move over the window, e.g. 0.15 = +15%
    volume_x: float | None = None   # volume of the window / the coin's median hourly volume of 30 days


@dataclass
class BotView:
    name: str
    coins: set[str] = field(default_factory=set)        # every coin of its pairs
    pairs_per_coin: dict[str, int] = field(default_factory=dict)
    long: dict[str, int] = field(default_factory=dict)  # open positions long / short the coin
    short: dict[str, int] = field(default_factory=dict)
    upnl: dict[str, float] = field(default_factory=dict)  # unrealized PnL of the pairs holding the coin
    paused: set[str] = field(default_factory=set)
    heartbeat_ms: int | None = None
    stops: list[tuple[int, str, float]] = field(default_factory=list)  # (exit ts, culprit coin, pnl)
    # (pair, coin a, coin b, qty a, qty b, mark a, mark b) of each open pair
    legs: list[tuple[str, str, str, float, float, float, float]] = field(default_factory=list)
    equity: float | None = None          # latest live equity
    equity_day: float | None = None      # equity at the local midnight
    peak: float | None = None            # peak equity of the kill switch
    kill_switch: bool = False
    kill_dd: float = 0.25
    closed_today: list[tuple[float, str]] = field(default_factory=list)  # (pnl, reason)
    opened_today: int = 0
    open_pairs: int = 0
    fees_today: float = 0.0


def coin_of(symbol: str) -> str:
    """'OGN/USDT:USDT' or 'OGNUSDT' -> 'OGN'."""
    s = symbol.split("/")[0]
    return s[:-4] if "/" not in symbol and s.endswith("USDT") else s


def culprit(t: dict) -> str | None:
    """The leg that lost the most in a closed trade ('a' or 'b')."""
    try:
        la = t["qty_a"] * (t["exit_px_a"] - t["entry_px_a"])
        lb = t["qty_b"] * (t["exit_px_b"] - t["entry_px_b"])
    except (KeyError, TypeError):
        return None
    return "a" if la <= lb else "b"


def bot_name(db: Path) -> str:
    return BOT_NAMES.get(db.stem, db.stem)


def day_start_ms(ms: int) -> int:
    """Epoch ms of the local (Athens) midnight that starts the day containing ``ms``."""
    t = datetime.fromtimestamp(ms / 1000, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(t.timestamp() * 1000)


def _kv(con: sqlite3.Connection, key: str):
    r = con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    try:
        return json.loads(r["value"]) if r else None
    except ValueError:
        return None


def read_bot(db: Path, since_ms: int, day_ms: int | None = None) -> BotView:
    """What one bot holds now, read-only (the bot keeps writing meanwhile).

    ``day_ms``: the local midnight; the day's equity, trades and fees are read from it."""
    v = BotView(bot_name(db))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        live = {r["pair_id"]: r for r in con.execute(
            "SELECT pair_id, unrealized, mid_a, mid_b FROM pair_live WHERE ts = (SELECT MAX(ts) FROM pair_live)")}
        unreal = {k: r["unrealized"] or 0.0 for k, r in live.items()}
        for r in con.execute("SELECT pair_id, symbol_a, symbol_b, strategy, book FROM pair_state"):
            a, b = coin_of(r["symbol_a"]), coin_of(r["symbol_b"])
            side = int(json.loads(r["strategy"]).get("side", 0))
            book, mark = json.loads(r["book"]), live.get(r["pair_id"])
            if side and mark is not None and mark["mid_a"] and mark["mid_b"]:
                v.legs.append((r["pair_id"], a, b, float(book.get("qty_a") or 0.0), float(book.get("qty_b") or 0.0),
                               float(mark["mid_a"]), float(mark["mid_b"])))
            for c in (a, b):
                v.coins.add(c)
                v.pairs_per_coin[c] = v.pairs_per_coin.get(c, 0) + 1
            if side:  # a long spread is long A and short B
                v.open_pairs += 1
                for c, sign in ((a, side), (b, -side)):
                    book = v.long if sign > 0 else v.short
                    book[c] = book.get(c, 0) + 1
                    v.upnl[c] = v.upnl.get(c, 0.0) + unreal.get(r["pair_id"], 0.0)
        v.paused = set(_kv(con, "paused_coins") or [])
        hb = con.execute("SELECT ts, equity FROM equity_live ORDER BY ts DESC LIMIT 1").fetchone()
        v.heartbeat_ms, v.equity = (int(hb["ts"]), float(hb["equity"])) if hb else (None, None)
        v.peak = _kv(con, "risk.peak_equity")
        v.kill_switch = bool(_kv(con, "risk.kill_switch"))
        cfg = con.execute("SELECT config FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
        try:
            v.kill_dd = float(((json.loads(cfg["config"]) if cfg else {}).get("risk") or {})
                              .get("portfolio_max_drawdown", v.kill_dd))
        except (TypeError, ValueError):
            pass
        if day_ms is not None:
            # equity at midnight: the last value written before it (live: 24 h kept; bar equity: all)
            row = (con.execute("SELECT equity FROM equity_live WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (day_ms,))
                   .fetchone()
                   or con.execute("SELECT equity FROM equity WHERE bar_ts <= ? ORDER BY bar_ts DESC LIMIT 1",
                                  (day_ms,)).fetchone()
                   or con.execute("SELECT equity FROM equity_live WHERE ts >= ? ORDER BY ts LIMIT 1",
                                  (day_ms,)).fetchone())
            v.equity_day = float(row["equity"]) if row else None
            v.closed_today = [(float(r["pnl"] or 0.0), r["reason"] or "") for r in con.execute(
                "SELECT pnl, reason FROM trades WHERE exit_ts >= ?", (day_ms,))]
            v.opened_today = con.execute("SELECT COUNT(*) FROM trades WHERE entry_ts >= ?", (day_ms,)).fetchone()[0]
            v.fees_today = float(con.execute("SELECT COALESCE(SUM(fee), 0) FROM fills WHERE ts >= ?",
                                             (day_ms,)).fetchone()[0])
        for r in con.execute("SELECT * FROM trades WHERE exit_ts >= ? AND reason IN (%s)"
                             % ",".join("?" * len(STOP_REASONS)), (since_ms, *sorted(STOP_REASONS))):
            t = dict(r)
            leg = culprit(t)
            if leg is None:
                continue
            sym = t["pair_id"].split("_")[0 if leg == "a" else 1]
            v.stops.append((int(t["exit_ts"]), coin_of(sym), float(t["pnl"] or 0.0)))
    finally:
        con.close()
    return v


# ------------------------------------------------------------------ Binance
def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def binance_moves(coins: list[str], window_min: int = 60) -> dict[str, Move]:
    """Price move of each coin over the last ``window_min`` minutes, from 5-minute bars."""
    n = window_min // 5
    out = {}
    for c in coins:
        try:
            k = _get(f"{FAPI}/klines?symbol={c}USDT&interval=5m&limit={n + 1}")  # the last bar is still open
            if len(k) >= 2:
                out[c] = Move(float(k[-1][4]) / float(k[0][1]) - 1.0)
        except Exception as exc:  # one coin's failure must not hide the others
            log.debug("price of %s: %s", c, exc)
    return out


def binance_volume_x(coin: str, window_min: int = 60) -> float | None:
    """Volume of the last window / median hourly volume of the previous 30 days (information only)."""
    try:
        h = _get(f"{FAPI}/klines?symbol={coin}USDT&interval=1h&limit=721")
        m = _get(f"{FAPI}/klines?symbol={coin}USDT&interval=5m&limit={window_min // 5}")
        vols = sorted(float(x[7]) for x in h[:-1])
        med = vols[len(vols) // 2] if vols else 0.0
        return sum(float(x[7]) for x in m) / med if med > 0 else None
    except Exception as exc:
        log.debug("volume of %s: %s", coin, exc)
        return None


def binance_funding() -> dict[str, tuple[float, float]]:
    """{'OGN': (current funding rate, hours between settlements)} for every USDⓈ-M symbol: two requests."""
    try:
        hours = {r["symbol"]: float(r["fundingIntervalHours"]) for r in _get(f"{FAPI}/fundingInfo")}
    except Exception as exc:  # only the symbols that differ from 8 h are listed: 8 h is the safe default
        log.debug("funding intervals: %s", exc)
        hours = {}
    out = {}
    for r in _get(f"{FAPI}/premiumIndex"):
        sym = r.get("symbol", "")
        if sym.endswith("USDT") and r.get("lastFundingRate") not in (None, ""):
            out[coin_of(sym)] = (float(r["lastFundingRate"]), hours.get(sym, 8.0))
    return out


def carry_day(qty: float, px: float, rate: float, hours: float) -> float:
    """USDT per day one leg receives (+) or pays (-): longs pay a positive rate, shorts a negative one."""
    return -qty * px * rate * 24.0 / hours


# ------------------------------------------------------------------ Telegram
class Telegram:
    def __init__(self, token: str | None, chat_id: str | None) -> None:
        self.token, self.chat_id = token, chat_id

    @property
    def ready(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.ready:
            log.warning("Telegram is not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); message only logged:\n%s",
                        text)
            return False
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{self.token}/sendMessage", data, timeout=15) as r:
                ok = json.loads(r.read()).get("ok", False)
        except Exception as exc:
            log.error("Telegram send failed: %s", exc)
            return False
        if not ok:
            log.error("Telegram refused the message")
        return bool(ok)

    def chats(self) -> list[tuple[str, str]]:
        """(chat id, name) of everyone who wrote to the bot lately: how the trader finds their chat id."""
        res = _get(f"https://api.telegram.org/bot{self.token}/getUpdates")
        out = {}
        for u in res.get("result", []):
            chat = (u.get("message") or u.get("edited_message") or {}).get("chat") or {}
            if "id" in chat:
                out[str(chat["id"])] = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
        return list(out.items())


# ------------------------------------------------------------------ monitor
def _num(x: float, digits: int = 1) -> str:
    return f"{x:+,.{digits}f}".replace(",", " ").replace(".", ",")


def _money(x: float, digits: int = 0) -> str:
    return f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")


def _pairs(n: int) -> str:
    return f"{n} ζεύγ{'ος' if n == 1 else 'η'}"


class AlertMonitor:
    def __init__(self, state_dir: Path, cfg: AlertConfig, send: Callable[[str], bool],
                 moves: Callable[[list[str]], dict[str, Move]], volume_x: Callable[[str], float | None] | None = None,
                 now: Callable[[], int] = lambda: int(time.time() * 1000),
                 funding: Callable[[], dict[str, tuple[float, float]]] | None = None,
                 news: Callable[[], list[NewsItem]] | None = None) -> None:
        self.state_dir, self.cfg, self.send, self.moves, self.volume_x, self.now, self.funding, self.news = (
            Path(state_dir), cfg, send, moves, volume_x, now, funding, news)
        self.memory_path = self.state_dir / "alerts.json"
        try:
            self.memory = json.loads(self.memory_path.read_text())
        except (OSError, ValueError):
            self.memory = {}
        self.memory.setdefault("sent", {})
        self.memory.setdefault("down", [])
        self.memory.setdefault("loss", {})    # "<bot>:<day ms>" -> steps of day_loss_pct already said
        self.memory.setdefault("killed", [])  # bots whose kill switch was already said
        self.memory.setdefault("news_seen", [])  # links already sent (or looked at), newest last
        self.memory.setdefault("news_at", 0)

    def _save(self) -> None:
        tmp = self.memory_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.memory))
        tmp.replace(self.memory_path)

    def _due(self, key: str, now: int, hours: float) -> bool:
        return now - int(self.memory["sent"].get(key, 0)) >= hours * HOUR_MS

    def bots(self, now: int) -> list[BotView]:
        since = now - int((self.cfg.stops_window_h + 1) * HOUR_MS)  # a bar stop is stamped at the bar's open
        out = []
        for db in sorted(self.state_dir.glob("*.db"), key=lambda p: (bot_name(p), p.name)):
            try:
                out.append(read_bot(db, since, day_start_ms(now)))
            except sqlite3.Error as exc:
                log.debug("skipping %s: %s", db.name, exc)
        # Archived databases (paper --fresh) have not been written for days: not a running bot.
        return [b for b in out if b.coins and b.heartbeat_ms is not None and now - b.heartbeat_ms < 7 * 24 * HOUR_MS]

    def _holdings(self, coin: str, bots: list[BotView]) -> list[str]:
        lines = []
        for b in bots:
            if coin not in b.coins:
                continue
            lo, sh = b.long.get(coin, 0), b.short.get(coin, 0)
            held = ", ".join(x for x in (f"long σε {_pairs(lo)}" if lo else "",
                                         f"short σε {_pairs(sh)}" if sh else "") if x)
            line = (f"{b.name}: {held} · ανοιχτά {_num(b.upnl.get(coin, 0.0))} USDT" if held
                    else f"{b.name}: καμία θέση ({_pairs(b.pairs_per_coin.get(coin, 0))})")
            if coin in b.paused:
                line += " · <i>ήδη σε pause</i>"
            lines.append(line)
        return lines

    def check(self) -> list[str]:
        """One round: returns the alert blocks it sent (in one message)."""
        now, cfg = self.now(), self.cfg
        bots = self.bots(now)
        blocks: list[str] = []
        before = json.loads(json.dumps(self.memory))  # a message that fails to go is tried again next round

        # 1. a bot that stopped writing, and its return
        for b in bots:
            age = (now - b.heartbeat_ms) / MINUTE_MS if b.heartbeat_ms else None
            down = age is None or age >= cfg.down_after_min
            if down and b.name not in self.memory["down"]:
                self.memory["down"].append(b.name)
                blocks.append(f"🔴 <b>Το bot {b.name} δεν τρέχει</b> (καμία ενημέρωση εδώ και "
                              f"{'—' if age is None else f'{age:.0f}'} λεπτά)")
            elif not down and b.name in self.memory["down"]:
                self.memory["down"].remove(b.name)
                blocks.append(f"🟢 Το bot {b.name} τρέχει ξανά")

        # 2. a burst of stops caused by one coin
        for b in bots:
            per: dict[str, list[tuple[int, float]]] = {}
            for ts, coin, pnl in b.stops:
                if ts >= now - (cfg.stops_window_h + 1) * HOUR_MS:
                    per.setdefault(coin, []).append((ts, pnl))
            for coin, hits in sorted(per.items()):
                key = f"stops:{b.name}:{coin}"
                if len(hits) >= cfg.stops_n and self._due(key, now, cfg.stops_window_h):
                    self.memory["sent"][key] = now
                    blocks.append("\n".join([
                        f"🛑 <b>{html.escape(coin)}: {len(hits)} stops στο {b.name}</b> τις τελευταίες "
                        f"{cfg.stops_window_h:g} ώρες ({_num(sum(p for _, p in hits))} USDT)",
                        *self._holdings(coin, bots)]))

        # 3. a coin shock
        coins = sorted(set().union(*(b.coins for b in bots))) if bots else []
        moves = self.moves(coins) if coins else {}
        for coin in coins:
            m = moves.get(coin)
            key = f"shock:{coin}"
            if m is None or abs(m.pct) < cfg.move_pct or not self._due(key, now, cfg.cooldown_h):
                continue
            self.memory["sent"][key] = now
            vx = m.volume_x if m.volume_x is not None else (self.volume_x(coin) if self.volume_x else None)
            head = (f"⚠️ <b>{html.escape(coin)} {_num(100 * m.pct)}% σε {cfg.window_min} λεπτά</b>"
                    + (f" (όγκος {vx:.0f}× το κανονικό)" if vx and vx >= 2 else ""))
            blocks.append("\n".join([head, *self._holdings(coin, bots)]))

        blocks += self._funding_blocks(bots, now)
        blocks += self._loss_blocks(bots, now)
        blocks += self._summary_blocks(bots, now)
        blocks += self._news_blocks(bots, now)

        if blocks:
            msg = "\n\n".join(blocks) + f"\n\n👉 Risk &amp; controls: {cfg.dashboard_url}/controls"
            if not self.send(msg):
                self.memory = before
                blocks = []
        self._save()
        return blocks

    # 4. funding that eats the edge
    def _funding_blocks(self, bots: list[BotView], now: int) -> list[str]:
        cfg = self.cfg
        if self.funding is None or not any(b.legs for b in bots):
            return []
        try:
            rates = self.funding()
        except Exception as exc:  # no rates this round: try again at the next one
            log.debug("funding rates: %s", exc)
            return []
        out = []
        for b in bots:
            lines = []
            for pair, a, bb, qa, qb, pa, pb in b.legs:
                if a not in rates or bb not in rates:
                    continue
                legs = [(c, q, carry_day(q, px, *rates[c])) for c, q, px in ((a, qa, pa), (bb, qb, pb))]
                day, notional = sum(x for *_, x in legs), abs(qa) * pa + abs(qb) * pb
                key = f"carry:{b.name}:{pair}"
                if notional <= 0 or day > -cfg.carry_pct * notional or not self._due(key, now, cfg.carry_cooldown_h):
                    continue
                self.memory["sent"][key] = now
                worst = min(legs, key=lambda x: x[2])
                lines.append(f"{html.escape(a)}/{html.escape(bb)}: πληρώνει {_money(-day, 2)} USDT την ημέρα "
                             f"({_money(100 * -day / notional, 2)}% της θέσης {_money(notional)} USDT), κυρίως το "
                             f"{'long' if worst[1] > 0 else 'short'} {html.escape(worst[0])}")
            if lines:
                out.append("\n".join([f"💸 <b>Ακριβό funding στο {b.name}</b> (με τα τωρινά rates)", *lines]))
        return out

    # 5. a bad day, and the kill switch
    def _loss_blocks(self, bots: list[BotView], now: int) -> list[str]:
        cfg, day = self.cfg, day_start_ms(now)
        self.memory["loss"] = {k: n for k, n in self.memory["loss"].items() if k.endswith(f":{day}")}
        out = []
        for b in bots:
            if b.equity is None:
                continue
            if b.equity_day:
                lost = 1.0 - b.equity / b.equity_day
                step = int(lost / cfg.day_loss_pct + 1e-9) if lost > 0 else 0
                key = f"{b.name}:{day}"
                if step >= 1 and step > self.memory["loss"].get(key, 0):
                    self.memory["loss"][key] = step
                    out.append(f"📉 <b>Το bot {b.name} έχασε {_money(100 * lost, 1)}% σήμερα</b> "
                               f"({_num(b.equity - b.equity_day)} USDT, equity {_money(b.equity)})")
            dd = 1.0 - b.equity / b.peak if b.peak else 0.0
            if b.kill_switch and b.name not in self.memory["killed"]:
                self.memory["killed"].append(b.name)
                out.append(f"⛔ <b>Kill switch στο {b.name}</b>: πτώση {_money(100 * dd, 1)}% από την κορυφή. "
                           "Δεν ανοίγει νέες θέσεις μέχρι το reset από το Risk &amp; controls.")
            elif not b.kill_switch:
                if b.name in self.memory["killed"]:
                    self.memory["killed"].remove(b.name)
                key = f"killnear:{b.name}"
                if dd >= b.kill_dd - cfg.kill_near_pct and self._due(key, now, 24.0):
                    self.memory["sent"][key] = now
                    out.append(f"🟠 <b>Το bot {b.name} πλησιάζει το kill switch</b>: πτώση {_money(100 * dd, 1)}% από την "
                               f"κορυφή ({_money(b.peak)} USDT), το όριο είναι {_money(100 * b.kill_dd)}%")
        return out

    # 6. the day in one message
    def _summary_blocks(self, bots: list[BotView], now: int) -> list[str]:
        cfg, day = self.cfg, day_start_ms(now)
        key = f"summary:{day}"
        if datetime.fromtimestamp(now / 1000, TZ).hour < cfg.summary_hour or key in self.memory["sent"] or not bots:
            return []
        self.memory["sent"] = {k: t for k, t in self.memory["sent"].items()
                               if not k.startswith("summary:") or now - t < 7 * 24 * HOUR_MS}
        self.memory["sent"][key] = now
        rates = {}
        if self.funding is not None and any(b.legs for b in bots):
            try:
                rates = self.funding()
            except Exception as exc:
                log.debug("funding rates: %s", exc)
        lines = [f"📊 <b>Σύνοψη ημέρας {datetime.fromtimestamp(now / 1000, TZ):%d/%m}</b>"]
        for b in bots:
            if b.equity is None:
                continue
            pnl = b.equity - b.equity_day if b.equity_day else None
            head = f"<b>{b.name}</b>: equity {_money(b.equity)} USDT"
            if pnl is not None:
                head += f", σήμερα {_num(pnl)} ({_num(100 * pnl / b.equity_day, 2)}%)"
            wins = sum(1 for p, _ in b.closed_today if p > 0)
            stops = sum(1 for _, r in b.closed_today if r in STOP_REASONS)
            closed = (f"κλεισίματα {len(b.closed_today)} ({wins} κερδισμένα, {stops} stops, "
                      f"{_num(sum(p for p, _ in b.closed_today))} USDT)" if b.closed_today else "κανένα κλείσιμο")
            carry = None
            if rates and b.legs:
                carry = sum(carry_day(q, px, *rates[c]) for _, a, bb, qa, qb, pa, pb in b.legs
                            for c, q, px in ((a, qa, pa), (bb, qb, pb)) if c in rates)
            costs = f"fees σήμερα {_money(b.fees_today, 2)} USDT"
            if carry is not None:
                costs += f" · funding τώρα {_num(carry, 2)} USDT/ημέρα"
            lines += [head, f"  {closed} · ανοίγματα {b.opened_today} · ανοιχτές θέσεις {b.open_pairs}",
                      f"  {costs}"]
        return ["\n".join(lines)] if len(lines) > 1 else []

    # 7. headlines about the coins of the list
    def _news_blocks(self, bots: list[BotView], now: int) -> list[str]:
        cfg = self.cfg
        if self.news is None or not bots or now - int(self.memory["news_at"]) < cfg.news_every_s * 1000:
            return []
        self.memory["news_at"] = now
        try:
            items = self.news()
        except Exception as exc:
            log.debug("news: %s", exc)
            return []
        coins = sorted(set().union(*(b.coins for b in bots)))
        seen = set(self.memory["news_seen"])
        fresh = sorted((i for i in items if i.link not in seen and now - i.ts <= cfg.news_max_age_h * HOUR_MS),
                       key=lambda i: i.ts)
        out = []
        for it in fresh:
            self.memory["news_seen"].append(it.link)
            named = coins_in(it.title, coins)
            if not named or len(out) >= cfg.news_max:
                continue
            lines = [f"📰 <b>{', '.join(html.escape(c) for c in named)}</b> · {html.escape(it.source)}: "
                     f"<a href=\"{html.escape(it.link, quote=True)}\">{html.escape(it.title)}</a>"]
            for c in named:
                lines += self._holdings(c, bots)
            out.append("\n".join(lines))
        self.memory["news_seen"] = self.memory["news_seen"][-2000:]
        return out

    def run(self) -> None:
        c = self.cfg
        log.info("alerts: watching %s every %.0f s (shock %.0f%% in %d min, %d stops in %g h, bot down after %g min, "
                 "funding %.1f%%/day, day loss %.0f%%, summary at %02d:00)",
                 self.state_dir, c.check_every_s, 100 * c.move_pct, c.window_min, c.stops_n, c.stops_window_h,
                 c.down_after_min, 100 * c.carry_pct, 100 * c.day_loss_pct, c.summary_hour)
        while True:
            try:
                sent = self.check()
                if sent:
                    log.info("alert sent: %s", " | ".join(s.splitlines()[0] for s in sent))
            except Exception:  # keep watching after a broken round
                log.exception("alerts: check failed")
            time.sleep(self.cfg.check_every_s)

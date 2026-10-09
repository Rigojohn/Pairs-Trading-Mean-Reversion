"""Manual controls: commands queued by the dashboard, validated and run by the bot.

The dashboard never trades or edits state itself. It only inserts a row in the
``commands`` table; the bot picks it up within a couple of seconds, runs it with
its own safety rules, and writes the result back. Commands older than
``max_age_s`` are rejected, so a click made while the bot was down is not
replayed hours later.
"""

from __future__ import annotations

import json
import logging

from cryptopairs.core.strategy import PairParams
from cryptopairs.runtime.engine import StrategyEngine
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import StateStore

log = logging.getLogger(__name__)

PAIR_COMMANDS = {"close_pair", "add_pair", "pause_pair", "resume_pair", "set_params", "clear_params", "open_pair",
                 "set_exit_plan"}


def _opt_float(p: dict, key: str) -> float | None:
    v = p.get(key)
    return None if v is None else float(v)


class CommandProcessor:
    def __init__(self, store: StateStore, engine: StrategyEngine, risk: RiskManager, max_age_s: float = 120.0) -> None:
        self.store, self.engine, self.risk = store, engine, risk
        self.max_age_ms = int(max_age_s * 1000)

    async def poll(self) -> int:
        """Run every pending command once. Returns how many were handled."""
        handled = 0
        for cmd in self.store.pending_commands():
            try:
                ok, msg = await self._run(cmd)
            except Exception as exc:  # a broken command must not stop the bot
                log.exception("command %s failed", cmd)
                ok, msg = False, f"error: {exc}"
            self.store.finish_command(cmd["id"], ok, msg)
            self.store.event("command", cmd.get("pair_id"), command=cmd["kind"], ok=ok, result=msg)
            log.info("command #%d %s %s -> %s: %s", cmd["id"], cmd["kind"], cmd.get("pair_id") or "",
                     "done" if ok else "rejected", msg)
            handled += 1
        return handled

    async def _run(self, cmd: dict) -> tuple[bool, str]:
        kind, pid = cmd["kind"], cmd.get("pair_id")
        if self.engine.feed.now_ms() - int(cmd["ts"]) > self.max_age_ms:
            return False, "expired (sent while the bot was not running)"
        if kind in PAIR_COMMANDS and self.engine.pair(pid) is None:
            return False, f"unknown pair {pid!r}"
        if kind == "close_pair":  # no payload = close everything at market (the original button)
            p = json.loads(cmd.get("payload") or "{}")
            try:
                fraction = float(p.get("fraction", 1.0))
            except (TypeError, ValueError) as exc:
                return False, f"invalid fraction: {exc}"
            return await self.engine.close_pair(pid, fraction=fraction, execution=str(p.get("execution", "market")))
        if kind == "add_pair":
            p = json.loads(cmd.get("payload") or "{}")
            try:
                fraction = float(p["fraction"])
            except (KeyError, TypeError, ValueError) as exc:
                return False, f"invalid addition: {exc}"
            return await self.engine.add_pair(pid, fraction, str(p.get("execution", "market")))
        if kind == "open_pair":
            p = json.loads(cmd.get("payload") or "{}")
            try:
                side = int(p["side"])
                leverage = _opt_float(p, "leverage")
            except (KeyError, TypeError, ValueError) as exc:
                return False, f"invalid order: {exc}"
            return await self.engine.open_pair(pid, side, leverage, str(p.get("execution", "market")))
        if kind == "set_exit_plan":
            p = json.loads(cmd.get("payload") or "{}")
            try:
                z_exit, stop = _opt_float(p, "z_exit"), _opt_float(p, "stop_loss_pct")
            except (TypeError, ValueError) as exc:
                return False, f"invalid exit plan: {exc}"
            return await self.engine.set_exit_plan(pid, z_exit, stop)
        if kind == "pause_pair":
            self.engine.set_paused(pid, True)
            return True, "no new entries for this pair (exits and stops still run)"
        if kind == "resume_pair":
            self.engine.set_paused(pid, False)
            return True, "entries allowed again"
        if kind == "set_params":
            p = json.loads(cmd.get("payload") or "{}")
            try:
                delta = p.get("kalman_delta")
                params = PairParams(int(p["lookback"]), float(p["z_entry"]), float(p["z_exit"]),
                                    None if delta is None else float(delta))
            except (KeyError, TypeError, ValueError) as exc:
                return False, f"invalid parameters: {exc}"
            return await self.engine.set_params(pid, params)
        if kind == "clear_params":
            return await self.engine.set_params(pid, None)
        if kind == "pause_entries":
            self.engine.set_entries_paused(True)
            return True, "no new entries on any pair (exits and stops still run)"
        if kind == "resume_entries":
            self.engine.set_entries_paused(False)
            return True, "entries allowed again"
        if kind in ("pause_coin", "resume_coin"):
            coin = str(json.loads(cmd.get("payload") or "{}").get("coin") or "").strip().upper()
            n = self.engine.pairs_with_coin(coin)
            if kind == "pause_coin":
                if n == 0:
                    return False, f"no pair trades {coin or 'that coin'}"
                self.engine.set_coin_paused(coin, True)
                return True, (f"no new automatic entries in the {n} pair(s) with {coin} "
                              "(exits, stops and manual orders still run)")
            self.engine.set_coin_paused(coin, False)
            return True, f"automatic entries allowed again in the {n} pair(s) with {coin}"
        if kind == "reset_kill_switch":
            self.risk.kill_switch = False
            self.risk.peak_equity = None  # drawdown is measured afresh from now
            self.store.kv_set("risk.kill_switch", False)
            self.store.kv_set("risk.peak_equity", None)
            return True, "kill switch reset"
        if kind == "flatten_all":
            n = await self.engine.flatten_all("manual_flatten")
            return True, f"closed {n} position(s) at market"
        return False, f"unknown command {kind!r}"

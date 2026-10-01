"""Wires DataFeed -> StrategyEngine -> RiskManager -> ExecutionEngine and runs the loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import asdict
from pathlib import Path

import yaml

from cryptopairs.config import AppConfig
from cryptopairs.core.sizing import LotRules
from cryptopairs.core.strategy import PairParams
from cryptopairs.runtime.commands import CommandProcessor
from cryptopairs.runtime.engine import PairSpec, StrategyEngine, orphan_specs
from cryptopairs.runtime.execution import PairExecutor, PaperVenue
from cryptopairs.runtime.feed import DataFeed
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import StateStore

log = logging.getLogger(__name__)


def load_pairs_file(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    pairs = doc.get("pairs", [])
    if not pairs:
        raise ValueError(f"no pairs in {path}")
    for p in pairs:
        missing = {"a", "b", "lookback", "z_entry", "z_exit"} - set(p)
        if missing:
            raise ValueError(f"pair {p} missing keys {sorted(missing)}")
    return pairs


def _unified_symbol(exchange, exchange_id: str) -> str | None:
    """Binance id (BANDUSDT) -> ccxt symbol (BAND/USDT:USDT) for the linear perpetual."""
    found = exchange.markets_by_id.get(exchange_id) or []
    if isinstance(found, dict):
        found = [found]
    for m in found:
        if m.get("swap") and m.get("linear"):
            return m["symbol"]
    return None


def _lot_rules(exchange, symbol: str) -> LotRules:
    import ccxt

    m = exchange.market(symbol)
    prec = m.get("precision", {}).get("amount")
    if prec is None:
        step = 0.0
    elif exchange.precisionMode == ccxt.TICK_SIZE:
        step = float(prec)
    else:
        step = 10.0 ** (-int(prec))
    limits = m.get("limits", {})
    return LotRules(
        step=step,
        min_qty=float((limits.get("amount") or {}).get("min") or 0.0),
        min_notional=float((limits.get("cost") or {}).get("min") or 0.0),
    )


def price_tick(exchange, symbol: str) -> float:
    """Minimum price increment, used to place maker orders one tick inside the spread."""
    import ccxt

    prec = exchange.market(symbol).get("precision", {}).get("price")
    if prec is None:
        return 0.0
    return float(prec) if exchange.precisionMode == ccxt.TICK_SIZE else 10.0 ** (-int(prec))


def build_specs(exchange, pairs: list[dict], capital: float) -> list[PairSpec]:
    specs = []
    for p in pairs:
        sa, sb = _unified_symbol(exchange, p["a"]), _unified_symbol(exchange, p["b"])
        if sa is None or sb is None:
            log.warning("skipping %s-%s: symbol not listed on the exchange", p["a"], p["b"])
            continue
        if not (exchange.market(sa).get("active", True) and exchange.market(sb).get("active", True)):
            log.warning("skipping %s-%s: a leg is not trading", p["a"], p["b"])
            continue
        specs.append(PairSpec(
            id=p.get("id", f"{p['a']}_{p['b']}"), symbol_a=sa, symbol_b=sb,
            params=PairParams(int(p["lookback"]), float(p["z_entry"]), float(p["z_exit"]),
                              float(p["kalman_delta"]) if p.get("kalman_delta") is not None else None),
            capital=float(p.get("capital", capital)),
            lots_a=_lot_rules(exchange, sa), lots_b=_lot_rules(exchange, sb),
        ))
    return specs


def state_path(cfg: AppConfig) -> Path:
    """One database per bot: state/<name>.db, where the name defaults to the mode."""
    return Path(cfg.runtime.state_dir) / f"{cfg.runtime.state_name or cfg.runtime.mode}.db"


def archive_state(cfg: AppConfig) -> Path | None:
    """Move the current state database aside (``paper --fresh``)."""
    path = state_path(cfg)
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    target = path.with_name(f"{path.stem}_{stamp}.db")
    path.rename(target)
    for suffix in ("-wal", "-shm"):
        side = Path(str(path) + suffix)
        if side.exists():
            side.rename(Path(str(target) + suffix))
    return target


async def _load_markets(exchange, attempts: int = 5) -> None:
    """Exchange metadata at start-up; a network hiccup must not stop the bot."""
    import ccxt

    for attempt in range(1, attempts + 1):
        try:
            await exchange.load_markets()
            return
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
            if attempt == attempts:
                raise
            log.warning("loading the exchange markets failed (%s: %s); retry %d/%d in %d s",
                        type(exc).__name__, exc, attempt, attempts - 1, 5 * attempt)
            await asyncio.sleep(5 * attempt)


async def run_app(cfg: AppConfig) -> None:
    rt = cfg.runtime
    if rt.mode != "paper":
        raise SystemExit(f"runtime.mode={rt.mode!r} is not available yet (live trading arrives in Phase 3)")
    import ccxt.pro as ccxtpro

    store = StateStore(state_path(cfg))
    session = store.begin_session(rt.mode, cfg.profile, {"strategy": asdict(cfg.strategy), "risk": asdict(cfg.risk)})
    pending = store.pending_orders()
    if pending:
        # Paper fills only exist in memory, so an order still pending was never applied.
        n = store.abandon_pending("process stopped before the fill was recorded; paper mode, nothing executed")
        store.event("recovered_pending_orders", count=n)
        log.warning("recovery: %d order(s) were in flight when the last session stopped; marked abandoned", n)

    exchange = getattr(ccxtpro, rt.exchange)({"enableRateLimit": True})
    feed = None
    tasks: list[asyncio.Task] = []
    try:
        await _load_markets(exchange)
        specs = build_specs(exchange, load_pairs_file(rt.pairs_file), rt.capital_per_pair)
        orphans = orphan_specs(specs, store.load_pairs())
        for o in orphans:
            log.warning("%s: no longer in the pairs file but holds a position; managing it until it closes", o.id)
        specs += orphans
        if not specs:
            raise SystemExit("no tradable pairs")
        symbols = sorted({s.symbol_a for s in specs} | {s.symbol_b for s in specs})
        history = max(
            max(cfg.strategy.kalman_history, s.params.lookback) if s.params.kalman_delta else s.params.lookback
            for s in specs
        ) + 48
        log.info("paper session %s: %d pairs, %d symbols, %d bars of history, state in %s",
                 session, len(specs), len(symbols), history, store.path)
        maker_first = rt.execution == "maker_first"
        feed = DataFeed(exchange, symbols, history, grace_s=rt.bar_grace_seconds,
                        quote_stale_s=rt.quote_stale_seconds, max_concurrency=rt.max_rest_concurrency,
                        snapshot_s=rt.quote_snapshot_seconds, trade_stream=maker_first)
        await feed.start()
        risk = RiskManager(cfg.risk)
        risk.peak_equity = store.kv_get("risk.peak_equity")
        risk.kill_switch = bool(store.kv_get("risk.kill_switch", False))
        if risk.kill_switch:
            log.error("KILL SWITCH is still engaged from a previous session: no new entries "
                      "(reset with: python -m cryptopairs reset-kill-switch)")
        venue = PaperVenue(feed, cfg.costs.taker_fee, cfg.costs.maker_fee, cfg.costs.slippage_bps)
        execution = PairExecutor(
            venue, maker_first=maker_first, timeout_s=rt.maker_timeout_seconds,
            reprice_s=rt.maker_reprice_seconds, poll_s=rt.maker_poll_seconds,
            ticks={sym: price_tick(exchange, sym) for sym in symbols},
        )
        log.info("execution: %s", "maker first (post-only on the larger leg, hedge at once, "
                 f"market after {rt.maker_timeout_seconds:.0f}s)" if maker_first else "taker (market orders)")
        engine = StrategyEngine(specs, cfg, feed, risk, execution, store)
        commands = CommandProcessor(store, engine, risk, rt.command_max_age_seconds)
        fit_backfilled = False
        store.event("session_start", profile=cfg.profile, pairs=[s.id for s in specs],
                    open_positions=engine.open_pairs(), equity=engine.total_equity())
        log.info("restored state: %d open position(s), equity %.2f", engine.open_pairs(), engine.total_equity())

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                pass

        async def process_bar(bar_ts: int) -> None:
            # Funding is charged on positions held during the bar, before new decisions.
            await engine.settle_funding(bar_ts + 3_600_000)
            await engine.on_bar(bar_ts)
            store.record_marks(bar_ts, engine.marks_rows())
            nonlocal fit_backfilled
            try:  # model history for the dashboard; the first bar back-fills the last 30 days
                store.record_fit_history(engine.fit_rows(bar_ts, history_bars=1 if fit_backfilled else 720))
                fit_backfilled = True
            except Exception:
                log.exception("fit history snapshot failed")
            total = engine.total_equity()
            if risk.update_equity(total):
                store.event("kill_switch", equity=total, peak=risk.peak_equity)
                if cfg.risk.flatten_on_kill:
                    await engine.flatten_all("kill_switch")
            store.kv_set("risk.peak_equity", risk.peak_equity)
            store.kv_set("risk.kill_switch", risk.kill_switch)
            store.record_equity(bar_ts, total, engine.open_pairs(), risk.kill_switch)
            log.info("bar %s | equity %.2f | open pairs %d/%d%s",
                     time.strftime("%Y-%m-%d %H:%M", time.gmtime(bar_ts / 1000)), total,
                     engine.open_pairs(), len(specs), " | KILL SWITCH" if risk.kill_switch else "")

        async def bar_loop() -> None:
            if rt.evaluate_on_start:
                # Act now on the last closed bar, with live quotes, instead of waiting for the
                # next hour. Safe on restarts: open positions and settled funding are remembered.
                ready = await feed.wait_for_quotes(timeout_s=20)
                bar_ts = feed.last_closed_bar()
                await feed.refresh(bar_ts)
                log.info("start-up evaluation on the last closed bar (%s UTC), quotes for %d/%d symbols",
                         time.strftime("%H:%M", time.gmtime(bar_ts / 1000)), ready, len(symbols))
                await process_bar(bar_ts)
            while True:
                await process_bar(await feed.wait_for_bar_close())

        async def live_loop() -> None:
            last_prune = 0
            while True:  # equity and every pair at live prices, for the real-time dashboard
                try:
                    now = feed.now_ms()
                    store.record_live_equity(now, engine.total_equity(), engine.open_pairs())
                    store.record_live(engine.live_rows(now))
                    if now - last_prune > 3_600_000:
                        store.prune_live(now - int(rt.live_retention_hours * 3_600_000))
                        last_prune = now
                except Exception:
                    log.exception("live snapshot failed")
                await asyncio.sleep(rt.live_snapshot_seconds)

        async def command_loop() -> None:
            while True:
                try:
                    await commands.poll()
                except Exception:
                    log.exception("command polling failed")
                await asyncio.sleep(rt.command_poll_seconds)

        async def stop_loop() -> None:
            while True:
                await asyncio.sleep(cfg.risk.stop_check_seconds)
                await engine.check_stops()

        tasks.append(asyncio.create_task(bar_loop(), name="bars"))
        tasks.append(asyncio.create_task(live_loop(), name="live"))
        tasks.append(asyncio.create_task(command_loop(), name="commands"))
        if cfg.risk.continuous_stop or cfg.strategy.catastrophe_stop_pct is not None:
            tasks.append(asyncio.create_task(stop_loop(), name="stops"))
        waiter = asyncio.create_task(stop.wait(), name="signal")
        done, _ = await asyncio.wait([*tasks, waiter], return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is not waiter and t.exception() is not None:
                raise t.exception()
        log.info("shutting down; state saved in %s", store.path)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if feed is not None:
            await feed.stop()
        await exchange.close()
        store.event("session_end")
        store.end_session()
        store.close()

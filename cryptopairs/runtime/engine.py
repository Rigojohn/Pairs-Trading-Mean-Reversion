"""StrategyEngine: runs one PairStrategy per pair on live bars and quotes.

Flow per closed bar and pair:
    DataFeed window -> fit/Z-scores -> PairStrategy intent
    -> RiskManager.approve -> StateStore (pending order) -> ExecutionEngine.execute
    -> book + strategy update -> StateStore (fills, trade, pair state in one transaction)
The decision logic is the same ``PairStrategy`` the backtester uses.

On start the engine restores every pair from the StateStore: open positions,
regression fits, cooldowns, deactivation and the book all continue where the
last session stopped.
"""

from __future__ import annotations

import asyncio
import logging
import math

import numpy as np
from dataclasses import asdict, dataclass, field, replace

from cryptopairs.config import HOUR_MS, AppConfig
from cryptopairs.core.book import PairBook
from cryptopairs.core.signals import fit_ols, kalman_filter, ou_half_life, rolling_ols
from cryptopairs.core.sizing import (LotRules, SizingError, floor_to_step, round_to_lots, signed_quantities,
                                     target_quantities)
from cryptopairs.core.strategy import FLAT, LONG, SHORT, STOP_REASONS, PairParams, PairStrategy
from cryptopairs.runtime.execution import ExecutionEngine, ExecutionReport, PairOrder
from cryptopairs.runtime.feed import DataFeed
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import StateStore, StoredPair

log = logging.getLogger(__name__)


@dataclass
class PairSpec:
    id: str
    symbol_a: str  # exchange symbol, e.g. "BAND/USDT:USDT"
    symbol_b: str
    params: PairParams
    capital: float
    lots_a: LotRules | None = None
    lots_b: LotRules | None = None
    # Pairs dropped from pairs.yaml that still hold a position: managed until flat, no new entries.
    close_only: bool = False


@dataclass
class PairRuntime:
    spec: PairSpec
    strat: PairStrategy
    book: PairBook
    trade: dict | None = None
    last_z: tuple[float, float] = field(default=(math.nan, math.nan))
    # New parameters from pairs.yaml, applied once the open position closes.
    next_params: PairParams | None = None
    # True while an order for this pair is being worked (stops and new decisions skip it).
    busy: bool = False


@dataclass
class Intent:
    """A decision taken at bar close, executed afterwards (possibly for minutes)."""

    pr: PairRuntime
    order: PairOrder
    ts: int
    bar_index: int = 0
    side: int = 0
    zb: float = math.nan
    zs: float = math.nan
    marks: "Marks | None" = None
    half_life: float = math.nan
    entry_reason: str = "signal"  # "manual" for an entry from the dashboard order ticket


@dataclass
class Marks:
    mid_a: float
    mid_b: float
    log_bid_a: float
    log_ask_a: float
    log_bid_b: float
    log_ask_b: float
    quotes_ok: bool


def orphan_specs(specs: list[PairSpec], stored: dict[str, StoredPair]) -> list[PairSpec]:
    """Stored pairs with an open position that are no longer in the pairs file."""
    ids = {s.id for s in specs}
    return [
        PairSpec(r.pair_id, r.symbol_a, r.symbol_b, r.params, r.capital, close_only=True)
        for r in stored.values()
        if r.pair_id not in ids and int(r.strategy.get("side", FLAT)) != FLAT
    ]


class StrategyEngine:
    def __init__(
        self,
        specs: list[PairSpec],
        cfg: AppConfig,
        feed: DataFeed,
        risk: RiskManager,
        execution: ExecutionEngine,
        store: StateStore,
    ) -> None:
        self.cfg = cfg
        self.feed = feed
        self.risk = risk
        self.execution = execution
        self.store = store
        self._rt_cost = 2.0 * (cfg.costs.taker_fee + (cfg.costs.half_spread_bps + cfg.costs.slippage_bps) * 1e-4)
        # Manual controls from the dashboard, persisted so they survive restarts.
        self.paused_pairs: set[str] = set(store.kv_get("paused_pairs", []) or [])
        self.entries_paused: bool = bool(store.kv_get("entries_paused", False))
        # Manual parameter overrides: {pair_id: {"params": {...}, "base": {...}, "ts": ms}}.
        # "base" is the calibration they replaced; a recalibrated pairs file supersedes them.
        self.overrides: dict[str, dict] = dict(store.kv_get("param_overrides", {}) or {})
        specs = [self._with_override(s) for s in specs]
        stored = store.load_pairs()
        self.pairs = [self._restore(s, stored.get(s.id)) for s in specs]
        store.save_pairs(self.pairs)
        self._lock = asyncio.Lock()
        # Manual orders worked in the background (maker-first can take minutes).
        self._background: set[asyncio.Task] = set()

    # ------------------------------------------------------------ restore
    def _with_override(self, spec: PairSpec) -> PairSpec:
        o = self.overrides.get(spec.id)
        if not o or spec.close_only:
            return spec
        if PairParams(**o["base"]) != spec.params:
            log.warning("%s: the pairs file was recalibrated; dropping the manual override %s", spec.id, o["params"])
            self.overrides.pop(spec.id)
            self.store.kv_set("param_overrides", self.overrides)
            self.store.event("params_override_dropped", spec.id, override=o["params"], calibrated=asdict(spec.params))
            return spec
        spec.params = PairParams(**o["params"])
        log.info("%s: manual parameter override in force %s (calibrated %s)", spec.id, o["params"], o["base"])
        return spec

    def _new(self, spec: PairSpec) -> PairRuntime:
        strat = PairStrategy(spec.params, self.cfg.strategy, spec.capital, round_trip_cost=self._rt_cost)
        return PairRuntime(spec, strat, PairBook(cash=spec.capital))

    def _restore(self, spec: PairSpec, rec: StoredPair | None) -> PairRuntime:
        pr = self._new(spec)
        if rec is None:
            return pr
        open_pos = int(rec.strategy.get("side", FLAT)) != FLAT
        if (rec.symbol_a, rec.symbol_b) != (spec.symbol_a, spec.symbol_b):
            if open_pos:
                raise RuntimeError(
                    f"{spec.id}: the stored open position is on {rec.symbol_a}/{rec.symbol_b}, "
                    f"but the pairs file now says {spec.symbol_a}/{spec.symbol_b}; use a new pair id"
                )
            log.warning("%s: symbols changed while flat; starting this pair fresh", spec.id)
            return pr
        pr.book = PairBook(**rec.book)
        pr.strat.restore(rec.strategy)
        stored_params = rec.params
        if stored_params != spec.params:
            if open_pos:
                log.warning("%s: parameters changed; keeping %s until the open position closes", spec.id, stored_params)
                pr.next_params, spec.params, pr.strat.params = spec.params, stored_params, stored_params
            else:
                pr.strat.fit = None  # new lookback: refit on the next bar
        if open_pos:
            pr.trade = dict(rec.open_trade) if rec.open_trade else {"pair_id": spec.id}
            log.info("%s: restored open %s position (qty %.6g / %.6g, entry equity %.4f)", spec.id,
                     "LONG" if pr.strat.side > 0 else "SHORT", pr.book.qty_a, pr.book.qty_b, pr.strat.entry_equity)
        return pr

    # -------------------------------------------------------------- marks
    def _marks(self, pr: PairRuntime) -> Marks | None:
        s = pr.spec
        qa, qb = self.feed.quote(s.symbol_a), self.feed.quote(s.symbol_b)
        if qa is not None and qb is not None:
            return Marks(qa.mid, qb.mid, math.log(qa.bid), math.log(qa.ask),
                         math.log(qb.bid), math.log(qb.ask), True)
        ca, cb = self.feed.candles.last_close(s.symbol_a), self.feed.candles.last_close(s.symbol_b)
        if ca is None or cb is None:
            return None
        la, lb = math.log(ca), math.log(cb)
        return Marks(ca, cb, la, la, lb, lb, False)

    def total_equity(self) -> float:
        total = 0.0
        for pr in self.pairs:
            m = self._marks(pr)
            total += pr.book.equity(m.mid_a, m.mid_b) if m else pr.book.cash
        return total

    def gross_by_symbol(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for pr in self.pairs:
            if not pr.book.in_position:
                continue
            m = self._marks(pr)
            if m is None:
                continue
            out[pr.spec.symbol_a] = out.get(pr.spec.symbol_a, 0.0) + abs(pr.book.qty_a) * m.mid_a
            out[pr.spec.symbol_b] = out.get(pr.spec.symbol_b, 0.0) + abs(pr.book.qty_b) * m.mid_b
        return out

    def open_pairs(self) -> int:
        return sum(1 for pr in self.pairs if pr.strat.side != FLAT)

    # ---------------------------------------------------------------- bar
    async def on_bar(self, bar_ts: int) -> None:
        """Decide for every pair, then execute all orders concurrently.

        Decisions are taken under the lock in a few milliseconds. Execution can take
        minutes with maker orders, so it runs outside the lock: the stop watchdog keeps
        working on the other pairs meanwhile.
        """
        intents: list[Intent] = []
        async with self._lock:
            pending_gross: dict[str, float] = {}
            for pr in self.pairs:
                if pr.busy:
                    continue
                try:
                    it = self._decide(pr, bar_ts, pending_gross, sum(1 for i in intents if i.order.kind == "open"))
                except Exception:  # one broken pair must not stop the others
                    log.exception("pair %s: evaluation failed", pr.spec.id)
                    continue
                if it is not None:
                    pr.busy = True
                    intents.append(it)
                    if it.order.kind == "open":
                        for sym, q, px in ((it.order.symbol_a, it.order.qty_a, it.order.ref_a),
                                           (it.order.symbol_b, it.order.qty_b, it.order.ref_b)):
                            pending_gross[sym] = pending_gross.get(sym, 0.0) + abs(q) * px
            self.store.save_pairs(self.pairs)
        if intents:
            await asyncio.gather(*(self._run(it) for it in intents))
            async with self._lock:
                self.store.save_pairs(self.pairs)

    def _decide(self, pr: PairRuntime, bar_ts: int, pending_gross: dict[str, float],
                pending_opens: int) -> Intent | None:
        s, strat, book = pr.spec, pr.strat, pr.book
        if s.close_only and strat.side == FLAT:
            return None
        L = s.params.lookback
        window = self._window(pr, bar_ts)
        if window is None:
            log.debug("%s: no complete %d-bar window at %d; skipping", s.id, L, bar_ts)
            return None
        marks = self._marks(pr)
        if marks is None:
            return None
        bar_index = bar_ts // HOUR_MS
        eq = book.equity(marks.mid_a, marks.mid_b)
        strat.observe_equity(eq)

        if strat.side != FLAT:
            zb, zs = strat.z_scores(marks.log_bid_a, marks.log_ask_a, marks.log_bid_b, marks.log_ask_b)
            pr.last_z = (zb, zs)
            reason = strat.check_exit(bar_ts, bar_index, eq, zb, zs)
            if reason is None:
                return None
            # One action per pair per bar: an exit is never followed by a re-entry on the same bar.
            return Intent(pr, self._close_order(pr, reason, marks), bar_ts, bar_index, marks=marks)

        if s.close_only:
            return None
        if strat.wants_refit(bar_index):
            strat.set_fit(*self._estimate(pr, window), bar_index)
        if strat.fit is None:
            return None
        zb, zs = strat.z_scores(marks.log_bid_a, marks.log_ask_a, marks.log_bid_b, marks.log_ask_b)
        pr.last_z = (zb, zs)
        if self.entries_paused or s.id in self.paused_pairs:
            return None  # manual pause: no new entries (exits and stops still run)
        side = strat.check_entry(bar_ts, bar_index, zb, zs)
        if side == FLAT:
            return None
        order, _ = self._open_order(pr, side, marks, pending_gross, pending_opens)
        if order is None:
            return None
        return Intent(pr, order, bar_ts, bar_index, side, zb, zs, marks, self._half_life(pr, window))

    def _half_life(self, pr: PairRuntime, window: tuple[np.ndarray, np.ndarray]) -> float:
        """Spread half-life for the time stop (only computed when that overlay is on)."""
        if self.cfg.strategy.time_stop_halflives is None or pr.strat.fit is None:
            return math.nan
        f, L = pr.strat.fit, pr.spec.params.lookback
        return ou_half_life(window[0][-L:] - f.alpha - f.beta * window[1][-L:])

    def _window(self, pr: PairRuntime, bar_ts: int,
                params: PairParams | None = None) -> tuple[np.ndarray, np.ndarray] | None:
        s, p = pr.spec, params or pr.spec.params
        if p.kalman_delta is not None:  # the filter needs a long history to converge; accept less once warm
            return self.feed.candles.aligned_logs(s.symbol_a, s.symbol_b, bar_ts,
                                                  self.cfg.strategy.kalman_history, min_n=2 * p.lookback)
        return self.feed.candles.aligned_logs(s.symbol_a, s.symbol_b, bar_ts, p.lookback)

    def _estimate(self, pr: PairRuntime, window: tuple[np.ndarray, np.ndarray],
                  params: PairParams | None = None) -> tuple[float, float, float]:
        p, sc = params or pr.spec.params, self.cfg.strategy
        if p.kalman_delta is not None:
            al, be, si = kalman_filter(window[0], window[1], p.kalman_delta, p.lookback,
                                       sc.kalman_outlier_z, sc.kalman_max_skips)
            return al[-1], be[-1], si[-1]
        return fit_ols(*window)

    # ------------------------------------------------------------- orders
    def _open_order(self, pr: PairRuntime, side: int, marks: Marks, pending_gross: dict[str, float],
                    pending_opens: int, leverage: float | None = None,
                    reason: str | None = None) -> tuple[PairOrder | None, str]:
        """Size the entry and ask the risk manager. Returns (order, "") or (None, why not)."""
        s, strat, book = pr.spec, pr.strat, pr.book
        beta = strat.fit.beta
        lev = self.cfg.strategy.leverage if leverage is None else leverage
        try:
            qa, qb = target_quantities(book.cash, lev, beta, marks.mid_a, marks.mid_b)
            if s.lots_a is not None and s.lots_b is not None:
                qa, qb = round_to_lots(qa, qb, marks.mid_a, marks.mid_b, s.lots_a, s.lots_b, beta)
        except SizingError as exc:
            self.store.event("entry_skipped", s.id, reason=str(exc))
            log.info("%s: entry skipped: %s", s.id, exc)
            return None, f"cannot be sized: {exc}"
        sa, sb = signed_quantities(side, qa, qb)
        order = PairOrder(s.id, "open", s.symbol_a, s.symbol_b, sa, sb, marks.mid_a, marks.mid_b,
                          reason or ("long_spread" if side > 0 else "short_spread"))
        gross = self.gross_by_symbol()
        for sym, v in pending_gross.items():
            gross[sym] = gross.get(sym, 0.0) + v
        decision = self.risk.approve(order, open_pairs=self.open_pairs() + pending_opens, gross_by_symbol=gross,
                                     total_equity=self.total_equity(), quotes_ok=marks.quotes_ok)
        if not decision.approved:
            self.store.event("entry_rejected", s.id, reason=decision.reason)
            log.info("%s: entry rejected by risk: %s", s.id, decision.reason)
            return None, f"rejected by risk: {decision.reason}"
        return order, ""

    @staticmethod
    def _close_order(pr: PairRuntime, reason: str, marks: Marks) -> PairOrder:
        s, book = pr.spec, pr.book
        return PairOrder(s.id, "close", s.symbol_a, s.symbol_b, -book.qty_a, -book.qty_b,
                         marks.mid_a, marks.mid_b, reason, urgent=reason in STOP_REASONS)

    async def _run(self, it: Intent) -> None:
        """Write-ahead, execute (outside the lock), then apply the fills atomically."""
        pr, order = it.pr, it.order
        try:
            order_id, _client_id = self.store.order_pending(order)
            report = await self.execution.execute(order)
            async with self._lock:
                self._settle(it, order_id, report)
        except Exception:
            log.exception("pair %s: order handling failed", pr.spec.id)
        finally:
            pr.busy = False

    def _settle(self, it: Intent, order_id: int, report: ExecutionReport) -> bool:
        """Apply an execution report to the book, the strategy and the store."""
        pr, order = it.pr, it.order
        s = pr.spec
        if not report.ok:
            self.store.order_failed(order_id, report.error)
            traded = [f for f in report.fills if abs(f.qty) > 0]
            if traded:  # partial execution: never lose track of what actually traded
                self.store.event("execution_imbalance", s.id, error=report.error,
                                 fills=[(f.symbol, f.qty, f.price) for f in traded])
                log.error("%s: PARTIAL EXECUTION %s; manual check needed", s.id, traded)
            kind = {"open": "entry_failed", "reduce": "reduce_failed"}.get(order.kind, "exit_failed")
            self.store.event(kind, s.id, reason=order.reason, error=report.error)
            log.error("%s: %s (%s): %s", s.id, kind.upper(), order.reason, report.error)
            return False
        if order.kind == "open":
            self._apply_open(pr, it.side, it.ts, it.bar_index, it.zb, it.zs, it.marks, report, it.half_life,
                             it.entry_reason)
            self.store.commit_open(order_id, report, pr)
        elif order.kind == "reduce":
            self._apply_reduce(pr, it.ts, report, order_id)
        else:
            self._apply_close(pr, order.reason, it.ts, report, order_id)
        if report.stats:  # diagnostics only: recorded after the fill is safely stored
            try:
                self.store.event("execution", s.id, reason=order.reason, **report.stats)
            except Exception:
                log.exception("%s: could not record execution stats", s.id)
        return True

    def _apply_open(self, pr: PairRuntime, side: int, ts: int, bar_index: int,
                    zb: float, zs: float, marks: Marks, report: ExecutionReport,
                    half_life: float = math.nan, entry_reason: str = "signal") -> None:
        s, strat, book = pr.spec, pr.strat, pr.book
        qa, qb = report.qty(s.symbol_a), report.qty(s.symbol_b)
        cash_before = book.cash
        book.open(qa, qb, report.price(s.symbol_a), report.price(s.symbol_b), report.fee)
        eq = book.equity(marks.mid_a, marks.mid_b)
        strat.on_opened(side, ts, bar_index, eq, zb, zs, half_life)
        strat.observe_equity(eq)
        f = strat.fit
        pr.trade = {
            "pair_id": s.id, "side": side, "entry_ts": ts, "z_entry": zb if side > 0 else zs,
            "alpha": f.alpha, "beta": f.beta, "sigma": f.sigma,
            "qty_a": qa, "qty_b": qb,
            "entry_px_a": report.price(s.symbol_a), "entry_px_b": report.price(s.symbol_b),
            "equity_before": cash_before, "fees": report.fee, "funding": 0.0, "entry_reason": entry_reason,
            # Expected log-spread return to the exit band per unit notional (paper 5.2).
            "expected_ret": f.sigma / (1 + f.beta) * (abs(zb if side > 0 else zs) - s.params.z_exit),
        }
        log.info("%s: OPEN %s%s z=%.2f beta=%.3f sigma=%.4f qty=(%.6g, %.6g) fee=%.4f maker=%s",
                 s.id, "LONG" if side > 0 else "SHORT", " (manual)" if entry_reason == "manual" else "",
                 pr.trade["z_entry"], f.beta, f.sigma, qa, qb, report.fee, report.stats.get("maker_share"))

    def _apply_reduce(self, pr: PairRuntime, ts: int, report: ExecutionReport, order_id: int) -> None:
        """A manual partial close: realize part of the PnL; the trade stays open."""
        s, book = pr.spec, pr.book
        qa, qb = report.qty(s.symbol_a), report.qty(s.symbol_b)
        realized = book.reduce(qa, qb, report.price(s.symbol_a), report.price(s.symbol_b), report.fee)
        if pr.trade is not None:
            pr.trade["fees"] = (pr.trade.get("fees") or 0.0) + report.fee
        if not book.in_position:  # never planned (the ticket keeps a remainder), but stay consistent
            pr.strat.on_closed("manual_close", ts)
            trade = pr.trade or {"pair_id": s.id}
            trade.update(exit_ts=ts, reason="manual_close", exit_px_a=report.price(s.symbol_a),
                         exit_px_b=report.price(s.symbol_b), equity_after=book.cash,
                         pnl=book.cash - (trade.get("equity_before") or book.cash))
            pr.trade = None
            self.store.commit_close(order_id, report, pr, trade)
            return
        self.store.commit_reduce(order_id, report, pr)
        self.store.event("position_reduced", s.id, traded_a=qa, traded_b=qb, realized=realized, fee=report.fee,
                         remaining_a=book.qty_a, remaining_b=book.qty_b)
        log.info("%s: REDUCE qty=(%.6g, %.6g) realized=%.4f fee=%.4f, left (%.6g, %.6g)", s.id, qa, qb,
                 realized, report.fee, book.qty_a, book.qty_b)

    def _apply_close(self, pr: PairRuntime, reason: str, ts: int, report: ExecutionReport, order_id: int) -> None:
        s, strat, book = pr.spec, pr.strat, pr.book
        funding_before = book.funding_paid
        cash = book.close(report.price(s.symbol_a), report.price(s.symbol_b), report.fee)
        strat.on_closed(reason, ts)
        strat.observe_equity(cash)
        trade = pr.trade or {"pair_id": s.id}
        trade.update(exit_ts=ts, reason=reason, exit_px_a=report.price(s.symbol_a),
                     exit_px_b=report.price(s.symbol_b), fees=(trade.get("fees") or 0.0) + report.fee,
                     funding=(trade.get("funding") or 0.0) + (book.funding_paid - funding_before),
                     equity_after=cash, pnl=cash - (trade.get("equity_before") or cash))
        pr.trade = None
        if pr.next_params is not None:  # parameters changed while the position was open
            log.info("%s: switching to new parameters %s", s.id, pr.next_params)
            s.params = strat.params = pr.next_params
            strat.fit, pr.next_params = None, None
        self.store.commit_close(order_id, report, pr, trade)
        log.info("%s: CLOSE (%s) pnl=%.4f equity=%.4f maker=%s", s.id, reason, trade["pnl"], cash,
                 report.stats.get("maker_share"))

    async def _close(self, pr: PairRuntime, reason: str, ts: int, marks: Marks) -> bool:
        """Immediate close under the caller's lock (stops, kill switch): market orders only."""
        order = replace(self._close_order(pr, reason, marks), urgent=True)
        return await self._execute_now(Intent(pr, order, ts, marks=marks))

    async def _execute_now(self, it: Intent) -> bool:
        """Write-ahead, execute and settle under the caller's lock (market orders: seconds)."""
        order_id, _client_id = self.store.order_pending(it.order)
        report = await self.execution.execute(it.order)
        return self._settle(it, order_id, report)

    def _spawn(self, coro) -> None:
        """Run a manual order in the background; the pair stays busy until it settles."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def wait_background(self) -> None:
        """Wait for manual orders still being worked (tests, orderly shutdown)."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    async def cancel_background(self) -> None:
        for t in list(self._background):
            t.cancel()
        await asyncio.gather(*list(self._background), return_exceptions=True)

    # ------------------------------------------------------------- funding
    async def settle_funding(self, until_ms: int) -> float:
        """Paper mode: apply the exchange's real funding settlements to open positions.

        Longs pay a positive rate and shorts receive it, on the leg's notional at the
        current mark. Settlements already applied are remembered in the store, so a
        restart never charges one twice. Returns the total paid (negative = received).
        """
        last = self.store.kv_get("funding.last_ts")
        since = int(last) if last is not None else until_ms - HOUR_MS
        total = 0.0
        async with self._lock:
            holders: dict[str, list[tuple[PairRuntime, str]]] = {}
            for pr in self.pairs:
                if pr.book.in_position:
                    holders.setdefault(pr.spec.symbol_a, []).append((pr, "a"))
                    holders.setdefault(pr.spec.symbol_b, []).append((pr, "b"))
            for sym, legs in holders.items():
                events = await self.feed.funding_events(sym, since, until_ms)
                px = self.feed.candles.last_close(sym)
                q = self.feed.quote(sym)
                if q is not None:
                    px = q.mid
                if not events or px is None:
                    continue
                for _ts, rate in events:
                    for pr, leg in legs:
                        if leg == "a":
                            paid = pr.book.apply_funding(rate, px, 0.0, 0.0)
                        else:
                            paid = pr.book.apply_funding(0.0, 0.0, rate, px)
                        if pr.trade is not None:
                            pr.trade["funding"] = (pr.trade.get("funding") or 0.0) + paid
                        total += paid
            self.store.kv_set("funding.last_ts", until_ms)
        if total:
            log.info("funding settled: %.4f paid (negative = received)", total)
        return total

    # ------------------------------------------------------------ live views
    def live_rows(self, ts: int) -> list[dict]:
        """Per-pair snapshot at live prices: equity, unrealized PnL, Z and the spread now."""
        rows = []
        for pr in self.pairs:
            m = self._marks(pr)
            if m is None:
                continue
            f = pr.strat.fit
            eq = pr.book.equity(m.mid_a, m.mid_b)
            zb, zs = pr.strat.z_scores(m.log_bid_a, m.log_ask_a, m.log_bid_b, m.log_ask_b)
            u = (math.log(m.mid_a) - f.alpha - f.beta * math.log(m.mid_b)) if f else math.nan
            hs = lambda lb, la: 1e4 * math.expm1((la - lb) / 2.0)  # noqa: E731 - half-spread in bps
            rows.append({
                "ts": ts, "pair_id": pr.spec.id, "side": pr.strat.side, "equity": eq,
                "unrealized": eq - pr.book.cash if pr.book.in_position else 0.0,
                "z_buy": zb, "z_sell": zs, "spread_u": u,
                "alpha": f.alpha if f else None, "beta": f.beta if f else None, "sigma": f.sigma if f else None,
                "mid_a": m.mid_a, "mid_b": m.mid_b,
                "hs_bps_a": hs(m.log_bid_a, m.log_ask_a), "hs_bps_b": hs(m.log_bid_b, m.log_ask_b),
            })
        return rows

    def fit_rows(self, bar_ts: int, history_bars: int = 1) -> list[dict]:
        """Model state per pair for the last ``history_bars`` bars.

        kf_* is what the estimator says now (it keeps running while a position is open);
        used_* is the fit the strategy trades on (frozen during a position). Display only:
        it never changes a decision.
        """
        rows = []
        sc = self.cfg.strategy
        for pr in self.pairs:
            s = pr.spec
            L = s.params.lookback
            kalman = s.params.kalman_delta is not None
            n = sc.kalman_history if kalman else L + history_bars
            w = self.feed.candles.aligned_window(s.symbol_a, s.symbol_b, bar_ts, n, min_n=L)
            if w is None:
                continue
            ts, la, lb = w
            if kalman:
                al, be, si = kalman_filter(la, lb, s.params.kalman_delta, L, sc.kalman_outlier_z, sc.kalman_max_skips)
            else:
                al, be, si = rolling_ols(la, lb, L)
            f = pr.strat.fit
            k = min(history_bars, ts.shape[0])
            for j in range(ts.shape[0] - k, ts.shape[0]):
                if not np.isfinite(be[j]):  # warm-up bars have no estimate yet
                    continue
                u = la[j] - al[j] - be[j] * lb[j]
                last = j == ts.shape[0] - 1
                rows.append({
                    "bar_ts": int(ts[j]), "pair_id": s.id,
                    "kf_alpha": al[j], "kf_beta": be[j], "kf_sigma": si[j],
                    "used_alpha": f.alpha if (f and last) else None,
                    "used_beta": f.beta if (f and last) else None,
                    "used_sigma": f.sigma if (f and last) else None,
                    "spread_u": u, "z": u / si[j] if si[j] and si[j] > 0 else None,
                    "log_a": la[j], "log_b": lb[j],
                })
        return rows

    # ------------------------------------------------------------- controls
    def pair(self, pair_id: str) -> PairRuntime | None:
        return next((pr for pr in self.pairs if pr.spec.id == pair_id), None)

    def set_paused(self, pair_id: str, paused: bool) -> None:
        (self.paused_pairs.add if paused else self.paused_pairs.discard)(pair_id)
        self.store.kv_set("paused_pairs", sorted(self.paused_pairs))

    def set_entries_paused(self, paused: bool) -> None:
        self.entries_paused = paused
        self.store.kv_set("entries_paused", paused)

    async def open_pair(self, pair_id: str, side: int, leverage: float | None = None,
                        execution: str = "market") -> tuple[bool, str]:
        """Open a position now, on the trader's call instead of the Z signal (dashboard ticket).

        Sizing, lot rounding and the risk checks are those of an automatic entry, and from
        then on the strategy manages the exit and the stops as usual. A pause or a cooldown
        only stops automatic entries, so neither blocks this; a deactivated pair, a broken
        fit or stale quotes do. ``execution``: "market", or "maker" (post-only first, as the
        bot's own entries; worked in the background for up to the maker timeout).
        """
        if side not in (LONG, SHORT):
            return False, "side must be +1 (long spread) or -1 (short spread)"
        if execution not in ("market", "maker"):
            return False, f"unknown execution {execution!r}"
        sc = self.cfg.strategy
        cap = self.cfg.risk.manual_max_leverage or sc.leverage
        lev = sc.leverage if leverage is None else float(leverage)
        if not (math.isfinite(lev) and 0 < lev <= cap * (1 + 1e-9)):
            return False, f"leverage must be above 0 and at most {cap:g}x"
        async with self._lock:
            pr = self.pair(pair_id)
            if pr is None:
                return False, "unknown pair"
            s, strat = pr.spec, pr.strat
            if s.close_only:
                return False, "this pair is no longer in the pairs file; it is only being closed"
            if strat.side != FLAT:
                return False, "the pair already holds a position; close it first"
            if pr.busy:
                return False, "an order is being executed for this pair; try again in a minute"
            if strat.deactivated:
                return False, "the pair is deactivated (equity floor reached)"
            m = self._marks(pr)
            if m is None or not m.quotes_ok:
                return False, "no fresh quotes for both legs"
            bar_ts = self.feed.last_closed_bar()
            window = self._window(pr, bar_ts)
            if strat.fit is None:  # e.g. new parameters: fit on the last closed bar first
                if window is None:
                    return False, "not enough price history to fit the spread"
                strat.set_fit(*self._estimate(pr, window), bar_ts // HOUR_MS)
                if strat.fit is None:
                    return False, "the spread fit is not valid right now"
            f = strat.fit
            if not sc.beta_min <= f.beta <= sc.beta_max:
                return False, f"hedge ratio β {f.beta:.3f} is outside [{sc.beta_min:g}, {sc.beta_max:g}]"
            zb, zs = strat.z_scores(m.log_bid_a, m.log_ask_a, m.log_bid_b, m.log_ask_b)
            z = zb if side == LONG else zs
            if not math.isfinite(z):
                return False, "no valid Z right now"
            if sc.max_entry_z is not None and abs(z) > sc.max_entry_z:
                return False, f"|Z| {abs(z):.1f} is above max_entry_z {sc.max_entry_z:g}: the prices look broken"
            order, why = self._open_order(pr, side, m, {}, 0, leverage=lev, reason="manual_open")
            if order is None:
                return False, why
            order = replace(order, urgent=execution == "market")
            signal = (side == LONG and z < -s.params.z_entry) or (side == SHORT and z > s.params.z_entry)
            it = Intent(pr, order, self.feed.now_ms(), bar_ts // HOUR_MS, side, zb, zs, m,
                        self._half_life(pr, window) if window is not None else math.nan, entry_reason="manual")
            pr.busy = True
            self.store.event("manual_open", s.id, side=side, leverage=lev, execution=execution, z=z,
                             with_signal=signal, paused=s.id in self.paused_pairs or self.entries_paused,
                             cooldown=strat.cooldown_until > self.feed.now_ms())
        self._spawn(self._run(it))
        name = "LONG" if side == LONG else "SHORT"
        return True, (f"{name} spread submitted at Z {z:+.2f}, {lev:g}x, {self._how(execution)}"
                      + ("" if signal else "; no entry signal now (manual call)"))

    def _how(self, execution: str) -> str:
        if execution == "market":
            return "market orders"
        if not getattr(self.execution, "maker_first", False):
            return "market orders (this bot is configured for taker execution)"
        return f"maker first (up to {getattr(self.execution, 'timeout_s', 0):.0f} s, then market)"

    def _reduce_order(self, pr: PairRuntime, fraction: float, marks: Marks) -> PairOrder:
        """Trade ``fraction`` of both legs against the holdings, rounded down to the lot step."""
        s, book = pr.spec, pr.book
        legs = []
        for held, rules in ((book.qty_a, s.lots_a), (book.qty_b, s.lots_b)):
            q = abs(held) * fraction
            if rules is not None:
                q = floor_to_step(q, rules.step)
                left = abs(held) - q
                if left > 1e-12 and (left < max(rules.min_qty, rules.step) - 1e-12):
                    raise SizingError("the rest would be below the exchange minimum; close 100% instead")
            if not q > 0:
                raise SizingError("this fraction is smaller than one lot; choose a larger one")
            legs.append(-math.copysign(q, held))
        return PairOrder(s.id, "reduce", s.symbol_a, s.symbol_b, legs[0], legs[1], marks.mid_a, marks.mid_b,
                         "manual_reduce")

    async def close_pair(self, pair_id: str, reason: str = "manual_close", fraction: float = 1.0,
                         execution: str = "market") -> tuple[bool, str]:
        """Close all or part of a position (dashboard). A full market close runs at once;
        maker-first or partial closes run in the background like the bot's own orders."""
        if not (math.isfinite(fraction) and 0 < fraction <= 1):
            return False, "fraction must be above 0 and at most 1"
        if execution not in ("market", "maker"):
            return False, f"unknown execution {execution!r}"
        full = fraction >= 0.999
        async with self._lock:
            pr = self.pair(pair_id)
            if pr is None:
                return False, "unknown pair"
            if pr.strat.side == FLAT:
                return False, "no open position"
            if pr.busy:
                return False, "an order is being executed for this pair; try again in a minute"
            m = self._marks(pr)
            if m is None:
                return False, "no prices for this pair"
            now = self.feed.now_ms()
            if full and execution == "market":
                ok = await self._close(pr, reason, now, m)
                self.store.save_pairs(self.pairs)
                return ok, "closed at market" if ok else "close failed (see events)"
            if full:
                order = replace(self._close_order(pr, reason, m), urgent=False)
            else:
                try:
                    order = self._reduce_order(pr, fraction, m)
                except SizingError as exc:
                    return False, str(exc)
                order = replace(order, urgent=execution == "market")
            it = Intent(pr, order, now, now // HOUR_MS, marks=m)
            if order.urgent:  # a market reduction takes a second: settle it now, under the lock
                ok = await self._execute_now(it)
                self.store.save_pairs(self.pairs)
                return ok, f"reduced by {fraction:.0%} at market" if ok else "reduction failed (see events)"
            pr.busy = True
        self._spawn(self._run(it))
        what = "close" if full else f"reduction by {fraction:.0%}"
        return True, f"{what} submitted, {self._how(execution)}"

    async def set_exit_plan(self, pair_id: str, z_exit: float | None = None,
                            stop_loss_pct: float | None = None) -> tuple[bool, str]:
        """Exit band and stop-loss for the open position only (cleared when it closes).

        ``None`` keeps the pair's rule. The catastrophe stop is a hard limit and stays.
        """
        async with self._lock:
            pr = self.pair(pair_id)
            if pr is None:
                return False, "unknown pair"
            strat = pr.strat
            if strat.side == FLAT:
                return False, "no open position (for future trades change the parameters instead)"
            if z_exit is not None and not (math.isfinite(z_exit) and 0 <= z_exit < strat.params.z_entry):
                return False, f"exit Z must be at least 0 and below the entry threshold {strat.params.z_entry:g}"
            cat = self.cfg.strategy.catastrophe_stop_pct
            top = cat if cat is not None else 1.0
            if stop_loss_pct is not None and not (math.isfinite(stop_loss_pct) and 0 < stop_loss_pct < top):
                return False, (f"stop-loss must be above 0% and below the catastrophe stop {top:.0%}" if cat
                               else "stop-loss must be between 0% and 100%")
            strat.z_exit_override = None if z_exit is None else float(z_exit)
            strat.stop_pct_override = None if stop_loss_pct is None else float(stop_loss_pct)
            self.store.save_pairs(self.pairs)
            m = self._marks(pr)
            eq = pr.book.equity(m.mid_a, m.mid_b) if m else pr.book.cash
            self.store.event("exit_plan", pair_id, z_exit=z_exit, stop_loss_pct=stop_loss_pct,
                             stop_level=strat.stop_level(), equity=eq)
            log.info("%s: exit plan z_exit=%s stop=%s (stop level %.4f, equity %.4f)", pair_id, z_exit,
                     stop_loss_pct, strat.stop_level(), eq)
            msg = f"exit at Z ±{strat.exit_z():g}, stop at {strat.stop_level():,.2f} USDT equity"
            if eq <= strat.stop_level():
                msg += " (already below it: stops at the next check)"
            return True, msg

    async def set_params(self, pair_id: str, params: PairParams | None) -> tuple[bool, str]:
        """Manual parameter override (dashboard); ``None`` restores the calibrated parameters.

        A flat pair switches at once and, when the estimator settings changed, refits on the
        last closed bar. A pair in a position keeps trading on its parameters until it closes.
        Overrides are kept in kv so they survive restarts, until the pairs file is recalibrated.
        """
        async with self._lock:
            pr = self.pair(pair_id)
            if pr is None:
                return False, "unknown pair"
            if pr.spec.close_only:
                return False, "this pair is no longer in the pairs file; it is only being closed"
            if pr.busy:
                return False, "an order is being executed for this pair; try again in a minute"
            cur = pr.spec.params
            o = self.overrides.get(pair_id)
            base = PairParams(**o["base"]) if o else (pr.next_params or cur)
            target = base if params is None else params
            if (target.kalman_delta is None) != (cur.kalman_delta is None):
                return False, "the estimator (Kalman vs rolling OLS) cannot be changed here"
            max_z = self.cfg.strategy.max_entry_z
            if max_z is not None and target.z_entry >= max_z:
                return False, f"z_entry must stay below max_entry_z ({max_z:g})"
            bar_ts = self.feed.now_ms() // HOUR_MS * HOUR_MS - HOUR_MS  # the last closed bar
            window = self._window(pr, bar_ts, target)
            if window is None:
                return False, f"not enough price history for a lookback of {target.lookback} bars"

            if target == base:
                self.overrides.pop(pair_id, None)
            else:
                self.overrides[pair_id] = {"params": asdict(target), "base": asdict(base), "ts": self.feed.now_ms()}
            self.store.kv_set("param_overrides", self.overrides)
            if pr.strat.side != FLAT:
                pr.next_params = None if target == cur else target
                msg = "applies when the open position closes" if pr.next_params else "already in use"
            else:
                refit = (target.lookback, target.kalman_delta) != (cur.lookback, cur.kalman_delta)
                pr.spec.params = pr.strat.params = target
                pr.next_params = None
                if refit:
                    pr.strat.fit = None
                    pr.strat.set_fit(*self._estimate(pr, window, target), bar_ts // HOUR_MS)
                msg = "in force now" + (" (refitted on the last closed bar)" if refit else "")
            self.store.save_pairs(self.pairs)
            self.store.event("params_override", pair_id, params=asdict(target), calibrated=asdict(base),
                             previous=asdict(cur), result=msg)
            log.info("%s: parameters %s -> %s (calibrated %s): %s", pair_id, cur, target, base, msg)
            what = "calibrated parameters restored" if target == base else "override saved"
            return True, f"{what}; {msg}"

    def marks_rows(self) -> list[dict]:
        """Per-pair snapshot for the dashboard, taken after the bar has been evaluated."""
        rows = []
        for pr in self.pairs:
            m = self._marks(pr)
            eq = pr.book.equity(m.mid_a, m.mid_b) if m else pr.book.cash
            f = pr.strat.fit
            rows.append({"pair_id": pr.spec.id, "side": pr.strat.side, "equity": eq,
                         "z_buy": pr.last_z[0], "z_sell": pr.last_z[1],
                         "mid_a": m.mid_a if m else None, "mid_b": m.mid_b if m else None,
                         "beta": f.beta if f else None})
        return rows

    # -------------------------------------------------------- continuous stop
    async def check_stops(self) -> None:
        """Intrabar stops on live quotes: the full stop-loss if ``risk.continuous_stop``,
        otherwise only the catastrophe stop. The bar loop still runs the regular check."""
        async with self._lock:
            now = self.feed.now_ms()
            continuous = self.cfg.risk.continuous_stop
            for pr in self.pairs:
                if pr.strat.side == FLAT or pr.busy:
                    continue
                m = self._marks(pr)
                if m is None or not m.quotes_ok:
                    continue
                eq = pr.book.equity(m.mid_a, m.mid_b)
                if continuous and eq <= pr.strat.stop_level():
                    await self._close(pr, "stop_loss", now, m)
                elif eq <= pr.strat.catastrophe_level():
                    await self._close(pr, "stop_catastrophe", now, m)

    async def flatten_all(self, reason: str) -> int:
        async with self._lock:
            closed = 0
            for pr in self.pairs:
                if pr.strat.side != FLAT and not pr.busy:
                    m = self._marks(pr)
                    if m is not None and await self._close(pr, reason, self.feed.now_ms(), m):
                        closed += 1
            self.store.save_pairs(self.pairs)
            return closed

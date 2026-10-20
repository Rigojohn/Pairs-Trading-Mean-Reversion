"""Event-driven single-pair backtest (paper Algorithm 1) driven by the shared core.

All trading decisions come from ``PairStrategy``; this module only simulates
the market: fills, fees, spread, slippage, funding and the optional
pessimistic intrabar stop. With the default ``CostConfig`` it reproduces the
paper's assumptions (fills at the bar close, 5 bps taker fee, no funding).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptopairs.config import CostConfig, StrategyConfig
from cryptopairs.core.book import PairBook
from cryptopairs.core.signals import adaptive_entry_threshold, estimate_fits, ou_half_life
from cryptopairs.core.sizing import SizingError, signed_quantities, target_quantities
from cryptopairs.core.strategy import FLAT, STOP_REASONS, PairParams, PairStrategy
from cryptopairs.research.data import PairData

HOURS_PER_YEAR = 24 * 365


@dataclass
class BacktestResult:
    ts: np.ndarray
    equity: np.ndarray
    trades: list[dict] = field(default_factory=list)
    fees: float = 0.0
    funding: float = 0.0
    deactivated: bool = False
    cooldown_until: int = -1  # the strategy's cooldown at the end (set by the intrabar replay)

    def equity_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"ts": self.ts, "equity": self.equity})

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.trades)

    def metrics(self) -> dict:
        return performance(self.ts, self.equity, self.trades, self.fees, self.funding)


def sortino(rets: np.ndarray) -> float:
    """Annualized Sortino ratio of hourly simple returns, target 0: the mean over the root mean square of
    the losing hours, where every hour counts in the mean (a flat hour adds 0). NaN without a losing hour."""
    rets = np.asarray(rets, dtype=np.float64)
    if rets.size < 2:
        return math.nan
    downside = math.sqrt(float(np.mean(np.minimum(rets, 0.0) ** 2)))
    if not downside > 0:
        return math.nan
    return float(rets.mean() / downside * math.sqrt(HOURS_PER_YEAR))


def performance(ts: np.ndarray, equity: np.ndarray, trades: list[dict], fees: float = 0.0, funding: float = 0.0) -> dict:
    """Annualized Sharpe (sqrt(8760) on hourly simple returns, as in the paper) and friends."""
    n = equity.shape[0]
    out = {
        "Sharpe": math.nan, "Sortino": math.nan, "AnnRet": math.nan, "AnnVol": math.nan, "TotalRet": math.nan,
        "MaxDD": math.nan, "Trades": len(trades), "WinRate": math.nan,
        "StopLosses": sum(1 for t in trades if str(t.get("reason", "")).startswith("stop")),
        "Fees": fees, "Funding": funding, "FinalEquity": float(equity[-1]) if n else math.nan,
    }
    if n < 2 or not equity[0] > 0:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.diff(equity) / equity[:-1]
    rets = rets[np.isfinite(rets)]
    if rets.size > 1:
        sd = rets.std(ddof=1)
        out["AnnVol"] = float(sd * math.sqrt(HOURS_PER_YEAR))
        if sd > 0:
            out["Sharpe"] = float(rets.mean() / sd * math.sqrt(HOURS_PER_YEAR))
        out["Sortino"] = sortino(rets)
    out["TotalRet"] = float(equity[-1] / equity[0] - 1.0)
    years = (ts[-1] - ts[0]) / (3_600_000 * HOURS_PER_YEAR)
    if years > 0 and equity[-1] > 0:
        out["AnnRet"] = float((equity[-1] / equity[0]) ** (1.0 / years) - 1.0)
    peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, equity / peak - 1.0, np.nan)
    out["MaxDD"] = float(np.nanmin(dd))
    closed = [t for t in trades if "pnl" in t]
    if closed:
        out["WinRate"] = sum(1 for t in closed if t["pnl"] > 0) / len(closed)
    return out


def run_backtest(
    data: PairData,
    params: PairParams,
    cfg: StrategyConfig,
    costs: CostConfig,
    start_ms: int,
    end_ms: int,
    initial_equity: float,
    *,
    fits: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    record_trades: bool = True,
) -> BacktestResult:
    """Simulate one pair over [start_ms, end_ms] with warm-up bars taken from before start."""
    L = params.lookback
    i0, i1 = data.window(start_ms, end_ms)
    i0 = max(i0, L - 1)
    if i1 < i0:
        return BacktestResult(ts=np.empty(0, np.int64), equity=np.empty(0))
    alpha, beta, sigma = fits if fits is not None else estimate_fits(
        data.log_a, data.log_b, L, params.kalman_delta, cfg.kalman_outlier_z, cfg.kalman_max_skips)
    # entry_rule rolling_quantile: this bar's threshold from the model's Z over the previous bars.
    thr: list | None = None
    if cfg.entry_rule == "rolling_quantile":
        with np.errstate(divide="ignore", invalid="ignore"):
            z_path = (data.log_a - alpha - beta * data.log_b) / sigma
        thr = adaptive_entry_threshold(z_path, cfg.adaptive_window, cfg.adaptive_quantile,
                                       cfg.adaptive_min_z).tolist()

    fee = costs.taker_fee
    hs = costs.half_spread_bps * 1e-4
    cost = hs + costs.slippage_bps * 1e-4  # adverse move per fill, relative to the mid
    lbid, lask = math.log1p(-hs), math.log1p(hs)
    next_open = costs.fill_on == "next_open"
    use_funding, intrabar = costs.funding, costs.intrabar_stop
    allow_reentry = cfg.allow_same_bar_reentry

    spot_long = costs.long_leg == "spot"
    fee_long = costs.spot_fee if spot_long else fee
    strat = PairStrategy(params, cfg, initial_equity, round_trip_cost=(fee + fee_long) + 2.0 * cost,
                         anchor=i0, long_pays_funding=not spot_long)
    book = PairBook(cash=float(initial_equity))

    # Python lists make scalar access in the hot loop several times faster than numpy.
    ts_l = data.ts.tolist()
    la, lb = data.log_a.tolist(), data.log_b.tolist()
    ca, cb = data.close_a.tolist(), data.close_b.tolist()
    oa, ob = data.open_a.tolist(), data.open_b.tolist()
    ha, hb = data.high_a.tolist(), data.high_b.tolist()
    loa, lob = data.low_a.tolist(), data.low_b.tolist()
    fa, fb = data.funding_a.tolist(), data.funding_b.tolist()
    # Trailing average funding per hour (known at each bar close), for the funding filter.
    n_f = max(1, cfg.funding_lookback_hours)
    def trailing(x: np.ndarray) -> list:
        c = np.concatenate(([0.0], np.cumsum(x)))
        out = np.full(x.shape[0], np.nan)
        out[n_f - 1 :] = (c[n_f:] - c[:-n_f]) / n_f
        return out.tolist()
    use_ff = cfg.funding_filter != "off"
    use_fx = cfg.funding_exit != "off" and use_funding
    fra, frb = (trailing(data.funding_a), trailing(data.funding_b)) if use_ff or use_fx else (None, None)
    al, be, si = alpha.tolist(), beta.tolist(), sigma.tolist()
    data_ends_early = int(data.ts[-1]) < end_ms and i1 == len(data) - 1

    trades: list[dict] = []
    current: dict | None = None
    equity_out = np.empty(i1 - i0 + 1)

    maker_on = costs.maker_legs == 1
    mfee, adv = costs.maker_fee, costs.maker_adverse_bps * 1e-4
    urgent_reasons = STOP_REASONS | {"data_end"}

    def fill(px: float, qty: float, maker: bool = False) -> float:
        if maker:  # resting at the bid (buy) / ask (sell), minus adverse selection
            return px * (1.0 - hs + adv) if qty > 0 else px * (1.0 + hs - adv)
        return px * (1.0 + cost) if qty > 0 else px * (1.0 - cost)

    def fee_rate(position: float, maker: bool) -> float:
        if spot_long and position > 0:  # the long leg is held on spot
            return costs.spot_fee
        return mfee if maker else fee

    def legs(qa: float, qb: float, pa: float, pb: float, urgent: bool,
             pos_a: float, pos_b: float) -> tuple[float, float, float]:
        """Fill prices for trading qa/qb and the fee; the larger leg is maker when enabled.

        pos_a/pos_b: the position each leg holds (signed), which decides spot vs perp fees.
        """
        maker_a = maker_on and not urgent and abs(qa) * pa >= abs(qb) * pb
        maker_b = maker_on and not urgent and not maker_a
        fla, flb = fill(pa, qa, maker_a), fill(pb, qb, maker_b)
        return fla, flb, fee_rate(pos_a, maker_a) * abs(qa) * fla + fee_rate(pos_b, maker_b) * abs(qb) * flb

    def do_open(i: int, side: int, pa: float, pb: float, zb: float, zs: float) -> None:
        nonlocal current
        f = strat.fit
        try:
            qa, qb = target_quantities(book.cash, cfg.leverage, f.beta, pa, pb)
        except SizingError:
            return
        sa, sb = signed_quantities(side, qa, qb)
        fla, flb, fee_paid = legs(sa, sb, pa, pb, urgent=False, pos_a=sa, pos_b=sb)
        cash_before = book.cash
        book.open(sa, sb, fla, flb, fee_paid)
        eq = book.equity(pa, pb)
        hl = math.nan
        if cfg.time_stop_halflives is not None or cfg.funding_exit == "edge":
            lo = max(0, i - L + 1)
            hl = ou_half_life(data.log_a[lo : i + 1] - f.alpha - f.beta * data.log_b[lo : i + 1])
        strat.on_opened(side, ts_l[i], i, eq, zb, zs, hl)
        strat.observe_equity(eq)
        if record_trades:
            current = {
                "entry_ts": ts_l[i], "side": side, "z_entry": zb if side > 0 else zs,
                "alpha": f.alpha, "beta": f.beta, "sigma": f.sigma,
                "qty_a": sa, "qty_b": sb, "entry_px_a": fla, "entry_px_b": flb,
                "equity_before": cash_before, "fees": fee_paid,
                "funding_start": book.funding_paid,
            }
            if thr is not None:  # the threshold the signal crossed (decided on the previous bar with next_open)
                current["z_threshold"] = thr[i - 1 if next_open else i]

    def do_close(i: int, reason: str, pa: float, pb: float) -> None:
        nonlocal current
        qa, qb = book.qty_a, book.qty_b
        fla, flb, fee_paid = legs(-qa, -qb, pa, pb, urgent=reason in urgent_reasons, pos_a=qa, pos_b=qb)
        cash = book.close(fla, flb, fee_paid)
        strat.on_closed(reason, ts_l[i])
        strat.observe_equity(cash)
        if record_trades and current is not None:
            current.update(
                exit_ts=ts_l[i], reason=reason, exit_px_a=fla, exit_px_b=flb,
                fees=current["fees"] + fee_paid,
                funding=book.funding_paid - current.pop("funding_start"),
                equity_after=cash, pnl=cash - current["equity_before"],
            )
            trades.append(current)
            current = None

    pending: tuple | None = None
    for k, i in enumerate(range(i0, i1 + 1)):
        ts = ts_l[i]
        pa, pb = ca[i], cb[i]
        exited = False

        if pending is not None:  # orders decided at the previous close fill at this open
            kind, arg, zb_p, zs_p = pending
            pending = None
            if kind == "open":
                do_open(i, arg, oa[i], ob[i], zb_p, zs_p)
            else:
                do_close(i, arg, oa[i], ob[i])
                exited = True

        if book.in_position:
            if use_funding and (fa[i] != 0.0 or fb[i] != 0.0):
                if spot_long:  # spot holdings pay no funding; only the short perp settles it
                    book.apply_funding(fa[i] if book.qty_a < 0 else 0.0, pa, fb[i] if book.qty_b < 0 else 0.0, pb)
                else:
                    book.apply_funding(fa[i], pa, fb[i], pb)
            # Intrabar stops: detection is pessimistic (both legs at their adverse
            # extreme), but a continuously monitored stop exits where equity first
            # crosses the level, so the exit is placed on the open->extreme path.
            level = strat.stop_level() if intrabar else strat.catastrophe_level()
            if level > -math.inf:
                wa = loa[i] if book.qty_a > 0 else ha[i]
                wb = lob[i] if book.qty_b > 0 else hb[i]
                eq_w = book.equity(wa, wb)
                if eq_w <= level:
                    eq_o = book.equity(oa[i], ob[i])
                    if eq_o <= level:  # already through the level at the open
                        xa, xb = oa[i], ob[i]
                    else:
                        f = (eq_o - level) / (eq_o - eq_w)
                        xa, xb = oa[i] + f * (wa - oa[i]), ob[i] + f * (wb - ob[i])
                    do_close(i, "stop_loss_intrabar" if intrabar else "stop_catastrophe", xa, xb)
                    exited = True

        eq = book.equity(pa, pb)
        strat.observe_equity(eq)

        if strat.side != FLAT and pending is None:
            if eq <= 0.0:
                do_close(i, "liquidation", pa, pb)
                exited = True
            else:
                zb, zs = strat.z_scores(la[i] + lbid, la[i] + lask, lb[i] + lbid, lb[i] + lask)
                reason = strat.check_exit(ts, i, eq, zb, zs)
                if reason is None and use_fx and strat.check_funding_exit(eq, zb, zs, fra[i], frb[i]):
                    reason = "funding_exit"
                if reason is None and data_ends_early and i == i1:
                    reason = "data_end"
                if reason is not None:
                    if next_open and i < i1:
                        pending = ("close", reason, zb, zs)
                    else:
                        do_close(i, reason, pa, pb)
                        exited = True

        if strat.side == FLAT and pending is None and (allow_reentry or not exited):
            if strat.wants_refit(i):
                strat.set_fit(al[i], be[i], si[i], i)
            if strat.fit is not None:
                zb, zs = strat.z_scores(la[i] + lbid, la[i] + lask, lb[i] + lbid, lb[i] + lask)
                ze = thr[i] if thr is not None else None
                side = (strat.check_entry(ts, i, zb, zs, fra[i], frb[i], z_entry=ze) if use_ff
                        else strat.check_entry(ts, i, zb, zs, z_entry=ze))
                if side != FLAT and not (data_ends_early and i == i1):
                    if next_open:
                        if i < i1:
                            pending = ("open", side, zb, zs)
                    else:
                        do_open(i, side, pa, pb, zb, zs)

        equity_out[k] = book.equity(pa, pb)

    if record_trades and current is not None:  # still open at the end of the window
        current.pop("funding_start", None)
        trades.append(current)
    return BacktestResult(
        ts=data.ts[i0 : i1 + 1].copy(),
        equity=equity_out,
        trades=trades,
        fees=book.fees_paid,
        funding=book.funding_paid,
        deactivated=strat.deactivated,
    )

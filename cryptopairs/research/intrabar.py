"""Entries (and exits) on 1-minute closes: a pre-registered comparison of entry rules.

Why (2026-10-06): the bot checks entries only at the hourly close, so a Z that crosses the entry
threshold inside the hour and comes back is never traded. Entering on every crossing risks two
things: a spike that reverts within minutes (noise), and a spread that keeps running away
(momentum into the stop-loss). The rules compared here (plan ~/.claude/plans/lazy-gliding-wozniak.md):

    hourly      entries at the hourly close (bot B today)
    hourly_cap  the same, but no entry once |Z| went beyond threshold + 1.5 since it crossed
    i0          entry at the first minute close beyond the threshold (no filter; reference)
    i5          entry after 5 consecutive minute closes beyond the threshold (spike filter)
    turn        entry once Z has come back 0.3 from its extreme since it crossed (momentum filter)
    turn_cap    turn, and no entry once the extreme went beyond threshold + 1.5

Everything else is the hourly backtest (research/backtest.py): the same model, refitted at each
hourly close while flat and frozen in a position, the same costs and fills (a decision fills at
the next bar's open: the next minute's open here), funding at the hourly settlements, the stop-loss
and time stops at hourly closes, the catastrophe stop on the bar's extremes. Exits are also checked
on minute closes (bot B's realtime exits). One action per pair per bar, as in the bot. With
``minute_exits=False`` and the hourly rule, the result is the hourly backtest's (tests check it).
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from cryptopairs.config import AppConfig, CostConfig, StrategyConfig
from cryptopairs.core.book import PairBook
from cryptopairs.core.signals import estimate_fits
from cryptopairs.core.sizing import SizingError, signed_quantities, target_quantities
from cryptopairs.core.strategy import FLAT, LONG, SHORT, STOP_REASONS, PairParams, PairStrategy
from cryptopairs.research.backtest import BacktestResult, performance
from cryptopairs.research.data import DataStore, PairData, load_pair
from cryptopairs.research.experiments import STAGES as EXP_STAGES

log = logging.getLogger(__name__)
HOUR = 3_600_000
MINUTE = 60_000


@dataclass(frozen=True)
class EntryRule:
    minute: bool = False  # entries on every minute close (else at the hourly close only)
    confirm: int = 1  # consecutive minute closes beyond the threshold
    turn: float = 0.0  # Z must be back this much from its extreme since it crossed
    cap: float | None = None  # no entry once the extreme went beyond threshold + cap
    minute_exits: bool = True  # exit signals on minute closes too (bot B)


RULES: dict[str, EntryRule] = {
    "replica": EntryRule(minute_exits=False),  # sanity check only: must give the hourly backtest (K1)
    "hourly": EntryRule(),
    "hourly_cap": EntryRule(cap=1.5),
    "i0": EntryRule(minute=True),
    "i5": EntryRule(minute=True, confirm=5),
    "turn": EntryRule(minute=True, turn=0.3),
    "turn_cap": EntryRule(minute=True, turn=0.3, cap=1.5),
}


@dataclass
class Minutes:
    """1-minute bars of both legs on common timestamps (open time)."""

    ts: np.ndarray
    open_a: np.ndarray
    high_a: np.ndarray
    low_a: np.ndarray
    close_a: np.ndarray
    open_b: np.ndarray
    high_b: np.ndarray
    low_b: np.ndarray
    close_b: np.ndarray

    @classmethod
    def from_frames(cls, ka: pd.DataFrame, kb: pd.DataFrame, start_ms: int, end_ms: int) -> "Minutes":
        m = ka.merge(kb, on="ts", suffixes=("_a", "_b"), how="inner").sort_values("ts")
        m = m[(m["ts"] >= start_ms) & (m["ts"] < end_ms) & (m["close_a"] > 0) & (m["close_b"] > 0)]
        col = lambda c: m[c].to_numpy(dtype=np.float64)  # noqa: E731
        return cls(ts=m["ts"].to_numpy(dtype=np.int64), open_a=col("open_a"), high_a=col("high_a"),
                   low_a=col("low_a"), close_a=col("close_a"), open_b=col("open_b"), high_b=col("high_b"),
                   low_b=col("low_b"), close_b=col("close_b"))


def _runs(beyond: np.ndarray, u: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each minute: how many consecutive minutes (up to it) are beyond, and the extreme of u over them."""
    n = beyond.shape[0]
    streak = np.zeros(n, dtype=np.int64)
    ext = np.full(n, -np.inf)
    if n == 0:
        return streak, ext
    start = beyond & ~np.concatenate(([False], beyond[:-1]))
    run_id = np.cumsum(start)
    idx = np.arange(n)
    first = np.zeros(run_id.max() + 1, dtype=np.int64)
    first[run_id[start]] = idx[start]
    streak[beyond] = idx[beyond] - first[run_id[beyond]] + 1
    s = pd.Series(np.where(beyond, u, -np.inf))
    ext[beyond] = s.groupby(run_id).cummax().to_numpy()[beyond]
    return streak, ext


def entry_marks(zb: np.ndarray, zs: np.ndarray, z_entry: float, rule: EntryRule) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per minute: (candidate for an entry under the rule, extreme since the crossing on the short side,
    the same on the long side). Short: z_sell above +z_entry; long: z_buy below -z_entry."""
    out, exts = np.zeros(zb.shape[0], dtype=bool), []
    for u in (zs, -zb):  # short side, long side (both as "how far beyond, positive")
        with np.errstate(invalid="ignore"):
            beyond = np.nan_to_num(u, nan=-np.inf) > z_entry
        streak, ext = _runs(beyond, u)
        ok = beyond & (streak >= rule.confirm) & (ext - u >= rule.turn - 1e-12)
        if rule.cap is not None:
            ok &= ext <= z_entry + rule.cap
        out |= ok
        exts.append(ext)
    return out, exts[0], exts[1]


def run_intrabar(
    data: PairData,
    mins: Minutes,
    params: PairParams,
    cfg: StrategyConfig,
    costs: CostConfig,
    start_ms: int,
    end_ms: int,
    initial_equity: float,
    rule: EntryRule,
    *,
    fits: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
) -> BacktestResult:
    """One pair over [start_ms, end_ms]; see the module docstring. Requires ``fill_on: next_open``."""
    if costs.fill_on != "next_open" or cfg.entry_rule != "fixed" or costs.long_leg != "perp":
        raise ValueError("intrabar replay supports fill_on next_open, entry_rule fixed and perp legs only")
    if cfg.funding_filter != "off" or cfg.funding_exit != "off" or costs.intrabar_stop:
        raise ValueError("intrabar replay does not model the funding filter/exit or the intrabar stop-loss")
    L = params.lookback
    i0, i1 = data.window(start_ms, end_ms)
    i0 = max(i0, L - 1)
    if i1 < i0:
        return BacktestResult(ts=np.empty(0, np.int64), equity=np.empty(0))
    alpha, beta, sigma = fits if fits is not None else estimate_fits(
        data.log_a, data.log_b, L, params.kalman_delta, cfg.kalman_outlier_z, cfg.kalman_max_skips)

    fee = costs.taker_fee
    hs = costs.half_spread_bps * 1e-4
    cost = hs + costs.slippage_bps * 1e-4
    lbid, lask = math.log1p(-hs), math.log1p(hs)
    use_funding = costs.funding
    allow_reentry = cfg.allow_same_bar_reentry
    strat = PairStrategy(params, cfg, initial_equity, round_trip_cost=2.0 * fee + 2.0 * cost, anchor=i0)
    book = PairBook(cash=float(initial_equity))
    maker_on = costs.maker_legs == 1
    mfee, adv = costs.maker_fee, costs.maker_adverse_bps * 1e-4
    urgent_reasons = STOP_REASONS | {"data_end"}

    ts_l = data.ts.tolist()
    la, lb = data.log_a.tolist(), data.log_b.tolist()
    ca, cb = data.close_a.tolist(), data.close_b.tolist()
    oa, ob = data.open_a.tolist(), data.open_b.tolist()
    ha, hb = data.high_a.tolist(), data.high_b.tolist()
    loa, lob = data.low_a.tolist(), data.low_b.tolist()
    fa, fb = data.funding_a.tolist(), data.funding_b.tolist()
    al, be, si = alpha.tolist(), beta.tolist(), sigma.tolist()
    data_ends_early = int(data.ts[-1]) < end_ms and i1 == len(data) - 1

    # ---- minutes: bar i holds the minutes that open in [ts_i, ts_i + 1h); the last one closes at the bar close
    use_min = rule.minute or rule.minute_exits or rule.cap is not None
    mts = mins.ts
    m_lo = np.searchsorted(mts, data.ts[i0 : i1 + 1], side="left")
    m_hi = np.searchsorted(mts, data.ts[i0 : i1 + 1] + HOUR, side="left")
    lca, lcb = np.log(mins.close_a), np.log(mins.close_b)
    # Z while flat on each minute close: the fit set at the previous hourly close, or at this one for the
    # minute that closes on the hour (the refit comes before the decision, as in the backtest).
    fit_ix = np.full(mts.shape[0], -1, dtype=np.int64)
    bar_of = np.full(mts.shape[0], -1, dtype=np.int64)
    for k, i in enumerate(range(i0, i1 + 1)):
        lo, hi = m_lo[k], m_hi[k]
        bar_of[lo:hi] = i
        fit_ix[lo:hi] = i - 1
        if hi > lo and mts[hi - 1] + MINUTE == ts_l[i] + HOUR:
            fit_ix[hi - 1] = i
    ok = fit_ix >= 0
    fa_, fb_, fs_ = (np.where(ok, np.asarray(x)[np.maximum(fit_ix, 0)], np.nan) for x in (alpha, beta, sigma))
    with np.errstate(invalid="ignore", divide="ignore"):
        zb_flat = (lca + lask - fa_ - fb_ * (lcb + lbid)) / fs_
        zs_flat = (lca + lbid - fa_ - fb_ * (lcb + lask)) / fs_
    cand, ext_s, ext_l = entry_marks(zb_flat, zs_flat, params.z_entry, rule) if use_min else (None, None, None)
    cand_ix = np.flatnonzero(cand) if rule.minute else np.empty(0, dtype=np.int64)

    trades: list[dict] = []
    current: dict | None = None
    equity_out = np.empty(i1 - i0 + 1)

    def fill(px: float, qty: float, maker: bool = False) -> float:
        if maker:
            return px * (1.0 - hs + adv) if qty > 0 else px * (1.0 + hs - adv)
        return px * (1.0 + cost) if qty > 0 else px * (1.0 - cost)

    def legs(qa, qb, pa, pb, urgent):
        maker_a = maker_on and not urgent and abs(qa) * pa >= abs(qb) * pb
        maker_b = maker_on and not urgent and not maker_a
        fla, flb = fill(pa, qa, maker_a), fill(pb, qb, maker_b)
        return fla, flb, (mfee if maker_a else fee) * abs(qa) * fla + (mfee if maker_b else fee) * abs(qb) * flb

    def do_open(i, ts, side, pa, pb, zb, zs, z_ext=math.nan):
        nonlocal current
        f = strat.fit
        try:
            qa, qb = target_quantities(book.cash, cfg.leverage, f.beta, pa, pb)
        except SizingError:
            return False
        sa, sb = signed_quantities(side, qa, qb)
        fla, flb, fee_paid = legs(sa, sb, pa, pb, urgent=False)
        cash_before = book.cash
        book.open(sa, sb, fla, flb, fee_paid)
        eq = book.equity(pa, pb)
        strat.on_opened(side, ts, i, eq, zb, zs, math.nan)
        strat.observe_equity(eq)
        current = {"entry_ts": ts, "side": side, "z_entry": zb if side > 0 else zs, "z_extreme": z_ext,
                   "alpha": f.alpha, "beta": f.beta, "sigma": f.sigma, "qty_a": sa, "qty_b": sb,
                   "entry_px_a": fla, "entry_px_b": flb, "equity_before": cash_before, "fees": fee_paid,
                   "funding_start": book.funding_paid}
        return True

    def do_close(ts, reason, pa, pb):
        nonlocal current
        qa, qb = book.qty_a, book.qty_b
        fla, flb, fee_paid = legs(-qa, -qb, pa, pb, urgent=reason in urgent_reasons)
        cash = book.close(fla, flb, fee_paid)
        strat.on_closed(reason, ts)
        strat.observe_equity(cash)
        if current is not None:
            current.update(exit_ts=ts, reason=reason, exit_px_a=fla, exit_px_b=flb, fees=current["fees"] + fee_paid,
                           funding=book.funding_paid - current.pop("funding_start"), equity_after=cash,
                           pnl=cash - current["equity_before"])
            trades.append(current)
            current = None

    def ext_for(side: int, m: int) -> float:
        if ext_s is None:
            return math.nan
        e = ext_s[m] if side == SHORT else ext_l[m]
        return float(e) if np.isfinite(e) else math.nan

    def first_exit(lo: int, hi: int) -> int:
        """Index of the first minute close in [lo, hi) whose Z crosses the exit band (the rule of
        PairStrategy.check_exit without stops), or -1; keeps prev_exit_z as check_exit would."""
        if hi <= lo:
            return -1
        f = strat.fit
        if strat.side == LONG:
            z = (lca[lo:hi] + lbid - f.alpha - f.beta * (lcb[lo:hi] + lask)) / f.sigma  # z_sell
        else:
            z = (lca[lo:hi] + lask - f.alpha - f.beta * (lcb[lo:hi] + lbid)) / f.sigma  # z_buy
        prev = pd.Series(np.concatenate(([strat.prev_exit_z], z[:-1]))).ffill().to_numpy()
        ze = strat.exit_z()
        with np.errstate(invalid="ignore"):
            hit = (prev < -ze) & (z >= -ze) if strat.side == LONG else (prev > ze) & (z <= ze)
        if hit.any():
            k = int(np.argmax(hit))
            strat.prev_exit_z = float(z[k])
            return lo + k
        good = z[np.isfinite(z)]
        if good.size:
            strat.prev_exit_z = float(good[-1])
        return -1

    pending: tuple | None = None
    for k, i in enumerate(range(i0, i1 + 1)):
        ts = ts_l[i]
        pa, pb = ca[i], cb[i]
        exited = entered_at = False
        entry_minute = -1
        if pending is not None:  # decided at the previous hourly close: fills at this bar's open
            kind, arg, zb_p, zs_p, zx_p = pending
            pending = None
            if kind == "open":
                do_open(i, ts, arg, oa[i], ob[i], zb_p, zs_p, zx_p)
            else:
                do_close(ts, arg, oa[i], ob[i])
                exited = True

        # ---- inside the bar: minute closes before the hourly close (decisions fill at the next minute's open)
        lo, hi = int(m_lo[k]), int(m_hi[k])
        last = hi - 1 if hi > lo and mts[hi - 1] + MINUTE == ts + HOUR else hi  # the minute closing on the hour
        m = lo
        while use_min and m < last - 1:  # a decision at minute m needs minute m + 1 inside the bar to fill
            if strat.side != FLAT and rule.minute_exits:
                x = first_exit(m, last - 1)
                if x < 0:
                    break
                do_close(int(mts[x + 1]), "signal", mins.open_a[x + 1], mins.open_b[x + 1])
                exited = True
                break  # one action per bar: no entry after an exit
            if strat.side != FLAT:
                break
            if not rule.minute or exited or entered_at or strat.fit is None:
                break
            j = int(np.searchsorted(cand_ix, m))
            opened = False
            while j < cand_ix.shape[0] and cand_ix[j] < last - 1:
                c = int(cand_ix[j])
                zb, zs = float(zb_flat[c]), float(zs_flat[c])
                side = strat.check_entry(int(mts[c]) + MINUTE, i - 1, zb, zs)
                if side != FLAT and do_open(i, int(mts[c + 1]), side, mins.open_a[c + 1], mins.open_b[c + 1],
                                            zb, zs, ext_for(side, c)):
                    entered_at, opened, entry_minute = True, True, c + 1
                    m = c + 1  # the fill minute; exits are checked from its close on
                    break
                j += 1
            if not opened:
                break

        if strat.side != FLAT:
            if use_funding and (fa[i] != 0.0 or fb[i] != 0.0):
                book.apply_funding(fa[i], pa, fb[i], pb)
            level = strat.catastrophe_level()
            if level > -math.inf:
                if entry_minute >= 0:  # opened inside this bar: only the minutes since the fill
                    s = slice(entry_minute, hi)
                    hia, loa_, hib, lob_ = mins.high_a[s].max(), mins.low_a[s].min(), mins.high_b[s].max(), mins.low_b[s].min()
                    o_a, o_b = mins.open_a[entry_minute], mins.open_b[entry_minute]
                else:
                    hia, loa_, hib, lob_, o_a, o_b = ha[i], loa[i], hb[i], lob[i], oa[i], ob[i]
                wa = loa_ if book.qty_a > 0 else hia
                wb = lob_ if book.qty_b > 0 else hib
                eq_w = book.equity(wa, wb)
                if eq_w <= level:
                    eq_o = book.equity(o_a, o_b)
                    if eq_o <= level:
                        xa, xb = o_a, o_b
                    else:
                        f = (eq_o - level) / (eq_o - eq_w)
                        xa, xb = o_a + f * (wa - o_a), o_b + f * (wb - o_b)
                    do_close(ts, "stop_catastrophe", xa, xb)
                    exited = True

        eq = book.equity(pa, pb)
        strat.observe_equity(eq)

        # ---- the hourly close
        if strat.side != FLAT and pending is None:
            if eq <= 0.0:
                do_close(ts, "liquidation", pa, pb)
                exited = True
            else:
                zb, zs = strat.z_scores(la[i] + lbid, la[i] + lask, lb[i] + lbid, lb[i] + lask)
                reason = strat.check_exit(ts, i, eq, zb, zs)
                if reason is None and data_ends_early and i == i1:
                    reason = "data_end"
                if reason is not None:
                    if i < i1:
                        pending = ("close", reason, zb, zs, math.nan)
                    else:  # the window's last bar: close at its close, as the backtest does
                        do_close(ts, reason, pa, pb)
                        exited = True

        # The refit happens even after an exit in this bar (only the entry waits for the next bar), so the
        # minute rules can enter during the next hour; for the hourly rule this changes nothing.
        if strat.side == FLAT and pending is None and strat.wants_refit(i) and not entered_at:
            strat.set_fit(al[i], be[i], si[i], i)
        if strat.side == FLAT and pending is None and (allow_reentry or not exited) and not entered_at:
            if strat.fit is not None:
                zb, zs = strat.z_scores(la[i] + lbid, la[i] + lask, lb[i] + lbid, lb[i] + lask)
                side = strat.check_entry(ts, i, zb, zs)
                zx = math.nan
                if side != FLAT and use_min:
                    if last < hi:
                        zx = ext_for(side, last)
                        if rule.minute and not cand[last]:
                            side = FLAT  # the minute rule decides at the hourly close too
                        elif rule.cap is not None and np.isfinite(zx) and zx > params.z_entry + rule.cap:
                            side = FLAT
                    elif rule.minute or rule.cap is not None:
                        side = FLAT  # no minute closing on the hour: the rule cannot be checked
                if side != FLAT and not (data_ends_early and i == i1) and i < i1:
                    pending = ("open", side, zb, zs, zx)

        equity_out[k] = book.equity(pa, pb)

    if current is not None:
        current.pop("funding_start", None)
        trades.append(current)
    return BacktestResult(ts=data.ts[i0 : i1 + 1].copy(), equity=equity_out, trades=trades, fees=book.fees_paid,
                          funding=book.funding_paid, deactivated=strat.deactivated)


# ------------------------------------------------------------------ portfolio experiment
STAGES = {s: (d["test_start"], d["test_end"]) for s, d in EXP_STAGES.items()}


def _ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").value // 1_000_000)


def _one(job: tuple) -> tuple[str, str, dict]:
    """Every rule for one pair and stage (the minute data is loaded once)."""
    cfg, stage, row, rule_names, capital = job
    store, mstore = DataStore(Path(cfg.data.dir), "1h"), DataStore(Path(cfg.data.dir), "1m")
    start, end = (_ms(x) for x in STAGES[stage])
    rc = replace(cfg.research, **EXP_STAGES[stage])  # the stage's dates and history: where the Kalman starts
    sc = cfg.strategy
    data = load_pair(store, row["a"], row["b"], rc.data_start_ms, end)
    params = PairParams(int(row["lookback"]), float(row["z_entry"]), float(row["z_exit"]),
                        None if pd.isna(row["kalman_delta"]) else float(row["kalman_delta"]))
    fits = estimate_fits(data.log_a, data.log_b, params.lookback, params.kalman_delta, sc.kalman_outlier_z,
                         sc.kalman_max_skips)
    mins = Minutes.from_frames(mstore.load_klines(row["a"]), mstore.load_klines(row["b"]), start - HOUR, end + 2 * HOUR)
    out = {}
    for name in rule_names:
        res = run_intrabar(data, mins, params, sc, cfg.costs, start, end, capital, RULES[name], fits=fits)
        out[name] = {"ts": res.ts, "equity": res.equity, "trades": res.trades, "fees": res.fees, "funding": res.funding}
    return stage, f"{row['a']}-{row['b']}", out


def portfolio(stage: str, per_pair: dict[str, dict], capital: float) -> tuple[dict, list[dict], pd.DataFrame]:
    start, end = (_ms(x) for x in STAGES[stage])
    grid = np.arange(start, end + 1, HOUR, dtype=np.int64)
    total = np.zeros(grid.shape[0])
    trades = []
    for key, r in per_pair.items():
        if r["ts"].size == 0:
            total += capital
            continue
        total += pd.Series(r["equity"], index=r["ts"]).reindex(grid).ffill().fillna(capital).to_numpy()
        trades += [{**t, "pair": key} for t in r["trades"]]
    m = performance(grid, total, trades, fees=sum(r["fees"] for r in per_pair.values()),
                    funding=sum(r["funding"] for r in per_pair.values()))
    closed = [t for t in trades if "pnl" in t]
    stops = [t for t in closed if str(t["reason"]).startswith("stop")]
    m.update({
        "Stops%": 100 * len(stops) / len(closed) if closed else math.nan,
        "AvgStopLoss%": 100 * float(np.mean([t["pnl"] / t["equity_before"] for t in stops])) if stops else math.nan,
        "AvgTrade%": 100 * float(np.mean([t["pnl"] / t["equity_before"] for t in closed])) if closed else math.nan,
        "MedianAbsZEntry": float(np.median([abs(t["z_entry"]) for t in trades])) if trades else math.nan,
    })
    return m, trades, pd.DataFrame({"ts": grid, "equity": total})


def run_experiment(cfg: AppConfig, k1_dir: str | Path, out_dir: str | Path, rules: list[str] | None = None,
                   workers: int = 4) -> pd.DataFrame:
    """Every rule on K1's pairs and parameters (``k1_dir/<stage>/k1_kalman_filter/pairs_results.csv``)."""
    rules = rules or list(RULES)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    caps = {}
    for stage in STAGES:
        pr = pd.read_csv(Path(k1_dir) / stage / "k1_kalman_filter" / "pairs_results.csv")
        caps[stage] = cfg.research.initial_capital / len(pr)
        jobs += [(cfg, stage, r, rules, caps[stage]) for r in pr.to_dict("records")]
    results: dict[str, dict[str, dict]] = {s: {} for s in STAGES}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, (stage, key, res) in enumerate(pool.map(_one, jobs, chunksize=1), 1):
            results[stage][key] = res
            if n % 20 == 0:
                log.info("%d / %d pairs done", n, len(jobs))
    rows = []
    for stage in STAGES:
        for name in rules:
            per_pair = {k: v[name] for k, v in results[stage].items()}
            m, trades, eq = portfolio(stage, per_pair, caps[stage])
            rows.append({"stage": stage, "rule": name, **m})
            pd.DataFrame(trades).to_csv(out / f"trades_{stage}_{name}.csv", index=False)
            eq.to_csv(out / f"equity_{stage}_{name}.csv", index=False)
    df = pd.DataFrame(rows)
    df.to_csv(out / "results.csv", index=False)
    return df

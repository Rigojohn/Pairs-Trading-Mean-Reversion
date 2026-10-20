"""Walk-forward test of bot B's recipe, re-run every month (2026-10-19).

Bot B's pairs come from ``research --final``: the funnel on the last 800 days and the per-pair
calibration on the last 730 days, run once (30/9/2026) and never refreshed. This module replays that
recipe on rolling windows. Before each month it sees only the data up to the hour before the month
(every load is cut there, as in experiments.py), picks the pairs and their parameters, and then
trades the month with B's rules (research/intrabar.py, rule "hourly": entries at the hourly close,
exits on minute closes, the catastrophe stop on minute extremes). Every month is out of sample and
nothing is chosen by looking at the months traded (the protocol of Deep, Deep & Lamptey 2025,
arXiv:2512.12924: rolling folds, frozen test windows, statistics over the folds).

Two scores pick each pair's parameters from the same calibration grid (the pairs are the same):
    sharpe       highest training Sharpe (today's recipe)
    sortino_dd   best sum of the ranks of training Sortino and training max drawdown

At a month end, as in the bot: an open position keeps the parameters it opened with until it exits;
a pair that stays in the list switches to its new parameters once flat; a pair that leaves the list
takes no new entries. Capital: a pair that enters the list gets 1/slots of the portfolio's equity at
the month's start; a pair that stays keeps its own equity; nothing is added from outside.

Not modelled (as in every B backtest so far): the per-coin cap (risk.max_symbol_gross_leverage), the
open-pairs cap and the kill switch (pairs run independently), exits checked every 5 s (here: every
minute), manual trades.

    python -m cryptopairs --profile kalman_b walkforward data        # data_wf/: the archive from 2020-01
    python -m cryptopairs --profile kalman_b walkforward reproduce   # today's B numbers, on data/
    python -m cryptopairs --profile kalman_b walkforward run         # select, simulate, report
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import shutil
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from cryptopairs.config import AppConfig
from cryptopairs.core.signals import estimate_fits
from cryptopairs.core.strategy import PairParams
from cryptopairs.research.backtest import HOURS_PER_YEAR, sortino
from cryptopairs.research.calibrate import calibrate_all, select_best
from cryptopairs.research.data import (BUCKET, KLINE_COLS, UM, DataStore, _fetch_kline_month, _get,
                                       list_archive_symbols, load_pair, month_range)
from cryptopairs.research.funnel import run_funnel
from cryptopairs.research.intrabar import RULES, Minutes, run_intrabar
from cryptopairs.research.metadata import load_chain_map

log = logging.getLogger(__name__)
HOUR = 3_600_000
DAY = 24 * HOUR
VARIANTS: dict[str, str] = {"sharpe": "argmax", "sortino_dd": "sortino_dd"}
BASELINE, CANDIDATE = "sharpe", "sortino_dd"
CHOSEN_COLS = ["a", "b", "ic_rank", "lookback", "z_entry", "z_exit", "kalman_delta"]
PARAM_COLS = ["a", "b", "lookback", "z_entry", "z_exit", "kalman_delta"]


# ------------------------------------------------------------------------------ folds
@dataclass(frozen=True)
class Fold:
    month: str  # "2022-10"
    start_ms: int  # first bar of the month
    end_ms: int  # first bar of the next month (exclusive)


def month_folds(first: str, last: str) -> list[Fold]:
    out = []
    for p in pd.period_range(first, last, freq="M"):
        start = int(pd.Timestamp(p.start_time).value // 1_000_000)
        end = int(pd.Timestamp((p + 1).start_time).value // 1_000_000)
        out.append(Fold(str(p), start, end))
    return out


def fold_config(cfg: AppConfig, start_ms: int, end_ms: int, data_dir: str | Path) -> AppConfig:
    """The recipe's config for one month: training data ends at the hour before ``start_ms``."""
    out = copy.deepcopy(cfg)
    out.data.dir = str(data_dir)
    out.research.test_start = int(start_ms)
    out.research.test_end = int(end_ms - HOUR)
    return out


# -------------------------------------------------------------------------- selection
def select_fold(cfg: AppConfig, chains: dict[str, set[str]] | None,
                selections: dict[str, str] = VARIANTS) -> dict:
    """B's recipe at one date: the funnel (top_n by IC), the grid per pair, then each score's pick.

    Every load here ends at ``cfg.research.train_end_ms``: run_funnel cuts its price matrix there and
    the calibration loads each pair up to it (load_pair end), so later bars cannot reach the choice.
    """
    rc = cfg.research
    if rc.same_chain and not chains:
        raise ValueError("same_chain needs data/meta/chains.csv (B's recipe uses it)")
    store = DataStore(Path(cfg.data.dir), cfg.data.interval)
    funnel = run_funnel(store, rc, chains)
    table = funnel.table
    pairs = list(zip(table["a"], table["b"]))
    tables = {(c.a, c.b): c.table for c in calibrate_all(pairs, cfg)} if pairs else {}
    grid = pd.concat([t.assign(a=a, b=b) for (a, b), t in tables.items() if not t.empty], ignore_index=True) \
        if tables else pd.DataFrame()
    chosen = {}
    for name, sel in selections.items():
        rows = []
        for rank, (a, b) in enumerate(pairs):
            t = tables.get((a, b))
            best = None if t is None or t.empty else select_best(t, sel, rc.min_train_trades)
            if best is not None:
                rows.append({"a": a, "b": b, "ic_rank": rank, **best})
        chosen[name] = pd.DataFrame(rows) if rows else pd.DataFrame(columns=CHOSEN_COLS)
    return {"funnel": table, "counts": funnel.counts, "grid": grid, "chosen": chosen}


def _fold_dir(out_dir: Path, month: str) -> Path:
    return out_dir / "folds" / month


def run_selection(cfg: AppConfig, folds: list[Fold], data_dir: str | Path, out_dir: str | Path) -> None:
    """Select every month (resumable: a month with done.json is skipped)."""
    out_dir = Path(out_dir)
    chains = load_chain_map(Path(data_dir) / "meta" / "chains.csv") or None
    for fold in folds:
        fdir = _fold_dir(out_dir, fold.month)
        if (fdir / "done.json").exists():
            continue
        t0 = time.time()
        res = select_fold(fold_config(cfg, fold.start_ms, fold.end_ms, data_dir), chains)
        fdir.mkdir(parents=True, exist_ok=True)
        res["funnel"].to_csv(fdir / "funnel.csv", index=False)
        res["grid"].to_csv(fdir / "grid.csv.gz", index=False)
        for name, df in res["chosen"].items():
            df.to_csv(fdir / f"chosen_{name}.csv", index=False)
        info = {"month": fold.month, "seconds": round(time.time() - t0, 1), "counts": res["counts"],
                **{f"pairs_{k}": len(v) for k, v in res["chosen"].items()}}
        (fdir / "done.json").write_text(json.dumps(info, indent=2, default=int))
        log.info("selected %s: %d pairs in the funnel, %s chosen, %.0f s", fold.month, len(res["funnel"]),
                 {k: len(v) for k, v in res["chosen"].items()}, time.time() - t0)


def load_chosen(out_dir: str | Path, month: str, variant: str) -> pd.DataFrame:
    p = _fold_dir(Path(out_dir), month) / f"chosen_{variant}.csv"
    if not p.exists():
        raise FileNotFoundError(f"{p}: run the selection first")
    try:
        return pd.read_csv(p)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=CHOSEN_COLS)


def _csv_roundtrip(df: pd.DataFrame) -> pd.DataFrame:
    """The frame as it reads back from a CSV, so a fresh result compares exactly with a saved one."""
    import io

    return pd.read_csv(io.StringIO(df.to_csv(index=False))) if len(df.columns) else df


# --------------------------------------------------------------------- minute bars
class MinuteStore:
    """1-minute klines, one file per symbol and month (<root>/klines/1m_monthly/<SYMBOL>/<YYYY-MM>.pkl).

    A month the archive does not have is stored as an empty file, so it is not requested again.
    """

    def __init__(self, root: str | Path):
        self.dir = Path(root) / "klines" / "1m_monthly"

    def path(self, symbol: str, month: str) -> Path:
        return self.dir / symbol / f"{month}.pkl"

    def _save(self, path: Path, df: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        df.to_pickle(tmp)
        tmp.replace(path)

    def ensure(self, symbol: str, months: list[str]) -> int:
        n = 0
        for m in months:
            p = self.path(symbol, m)
            if p.exists():
                continue
            df = _fetch_kline_month(symbol, "1m", m)
            self._save(p, pd.DataFrame(columns=KLINE_COLS) if df is None else df)
            n += 1
        return n

    def load(self, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        """Minutes opening in [start_ms, end_ms)."""
        months = month_range(start_ms, end_ms - 1)
        self.ensure(symbol, months)
        frames = [f for f in (pd.read_pickle(self.path(symbol, m)) for m in months) if len(f)]
        if not frames:
            return pd.DataFrame(columns=KLINE_COLS)
        df = pd.concat(frames, ignore_index=True)
        return df[(df["ts"] >= start_ms) & (df["ts"] < end_ms)]

    def import_legacy(self, legacy: DataStore) -> int:
        """Split data/klines/1m/<SYMBOL>.pkl into complete months (an incomplete one is downloaded later)."""
        n = 0
        for sym in legacy.symbols():
            df = legacy.load_klines(sym)
            if df.empty:
                continue
            month = pd.to_datetime(df["ts"], unit="ms").dt.to_period("M").astype(str)
            for m, part in df.groupby(month):
                p = self.path(sym, m)
                if p.exists():
                    continue
                if len(part) == pd.Period(m, freq="M").days_in_month * 1440:
                    self._save(p, part.reset_index(drop=True))
                    n += 1
        return n


# ------------------------------------------------------------------------ segments
@dataclass
class Segment:
    """One pair under one month's parameters, from its first free bar to its first flat bar after the month."""

    variant: str
    month: str
    a: str
    b: str
    lookback: int
    z_entry: float
    z_exit: float
    kalman_delta: float | None
    start_ms: int
    entries_until_ms: int | None  # None: a fixed window ending at end_ms (reproduction)
    end_ms: int  # the first horizon; extended while a position is still open
    initial_equity: float
    deactivation_base: float
    cooldown_until: int
    new_bucket: bool


_CTX: dict = {}


def _init_worker(cfg: AppConfig, data_dir: str, minute_root: str | None, global_end_ms: int,
                 kalman_from_ms: int | None) -> None:
    logging.getLogger("cryptopairs").setLevel(logging.WARNING)
    _CTX.update(cfg=cfg, store=DataStore(Path(data_dir), "1h"), global_end_ms=global_end_ms,
                kalman_from_ms=kalman_from_ms,
                minutes=MinuteStore(minute_root) if minute_root else None,
                legacy=None if minute_root else DataStore(Path(data_dir), "1m"))


def _minutes(sym: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    if _CTX["minutes"] is not None:
        return _CTX["minutes"].load(sym, start_ms, end_ms)
    return _CTX["legacy"].load_klines(sym)


def simulate_segment(seg: Segment) -> dict:
    cfg: AppConfig = _CTX["cfg"]
    sc, global_end = cfg.strategy, _CTX["global_end_ms"]
    params = PairParams(int(seg.lookback), float(seg.z_entry), float(seg.z_exit),
                        None if seg.kalman_delta is None or pd.isna(seg.kalman_delta) else float(seg.kalman_delta))
    data = load_pair(_CTX["store"], seg.a, seg.b, _CTX["kalman_from_ms"], global_end)
    fits = estimate_fits(data.log_a, data.log_b, params.lookback, params.kalman_delta,
                         sc.kalman_outlier_z, sc.kalman_max_skips)
    if seg.entries_until_ms is None:
        horizons = [seg.end_ms]
    else:
        last_bar = seg.entries_until_ms - HOUR
        horizons = sorted({min(h, global_end) for h in (seg.end_ms, last_bar + 92 * DAY, last_bar + 366 * DAY,
                                                        global_end)})
    for h in horizons:
        lo, hi = seg.start_ms - HOUR, min(h + 2 * HOUR, global_end + HOUR)  # the replay reads minutes < h + 1h
        mins = Minutes.from_frames(_minutes(seg.a, lo, hi), _minutes(seg.b, lo, hi), lo, hi)
        res = run_intrabar(data, mins, params, sc, cfg.costs, seg.start_ms, h, seg.initial_equity, RULES["hourly"],
                           fits=fits, entries_until_ms=seg.entries_until_ms, cooldown_until=seg.cooldown_until,
                           deactivation_base=seg.deactivation_base)
        open_at_end = bool(res.trades) and "exit_ts" not in res.trades[-1]
        if not open_at_end or seg.entries_until_ms is None:
            break
    n = res.ts.shape[0]
    trades = [{**t, "variant": seg.variant, "month": seg.month, "pair": f"{seg.a}-{seg.b}",
               "lookback": params.lookback, "z_entry_param": params.z_entry, "z_exit_param": params.z_exit,
               "kalman_delta": params.kalman_delta} for t in res.trades]
    return {"ts": res.ts, "equity": res.equity, "trades": trades, "fees": res.fees, "funding": res.funding,
            "final": float(res.equity[-1]) if n else seg.initial_equity,
            "next_free_ms": int(res.ts[-1]) + HOUR if n else (seg.entries_until_ms or seg.end_ms + HOUR),
            "cooldown_until": int(res.cooldown_until), "open_at_end": open_at_end, "deactivated": res.deactivated}


class Portfolio:
    """Equity of independent pair buckets on one hourly grid: E(t) = E0 + the PnL of every segment."""

    def __init__(self, start_ms: int, end_ms: int, initial: float):
        self.start = int(start_ms)
        self.ts = np.arange(start_ms, end_ms + 1, HOUR, dtype=np.int64)
        self.e0 = float(initial)
        self.inside = np.zeros(self.ts.shape[0])
        self.tail = np.zeros(self.ts.shape[0] + 1)  # PnL kept after a segment ends (difference array)

    def add(self, ts: np.ndarray, equity: np.ndarray, initial: float) -> None:
        if ts.shape[0] == 0:
            return
        i0, i1 = (int(t - self.start) // HOUR for t in (ts[0], ts[-1]))
        s = pd.Series(np.asarray(equity) - initial, index=ts).reindex(self.ts[i0 : i1 + 1]).ffill().fillna(0.0)
        self.inside[i0 : i1 + 1] += s.to_numpy()
        self.tail[i1 + 1] += float(equity[-1]) - initial

    def equity(self) -> np.ndarray:
        return self.e0 + self.inside + np.cumsum(self.tail)[:-1]

    def value_at(self, t_ms: int) -> float:
        """Equity at time t (the close of the bar that opens at t - 1h)."""
        i = (int(t_ms) - HOUR - self.start) // HOUR
        return self.e0 if i < 0 else float(self.equity()[min(i, self.ts.shape[0] - 1)])


def run_simulation(cfg: AppConfig, folds: list[Fold], data_dir: str | Path, out_dir: str | Path,
                   slots: int = 100, workers: int = 3, variants: list[str] | None = None) -> dict[str, Portfolio]:
    out_dir = Path(out_dir)
    variants = variants or list(VARIANTS)
    global_end = folds[-1].end_ms - HOUR
    e0 = float(cfg.research.initial_capital)
    ports = {v: Portfolio(folds[0].start_ms, global_end, e0) for v in variants}
    state: dict[str, dict[tuple[str, str], dict]] = {v: {} for v in variants}
    trades: list[dict] = []
    seg_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                             initargs=(cfg, str(data_dir), str(data_dir), global_end, None)) as pool:
        for k, fold in enumerate(folds):
            jobs: list[Segment] = []
            for v in variants:
                equity_now = ports[v].value_at(fold.start_ms)
                for r in load_chosen(out_dir, fold.month, v).itertuples():
                    key = (r.a, r.b)
                    st = state[v].get(key)
                    continuing = st is not None and st["last_month"] == k - 1
                    start = fold.start_ms if st is None else max(fold.start_ms, st["next_free_ms"])
                    if start + HOUR >= fold.end_ms:  # still in last month's position: no entry possible
                        if continuing:
                            st["last_month"] = k
                        continue
                    init = st["final"] if continuing else equity_now / slots
                    jobs.append(Segment(
                        variant=v, month=fold.month, a=r.a, b=r.b, lookback=int(r.lookback),
                        z_entry=float(r.z_entry), z_exit=float(r.z_exit),
                        kalman_delta=None if pd.isna(r.kalman_delta) else float(r.kalman_delta),
                        start_ms=start, entries_until_ms=fold.end_ms,
                        end_ms=min(fold.end_ms - HOUR + 31 * DAY, global_end), initial_equity=init,
                        deactivation_base=st["base"] if continuing else init,
                        cooldown_until=st["cooldown_until"] if st else -1, new_bucket=not continuing))
            t0 = time.time()
            for seg, res in zip(jobs, pool.map(simulate_segment, jobs, chunksize=1)):
                ports[seg.variant].add(res["ts"], res["equity"], seg.initial_equity)
                state[seg.variant][(seg.a, seg.b)] = {
                    "last_month": k, "next_free_ms": res["next_free_ms"], "final": res["final"],
                    "base": seg.deactivation_base, "cooldown_until": res["cooldown_until"]}
                trades += res["trades"]
                seg_rows.append({**{f: getattr(seg, f) for f in ("variant", "month", "a", "b", "lookback", "z_entry",
                                                                   "z_exit", "kalman_delta", "initial_equity",
                                                                   "new_bucket")},
                                 "start": pd.Timestamp(seg.start_ms, unit="ms"), "final": res["final"],
                                 "pnl": res["final"] - seg.initial_equity, "trades": len(res["trades"]),
                                 "fees": res["fees"], "funding": res["funding"],
                                 "free_from": pd.Timestamp(res["next_free_ms"], unit="ms"),
                                 "open_at_end": res["open_at_end"]})
            log.info("simulated %s: %d segments in %.0f s | %s", fold.month, len(jobs), time.time() - t0,
                     " | ".join(f"{v} {ports[v].value_at(fold.end_ms):,.0f}" for v in variants))
    for v in variants:
        pd.DataFrame({"ts": ports[v].ts, "equity": ports[v].equity()}).to_csv(out_dir / f"equity_{v}.csv", index=False)
    pd.DataFrame(trades).to_csv(out_dir / "trades.csv", index=False)
    pd.DataFrame(seg_rows).to_csv(out_dir / "segments.csv", index=False)
    return ports


# ------------------------------------------------------------------------ statistics
def max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float(np.min(equity / peak - 1.0))


def curve_stats(ts: np.ndarray, equity: np.ndarray) -> dict:
    eq = np.asarray(equity, dtype=np.float64)
    rets = np.diff(eq) / eq[:-1]
    sd = rets.std(ddof=1)
    years = (ts[-1] - ts[0] + HOUR) / (HOUR * HOURS_PER_YEAR)
    day = pd.Series(eq, index=pd.to_datetime(ts + HOUR, unit="ms")).resample("1D").last().dropna()
    daily = day.pct_change().dropna()
    return {
        "Sortino": sortino(rets), "MaxDD": max_drawdown(eq),
        "Sharpe": float(rets.mean() / sd * math.sqrt(HOURS_PER_YEAR)) if sd > 0 else math.nan,
        "AnnRet": float((eq[-1] / eq[0]) ** (1 / years) - 1) if eq[-1] > 0 else math.nan,
        "TotalRet": float(eq[-1] / eq[0] - 1), "WorstDay": float(daily.min()), "BestDay": float(daily.max()),
    }


def monthly_returns(ts: np.ndarray, equity: np.ndarray, folds: list[Fold], e0: float) -> pd.Series:
    """Each month's return: equity at its end over equity at its start (E0 before the first month)."""
    s = pd.Series(np.asarray(equity, dtype=np.float64), index=ts)

    def at(t: int) -> float:
        i = int(np.searchsorted(ts, t - HOUR, side="right")) - 1
        return e0 if i < 0 else float(s.iloc[i])

    return pd.Series({f.month: at(f.end_ms) / at(f.start_ms) - 1.0 for f in folds})


def fold_tests(m: np.ndarray, n_resamples: int = 10_000, seed: int = 0) -> dict:
    """The paper's tests on the monthly returns: t-test, bootstrap CI of the mean, sign-flip permutation,
    binomial test of the share of positive months (all two-sided, no multiple-testing correction)."""
    from scipy import stats

    m = np.asarray(m, dtype=np.float64)
    rng = np.random.default_rng(seed)
    n = m.shape[0]
    boot = rng.choice(m, size=(n_resamples, n), replace=True).mean(axis=1)
    flips = rng.choice([-1.0, 1.0], size=(n_resamples, n)) * np.abs(m)
    k = int((m > 0).sum())
    return {
        "months": n, "mean": float(m.mean()), "median": float(np.median(m)), "positive": k,
        "positive_share": k / n if n else math.nan,
        "t": float(stats.ttest_1samp(m, 0.0).statistic), "p_t": float(stats.ttest_1samp(m, 0.0).pvalue),
        "ci95_mean": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
        "p_permutation": float((np.abs(flips.mean(axis=1)) >= abs(m.mean()) - 1e-15).mean()),
        "p_binomial": float(stats.binomtest(k, n, 0.5).pvalue) if n else math.nan,
    }


def compare(new: dict, old: dict, folds: list[Fold], n_resamples: int = 2_000, seed: int = 1) -> dict:
    """Candidate vs baseline on the same months: paired monthly differences, and a month-block bootstrap
    of the two decision metrics (Sortino, max drawdown) on resampled months."""
    from scipy import stats

    d = (new["monthly"] - old["monthly"]).to_numpy()
    rng = np.random.default_rng(seed)
    boot_d = rng.choice(d, size=(10_000, d.shape[0]), replace=True).mean(axis=1)
    month_of = pd.to_datetime(new["ts"], unit="ms").to_period("M").astype(str).to_numpy()
    r_new, r_old = (np.diff(x["equity"]) / x["equity"][:-1] for x in (new, old))
    months = [f.month for f in folds]
    idx = {m: np.flatnonzero(month_of[1:] == m) for m in months}
    better_s = better_dd = both = 0
    for _ in range(n_resamples):
        pick = rng.choice(months, size=len(months), replace=True)
        sel = np.concatenate([idx[m] for m in pick])
        s_new, s_old = sortino(r_new[sel]), sortino(r_old[sel])
        dd_new, dd_old = max_drawdown(np.cumprod(1 + r_new[sel])), max_drawdown(np.cumprod(1 + r_old[sel]))
        better_s += s_new > s_old
        better_dd += dd_new > dd_old
        both += (s_new > s_old) and (dd_new > dd_old)
    return {
        "months_new_better": int((d > 0).sum()), "months": int(d.shape[0]), "mean_diff": float(d.mean()),
        "p_t_paired": float(stats.ttest_1samp(d, 0.0).pvalue),
        "ci95_mean_diff": [float(np.quantile(boot_d, 0.025)), float(np.quantile(boot_d, 0.975))],
        "bootstrap_share_sortino_better": better_s / n_resamples,
        "bootstrap_share_drawdown_better": better_dd / n_resamples,
        "bootstrap_share_both_better": both / n_resamples,
    }


FULL_BOOK_NOTE = "information only: the decision is taken on the whole walk-forward"


def full_book_folds(folds: list[Fold], out_dir: str | Path, variant: str) -> list[Fold]:
    """The months from the first one with the full list of pairs (the early folds hold fewer, since few
    coins had the history the funnel asks for; later months may dip again and stay in). Read from the
    selection alone."""
    counts = [len(load_chosen(out_dir, f.month, variant)) for f in folds]
    if not counts or max(counts) == 0:
        return []
    return folds[counts.index(max(counts)):]


def report(cfg: AppConfig, folds: list[Fold], out_dir: str | Path, variants: list[str] | None = None) -> dict:
    out_dir = Path(out_dir)
    variants = variants or list(VARIANTS)
    e0 = float(cfg.research.initial_capital)
    try:
        trades = pd.read_csv(out_dir / "trades.csv")
    except pd.errors.EmptyDataError:
        trades = pd.DataFrame()
    res: dict = {"folds": [folds[0].month, folds[-1].month], "variants": {}}
    for v in variants:
        eq = pd.read_csv(out_dir / f"equity_{v}.csv")
        ts, e = eq["ts"].to_numpy(dtype=np.int64), eq["equity"].to_numpy(dtype=np.float64)
        monthly = monthly_returns(ts, e, folds, e0)
        tv = trades[trades["variant"] == v] if len(trades) else trades
        closed = tv[tv["pnl"].notna()] if len(tv) else tv
        years = {}
        for y, fy in pd.Series(folds).groupby([f.month[:4] for f in folds]):
            fy = list(fy)
            sl = (ts >= fy[0].start_ms - HOUR) & (ts < fy[-1].end_ms)
            years[y] = {**curve_stats(ts[sl], e[sl]), "months": len(fy)}
        res["variants"][v] = {
            **curve_stats(ts, e), "trades": int(len(closed)),
            "win_rate": float((closed["pnl"] > 0).mean()) if len(closed) else math.nan,
            "fold_tests": fold_tests(monthly.to_numpy()), "years": years,
            "monthly": {k: float(x) for k, x in monthly.items()},
        }
        res["variants"][v]["_series"] = {"ts": ts, "equity": e, "monthly": monthly}
    if BASELINE in res["variants"] and CANDIDATE in res["variants"]:
        new, old = (res["variants"][x] for x in (CANDIDATE, BASELINE))
        res["comparison"] = compare(new["_series"], old["_series"], folds)
        res["rule"] = ("sortino_dd is proposed only if, over the whole walk-forward, its Sortino is higher AND its "
                       "max drawdown is smaller (shallower) than sharpe's; otherwise sharpe stays")
        res["decision"] = bool(new["Sortino"] > old["Sortino"] and new["MaxDD"] > old["MaxDD"])
    full = full_book_folds(folds, out_dir, variants[0])
    if full and len(full) < len(folds):
        res["full_book"] = {"from": full[0].month, "months": len(full), "note": FULL_BOOK_NOTE, "variants": {}}
        for v, r in res["variants"].items():
            ts, e, monthly = (r["_series"][k] for k in ("ts", "equity", "monthly"))
            sl = ts >= full[0].start_ms - HOUR
            res["full_book"]["variants"][v] = {**curve_stats(ts[sl], e[sl]),
                                               "fold_tests": fold_tests(monthly[[f.month for f in full]].to_numpy())}
    for v in res["variants"].values():
        v.pop("_series")
    (out_dir / "report.json").write_text(json.dumps(res, indent=2, default=float))
    (out_dir / "summary.txt").write_text(summary_text(res))
    return res


def summary_text(res: dict) -> str:
    lines = [f"Walk-forward of bot B's recipe, monthly folds {res['folds'][0]} .. {res['folds'][1]}", ""]
    head = f"{'':12s} {'Sortino':>8s} {'MaxDD':>8s} {'Sharpe':>7s} {'AnnRet':>8s} {'Total':>8s} " \
           f"{'WorstDay':>9s} {'Trades':>7s} {'Win%':>6s} {'+months':>8s} {'p(t)':>6s}"
    lines.append(head)
    for v, r in res["variants"].items():
        ft = r["fold_tests"]
        lines.append(f"{v:12s} {r['Sortino']:8.2f} {100 * r['MaxDD']:7.1f}% {r['Sharpe']:7.2f} "
                     f"{100 * r['AnnRet']:7.1f}% {100 * r['TotalRet']:7.1f}% {100 * r['WorstDay']:8.1f}% "
                     f"{r['trades']:7d} {100 * r['win_rate']:5.1f}% {ft['positive']:>3d}/{ft['months']:<4d} "
                     f"{ft['p_t']:6.3f}")
    for v, r in res["variants"].items():
        ft = r["fold_tests"]
        lines += ["", f"{v}: mean month {100 * ft['mean']:+.2f}% (95% CI {100 * ft['ci95_mean'][0]:+.2f}% .. "
                      f"{100 * ft['ci95_mean'][1]:+.2f}%), median {100 * ft['median']:+.2f}%, "
                      f"p permutation {ft['p_permutation']:.3f}, p binomial {ft['p_binomial']:.3f}"]
        for y, s in r["years"].items():
            lines.append(f"  {y} ({s['months']:2d} m): Sortino {s['Sortino']:6.2f} | MaxDD {100 * s['MaxDD']:6.1f}% | "
                         f"return {100 * s['TotalRet']:+7.1f}% | worst day {100 * s['WorstDay']:+6.1f}%")
    if "comparison" in res:
        c = res["comparison"]
        lines += ["", f"{CANDIDATE} vs {BASELINE}: better in {c['months_new_better']}/{c['months']} months, "
                      f"mean monthly difference {100 * c['mean_diff']:+.2f}% (95% CI {100 * c['ci95_mean_diff'][0]:+.2f}% .. "
                      f"{100 * c['ci95_mean_diff'][1]:+.2f}%, paired p {c['p_t_paired']:.3f})",
                  f"month-block bootstrap: Sortino better in {100 * c['bootstrap_share_sortino_better']:.0f}%, "
                  f"drawdown better in {100 * c['bootstrap_share_drawdown_better']:.0f}%, both in "
                  f"{100 * c['bootstrap_share_both_better']:.0f}% of resamples",
                  f"rule: {res['rule']}", f"decision: {'PROPOSE sortino_dd' if res['decision'] else 'KEEP sharpe'}"]
    if "full_book" in res:
        fb = res["full_book"]
        lines += ["", f"from the first month with the full list of pairs, {fb['from']} .. {res['folds'][1]} "
                      f"({fb['months']} months; {fb['note']})"]
        for v, r in fb["variants"].items():
            ft = r["fold_tests"]
            lines.append(f"  {v:12s} Sortino {r['Sortino']:6.2f} | MaxDD {100 * r['MaxDD']:6.1f}% | Sharpe "
                         f"{r['Sharpe']:5.2f} | return {100 * r['TotalRet']:+7.1f}% | +months {ft['positive']}/"
                         f"{ft['months']} | mean month {100 * ft['mean']:+.2f}% (p {ft['p_t']:.3f})")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------- leakage check
def truncated_copy(src: str | Path, dst: str | Path, cut_ms: int) -> None:
    """A copy of the hourly data with every bar after ``cut_ms`` deleted (funding up to the cut bar's close)."""
    src, dst = Path(src), Path(dst)
    if dst.exists():
        shutil.rmtree(dst)
    s_kl, s_fu = DataStore(src, "1h"), DataStore(src, "1h")
    d = DataStore(dst, "1h")
    for sym in s_kl.symbols():
        kl = s_kl.load_klines(sym)
        kl = kl[kl["ts"] <= cut_ms]
        if kl.empty:
            continue
        d.kline_path(sym).parent.mkdir(parents=True, exist_ok=True)
        kl.reset_index(drop=True).to_pickle(d.kline_path(sym))
        fu = s_fu.load_funding(sym)
        if len(fu):
            d.funding_path(sym).parent.mkdir(parents=True, exist_ok=True)
            fu[fu["ts"] <= cut_ms + HOUR].reset_index(drop=True).to_pickle(d.funding_path(sym))
    (dst / "meta").mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "meta" / "chains.csv", dst / "meta" / "chains.csv")


def poison_check(cfg: AppConfig, fold: Fold, data_dir: str | Path, out_dir: str | Path, tmp_dir: str | Path) -> dict:
    """Select one month again on a copy of the data with the future deleted: the pairs and every
    parameter must come out identical, or the selection is seeing data it should not."""
    truncated_copy(data_dir, tmp_dir, fold.start_ms - HOUR)
    chains = load_chain_map(Path(tmp_dir) / "meta" / "chains.csv") or None
    res = select_fold(fold_config(cfg, fold.start_ms, fold.end_ms, tmp_dir), chains)
    out = {"month": fold.month}
    ref_f = pd.read_csv(_fold_dir(Path(out_dir), fold.month) / "funnel.csv")
    out["funnel_identical"] = bool(ref_f.equals(_csv_roundtrip(res["funnel"])))
    for v in VARIANTS:
        ref = load_chosen(out_dir, fold.month, v)
        got = _csv_roundtrip(res["chosen"][v])
        out[f"{v}_identical"] = bool(ref.equals(got))  # pairs, parameters and every training metric
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return out


# ---------------------------------------------------------------------- reproduction
def reproduce(cfg: AppConfig, data_dir: str | Path = "data", ref_dir: str | Path = "runs/experiments_ab",
              ref_results: str | Path = "runs/intrabar/results.csv", out_dir: str | Path = "runs/walkforward_b/reproduce",
              workers: int = 3) -> pd.DataFrame:
    """Today's two-window B test through this module's code: the same pairs and parameters as
    runs/experiments_ab (K1) and the same portfolio as runs/intrabar (rule "hourly")."""
    from cryptopairs.research.experiments import STAGES
    from cryptopairs.research.intrabar import portfolio

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chains = load_chain_map(Path(data_dir) / "meta" / "chains.csv") or None
    ref_m = pd.read_csv(ref_results)
    rows = []
    cols = ["a", "b", "lookback", "z_entry", "z_exit", "kalman_delta"]
    for stage, dates in STAGES.items():
        scfg = copy.deepcopy(cfg)
        scfg.data.dir = str(data_dir)
        for k, v in dates.items():
            setattr(scfg.research, k, v)
        sel = select_fold(scfg, chains, {BASELINE: VARIANTS[BASELINE]})
        chosen = sel["chosen"][BASELINE]
        chosen.to_csv(out_dir / f"chosen_{stage}.csv", index=False)
        ref = pd.read_csv(Path(ref_dir) / stage / "k1_kalman_filter" / "pairs_results.csv")
        a = chosen[cols].sort_values(["a", "b"]).reset_index(drop=True)
        b = ref[cols].sort_values(["a", "b"]).reset_index(drop=True)
        same_pairs = a.shape == b.shape and a[["a", "b"]].equals(b[["a", "b"]])
        same_params = bool(same_pairs and np.allclose(a[cols[2:]].astype(float).fillna(-1),
                                                      b[cols[2:]].astype(float).fillna(-1)))
        rc = scfg.research
        capital = rc.initial_capital / len(chosen)
        jobs = [Segment(BASELINE, stage, r.a, r.b, int(r.lookback), float(r.z_entry), float(r.z_exit),
                        None if pd.isna(r.kalman_delta) else float(r.kalman_delta), rc.test_start_ms, None,
                        rc.test_end_ms, capital, capital, -1, True) for r in chosen.itertuples()]
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(scfg, str(data_dir), None, rc.test_end_ms, rc.data_start_ms)) as pool:
            per_pair = {f"{s.a}-{s.b}": r for s, r in zip(jobs, pool.map(simulate_segment, jobs, chunksize=1))}
        m, _, _ = portfolio(stage, per_pair, capital)
        want = ref_m[(ref_m["stage"] == stage) & (ref_m["rule"] == "hourly")].iloc[0]
        rows.append({"stage": stage, "same_pairs": same_pairs, "same_params": same_params, "pairs": len(chosen),
                     "Sharpe": m["Sharpe"], "ref_Sharpe": want["Sharpe"], "TotalRet": m["TotalRet"],
                     "ref_TotalRet": want["TotalRet"], "MaxDD": m["MaxDD"], "ref_MaxDD": want["MaxDD"],
                     "Trades": m["Trades"], "ref_Trades": want["Trades"]})
        log.info("reproduce %s: pairs %s, params %s | Sharpe %.4f (ref %.4f) | return %.4f (ref %.4f)", stage,
                 same_pairs, same_params, m["Sharpe"], want["Sharpe"], m["TotalRet"], want["TotalRet"])
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "reproduce.csv", index=False)
    return df


# ---------------------------------------------------------------------------- data
def archive_months(symbol: str, interval: str = "1h") -> list[str]:
    """Months with a monthly kline file in the Binance archive (from the bucket listing)."""
    prefix = f"{UM}/monthly/klines/{symbol}/{interval}/"
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    months, marker = [], ""
    pat = re.compile(rf"{re.escape(symbol)}-{interval}-(\d{{4}}-\d{{2}})\.zip$")
    while True:
        blob = _get(f"{BUCKET}?prefix={prefix}&marker={marker}")
        if blob is None:
            break
        root = ET.fromstring(blob)
        keys = [k.text for k in root.findall("s3:Contents/s3:Key", ns)]
        months += [m.group(1) for k in keys if (m := pat.search(k or ""))]
        if root.findtext("s3:IsTruncated", default="false", namespaces=ns) != "true" or not keys:
            break
        marker = keys[-1]
    return sorted(set(months))


def prepare_data(src: str | Path = "data", dst: str | Path = "data_wf", first_month: str = "2020-01",
                 last_month: str = "2026-09", listed_by: str = "2023-12", threads: int = 8) -> pd.DataFrame:
    """The walk-forward's own data folder (data/ is not touched): hourly klines and funding of every
    USDT perpetual the archive ever had (delisted ones included) that was listed by ``listed_by``
    (later listings cannot pass B's 1000-day history filter by the last month), from ``first_month``."""
    src, dst = Path(src), Path(dst)
    symbols = list_archive_symbols()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        months = dict(zip(symbols, pool.map(archive_months, symbols)))
    keep = {s: [m for m in ms if first_month <= m <= last_month] for s, ms in months.items()
            if ms and ms[0] <= listed_by and ms[-1] >= first_month}
    log.info("%d archive symbols, %d listed by %s", len(symbols), len(keep), listed_by)
    store = DataStore(dst, "1h")
    for sym in keep:  # seed with what data/ already has, so only the missing months are fetched
        for s_path, d_path in ((DataStore(src, "1h").kline_path(sym), store.kline_path(sym)),
                               (DataStore(src, "1h").funding_path(sym), store.funding_path(sym))):
            if s_path.exists() and not d_path.exists():
                d_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(s_path, d_path)
    with ThreadPoolExecutor(max_workers=threads) as pool:
        status = dict(zip(keep, pool.map(lambda s: store.update_symbol(s, keep[s], None, True), keep)))
    if (src / "meta" / "chains.csv").exists() and not (dst / "meta" / "chains.csv").exists():
        (dst / "meta").mkdir(parents=True, exist_ok=True)
        shutil.copy(src / "meta" / "chains.csv", dst / "meta" / "chains.csv")
    rows = [{"symbol": s, "first_month": months[s][0], "last_month": months[s][-1], "status": status[s]}
            for s in keep]
    df = pd.DataFrame(rows)
    (dst / "meta").mkdir(parents=True, exist_ok=True)
    df.to_csv(dst / "meta" / "universe.csv", index=False)
    return df


def prefetch_minutes(folds: list[Fold], data_dir: str | Path, out_dir: str | Path, threads: int = 8,
                     ahead_months: int = 1, legacy_dir: str | Path = "data") -> int:
    """Download the minute bars the simulation will read: each chosen coin, its month and the next
    (a longer position fetches the rest when it is simulated)."""
    ms = MinuteStore(data_dir)
    legacy = DataStore(Path(legacy_dir), "1m")
    if legacy.symbols():
        log.info("imported %d complete months from data/klines/1m", ms.import_legacy(legacy))
    need: dict[str, set[str]] = {}
    months = [f.month for f in folds]
    for k, f in enumerate(folds):
        for v in VARIANTS:
            for r in load_chosen(out_dir, f.month, v).itertuples():
                for sym in (r.a, r.b):
                    need.setdefault(sym, set()).update(months[k : k + 1 + ahead_months])
    with ThreadPoolExecutor(max_workers=threads) as pool:
        n = sum(pool.map(lambda s: ms.ensure(s, sorted(need[s])), need))
    log.info("minute bars: %d coins, %d months downloaded", len(need), n)
    return n


def config_record(cfg: AppConfig) -> dict:
    """The recipe's settings as used (written next to the results)."""
    return {"profile": cfg.profile, "strategy": asdict(cfg.strategy), "costs": asdict(cfg.costs),
            "research": asdict(cfg.research), "risk": asdict(cfg.risk)}

"""Per-pair grid search (paper Section 6.2) and the out-of-sample portfolio test (6.4)."""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from cryptopairs.config import AppConfig
from cryptopairs.core.signals import estimate_fits
from cryptopairs.core.strategy import PairParams
from cryptopairs.research.backtest import BacktestResult, performance, run_backtest
from cryptopairs.research.data import DataStore, PairData, load_pair

log = logging.getLogger(__name__)


@dataclass
class Calibration:
    a: str
    b: str
    table: pd.DataFrame  # one row per grid point
    best: dict | None  # {"lookback", "z_entry", "z_exit", **train metrics}


def calibrate_pair(data: PairData, cfg: AppConfig, start_ms: int, end_ms: int, capital: float) -> Calibration:
    rc = cfg.research
    rows = []
    sc = cfg.strategy
    if sc.estimator == "kalman":  # the delta grid replaces the lookback grid
        estimators = [(sc.kalman_warmup, d) for d in rc.grid_kalman_delta]
    else:
        estimators = [(L, None) for L in rc.grid_lookback]
    for L, delta in estimators:
        if len(data) < L + 24:
            continue
        fits = estimate_fits(data.log_a, data.log_b, L, delta, sc.kalman_outlier_z, sc.kalman_max_skips)
        for ze, zx in product(rc.grid_z_entry, rc.grid_z_exit):
            if not zx < ze:
                continue
            params = PairParams(L, ze, zx, delta)
            res = run_backtest(data, params, cfg.strategy, cfg.costs, start_ms, end_ms, capital,
                               fits=fits, record_trades=True)
            m = res.metrics()
            rows.append({"lookback": L, "z_entry": ze, "z_exit": zx, "kalman_delta": delta, **m})
    table = pd.DataFrame(rows)
    return Calibration(data.a, data.b, table, select_best(table, rc.selection, rc.min_train_trades))


def params_from_row(row: dict) -> PairParams:
    delta = row.get("kalman_delta")
    delta = None if delta is None or pd.isna(delta) else float(delta)
    return PairParams(int(row["lookback"]), float(row["z_entry"]), float(row["z_exit"]), delta)


def select_best(table: pd.DataFrame, selection: str = "argmax", min_trades: int = 0) -> dict | None:
    """Pick the calibrated parameters from a pair's grid-search table.

    argmax:   highest training Sharpe (paper Sec 6.2).
    smoothed: highest median Sharpe over the grid point and its direct neighbours
              (one step in lookback, z_entry or z_exit). A configuration that only
              works in isolation is likely a lucky fit; the median ignores a single
              spike, so neither the spike nor its neighbours win. Untraded points count as 0.
    """
    if table.empty:
        return None
    ok = table[(table["Trades"] >= min_trades) & table["Sharpe"].notna()]
    if ok.empty:
        return None
    if selection == "argmax":
        return ok.sort_values("Sharpe", ascending=False).iloc[0].to_dict()
    if selection != "smoothed":
        raise ValueError(f"unknown selection {selection!r}")
    first = "kalman_delta" if "kalman_delta" in table and table["kalman_delta"].notna().any() else "lookback"
    cols = (first, "z_entry", "z_exit")
    axes = [sorted(table[c].unique()) for c in cols]
    sharpe = {tuple(getattr(r, c) for c in cols): (0.0 if pd.isna(r.Sharpe) else r.Sharpe)
              for r in table.itertuples()}

    def score(point: tuple) -> float:
        vals = [sharpe[point]]
        for d, axis in enumerate(axes):
            j = axis.index(point[d])
            for k in (j - 1, j + 1):
                if 0 <= k < len(axis):
                    nb = list(point)
                    nb[d] = axis[k]
                    vals.append(sharpe.get(tuple(nb), 0.0))
        return float(np.median(vals))

    ok = ok.assign(SmoothedSharpe=[score(tuple(getattr(r, c) for c in cols)) for r in ok.itertuples()])
    return ok.sort_values("SmoothedSharpe", ascending=False).iloc[0].to_dict()


def _calibrate_job(data_dir: str, a: str, b: str, cfg: AppConfig) -> Calibration:
    rc = cfg.research
    store = DataStore(Path(data_dir), cfg.data.interval)
    data = load_pair(store, a, b, rc.data_start_ms, rc.train_end_ms)
    return calibrate_pair(data, cfg, rc.calibration_start_ms, rc.train_end_ms, rc.initial_capital)


def calibrate_all(pairs: list[tuple[str, str]], cfg: AppConfig) -> list[Calibration]:
    """Grid search every pair, in parallel worker processes."""
    out: list[Calibration] = []
    workers = max(1, int(cfg.research.workers))
    if workers == 1:
        for i, (a, b) in enumerate(pairs, 1):
            out.append(_calibrate_job(cfg.data.dir, a, b, cfg))
            log.info("calibrated %d/%d (%s-%s)", i, len(pairs), a, b)
        return out
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_calibrate_job, cfg.data.dir, a, b, cfg): (a, b) for a, b in pairs}
        for i, fut in enumerate(as_completed(futs), 1):
            a, b = futs[fut]
            try:
                out.append(fut.result())
            except Exception as exc:
                log.warning("calibration %s-%s failed: %s", a, b, exc)
            log.info("calibrated %d/%d (%s-%s)", i, len(pairs), a, b)
    return out


@dataclass
class PortfolioResult:
    equity: pd.DataFrame  # ts, equity, open_positions
    per_pair: pd.DataFrame
    metrics: dict
    runs: dict[str, BacktestResult]


def evaluate_portfolio(calibrations: list[Calibration], cfg: AppConfig) -> PortfolioResult:
    """Run every calibrated pair on the test window with equal capital (paper 6.4)."""
    rc = cfg.research
    chosen = [c for c in calibrations if c.best is not None]
    if not chosen:
        raise RuntimeError("no calibrated pairs to test")
    capital = rc.initial_capital / len(chosen)
    store = DataStore(Path(cfg.data.dir), cfg.data.interval)
    runs: dict[str, BacktestResult] = {}
    rows = []
    for c in chosen:
        params = params_from_row(c.best)
        data = load_pair(store, c.a, c.b, rc.data_start_ms, rc.test_end_ms)
        res = run_backtest(data, params, cfg.strategy, cfg.costs, rc.test_start_ms, rc.test_end_ms, capital)
        key = f"{c.a}-{c.b}"
        runs[key] = res
        m = res.metrics()
        rows.append({"pair": key, "a": c.a, "b": c.b, "lookback": params.lookback,
                     "z_entry": params.z_entry, "z_exit": params.z_exit, "kalman_delta": params.kalman_delta,
                     "train_sharpe": c.best.get("Sharpe"), **{f"test_{k}": v for k, v in m.items()}})

    grid = np.arange(rc.test_start_ms, rc.test_end_ms + 1, 3_600_000, dtype=np.int64)
    total = np.zeros(grid.shape[0])
    open_pos = np.zeros(grid.shape[0])
    for key, res in runs.items():
        if res.ts.size == 0:
            total += capital
            continue
        s = pd.Series(res.equity, index=res.ts).reindex(grid).ffill().fillna(capital)
        total += s.to_numpy()
        pos = np.zeros(grid.shape[0])
        for t in res.trades:
            end = t.get("exit_ts", grid[-1] + 1)
            pos[(grid >= t["entry_ts"]) & (grid < end)] = 1
        open_pos += pos
    eq = pd.DataFrame({"ts": grid, "equity": total, "open_positions": open_pos})
    all_trades = [t for r in runs.values() for t in r.trades]
    metrics = performance(grid, total, all_trades,
                          fees=sum(r.fees for r in runs.values()),
                          funding=sum(r.funding for r in runs.values()))
    metrics["Pairs"] = len(runs)
    metrics["Deactivated"] = sum(1 for r in runs.values() if r.deactivated)
    per_pair = pd.DataFrame(rows).sort_values("test_Sharpe", ascending=False, na_position="last")
    return PortfolioResult(equity=eq, per_pair=per_pair.reset_index(drop=True), metrics=metrics, runs=runs)


def pairs_yaml(per_pair: pd.DataFrame, funnel: pd.DataFrame | None, top_n: int, rank_by: str) -> dict:
    """Runtime pair list. rank_by='ic' avoids picking pairs on test results (no snooping)."""
    df = per_pair.copy()
    if rank_by == "ic" and funnel is not None and not funnel.empty:
        ic = funnel.assign(pair=funnel["a"] + "-" + funnel["b"])[["pair", "best_ic"]]
        df = df.merge(ic, on="pair", how="left").sort_values("best_ic")
    elif rank_by == "test_sharpe":
        df = df.sort_values("test_Sharpe", ascending=False)
    df = df.head(top_n)
    return {
        "pairs": [
            {"id": f"{r.a}_{r.b}", "a": r.a, "b": r.b, "lookback": int(r.lookback),
             "z_entry": float(r.z_entry), "z_exit": float(r.z_exit),
             **({"kalman_delta": float(r.kalman_delta)}
                if "kalman_delta" in df and pd.notna(getattr(r, "kalman_delta", None)) else {})}
            for r in df.itertuples()
        ]
    }

"""Pair-selection funnel (paper Section 4), computed only on data before the test period.

Filter 1: enough history (listed >= min_history_days before the test start) and
          still trading at the end of training.
Filter 2: same blockchain (CoinGecko metadata).
Filter 3: Corr(log P) > 0.9 and Corr(returns) > 0.5 on hourly closes.
Filter 4: information coefficient (IC) between Z_T and the forward spread
          return R_{T,T2}; Best IC is the most negative over all (T1, T2).
Both regression directions (A on B, B on A) are scored and only the better one
is kept, so a pair is never traded twice.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptopairs.config import HOUR_MS, ResearchConfig
from cryptopairs.core.signals import rolling_ols
from cryptopairs.research.data import DataStore

log = logging.getLogger(__name__)


@dataclass
class FunnelResult:
    table: pd.DataFrame
    counts: dict[str, int] = field(default_factory=dict)


def select_pairs(table: pd.DataFrame, top_n: int, max_per_symbol: int | None = None) -> pd.DataFrame:
    """Take the best-ranked pairs, skipping any that would put a coin in too many pairs."""
    if max_per_symbol is None:
        return table.head(top_n).reset_index(drop=True)
    used: dict[str, int] = {}
    keep = []
    for idx, r in table.iterrows():
        if used.get(r["a"], 0) >= max_per_symbol or used.get(r["b"], 0) >= max_per_symbol:
            continue
        keep.append(idx)
        used[r["a"]] = used.get(r["a"], 0) + 1
        used[r["b"]] = used.get(r["b"], 0) + 1
        if len(keep) == top_n:
            break
    return table.loc[keep].reset_index(drop=True)


def information_coefficient(
    log_a: np.ndarray,
    log_b: np.ndarray,
    lookback: int,
    horizons: list[int],
    eval_from: int = 0,
    eval_to: int | None = None,
) -> dict[int, float]:
    """IC of Z_T vs R_{T,T2} for each horizon, over T in [eval_from, eval_to - T2].

    Z_T uses the rolling fit ending at T. R_{T,T2} uses beta_T, so the forward
    window never feeds back into the regression.
    """
    n = log_a.shape[0]
    eval_to = n - 1 if eval_to is None else eval_to
    alpha, beta, sigma = rolling_ols(log_a, log_b, lookback)
    z = (log_a - alpha - beta * log_b) / sigma
    out: dict[int, float] = {}
    for h in horizons:
        lo, hi = max(eval_from, lookback - 1), eval_to - h
        if hi - lo < 30:
            out[h] = math.nan
            continue
        t = np.arange(lo, hi + 1)
        r = (log_a[t + h] - log_a[t]) - beta[t] * (log_b[t + h] - log_b[t])
        zt = z[t]
        ok = np.isfinite(zt) & np.isfinite(r)
        if ok.sum() < 30:
            out[h] = math.nan
            continue
        out[h] = float(np.corrcoef(zt[ok], r[ok])[0, 1])
    return out


def close_matrix(store: DataStore, symbols: list[str], start_ms: int, end_ms: int) -> pd.DataFrame:
    """Hourly close prices, index = bar open time (ms), one column per symbol."""
    cols = {}
    for s in symbols:
        df = store.load_klines(s)
        if df.empty:
            continue
        df = df[(df["ts"] >= start_ms) & (df["ts"] <= end_ms)]
        cols[s] = pd.Series(df["close"].to_numpy(), index=df["ts"].to_numpy())
    return pd.DataFrame(cols).sort_index()


def run_funnel(
    store: DataStore,
    cfg: ResearchConfig,
    chain_map: dict[str, set[str]] | None,
    symbols: list[str] | None = None,
) -> FunnelResult:
    symbols = symbols or store.symbols()
    counts: dict[str, int] = {"symbols": len(symbols)}
    test_start, train_end = cfg.test_start_ms, cfg.train_end_ms
    listed_by = test_start - cfg.min_history_days * 24 * HOUR_MS

    # Filter 1: history depth, measured as of the selection date.
    eligible = []
    for s in symbols:
        ts = store.load_klines(s)["ts"]
        if len(ts) and int(ts.iloc[0]) <= listed_by and int(ts.iloc[-1]) >= train_end - 24 * HOUR_MS:
            eligible.append(s)
    counts["filter1_symbols"] = len(eligible)
    log.info("Filter 1 (history): %d of %d symbols", len(eligible), len(symbols))

    # Filter 2: same blockchain.
    pairs = []
    for i, a in enumerate(eligible):
        for b in eligible[i + 1 :]:
            if cfg.same_chain and chain_map is not None:
                ca, cb = chain_map.get(a, set()), chain_map.get(b, set())
                if not ca or not cb:
                    if cfg.unknown_chain_policy == "exclude":
                        continue
                    common = ""
                else:
                    shared = ca & cb
                    if not shared:
                        continue
                    common = ";".join(sorted(shared))
            else:
                common = ""
            pairs.append((a, b, common))
    counts["filter1_pairs"] = len(eligible) * (len(eligible) - 1) // 2
    counts["filter2_pairs"] = len(pairs)
    log.info("Filter 2 (same chain): %d pairs", len(pairs))

    # Filter 3: correlations on the funnel window.
    warm = max(cfg.ic_lookbacks) * HOUR_MS
    closes = close_matrix(store, eligible, cfg.funnel_start_ms - warm, train_end)
    in_window = closes.index >= cfg.funnel_start_ms
    logp = np.log(closes[in_window])
    corr_p = logp.corr(min_periods=500)
    corr_r = logp.diff().corr(min_periods=500)
    passed = []
    for a, b, common in pairs:
        cp, cr = corr_p.at[a, b], corr_r.at[a, b]
        if cp > cfg.min_corr_logp and cr > cfg.min_corr_ret:
            passed.append((a, b, common, float(cp), float(cr)))
    counts["filter3_pairs"] = len(passed)
    log.info("Filter 3 (correlation): %d pairs", len(passed))

    # Filter 4: information coefficient, both regression directions.
    rows = []
    for n_done, (a, b, common, cp, cr) in enumerate(passed, 1):
        pair = closes[[a, b]].dropna()
        ts = pair.index.to_numpy()
        eval_from = int(np.searchsorted(ts, cfg.funnel_start_ms))
        la, lb = np.log(pair[a].to_numpy()), np.log(pair[b].to_numpy())
        best = None
        for (x, y, lx, ly) in ((a, b, la, lb), (b, a, lb, la)):
            for T1 in cfg.ic_lookbacks:
                ics = information_coefficient(lx, ly, T1, cfg.ic_horizons, eval_from=eval_from)
                for T2, ic in ics.items():
                    if math.isfinite(ic) and (best is None or ic < best[2]):
                        best = (x, y, ic, T1, T2)
        if best is not None:
            rows.append(
                {"a": best[0], "b": best[1], "chains": common, "corr_logp": cp, "corr_ret": cr,
                 "best_ic": best[2], "best_lookback": best[3], "best_horizon": best[4]}
            )
        if n_done % 50 == 0:
            log.info("Filter 4: scored %d/%d pairs", n_done, len(passed))

    table = pd.DataFrame(rows, columns=["a", "b", "chains", "corr_logp", "corr_ret",
                                        "best_ic", "best_lookback", "best_horizon"])
    table = table[table["best_ic"] < 0].sort_values("best_ic").reset_index(drop=True)
    counts["filter4_pairs"] = len(table)
    table = select_pairs(table, cfg.top_n, cfg.max_pairs_per_symbol)
    counts["selected"] = len(table)
    return FunnelResult(table=table, counts=counts)

"""Pre-registered comparison of strategy variants with a validation/test protocol.

Why: trying ideas on the test window and keeping what looks good turns the test
into training data. So every variant is scored twice, each time with a fresh funnel and
calibration that only see data before the scoring window:

    validation: select/calibrate on data before 2025-08-01, score 2025-08-01 .. 2026-02-28
    test:       select/calibrate on data before 2026-03-01, score 2026-03-01 .. 2026-09-30

The variant to deploy is the one with the best VALIDATION Sharpe. Test results are
reported for every variant but must not be used to choose; they only confirm.

Run: python -m cryptopairs experiments   (about 15-25 minutes on 3 cores)
"""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path

import pandas as pd
import yaml

from cryptopairs.config import AppConfig
from cryptopairs.research.calibrate import Calibration, calibrate_all, evaluate_portfolio, pairs_yaml, select_best
from cryptopairs.research.data import DataStore
from cryptopairs.research.funnel import run_funnel, select_pairs
from cryptopairs.research.metadata import load_chain_map

log = logging.getLogger(__name__)

STAGES: dict[str, dict] = {
    # Data starts 2023-06, so the validation funnel can only require ~790 days of history.
    "validation": {"test_start": "2025-08-01", "test_end": "2026-02-28 23:00", "min_history_days": 790},
    "test": {"test_start": "2026-03-01", "test_end": "2026-09-30 23:00"},
}

_V1 = {"time_stop_halflives": 3.0}
_V2 = {"z_stop_offset": 2.0, "stop_loss_pct": 0.0}
_V3 = {"selection": "smoothed", "max_pairs_per_symbol": 3}

VARIANTS: dict[str, dict] = {
    "V0 baseline": {},
    "V1 time stop 3 half-lives": {"strategy": _V1},
    "V2 z-stop (z_entry+2) instead of 20%": {"strategy": _V2},
    "V3 smoothed params + max 3 pairs/coin": {"research": _V3},
    "V4 = V1+V2+V3": {"strategy": {**_V1, **_V2}, "research": _V3},
    # Kalman-filter spread (dynamic alpha/beta/sigma); the delta grid replaces the lookback grid.
    "K1 Kalman filter": {"strategy": {"estimator": "kalman"}},
    "K2 Kalman + time stop": {"strategy": {"estimator": "kalman", **_V1}},
}

# Informational only (never used to choose): the 2025-10-10/11 liquidation cascade.
CRASH_DAYS = ("2025-10-10", "2025-10-11")


def _diagnostics(res) -> dict:
    """Detect the failure modes of a too-sensitive estimator: no trades, or very short ones."""
    trades = [t for r in res.runs.values() for t in r.trades]
    closed = [t for t in trades if t.get("exit_ts") is not None]
    hours = [(t["exit_ts"] - t["entry_ts"]) / 3_600_000 for t in closed]
    eq = res.equity.set_index(pd.to_datetime(res.equity["ts"], unit="ms"))["equity"]
    rets = eq.pct_change().fillna(0.0)
    rets[rets.index.strftime("%Y-%m-%d").isin(CRASH_DAYS)] = 0.0
    return {
        "MedianHoldH": float(pd.Series(hours).median()) if hours else float("nan"),
        "SignalExit%": 100.0 * sum(t["reason"] == "signal" for t in closed) / len(closed) if closed else float("nan"),
        "MeanAbsZEntry": float(pd.Series([abs(t["z_entry"]) for t in trades]).mean()) if trades else float("nan"),
        "RetExCrash": float((1 + rets).prod() - 1),
    }


def _apply(cfg: AppConfig, overrides: dict) -> AppConfig:
    out = copy.deepcopy(cfg)
    for section, values in overrides.items():
        target = getattr(out, section)
        for k, v in values.items():
            if not hasattr(target, k):
                raise AttributeError(f"{section}.{k}")
            setattr(target, k, v)
    out.validate()
    return out


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]


def run_experiments(base: AppConfig, out_dir: str | Path = "runs/experiments",
                    only: list[str] | None = None) -> pd.DataFrame:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = DataStore(Path(base.data.dir), base.data.interval)
    chains = load_chain_map(Path(base.data.dir) / "meta" / "chains.csv") or None
    rows = []
    for stage, dates in STAGES.items():
        scfg = _apply(base, {"research": dates})
        log.info("=== %s stage: scoring %s .. %s", stage, dates["test_start"], dates["test_end"])
        full = _apply(scfg, {"research": {"top_n": 10**6, "max_pairs_per_symbol": None}})
        ranking = run_funnel(store, full.research, chains).table  # every pair that passed, best IC first
        grids: dict[str, dict[tuple[str, str], pd.DataFrame]] = {}
        for name, ov in VARIANTS.items():
            if only and not any(name.startswith(o) for o in only):
                continue
            vcfg = _apply(scfg, ov)
            rc = vcfg.research
            chosen = select_pairs(ranking, rc.top_n, rc.max_pairs_per_symbol)
            pairs = list(zip(chosen["a"], chosen["b"]))
            # Grid-search tables depend only on the trading rules, so variants that
            # differ only in selection reuse them.
            cache = grids.setdefault(json.dumps(ov.get("strategy", {}), sort_keys=True), {})
            need = [p for p in pairs if p not in cache]
            if need:
                log.info("%s | %s: calibrating %d pairs", stage, name, len(need))
                for c in calibrate_all(need, vcfg):
                    cache[(c.a, c.b)] = c.table
            cals = [Calibration(a, b, cache[(a, b)], select_best(cache[(a, b)], rc.selection, rc.min_train_trades))
                    for a, b in pairs if (a, b) in cache]
            res = evaluate_portfolio(cals, vcfg)
            m = res.metrics
            rows.append({"stage": stage, "variant": name, **{k: m[k] for k in
                         ("Sharpe", "TotalRet", "AnnRet", "MaxDD", "Trades", "StopLosses", "Fees", "Funding",
                          "Pairs", "Deactivated")}, **_diagnostics(res)})
            vdir = out / stage / _slug(name)
            vdir.mkdir(parents=True, exist_ok=True)
            res.per_pair.to_csv(vdir / "pairs_results.csv", index=False)
            res.equity.to_csv(vdir / "portfolio_equity.csv", index=False)
            (vdir / "pairs.yaml").write_text(yaml.safe_dump(pairs_yaml(res.per_pair, chosen, 20, "ic"), sort_keys=False))
            log.info("%s | %-40s Sharpe %5.2f | return %+6.1f%% | maxDD %6.1f%% | trades %d",
                     stage, name, m["Sharpe"], 100 * m["TotalRet"], 100 * m["MaxDD"], m["Trades"])
    df = pd.DataFrame(rows)
    df.to_csv(out / "results.csv", index=False)
    return df


def summary(df: pd.DataFrame) -> str:
    wide = df.pivot(index="variant", columns="stage", values=["Sharpe", "TotalRet", "MaxDD"])
    lines = [f"{'variant':40s} | {'VALIDATION':^28s} | {'TEST (confirmation only)':^28s}",
             f"{'':40s} | {'Sharpe':>7s} {'return':>9s} {'maxDD':>9s} | {'Sharpe':>7s} {'return':>9s} {'maxDD':>9s}"]
    chosen = wide[("Sharpe", "validation")].idxmax()
    for v in [v for v in VARIANTS if v in wide.index]:
        r = wide.loc[v]
        mark = "  <= chosen on validation" if v == chosen else ""
        lines.append(f"{v:40s} | {r[('Sharpe', 'validation')]:7.2f} {100 * r[('TotalRet', 'validation')]:+8.1f}% "
                     f"{100 * r[('MaxDD', 'validation')]:+8.1f}% | {r[('Sharpe', 'test')]:7.2f} "
                     f"{100 * r[('TotalRet', 'test')]:+8.1f}% {100 * r[('MaxDD', 'test')]:+8.1f}%{mark}")
    diag = df.set_index(["variant", "stage"])[["Trades", "MedianHoldH", "SignalExit%", "MeanAbsZEntry", "RetExCrash"]]
    lines += ["", "Diagnostics (trades, median hold in hours, % signal exits, mean |Z| at entry, "
              "return excluding 2025-10-10/11 - informational only):", diag.round(2).to_string()]
    return "\n".join(lines)

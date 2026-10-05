"""Command line: python -m cryptopairs <command> [--config config/config.yaml] [--profile hardened]"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import logging
import math
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

from cryptopairs.config import AppConfig, load_config, to_ms

log = logging.getLogger("cryptopairs")


def _setup_logging(cfg: AppConfig, command: str, verbose: bool, name: str | None = None) -> None:
    log_dir = Path(cfg.runtime.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    logging.Formatter.converter = time.gmtime  # log times are UTC on every machine; the dashboard localises them
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_dir / f"{name or command}.log", encoding="utf-8")],
        force=True,
    )
    logging.getLogger("ccxt").setLevel(logging.WARNING)


def _run_dir(cfg: AppConfig, tag: str) -> Path:
    d = Path(cfg.research.runs_dir) / tag
    d.mkdir(parents=True, exist_ok=True)
    return d


def _json_safe(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in d.items()}


def _store(cfg: AppConfig):
    from cryptopairs.research.data import DataStore

    return DataStore(Path(cfg.data.dir), cfg.data.interval)


# ------------------------------------------------------------------ commands
def cmd_download(cfg: AppConfig, args) -> None:
    from cryptopairs.research.data import download, list_archive_symbols

    rc = cfg.research
    symbols = args.symbols or list_archive_symbols(cfg.data.quote)
    probe = None if args.symbols else rc.test_start_ms - rc.min_history_days * 24 * 3_600_000
    log.info("downloading %d symbols from %s to %s", len(symbols),
             pd.Timestamp(rc.data_start_ms, unit="ms").date(), pd.Timestamp(rc.test_end_ms, unit="ms").date())
    res = download(_store(cfg), symbols, rc.data_start_ms, rc.test_end_ms, probe_ms=probe,
                   funding=not args.no_funding, threads=cfg.data.download_threads)
    kept = {s: r for s, r in res.items() if not r.startswith("skip")}
    errors = {s: r for s, r in res.items() if r.startswith("error")}
    log.info("done: %d symbols stored, %d skipped (listed too late), %d errors",
             len(kept) - len(errors), len(res) - len(kept), len(errors))
    for s, r in errors.items():
        log.warning("  %s: %s", s, r)


def cmd_metadata(cfg: AppConfig, args) -> None:
    from cryptopairs.research.metadata import fetch_chain_map

    store = _store(cfg)
    symbols = store.symbols()
    if not symbols:
        raise SystemExit("no downloaded symbols; run 'download' first")
    df = fetch_chain_map(symbols, cfg.data.quote)
    path = Path(cfg.data.dir) / "meta" / "chains.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    log.info("wrote %s (%d symbols, %d unresolved)", path, len(df), int((df["chains"] == "").sum()))


def cmd_funnel(cfg: AppConfig, args) -> pd.DataFrame:
    from cryptopairs.research.funnel import run_funnel
    from cryptopairs.research.metadata import load_chain_map

    chains = load_chain_map(Path(cfg.data.dir) / "meta" / "chains.csv")
    if cfg.research.same_chain and not chains:
        log.warning("no data/meta/chains.csv: the same-chain filter is skipped (run 'metadata' to enable it)")
    res = run_funnel(_store(cfg), cfg.research, chains or None)
    out = _run_dir(cfg, args.tag)
    res.table.to_csv(out / "funnel.csv", index=False)
    (out / "funnel_counts.json").write_text(json.dumps(res.counts, indent=2))
    log.info("funnel: %s -> %s", res.counts, out / "funnel.csv")
    return res.table


def cmd_calibrate(cfg: AppConfig, args) -> None:
    from cryptopairs.research.calibrate import calibrate_all

    out = _run_dir(cfg, args.tag)
    funnel = pd.read_csv(out / "funnel.csv")
    pairs = list(zip(funnel["a"], funnel["b"]))
    log.info("calibrating %d pairs x %d grid points with %d worker(s)", len(pairs),
             len(cfg.research.grid_lookback) * len(cfg.research.grid_z_entry) * len(cfg.research.grid_z_exit),
             cfg.research.workers)
    cals = calibrate_all(pairs, cfg)
    grid = pd.concat([c.table.assign(a=c.a, b=c.b) for c in cals if not c.table.empty], ignore_index=True)
    grid.to_csv(out / "calibration_grid.csv", index=False)
    best = pd.DataFrame([{"a": c.a, "b": c.b, **c.best} for c in cals if c.best is not None])
    best.to_csv(out / "calibration_best.csv", index=False)
    log.info("calibrated %d pairs -> %s", len(best), out / "calibration_best.csv")


def cmd_test(cfg: AppConfig, args) -> None:
    from cryptopairs.research.calibrate import Calibration, evaluate_portfolio, pairs_yaml

    out = _run_dir(cfg, args.tag)
    best = pd.read_csv(out / "calibration_best.csv")
    cals = [Calibration(r["a"], r["b"], pd.DataFrame(), r.to_dict()) for _, r in best.iterrows()]
    res = evaluate_portfolio(cals, cfg)
    res.per_pair.to_csv(out / "test_pairs.csv", index=False)
    res.equity.to_csv(out / "portfolio_equity.csv", index=False)
    (out / "portfolio_metrics.json").write_text(json.dumps(_json_safe(res.metrics), indent=2, default=str))
    funnel = pd.read_csv(out / "funnel.csv") if (out / "funnel.csv").exists() else None
    doc = pairs_yaml(res.per_pair, funnel, args.top, args.rank_by)
    (out / "pairs.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    m = res.metrics
    log.info("test portfolio: Sharpe %.2f | total return %.1f%% | max DD %.1f%% | trades %d | fees %.0f | funding %.0f",
             m["Sharpe"], 100 * m["TotalRet"], 100 * m["MaxDD"], m["Trades"], m["Fees"], m["Funding"])
    log.info("wrote %s (%d pairs, ranked by %s)", out / "pairs.yaml", len(doc["pairs"]), args.rank_by)


def cmd_research(cfg: AppConfig, args) -> None:
    cmd_funnel(cfg, args)
    cmd_calibrate(cfg, args)
    cmd_test(cfg, args)


def cmd_experiments(cfg: AppConfig, args) -> None:
    from cryptopairs.research.experiments import run_experiments, summary

    df = run_experiments(cfg, args.out, args.only)
    text = summary(df)
    Path(args.out, "summary.txt").write_text(text + "\n")
    print("\n" + text)


def cmd_backtest(cfg: AppConfig, args) -> None:
    from cryptopairs.core.strategy import PairParams
    from cryptopairs.research.backtest import run_backtest
    from cryptopairs.research.data import load_pair

    rc = cfg.research
    start = to_ms(args.start) if args.start else rc.test_start_ms
    end = to_ms(args.end) if args.end else rc.test_end_ms
    params = PairParams(args.lookback, args.z_entry, args.z_exit)
    data = load_pair(_store(cfg), args.a, args.b, start - (params.lookback + 24) * 3_600_000, end)
    res = run_backtest(data, params, cfg.strategy, cfg.costs, start, end, args.capital)
    for k, v in res.metrics().items():
        print(f"{k:>12}: {v:.4f}" if isinstance(v, float) else f"{k:>12}: {v}")
    if args.trades:
        res.trades_frame().to_csv(args.trades, index=False)
        print(f"trades -> {args.trades}")


def resolve_pairs_file(cfg: AppConfig, given: str | None) -> str:
    """The pairs file to trade: --pairs, else runs/<profile>/pairs.yaml, else runtime.pairs_file.

    A missing file stops here with the closest match, before anything is opened or recorded.
    """
    runs = Path(cfg.research.runs_dir)
    if given is None:
        own = runs / cfg.profile / "pairs.yaml"
        given = str(own) if own.exists() else cfg.runtime.pairs_file
    if not Path(given).is_file():
        available = sorted(str(p) for p in runs.glob("*/pairs.yaml"))
        guess = difflib.get_close_matches(given, available, n=1, cutoff=0.6)
        raise SystemExit(f"pairs file not found: {given}"
                         + (f"\n  did you mean:  {guess[0]}" if guess else "")
                         + ("\n  available:     " + "\n                 ".join(available) if available else ""))
    return given


def cmd_paper(cfg: AppConfig, args) -> None:
    from cryptopairs.runtime.app import archive_state, run_app

    cfg.runtime.pairs_file = resolve_pairs_file(cfg, args.pairs)
    log.info("pairs file: %s", cfg.runtime.pairs_file)
    cfg.runtime.mode = "paper"
    if args.fresh:
        old = archive_state(cfg)
        if old is not None:
            log.info("previous paper state archived to %s; starting fresh", old)
    try:
        asyncio.run(run_app(cfg))
    except KeyboardInterrupt:  # Ctrl+C before the signal handlers were installed (start-up)
        log.warning("stopped by Ctrl+C during start-up")
    except BaseException as exc:  # the reason goes to the log file, not only to the terminal
        if not isinstance(exc, SystemExit) or exc.code not in (0, None):
            log.exception("the bot stopped because of an error")
        raise


def _open_store(cfg: AppConfig, mode: str):
    from cryptopairs.runtime.app import state_path
    from cryptopairs.runtime.store import StateStore

    cfg.runtime.mode = mode
    path = state_path(cfg)  # honours runtime.state_name (--name)
    if not path.exists():
        raise SystemExit(f"no state database at {path} (start the bot first: python -m cryptopairs {mode})")
    return StateStore(path)


def cmd_status(cfg: AppConfig, args) -> None:
    from cryptopairs.runtime.report import status_text

    store = _open_store(cfg, args.mode)
    try:
        print(status_text(store, args.trades))
    finally:
        store.close()


def cmd_export(cfg: AppConfig, args) -> None:
    import time

    from cryptopairs.runtime.report import export_csv

    store = _open_store(cfg, args.mode)
    out = Path(args.out or f"exports/{args.mode}_{time.strftime('%Y%m%d_%H%M%S')}")
    try:
        for path in export_csv(store, out):
            print(f"wrote {path}")
    finally:
        store.close()


def cmd_dashboard(cfg: AppConfig, args) -> None:
    import subprocess

    script = Path(__file__).with_name("dashboard.py")
    root = script.parent.parent  # Streamlit reads the theme from <root>/.streamlit/config.toml
    absolute = lambda p: str(Path(p).resolve())  # noqa: E731 - Streamlit runs from root, not from here
    cmd = [sys.executable, "-m", "streamlit", "run", str(script),
           "--server.port", str(args.port), "--server.address", args.address,
           "--server.headless", "true" if args.no_browser else "false",
           "--browser.gatherUsageStats", "false",
           "--server.runOnSave", "true",  # reload the page by itself when the dashboard code changes
           "--", "--state-dir", absolute(cfg.runtime.state_dir), "--log-dir", absolute(cfg.runtime.log_dir),
           "--config", absolute(args.config), "--data-dir", absolute(cfg.data.dir)]
    if args.name:
        cmd += ["--db", f"{args.name}.db"]
    print(f"Dashboard: http://localhost:{args.port}   (Ctrl+C to stop; the bot keeps running)")
    try:
        subprocess.run(cmd, check=False, cwd=root)
    except KeyboardInterrupt:
        pass


def cmd_reset_kill_switch(cfg: AppConfig, args) -> None:
    store = _open_store(cfg, args.mode)
    try:
        store.kv_set("risk.kill_switch", False)
        store.kv_set("risk.peak_equity", None)  # the drawdown is measured afresh from here
        store.event("kill_switch_reset")
        print("kill switch reset; drawdown tracking restarts from the current equity")
    finally:
        store.close()


# ---------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="cryptopairs", description=__doc__)
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--profile", default=None, help="config profile overlay, e.g. strict or hardened")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("download", help="download klines + funding from the Binance archive")
    d.add_argument("--symbols", nargs="*", help="only these symbols (default: whole archive)")
    d.add_argument("--no-funding", action="store_true")

    sub.add_parser("metadata", help="fetch coin -> blockchain map from CoinGecko")

    for name, hlp in (("funnel", "select pairs (Filters 1-4)"), ("calibrate", "grid search per pair"),
                      ("test", "out-of-sample portfolio test + pairs.yaml"),
                      ("research", "funnel + calibrate + test")):
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--tag", default="latest", help="output folder under runs/")
        if name in ("test", "research"):
            sp.add_argument("--top", type=int, default=20, help="pairs to put in pairs.yaml")
            sp.add_argument("--rank-by", choices=["ic", "test_sharpe", "none"], default="ic")

    xp = sub.add_parser("experiments", help="compare strategy variants (validation picks, test confirms)")
    xp.add_argument("--out", default="runs/experiments")
    xp.add_argument("--only", nargs="*", help="variant name prefixes to run, e.g. V0 K1 K2")

    b = sub.add_parser("backtest", help="backtest one pair")
    b.add_argument("a")
    b.add_argument("b")
    b.add_argument("--lookback", type=int, required=True)
    b.add_argument("--z-entry", type=float, required=True)
    b.add_argument("--z-exit", type=float, default=0.0)
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--capital", type=float, default=10_000.0)
    b.add_argument("--trades", help="write trades to this CSV")

    pp = sub.add_parser("paper", help="run the paper-trading bot on live market data")
    pp.add_argument("--pairs", help="pairs.yaml (default: runs/<profile>/pairs.yaml if it exists, "
                                    "else runtime.pairs_file)")
    pp.add_argument("--fresh", action="store_true", help="archive the saved paper state and start over")
    pp.add_argument("--name", help="bot name: state/<name>.db and logs/<name>.log (default: paper)")

    st = sub.add_parser("status", help="show positions, trades and equity from the state database")
    st.add_argument("--mode", default="paper", choices=["paper", "live"])
    st.add_argument("--trades", type=int, default=10, help="how many recent trades to list")
    st.add_argument("--name", help="bot name used with 'paper --name'")

    ex = sub.add_parser("export", help="export the state database to CSV files")
    ex.add_argument("--mode", default="paper", choices=["paper", "live"])
    ex.add_argument("--out", help="output folder (default: exports/<mode>_<time>)")
    ex.add_argument("--name", help="bot name used with 'paper --name'")

    db = sub.add_parser("dashboard", help="open the live dashboard in the browser")
    db.add_argument("--name", help="bot to show first (state/<name>.db)")
    db.add_argument("--port", type=int, default=8501)
    db.add_argument("--address", default="127.0.0.1", help="127.0.0.1 = only this computer")
    db.add_argument("--no-browser", action="store_true", help="do not open a browser (servers)")

    rk = sub.add_parser("reset-kill-switch", help="allow new entries again after a kill switch")
    rk.add_argument("--mode", default="paper", choices=["paper", "live"])
    rk.add_argument("--name", help="bot name used with 'paper --name'")

    args = p.parse_args(argv)
    cfg = load_config(args.config if Path(args.config).exists() else None, args.profile)
    name = getattr(args, "name", None)
    if name:
        cfg.runtime.state_name = name
    _setup_logging(cfg, args.command, args.verbose, name)
    if args.command not in ("status", "export", "reset-kill-switch", "dashboard"):
        log.info("profile=%s config=%s", cfg.profile, args.config)
    handler = {
        "download": cmd_download, "metadata": cmd_metadata, "funnel": cmd_funnel,
        "calibrate": cmd_calibrate, "test": cmd_test, "research": cmd_research,
        "backtest": cmd_backtest, "paper": cmd_paper, "experiments": cmd_experiments, "status": cmd_status, "export": cmd_export,
        "reset-kill-switch": cmd_reset_kill_switch, "dashboard": cmd_dashboard,
    }[args.command]
    handler(cfg, args)

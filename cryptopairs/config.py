"""Typed configuration shared by the research pipeline and the runtime.

One YAML file holds every section. A ``profiles`` mapping can override any
section; ``--profile hardened`` deep-merges ``profiles.hardened`` on top of the
base values so strict-paper and crypto-hardened runs differ only by overlay.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

HOUR_MS = 3_600_000


@dataclass
class DataConfig:
    dir: str = "data"
    quote: str = "USDT"
    interval: str = "1h"
    download_threads: int = 8


@dataclass
class StrategyConfig:
    """Trading rules (paper Section 5 and Algorithm 1)."""

    leverage: float = 5.0
    # Refit the regression every N closed bars while flat; frozen while in a position.
    refit_every: int = 1
    # Paper Algorithm 1 never exits and re-enters on the same bar.
    allow_same_bar_reentry: bool = False
    # Stop when equity <= (1 - stop_loss_pct) * equity right after entry.
    stop_loss_pct: float = 0.20
    cooldown_hours: float = 120.0
    # "equity_floor": stop new entries when equity <= threshold * initial (Algorithm 1; with
    #   0.20 this is an 80% loss, consistent with the paper's results).
    # "peak_drawdown": stop new entries after a drawdown > threshold from the peak (Sec 5.3
    #   wording). Keep it above stop_loss_pct: a single stop-loss is already a >= 20% drawdown.
    deactivation_mode: str = "equity_floor"
    deactivation_threshold: float = 0.20
    # The 1/(1+beta) weights are only meaningful for a positive hedge ratio.
    beta_min: float = 0.05
    beta_max: float = 20.0
    # Refuse entries beyond this |Z|: such values mean a broken relationship or bad data
    # (e.g. a frozen delisting price), not an opportunity. None = no cap (paper).
    max_entry_z: float | None = None
    # Optional overlays (None = off, as in the paper).
    # Emergency stop checked continuously inside the bar (protects against hourly
    # gaps such as BAND +28% in one hour); the regular stop stays at bar close.
    catastrophe_stop_pct: float | None = None
    # Stop when the spread diverges to |Z| >= z_entry + z_stop_offset (adapts to each pair's
    # volatility; set stop_loss_pct: 0 to use it instead of the equity stop).
    z_stop_offset: float | None = None
    # Close a trade that has not reverted within this many half-lives of its spread
    # (half-life estimated from the fit window at entry; at least 24 bars).
    time_stop_halflives: float | None = None
    max_holding_bars: int | None = None
    # Spread estimator for calibration: "rolling_ols" (paper) or "kalman" (dynamic
    # alpha/beta; pairs then carry a kalman_delta and use lookback as the warm-up).
    estimator: str = "rolling_ols"
    kalman_outlier_z: float | None = 4.0
    kalman_max_skips: int = 48
    kalman_warmup: int = 720
    # Bars of history the live bot feeds the Kalman filter. None = the whole local archive
    # (data/klines/1h) plus REST bars since it ends: the same series the backtest and the
    # calibration filter, so the bot trades the model that was tested. The filter does not
    # forget its start as fast as 1/sqrt(delta) suggests, so a one-year window gives a
    # different beta for some pairs (audit 2026-10-02).
    kalman_history: int | None = 8760
    # Funding filter (not in the paper), based on each leg's trailing funding rate:
    #   "off"            ignore funding
    #   "short_positive" only enter if the coin we would short had positive funding
    #   "net_cost"       subtract the expected funding of the hold from the entry edge
    funding_filter: str = "off"
    funding_lookback_hours: int = 168
    funding_hold_hours: float = 120.0
    # Funding exit (not in the paper): close an open position when the funding it is expected to pay
    # over the next funding_exit_hours exceeds what it is expected to earn over the same hours
    # (plus the cost of closing). Rates are the trailing average of funding_lookback_hours.
    #   "off"          never
    #   "edge"         expected earnings = the distance Z still has to travel to the exit, reverting
    #                  at the pair's half-life; a loser far from its exit is held, a winner near it closed
    #   "profit_only"  close a position that is in profit as soon as the funding over the hours
    #                  exceeds the cost of closing (losers are always kept)
    funding_exit: str = "off"
    funding_exit_hours: float = 8.0
    funding_exit_cooldown_hours: float = 24.0  # no re-entry right after (the funding would be paid again)
    min_edge_cost_multiple: float | None = None
    # Entry threshold. "fixed": the pair's calibrated z_entry (paper). "rolling_quantile": the
    # adaptive_quantile of |Z| over the previous adaptive_window bars, at least adaptive_min_z:
    # the band widens after a stretch of large swings and narrows when the spread is calm.
    # Z is already scaled by sigma, so this adapts to the shape of recent swings, not their size.
    entry_rule: str = "fixed"
    adaptive_quantile: float = 0.95
    adaptive_window: int = 720
    adaptive_min_z: float = 1.5

    def validate(self) -> None:
        if self.leverage <= 0:
            raise ValueError("strategy.leverage must be > 0")
        if self.refit_every < 1:
            raise ValueError("strategy.refit_every must be >= 1")
        if not 0 <= self.stop_loss_pct < 1:
            raise ValueError("strategy.stop_loss_pct must be in [0, 1)")
        if self.deactivation_mode not in ("peak_drawdown", "equity_floor", "off"):
            raise ValueError(f"unknown deactivation_mode {self.deactivation_mode!r}")
        if self.deactivation_mode == "peak_drawdown" and self.deactivation_threshold <= self.stop_loss_pct:
            raise ValueError(
                "deactivation_threshold must exceed stop_loss_pct in peak_drawdown mode, "
                "otherwise the first stop-loss permanently disables the pair"
            )
        if self.catastrophe_stop_pct is not None and not self.stop_loss_pct < self.catastrophe_stop_pct < 1:
            raise ValueError("strategy.catastrophe_stop_pct must be between stop_loss_pct and 1")
        if self.z_stop_offset is not None and self.z_stop_offset <= 0:
            raise ValueError("strategy.z_stop_offset must be > 0")
        if self.time_stop_halflives is not None and self.time_stop_halflives <= 0:
            raise ValueError("strategy.time_stop_halflives must be > 0")
        if self.estimator not in ("rolling_ols", "kalman"):
            raise ValueError(f"unknown estimator {self.estimator!r}")
        if self.funding_filter not in ("off", "short_positive", "net_cost"):
            raise ValueError(f"unknown funding_filter {self.funding_filter!r}")
        if self.funding_exit not in ("off", "edge", "profit_only"):
            raise ValueError(f"unknown funding_exit {self.funding_exit!r}")
        if self.funding_exit_hours <= 0 or self.funding_exit_cooldown_hours < 0:
            raise ValueError("strategy.funding_exit_hours must be > 0 and funding_exit_cooldown_hours >= 0")
        if not 0 < self.beta_min < self.beta_max:
            raise ValueError("strategy.beta_min must be > 0 and < beta_max")
        if self.kalman_history is not None and self.kalman_history < 2 * self.kalman_warmup:
            raise ValueError("strategy.kalman_history must be >= 2 * kalman_warmup (or null = whole archive)")
        if self.entry_rule not in ("fixed", "rolling_quantile"):
            raise ValueError(f"unknown entry_rule {self.entry_rule!r}")
        if not 0.5 < self.adaptive_quantile < 1 or self.adaptive_window < 48 or self.adaptive_min_z <= 0:
            raise ValueError("adaptive_quantile must be in (0.5, 1), adaptive_window >= 48, adaptive_min_z > 0")


@dataclass
class CostConfig:
    """Execution cost model used by backtests and the paper broker."""

    taker_fee: float = 5e-4
    half_spread_bps: float = 0.0
    slippage_bps: float = 0.0
    funding: bool = False
    fill_on: str = "close"  # "close" (paper) | "next_open"
    intrabar_stop: bool = False  # pessimistic stop check on bar high/low
    # "perp": both legs are perpetuals. "spot": the long leg is bought on spot (no
    # funding, spot taker fee); the short leg stays a perpetual.
    long_leg: str = "perp"
    spot_fee: float = 1e-3
    # Maker execution: with maker_legs = 1 the larger-notional leg of every non-urgent fill
    # (entries, signal and time-stop exits) rests as a post-only limit order. It pays
    # maker_fee, earns the half-spread, and loses maker_adverse_bps to adverse selection.
    # Stops are always taker on both legs.
    maker_legs: int = 0
    maker_fee: float = 2e-4
    maker_adverse_bps: float = 1.0

    def validate(self) -> None:
        if self.maker_legs not in (0, 1):
            raise ValueError("costs.maker_legs must be 0 or 1")
        if self.long_leg not in ("perp", "spot"):
            raise ValueError("costs.long_leg must be 'perp' or 'spot'")
        if self.fill_on not in ("close", "next_open"):
            raise ValueError("costs.fill_on must be 'close' or 'next_open'")
        if self.taker_fee < 0 or self.half_spread_bps < 0 or self.slippage_bps < 0:
            raise ValueError("costs must be non-negative")


@dataclass
class ResearchConfig:
    test_start: str = "2026-03-01"
    test_end: str = "2026-09-30 23:00"
    funnel_days: int = 800
    calibration_days: int = 730
    min_history_days: int = 1000
    same_chain: bool = True
    unknown_chain_policy: str = "exclude"  # "exclude" | "include"
    min_corr_logp: float = 0.9
    min_corr_ret: float = 0.5
    ic_lookbacks: list[int] = field(default_factory=lambda: [168, 720, 2160, 4320])
    ic_horizons: list[int] = field(default_factory=lambda: [1, 3, 6, 12, 24, 72, 168])
    top_n: int = 100
    grid_lookback: list[int] = field(default_factory=lambda: [168, 360, 720, 2160, 4320])
    grid_z_entry: list[float] = field(default_factory=lambda: [0.8, 1.0, 1.5, 2.0, 2.5, 3.0])
    grid_z_exit: list[float] = field(default_factory=lambda: [0.0, 0.5])
    # Used instead of grid_lookback when strategy.estimator == "kalman".
    grid_kalman_delta: list[float] = field(default_factory=lambda: [1e-7, 1e-6, 1e-5])
    min_train_trades: int = 0
    # "argmax": best training Sharpe (paper). "smoothed": best average Sharpe of a grid
    # point and its neighbours, which favours parameters that are not a lucky spike.
    selection: str = "argmax"
    # Cap how many selected pairs may share one coin (None = no cap, as in the paper).
    max_pairs_per_symbol: int | None = None
    # Kalman history used when SCORING a portfolio (evaluate_portfolio). None = the whole series,
    # as in the calibration. 8760 replays a bot that refits on its last year only (bot A before
    # 2026-10-05); the calibration always uses the whole series, as the research did.
    score_kalman_history: int | None = None
    # Thresholds used when SCORING, the same for every pair (None = each pair's calibrated value).
    # Removes the per-pair threshold search, whose training Sharpe did not predict the test.
    fixed_z_entry: float | None = None
    fixed_z_exit: float | None = None
    initial_capital: float = 1_000_000.0
    workers: int = 3
    runs_dir: str = "runs"

    @property
    def test_start_ms(self) -> int:
        return to_ms(self.test_start)

    @property
    def test_end_ms(self) -> int:
        return to_ms(self.test_end)

    @property
    def train_end_ms(self) -> int:
        return self.test_start_ms - HOUR_MS

    @property
    def funnel_start_ms(self) -> int:
        return self.test_start_ms - self.funnel_days * 24 * HOUR_MS

    @property
    def calibration_start_ms(self) -> int:
        return self.test_start_ms - self.calibration_days * 24 * HOUR_MS

    @property
    def data_start_ms(self) -> int:
        """Earliest bar any research step needs (Filter 1 probe or the longest warm-up)."""
        warmup = max(max(self.grid_lookback), max(self.ic_lookbacks)) * HOUR_MS
        return min(
            self.test_start_ms - self.min_history_days * 24 * HOUR_MS,
            self.calibration_start_ms - warmup,
            self.funnel_start_ms - warmup,
        )


@dataclass
class RiskConfig:
    max_open_pairs: int = 50
    # Gross notional in one symbol, summed over pairs, as a multiple of total equity.
    max_symbol_gross_leverage: float = 2.0
    # Kill switch: no new entries after this drawdown of total equity from its peak.
    portfolio_max_drawdown: float = 0.25
    flatten_on_kill: bool = False
    # Check pair stop-losses continuously on live quotes (hardened) instead of only at bar close.
    continuous_stop: bool = False
    stop_check_seconds: float = 5.0
    require_quotes_for_entry: bool = True
    # Highest leverage a manual entry from the dashboard may use; None = strategy.leverage.
    manual_max_leverage: float | None = None


@dataclass
class RuntimeConfig:
    mode: str = "paper"
    exchange: str = "binanceusdm"
    pairs_file: str = "runs/latest/pairs.yaml"
    capital_per_pair: float = 100.0
    bar_grace_seconds: float = 5.0
    quote_stale_seconds: float = 45.0
    quote_snapshot_seconds: float = 20.0
    max_rest_concurrency: int = 4
    # "taker": both legs as market orders. "maker_first": the larger leg as a post-only limit
    # at the best price (repriced every maker_reprice_seconds), each fill hedged at once on
    # the other leg with a market order, and the rest taken after maker_timeout_seconds.
    # Stops always use market orders.
    execution: str = "taker"
    maker_timeout_seconds: float = 300.0
    maker_reprice_seconds: float = 10.0
    maker_poll_seconds: float = 2.0
    log_dir: str = "logs"
    # Evaluate the last closed bar right after start-up (with live quotes) instead of
    # waiting up to an hour for the next bar close.
    evaluate_on_start: bool = True
    # Exit the moment the live Z (bid/ask, the position's frozen fit) crosses the exit band,
    # checked every exit_check_seconds, instead of waiting for the bar close. Entries, the
    # stop-loss and the time stop stay on bar closes; no re-entry in the bar of such an exit.
    # The backtest still exits on bar closes.
    realtime_exits: bool = False
    exit_check_seconds: float = 5.0
    # Live equity snapshot for the dashboard curve (between hourly bars).
    # Live snapshot (equity and every pair's spread, Z and PnL at live prices) for the dashboard.
    live_snapshot_seconds: float = 5.0
    live_retention_hours: float = 24.0
    # Total equity marks (the dashboard's NAV curve): one per minute after live_retention_hours,
    # kept this many days.
    equity_history_days: float = 400.0
    # Dashboard buttons: how often the bot checks for commands, and how old one may be.
    command_poll_seconds: float = 2.0
    command_max_age_seconds: float = 120.0
    state_dir: str = "state"
    # Name of this bot's state database and log (state/<name>.db). Defaults to the mode,
    # so several paper bots (e.g. V0 and Kalman) can run side by side with different names.
    state_name: str | None = None


@dataclass
class AppConfig:
    data: DataConfig = field(default_factory=DataConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    profile: str = "strict"

    def validate(self) -> None:
        self.strategy.validate()
        self.costs.validate()
        if self.runtime.execution not in ("taker", "maker_first"):
            raise ValueError("runtime.execution must be 'taker' or 'maker_first'")


def to_ms(value: str | int | pd.Timestamp) -> int:
    """Parse a date/time (UTC) into epoch milliseconds."""
    if isinstance(value, int):
        return value
    ts = pd.Timestamp(value)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return int(ts.value // 1_000_000)


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _build(cls, raw: dict | None):
    raw = raw or {}
    known = {f.name: f for f in fields(cls)}
    unknown = set(raw) - set(known)
    if unknown:
        raise ValueError(f"unknown keys for {cls.__name__}: {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, value in raw.items():
        default = known[name].default_factory() if callable(known[name].default_factory) else None
        kwargs[name] = _build(type(default), value) if is_dataclass(default) else value
    return cls(**kwargs)


def load_config(path: str | Path | None = None, profile: str | None = None) -> AppConfig:
    raw: dict = {}
    if path is not None:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    profiles = raw.pop("profiles", {}) or {}
    chosen = profile or raw.get("profile", "strict")
    if chosen not in ("strict", *profiles):
        raise ValueError(f"profile {chosen!r} not found in config (have: {sorted(profiles)})")
    merged = _deep_merge(raw, profiles.get(chosen, {}))
    merged["profile"] = chosen
    cfg = _build(AppConfig, merged)
    cfg.validate()
    return cfg

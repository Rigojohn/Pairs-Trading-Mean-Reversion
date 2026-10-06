"""Per-pair trading state machine (paper Section 5 and Algorithm 1).

``PairStrategy`` makes every trading decision and does no I/O. The
backtester, the paper broker and the live runtime all drive the same class,
so the rules that were tested are the rules that trade.

Per bar the driver calls, in order:
    observe_equity(eq)              -> peak tracking and deactivation
    check_exit(...)                 -> "stop_loss" / "signal" / "time_stop" / None
    (after a fill) on_closed(...)
    wants_refit(i) / set_fit(...)   -> refresh alpha, beta, sigma while flat
    check_entry(...)                -> LONG / SHORT / FLAT
    (after a fill) on_opened(...)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cryptopairs.config import HOUR_MS, StrategyConfig
from cryptopairs.core.signals import executable_zscores

FLAT, LONG, SHORT = 0, 1, -1

STOP_REASONS = frozenset({"stop_loss", "stop_loss_intrabar", "stop_catastrophe", "stop_z", "liquidation"})


@dataclass(frozen=True, slots=True)
class PairParams:
    """Calibrated per-pair parameters theta = (L, z_entry, z_exit).

    With ``kalman_delta`` set, the spread comes from the Kalman filter and
    ``lookback`` is its warm-up / sigma window instead of the OLS window.
    """

    lookback: int
    z_entry: float
    z_exit: float
    kalman_delta: float | None = None

    def __post_init__(self) -> None:
        if self.lookback < 3:
            raise ValueError("lookback must be >= 3 bars")
        if not 0 <= self.z_exit < self.z_entry:
            raise ValueError("need 0 <= z_exit < z_entry")
        if self.kalman_delta is not None and not self.kalman_delta > 0:
            raise ValueError("kalman_delta must be > 0")


@dataclass(frozen=True, slots=True)
class Fit:
    alpha: float
    beta: float
    sigma: float
    bar_index: int


class PairStrategy:
    __slots__ = (
        "params", "cfg", "initial_equity", "round_trip_cost", "anchor", "long_pays_funding",
        "side", "fit", "entry_equity", "entry_index", "entry_ts", "prev_exit_z",
        "cooldown_until", "deactivated", "peak_equity", "_cooldown_ms", "hold_limit",
        "z_exit_override", "stop_pct_override", "half_life",
    )

    def __init__(
        self,
        params: PairParams,
        cfg: StrategyConfig,
        initial_equity: float,
        *,
        round_trip_cost: float = 0.0,
        anchor: int = 0,
        long_pays_funding: bool = True,
    ) -> None:
        self.params = params
        self.cfg = cfg
        self.initial_equity = float(initial_equity)
        # Fraction of gross notional lost to fees/spread/slippage over a round trip.
        self.round_trip_cost = float(round_trip_cost)
        # Bar index the refit schedule is aligned to (live uses hours since epoch, anchor 0).
        self.anchor = int(anchor)
        # False when the long leg is held on spot (spot pays no funding).
        self.long_pays_funding = bool(long_pays_funding)
        self.side = FLAT
        self.fit: Fit | None = None
        self.entry_equity = math.nan
        self.entry_index = -1
        self.entry_ts = -1
        self.prev_exit_z = math.nan
        self.cooldown_until = -1
        self.deactivated = False
        self.peak_equity = float(initial_equity)
        self._cooldown_ms = int(cfg.cooldown_hours * HOUR_MS)
        self.hold_limit = -1  # bars allowed in the current trade by the half-life time stop
        self.half_life = math.nan  # of the open trade's spread (bars), for the funding exit
        # Manual exit plan for the open position (dashboard); None = the configured rule.
        self.z_exit_override: float | None = None
        self.stop_pct_override: float | None = None

    # ------------------------------------------------------------------ fit
    def wants_refit(self, bar_index: int) -> bool:
        return self.side == FLAT and (bar_index - self.anchor) % self.cfg.refit_every == 0

    def set_fit(self, alpha: float, beta: float, sigma: float, bar_index: int) -> None:
        if self.side != FLAT:
            raise RuntimeError("the fit is frozen while a position is open")
        if math.isfinite(alpha) and math.isfinite(beta) and sigma > 0:
            self.fit = Fit(float(alpha), float(beta), float(sigma), int(bar_index))
        else:
            self.fit = None

    def fit_is_fresh(self, bar_index: int) -> bool:
        return self.fit is not None and bar_index - self.fit.bar_index < self.cfg.refit_every

    def z_scores(
        self, log_bid_a: float, log_ask_a: float, log_bid_b: float, log_ask_b: float
    ) -> tuple[float, float]:
        """(z_buy, z_sell) under the current fit; NaNs when there is no fit."""
        f = self.fit
        if f is None:
            return math.nan, math.nan
        return executable_zscores(log_bid_a, log_ask_a, log_bid_b, log_ask_b, f.alpha, f.beta, f.sigma)

    # ------------------------------------------------------------- equity
    def observe_equity(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.deactivated:
            return
        mode, thr = self.cfg.deactivation_mode, self.cfg.deactivation_threshold
        if mode == "equity_floor" and equity <= thr * self.initial_equity:
            self.deactivated = True
        elif mode == "peak_drawdown" and equity <= (1.0 - thr) * self.peak_equity:
            self.deactivated = True

    def deactivate(self) -> None:
        self.deactivated = True

    def stop_level(self) -> float:
        """Equity at or below which the open position is stopped out."""
        pct = self.cfg.stop_loss_pct if self.stop_pct_override is None else self.stop_pct_override
        if self.side == FLAT or pct <= 0:
            return -math.inf
        return (1.0 - pct) * self.entry_equity

    def exit_z(self) -> float:
        """Exit band of the open position: the manual exit plan, else the pair's z_exit."""
        return self.params.z_exit if self.z_exit_override is None else self.z_exit_override

    def catastrophe_level(self) -> float:
        """Equity at which the intrabar emergency stop fires (off unless configured)."""
        pct = self.cfg.catastrophe_stop_pct
        if self.side == FLAT or pct is None:
            return -math.inf
        return (1.0 - pct) * self.entry_equity

    # --------------------------------------------------------------- exit
    def check_exit(
        self, ts: int, bar_index: int, equity: float, z_buy: float, z_sell: float,
        check_stop: bool = True,
    ) -> str | None:
        """``check_stop=False``: only the exit signal (the Z crossing), for checks on live quotes
        between bar closes; the stop-loss, z-stop and time stop stay on bar closes."""
        if self.side == FLAT:
            return None
        if check_stop and equity <= self.stop_level():
            return "stop_loss"
        off = self.cfg.z_stop_offset
        if check_stop and off is not None:
            z_stop = self.params.z_entry + off
            if (self.side == LONG and z_sell <= -z_stop) or (self.side == SHORT and z_buy >= z_stop):
                return "stop_z"
        z_exit = self.exit_z()
        if self.side == LONG:
            z = z_sell
            hit = self.prev_exit_z < -z_exit and z >= -z_exit
        else:
            z = z_buy
            hit = self.prev_exit_z > z_exit and z <= z_exit
        if not math.isnan(z):
            self.prev_exit_z = z
        if hit:
            return "signal"
        if not check_stop:
            return None
        held = bar_index - self.entry_index
        mh = self.cfg.max_holding_bars
        if (mh is not None and held >= mh) or (self.hold_limit > 0 and held >= self.hold_limit):
            return "time_stop"
        return None

    def check_funding_exit(
        self, equity: float, z_buy: float, z_sell: float, funding_a: float, funding_b: float,
    ) -> bool:
        """True when the funding this position pays over ``funding_exit_hours`` outweighs what it earns.

        ``funding_a/b``: trailing average funding rate per hour of each leg (NaN = unknown, no exit).
        Both rules compare, per unit gross notional, the funding over the hours with the cost of
        closing (half a round trip); ``edge`` adds the expected earnings of the hours as well.
        """
        mode = self.cfg.funding_exit
        f = self.fit
        if mode == "off" or self.side == FLAT or f is None or math.isnan(funding_a) or math.isnan(funding_b):
            return False
        w_a, w_b = 1.0 / (1.0 + f.beta), f.beta / (1.0 + f.beta)
        if self.side == LONG:  # long A, short B
            r_long, w_long, r_short, w_short = funding_a, w_a, funding_b, w_b
        else:
            r_long, w_long, r_short, w_short = funding_b, w_b, funding_a, w_a
        long_part = w_long * r_long if self.long_pays_funding else 0.0
        per_hour = long_part - w_short * r_short  # > 0: the position pays
        if per_hour <= 0:
            return False
        hours = self.cfg.funding_exit_hours
        hurdle = hours * per_hour + 0.5 * self.round_trip_cost
        if mode == "profit_only":
            return equity > self.entry_equity and hours * per_hour > 0.5 * self.round_trip_cost
        if not (math.isfinite(self.half_life) and self.half_life > 0):
            return False
        z_exit = self.exit_z()
        dist = max(0.0, (-z_exit - z_sell) if self.side == LONG else (z_buy - z_exit))
        earned = f.sigma / (1.0 + f.beta) * dist * (1.0 - 2.0 ** (-hours / self.half_life))
        return earned < hurdle

    # -------------------------------------------------------------- entry
    def check_entry(
        self, ts: int, bar_index: int, z_buy: float, z_sell: float,
        funding_a: float = math.nan, funding_b: float = math.nan, z_entry: float | None = None,
    ) -> int:
        """``funding_a/b``: trailing average funding rate per hour of each leg (NaN = unknown).

        ``z_entry``: this bar's threshold when it is computed outside the pair's parameters
        (``entry_rule: rolling_quantile``); None = the calibrated ``params.z_entry``.
        """
        if (
            self.side != FLAT
            or self.deactivated
            or ts < self.cooldown_until
            or not self.fit_is_fresh(bar_index)
        ):
            return FLAT
        f = self.fit
        if not self.cfg.beta_min <= f.beta <= self.cfg.beta_max:
            return FLAT
        if z_entry is None:
            z_entry = self.params.z_entry
        elif not math.isfinite(z_entry) or z_entry <= self.params.z_exit:
            return FLAT  # no threshold yet (warm-up), or one that would sit inside the exit band
        if z_buy < -z_entry:
            side, z = LONG, z_buy
        elif z_sell > z_entry:
            side, z = SHORT, z_sell
        else:
            return FLAT
        if self.cfg.max_entry_z is not None and abs(z) > self.cfg.max_entry_z:
            return FLAT
        mult = self.cfg.min_edge_cost_multiple
        ff = self.cfg.funding_filter
        funding_cost = 0.0
        if ff != "off" and not (math.isnan(funding_a) or math.isnan(funding_b)):
            w_a, w_b = 1.0 / (1.0 + f.beta), f.beta / (1.0 + f.beta)
            if side == LONG:  # long A, short B
                r_long, w_long, r_short, w_short = funding_a, w_a, funding_b, w_b
            else:
                r_long, w_long, r_short, w_short = funding_b, w_b, funding_a, w_a
            if ff == "short_positive" and r_short < 0:
                return FLAT
            if ff == "net_cost":
                # Longs pay a positive rate, shorts receive it; per unit gross notional.
                long_part = w_long * r_long if self.long_pays_funding else 0.0
                funding_cost = self.cfg.funding_hold_hours * (long_part - w_short * r_short)
        if mult is not None or funding_cost != 0.0:
            # Expected return per unit gross notional if Z reverts to the exit band (Sec 5.2).
            edge = f.sigma / (1.0 + f.beta) * (abs(z) - self.params.z_exit)
            if edge < (mult or 0.0) * self.round_trip_cost + funding_cost:
                return FLAT
        return side

    # -------------------------------------------------------------- fills
    def on_opened(
        self, side: int, ts: int, bar_index: int, entry_equity: float, z_buy: float, z_sell: float,
        half_life: float = math.nan,
    ) -> None:
        if self.side != FLAT:
            raise RuntimeError("already in a position")
        if side not in (LONG, SHORT):
            raise ValueError("side must be LONG or SHORT")
        self.side = side
        self.entry_ts = ts
        self.entry_index = bar_index
        self.entry_equity = float(entry_equity)
        self.prev_exit_z = z_sell if side == LONG else z_buy
        self.half_life = half_life
        k = self.cfg.time_stop_halflives
        self.hold_limit = -1
        if k is not None and math.isfinite(half_life) and half_life > 0:
            self.hold_limit = max(24, int(math.ceil(k * half_life)))

    def on_closed(self, reason: str, ts: int) -> None:
        if self.side == FLAT:
            raise RuntimeError("no position to close")
        self.side = FLAT
        self.entry_equity = math.nan
        self.entry_index = -1
        self.entry_ts = -1
        self.prev_exit_z = math.nan
        self.hold_limit = -1
        self.half_life = math.nan
        self.z_exit_override = self.stop_pct_override = None
        if reason in STOP_REASONS:
            self.cooldown_until = ts + self._cooldown_ms
        elif reason == "funding_exit":
            self.cooldown_until = ts + int(self.cfg.funding_exit_cooldown_hours * HOUR_MS)
        if reason == "liquidation":
            self.deactivated = True

    # ---------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        """JSON-safe state for persistence; ``restore`` is its exact inverse."""
        f = self.fit

        def num(x: float) -> float | None:
            return None if math.isnan(x) else x

        return {
            "side": self.side,
            "alpha": f.alpha if f else None,
            "beta": f.beta if f else None,
            "sigma": f.sigma if f else None,
            "fit_bar": f.bar_index if f else None,
            "entry_equity": num(self.entry_equity),
            "entry_ts": self.entry_ts,
            "entry_index": self.entry_index,
            "prev_exit_z": num(self.prev_exit_z),
            "cooldown_until": self.cooldown_until,
            "deactivated": self.deactivated,
            "peak_equity": self.peak_equity,
            "hold_limit": self.hold_limit,
            "initial_equity": self.initial_equity,
            "z_exit_override": self.z_exit_override,
            "stop_pct_override": self.stop_pct_override,
        }

    def restore(self, snap: dict) -> None:
        def num(x) -> float:
            return math.nan if x is None else float(x)

        side = int(snap["side"])
        if side not in (FLAT, LONG, SHORT):
            raise ValueError(f"invalid stored side {side}")
        self.side = side
        if snap.get("alpha") is not None and snap.get("sigma"):
            self.fit = Fit(float(snap["alpha"]), float(snap["beta"]), float(snap["sigma"]), int(snap["fit_bar"]))
        else:
            self.fit = None
        if self.side != FLAT and self.fit is None:
            raise ValueError("stored open position has no regression fit")
        self.entry_equity = num(snap.get("entry_equity"))
        self.entry_ts = int(snap.get("entry_ts", -1))
        self.entry_index = int(snap.get("entry_index", -1))
        self.prev_exit_z = num(snap.get("prev_exit_z"))
        self.cooldown_until = int(snap.get("cooldown_until", -1))
        self.deactivated = bool(snap.get("deactivated", False))
        self.peak_equity = float(snap.get("peak_equity", self.peak_equity))
        self.initial_equity = float(snap.get("initial_equity", self.initial_equity))
        self.hold_limit = int(snap.get("hold_limit", -1))
        zo, so = snap.get("z_exit_override"), snap.get("stop_pct_override")
        self.z_exit_override = None if zo is None else float(zo)
        self.stop_pct_override = None if so is None else float(so)

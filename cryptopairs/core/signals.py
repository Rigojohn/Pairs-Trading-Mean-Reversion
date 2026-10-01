"""Spread estimation: log-price OLS and Z-scores (paper Sections 3.2-3.4).

``rolling_ols`` fits every window in O(n) with cumulative sums, for research.
``fit_ols`` fits a single window with a two-pass formula, for the live runtime.
Both define sigma as the sample standard deviation (ddof=1) of the in-window
residuals, matching the legacy code.
"""

from __future__ import annotations

import math

import numpy as np


def rolling_ols(
    log_a: np.ndarray, log_b: np.ndarray, window: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Regress log_a on log_b over each trailing window of ``window`` bars.

    Element t of each output uses bars [t - window + 1, t]. The first
    ``window - 1`` elements are NaN. Degenerate windows (no variance in log_b)
    are NaN as well.
    """
    y = np.asarray(log_a, dtype=np.float64)
    x = np.asarray(log_b, dtype=np.float64)
    n = y.shape[0]
    if x.shape[0] != n:
        raise ValueError("log_a and log_b must have the same length")
    if window < 3:
        raise ValueError("window must be >= 3")
    alpha = np.full(n, np.nan)
    beta = np.full(n, np.nan)
    sigma = np.full(n, np.nan)
    if n < window:
        return alpha, beta, sigma

    # Centre on global means so the cumulative sums stay small (less cancellation).
    cy, cx = float(np.mean(y)), float(np.mean(x))
    yc, xc = y - cy, x - cx

    def win_sum(v: np.ndarray) -> np.ndarray:
        c = np.concatenate(([0.0], np.cumsum(v)))
        return c[window:] - c[:-window]

    sx, sy = win_sum(xc), win_sum(yc)
    sxx, sxy, syy = win_sum(xc * xc), win_sum(xc * yc), win_sum(yc * yc)
    mx, my = sx / window, sy / window
    vxx = sxx - window * mx * mx
    vxy = sxy - window * mx * my
    vyy = syy - window * my * my

    with np.errstate(divide="ignore", invalid="ignore"):
        b = vxy / vxx
        a = (my - b * mx) + cy - b * cx
        sse = np.maximum(vyy - b * vxy, 0.0)
        s = np.sqrt(sse / (window - 1))
    bad = ~(vxx > 1e-14 * window) | ~np.isfinite(b) | ~(s > 0)
    b[bad], a[bad], s[bad] = np.nan, np.nan, np.nan

    alpha[window - 1 :], beta[window - 1 :], sigma[window - 1 :] = a, b, s
    return alpha, beta, sigma


def fit_ols(log_a: np.ndarray, log_b: np.ndarray) -> tuple[float, float, float]:
    """Single-window OLS of log_a on log_b. Returns (alpha, beta, sigma); NaNs if degenerate."""
    y = np.asarray(log_a, dtype=np.float64)
    x = np.asarray(log_b, dtype=np.float64)
    n = y.shape[0]
    if n < 3 or x.shape[0] != n or not (np.all(np.isfinite(y)) and np.all(np.isfinite(x))):
        return math.nan, math.nan, math.nan
    mx, my = x.mean(), y.mean()
    dx, dy = x - mx, y - my
    vxx = float(dx @ dx)
    if vxx <= 1e-14 * n:
        return math.nan, math.nan, math.nan
    b = float(dx @ dy) / vxx
    a = float(my - b * mx)
    resid = dy - b * dx
    s = math.sqrt(float(resid @ resid) / (n - 1))
    if not s > 0:
        return math.nan, math.nan, math.nan
    return a, b, s


def kalman_filter(
    log_a: np.ndarray,
    log_b: np.ndarray,
    delta: float,
    warmup: int = 720,
    outlier_z: float | None = 4.0,
    max_skips: int = 48,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Dynamic (alpha, beta, sigma) from a Kalman filter; same output contract as ``rolling_ols``.

    Model: log_a_t = a_t + beta_t * (log_b_t - xbar) + e_t, with (a, beta) a random walk
    whose step covariance is ``delta * R`` (R = current spread variance). ``delta`` is
    therefore a signal-to-noise ratio: the equilibrium "remembers" roughly
    1/sqrt(delta) bars (1e-7 ~ 4 months, 1e-6 ~ 6 weeks, 1e-5 ~ 2 weeks of hourly bars).

    Element t is computed BEFORE bar t updates the filter (the prior), so the
    Z-score at t never uses bar t:
        alpha[t], beta[t]  prior equilibrium (alpha converted to raw log_b units)
        sigma[t]           RMS of the prior spread (its distance from equilibrium, 0) over
                           the previous ``warmup`` bars; like the OLS sigma, not a std around
                           the window mean, so a persistent divergence raises sigma instead
                           of collapsing it
    The signal is the spread level (log_a - alpha - beta log_b) / sigma, as with OLS.
    The one-step innovation is not used as the signal, because on hourly bars it measures
    one hour's surprise.

    Robustness: an innovation beyond ``outlier_z`` standard deviations does not update
    the state, so a crash wick cannot drag the hedge ratio, and it enters the sigma
    window clipped to that bound, so it cannot inflate sigma either. After ``max_skips``
    consecutive skips the filter accepts the new level (a genuine regime change).
    The first ``warmup`` elements are NaN; the filter is seeded with OLS on them.
    """
    y = np.asarray(log_a, dtype=np.float64)
    x = np.asarray(log_b, dtype=np.float64)
    n = y.shape[0]
    alpha = np.full(n, np.nan)
    beta = np.full(n, np.nan)
    sigma = np.full(n, np.nan)
    if x.shape[0] != n:
        raise ValueError("log_a and log_b must have the same length")
    if not delta > 0:
        raise ValueError("delta must be > 0")
    w = int(warmup)
    if w < 10 or n <= w:
        return alpha, beta, sigma
    xbar = float(np.mean(x[:w]))
    xs = x - xbar
    a0, b0, s0 = fit_ols(y[:w], xs[:w])
    if not math.isfinite(s0):
        return alpha, beta, sigma

    # Seed with OLS on the warm-up. Since xs is centred on the warm-up mean, the OLS
    # covariance of (a, beta) is diagonal: Var(a) = R/w, Var(beta) = R/Sxx.
    r0 = s0 * s0
    sxx = float(np.sum(xs[:w] ** 2))
    m0, m1 = a0, b0
    p00, p01, p11 = r0 / w, 0.0, r0 / sxx
    u = np.empty(n)
    u[:w] = y[:w] - a0 - b0 * xs[:w]
    s2 = float(np.sum(u[:w] ** 2))
    skips = 0
    yl, xl = y.tolist(), xs.tolist()
    for t in range(w, n):
        var = s2 / w  # mean square distance from equilibrium (see docstring)
        if not var > 0:
            var = r0
        sd = math.sqrt(var)
        q = delta * var
        p00 += q
        p11 += q
        h1 = xl[t]
        e = yl[t] - (m0 + m1 * h1)
        # A frozen price (halted or delisting contract) drives sigma to ~0 and Z to infinity:
        # leave sigma undefined there so no signal can come from it.
        alpha[t], beta[t], sigma[t] = m0 - m1 * xbar, m1, (sd if sd > 1e-5 else math.nan)
        ph0 = p00 + p01 * h1
        ph1 = p01 + p11 * h1
        s_inn = ph0 + ph1 * h1 + var
        if outlier_z is not None and abs(e) > outlier_z * math.sqrt(s_inn) and skips < max_skips:
            skips += 1
        else:
            skips = 0
            k0, k1 = ph0 / s_inn, ph1 / s_inn
            m0 += k0 * e
            m1 += k1 * e
            p00 -= k0 * ph0
            p01 -= k0 * ph1
            p11 -= k1 * ph1
        if outlier_z is not None:  # a wick must not inflate sigma for the next `warmup` bars
            cap = outlier_z * sd
            e = cap if e > cap else (-cap if e < -cap else e)
        u[t] = e
        old = u[t - w]
        s2 += e * e - old * old
    return alpha, beta, sigma


def estimate_fits(
    log_a: np.ndarray, log_b: np.ndarray, lookback: int, kalman_delta: float | None = None,
    outlier_z: float | None = 4.0, max_skips: int = 48,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-bar (alpha, beta, sigma): rolling OLS (paper), or Kalman when ``kalman_delta`` is set.

    For Kalman pairs ``lookback`` is the warm-up and sigma window.
    """
    if kalman_delta is None:
        return rolling_ols(log_a, log_b, lookback)
    return kalman_filter(log_a, log_b, kalman_delta, lookback, outlier_z, max_skips)


def zscore(log_a: float, log_b: float, alpha: float, beta: float, sigma: float) -> float:
    """Z_t = (log P_A - alpha - beta log P_B) / sigma."""
    return (log_a - alpha - beta * log_b) / sigma


def executable_zscores(
    log_bid_a: float,
    log_ask_a: float,
    log_bid_b: float,
    log_ask_b: float,
    alpha: float,
    beta: float,
    sigma: float,
) -> tuple[float, float]:
    """Z-scores at executable prices, as the paper's live controller computes them.

    z_buy:  cost of buying the spread (buy A at ask, sell B at bid). Used for
            long entries and short exits.
    z_sell: proceeds of selling the spread (sell A at bid, buy B at ask). Used
            for short entries and long exits.
    For beta > 0, z_buy >= z_sell. With bid == ask both equal the mid Z-score.
    """
    z_buy = (log_ask_a - alpha - beta * log_bid_b) / sigma
    z_sell = (log_bid_a - alpha - beta * log_ask_b) / sigma
    return z_buy, z_sell


def ou_half_life(residuals: np.ndarray) -> float:
    """Half-life (in bars) of an AR(1) fitted to the residuals; inf if not mean-reverting.

    Not part of the paper; used only by optional overlays and diagnostics.
    """
    u = np.asarray(residuals, dtype=np.float64)
    u = u[np.isfinite(u)]
    if u.shape[0] < 10:
        return math.inf
    lag, diff = u[:-1], np.diff(u)
    lag_c = lag - lag.mean()
    denom = float(lag_c @ lag_c)
    if denom <= 0:
        return math.inf
    slope = float(lag_c @ (diff - diff.mean())) / denom
    phi = 1.0 + slope
    if not 0 < phi < 1:
        return math.inf
    return -math.log(2.0) / math.log(phi)

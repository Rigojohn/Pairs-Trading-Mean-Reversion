"""Statistics of a pair's relationship, for the dashboard's pair profile (numpy only).

Correlation of prices and of returns, the OLS hedge ratio, and the behaviour of the OLS
spread: half-life of mean reversion, Hurst exponent, Engle-Granger ADF t-statistic and
volatility. All on hourly closes.

These are diagnostics. The paper's strategy uses none of them as a filter (its final
version has no cointegration test), and the bot trades the Kalman spread, not this one.
"""

from __future__ import annotations

import math

import numpy as np

from cryptopairs.core.signals import ou_half_life

HOURS_PER_YEAR = 24 * 365
# Engle-Granger critical values, two variables with a constant (MacKinnon 2010, asymptotic).
EG_CRITICAL = {"1%": -3.90, "5%": -3.34, "10%": -3.04}
WINDOWS = {"30d": 30 * 24, "90d": 90 * 24, "1y": 365 * 24}


def ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """(alpha, beta) of y = alpha + beta * x."""
    xm, ym = x.mean(), y.mean()
    xc = x - xm
    beta = float(xc @ (y - ym) / (xc @ xc)) if xc @ xc > 0 else math.nan
    return float(ym - beta * xm), beta


def adf_tstat(u: np.ndarray, lags: int = 1) -> float:
    """t-statistic of gamma in  du_t = c + gamma u_{t-1} + sum_i phi_i du_{t-i} + e_t."""
    u = np.asarray(u, dtype=float)
    u = u[np.isfinite(u)]
    if u.size < 20 + lags:
        return math.nan
    du = np.diff(u)
    y = du[lags:]
    cols = [np.ones_like(y), u[lags:-1]]
    cols += [du[lags - i:-i] for i in range(1, lags + 1)]
    X = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = y.size - X.shape[1]
    if dof <= 0:
        return math.nan
    s2 = float(resid @ resid) / dof
    try:
        cov = s2 * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return math.nan
    se = math.sqrt(cov[1, 1]) if cov[1, 1] > 0 else math.nan
    return float(coef[1] / se) if se else math.nan


def hurst(u: np.ndarray, max_lag: int = 100) -> float:
    """Hurst exponent from the scaling of lagged differences: < 0.5 mean-reverting, 0.5 random walk."""
    u = np.asarray(u, dtype=float)
    u = u[np.isfinite(u)]
    lags = np.unique(np.geomspace(2, min(max_lag, u.size // 4), 20).astype(int)) if u.size >= 16 else np.empty(0)
    if lags.size < 4:
        return math.nan
    tau = np.array([np.std(u[k:] - u[:-k]) for k in lags])
    ok = tau > 0
    if ok.sum() < 4:
        return math.nan
    return float(np.polyfit(np.log(lags[ok]), np.log(tau[ok]), 1)[0])


def rolling_corr(x: np.ndarray, y: np.ndarray, window: int) -> np.ndarray:
    """Rolling Pearson correlation (NaN until the window is full)."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    out = np.full(x.shape[0], np.nan)
    if x.shape[0] < window or window < 3:
        return out
    cx, cy = np.cumsum(np.r_[0, x]), np.cumsum(np.r_[0, y])
    cxx, cyy, cxy = np.cumsum(np.r_[0, x * x]), np.cumsum(np.r_[0, y * y]), np.cumsum(np.r_[0, x * y])
    n = window
    sx, sy = cx[n:] - cx[:-n], cy[n:] - cy[:-n]
    sxx, syy, sxy = cxx[n:] - cxx[:-n], cyy[n:] - cyy[:-n], cxy[n:] - cxy[:-n]
    cov = sxy - sx * sy / n
    var = (sxx - sx * sx / n) * (syy - sy * sy / n)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[n - 1:] = np.where(var > 0, cov / np.sqrt(np.maximum(var, 1e-300)), np.nan)
    return out


def window_stats(log_a: np.ndarray, log_b: np.ndarray) -> dict:
    """Relationship statistics over one window of hourly log closes."""
    la, lb = np.asarray(log_a, dtype=float), np.asarray(log_b, dtype=float)
    ok = np.isfinite(la) & np.isfinite(lb)
    la, lb = la[ok], lb[ok]
    out = {"bars": int(la.size)}
    if la.size < 48:
        return out
    ra, rb = np.diff(la), np.diff(lb)
    alpha, beta = ols(la, lb)
    u = la - alpha - beta * lb
    t = adf_tstat(u)
    out.update(
        corr_price=float(np.corrcoef(la, lb)[0, 1]),
        corr_ret=float(np.corrcoef(ra, rb)[0, 1]),
        beta_ols=beta,
        half_life_h=ou_half_life(u),
        hurst=hurst(u),
        adf_t=t,
        adf_level=next((k for k, c in EG_CRITICAL.items() if math.isfinite(t) and t < c), None),
        spread_sd=float(u.std(ddof=1)),
        vol_a=float(ra.std(ddof=1) * math.sqrt(HOURS_PER_YEAR)),
        vol_b=float(rb.std(ddof=1) * math.sqrt(HOURS_PER_YEAR)),
    )
    return out


def relationship(log_a: np.ndarray, log_b: np.ndarray) -> dict[str, dict]:
    """``window_stats`` over the last 30 days, 90 days and year (when there is that much data)."""
    n = len(log_a)
    out = {name: window_stats(log_a[-w:], log_b[-w:]) for name, w in WINDOWS.items() if n >= w}
    if not out and n >= 48:  # a young listing: whatever history there is
        out["all"] = window_stats(log_a, log_b)
    return out


def funding_carry(funding_a: np.ndarray, funding_b: np.ndarray, beta: float, side: int, hours: int = 30 * 24) -> float:
    """Average funding paid per day per unit of gross notional, holding ``side`` over the last ``hours``.

    ``funding_*`` are the rates charged during each hourly bar (0 when no settlement). Longs
    pay a positive rate, shorts receive it; the notional splits 1/(1+beta) : beta/(1+beta).
    """
    if not beta > 0 or side not in (1, -1):
        return math.nan
    fa, fb = np.asarray(funding_a[-hours:], dtype=float), np.asarray(funding_b[-hours:], dtype=float)
    if fa.size == 0:
        return math.nan
    w_a, w_b = 1 / (1 + beta), beta / (1 + beta)
    per_bar = side * w_a * np.nan_to_num(fa) - side * w_b * np.nan_to_num(fb)
    return float(per_bar.mean() * 24)

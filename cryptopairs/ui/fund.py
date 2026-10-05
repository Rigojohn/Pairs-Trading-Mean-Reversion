"""Fund-level numbers for the Overview page.

NAV returns by period (local days and months), risk ratios, trade statistics, PnL attribution
(spread / fees / funding), daily PnL, book exposure, funding carry and market beta. Pure
functions over the frames ``ui/data.py`` loads, so the tests can check them.

Conventions: equity curves are pandas Series indexed by naive local time (``data.to_time``);
fees and funding are positive when paid; funding carry is positive when received.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

HOURS_PER_YEAR = 24 * 365


# ----------------------------------------------------------------- returns
def value_before(curve: pd.Series, t: pd.Timestamp, default: float) -> float:
    """NAV just before ``t`` (the last value strictly earlier); ``default`` (the starting capital)
    when the curve starts later. Days and months close on this value everywhere on the dashboard."""
    if curve.empty:
        return default
    past = curve[curve.index < t]
    return float(past.iloc[-1]) if len(past) else default


def period_returns(curve: pd.Series, capital: float, now: pd.Timestamp) -> pd.DataFrame:
    """PnL and return for today, yesterday, 7 and 30 days, month to date and since inception."""
    if curve.empty:
        return pd.DataFrame(columns=["period", "from", "PnL", "return %"])
    end = float(curve.iloc[-1])
    midnight = now.normalize()
    rows = []

    def add(name: str, t0: pd.Timestamp, v_end: float, base: float | None = None) -> None:
        start = curve.index[0] if t0 < curve.index[0] else t0
        v0 = value_before(curve, t0, capital) if base is None else base
        rows.append({"period": name, "from": start, "PnL": v_end - v0, "return %": 100 * (v_end / v0 - 1) if v0 else np.nan})

    add("Today", midnight, end)
    if curve.index[0] < midnight:
        add("Yesterday", midnight - pd.Timedelta(days=1), value_before(curve, midnight, capital))
    add("7 days", now - pd.Timedelta(days=7), end)
    add("30 days", now - pd.Timedelta(days=30), end)
    add("Month to date", midnight.replace(day=1), end)
    add("Since inception", curve.index[0], end, base=capital)
    return pd.DataFrame(rows)


def daily_pnl(curve: pd.Series, capital: float) -> pd.DataFrame:
    """PnL of each local calendar day (the first day against the starting capital)."""
    if curve.empty:
        return pd.DataFrame(columns=["day", "PnL", "NAV"])
    close = curve.resample("D").last().dropna()  # the last NAV of each day = value_before(next midnight)
    prev = close.shift(1)
    prev.iloc[0] = capital
    return pd.DataFrame({"day": close.index, "PnL": (close - prev).to_numpy(), "NAV": close.to_numpy()})


def risk_stats(hourly_equity: np.ndarray, curve: pd.Series | None = None) -> dict:
    """Annualised (sqrt 8760) Sharpe, Sortino and volatility from hourly bar-close equity, and drawdowns."""
    out = {"sharpe": math.nan, "sortino": math.nan, "vol": math.nan, "hours": 0, "max_dd": math.nan,
           "dd_now": math.nan}
    eq = np.asarray(hourly_equity, dtype=float)
    if eq.size > 2:
        r = np.diff(eq) / eq[:-1]
        r = r[np.isfinite(r)]
        out["hours"] = int(r.size)
        if r.size > 1:
            sd = r.std(ddof=1)
            down = math.sqrt(float(np.mean(np.minimum(r, 0.0) ** 2)))
            if sd > 0:
                out["vol"] = float(sd * math.sqrt(HOURS_PER_YEAR))
            if r.size >= 24:  # below a day of bars the ratios mean nothing
                if sd > 0:
                    out["sharpe"] = float(r.mean() / sd * math.sqrt(HOURS_PER_YEAR))
                if down > 0:
                    out["sortino"] = float(r.mean() / down * math.sqrt(HOURS_PER_YEAR))
    if curve is not None and len(curve) > 1:
        dd = curve / curve.cummax() - 1
        out["max_dd"], out["dd_now"] = float(dd.min()), float(dd.iloc[-1])
    return out


# ------------------------------------------------------------------ trades
def trade_stats(trades: pd.DataFrame) -> dict:
    """Closed round trips: count, hit rate, profit factor, average win / loss, holding time."""
    out = {"n": 0, "wins": 0, "hit": math.nan, "profit_factor": math.nan, "avg_win": math.nan, "avg_loss": math.nan,
           "expectancy": math.nan, "avg_hold_h": math.nan, "best": math.nan, "worst": math.nan, "manual": 0}
    if trades is None or trades.empty:
        return out
    c = trades[trades["exit_ts"].notna()]
    if c.empty:
        return out
    pnl = c["pnl"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    out.update(n=len(c), wins=len(wins), hit=len(wins) / len(c), expectancy=float(pnl.mean()),
               avg_win=float(wins.mean()) if len(wins) else math.nan,
               avg_loss=float(losses.mean()) if len(losses) else math.nan,
               profit_factor=float(wins.sum() / -losses.sum()) if losses.sum() < 0 else math.inf if len(wins) else math.nan,
               avg_hold_h=float(((c["exit_ts"] - c["entry_ts"]) / 3_600_000).mean()),
               best=float(pnl.max()), worst=float(pnl.min()),
               manual=int((c["entry_reason"] == "manual").sum()) if "entry_reason" in c else 0)
    return out


# ------------------------------------------------------------- attribution
def attribution(rows: pd.DataFrame) -> pd.DataFrame:
    """Per pair: total PnL = spread (closed + open) - fees - funding paid.

    ``rows`` is ``data.pair_rows``: PnL (equity - capital), unrealized, and the book's
    cumulative fees and funding paid since the pair started.
    """
    if rows.empty:
        return pd.DataFrame(columns=["pair", "spread closed", "spread open", "fees", "funding", "total"])
    total = rows["PnL"].astype(float)
    fees, funding = rows["fees"].astype(float), rows["funding"].astype(float)
    spread = total + fees + funding
    out = pd.DataFrame({"pair": rows["pair"], "spread closed": spread - rows["unrealized"].astype(float),
                        "spread open": rows["unrealized"].astype(float), "fees": -fees, "funding": -funding,
                        "total": total})
    return out.sort_values("total", ignore_index=True)


# ------------------------------------------------------------------- book
def book_summary(rows: pd.DataFrame, nav: float) -> dict:
    """Gross and net notional of the open book, as USDT and as a multiple of NAV."""
    open_ = rows[rows["side"] != 0]
    gross = float(open_["notional"].sum()) if len(open_) else 0.0
    net = 0.0
    for r in open_.to_dict("records"):
        for qty, px in ((r["qty A"], r["mark A"]), (r["qty B"], r["mark B"])):
            if np.isfinite(px):
                net += qty * px
    return {"gross": gross, "net": net, "gross_x": gross / nav if nav else math.nan,
            "net_x": net / nav if nav else math.nan, "open": len(open_), "pairs": len(rows)}


def funding_day(qty: float, px: float, inst: dict | None) -> float:
    """USDT per day one leg receives (+) or pays (-) at its current funding rate.

    Longs pay a positive rate and shorts a negative one; legs settle every 8 h, some every 4 h or 1 h."""
    rate = (inst or {}).get("funding_rate")
    if rate is None or not np.isfinite(px):
        return math.nan
    return -qty * px * float(rate) * 24.0 / float((inst or {}).get("funding_hours") or 8.0)


def funding_carry(rows: pd.DataFrame, insts: dict | None) -> pd.Series:
    """USDT per day each open pair receives (+) or pays (-) at the legs' current funding rates."""
    out = {}
    for r in rows[rows["side"] != 0].to_dict("records"):
        if not insts:
            out[r["pair"]] = math.nan
            continue
        out[r["pair"]] = (funding_day(r["qty A"], r["mark A"], insts.get(r["sym_a"]))
                          + funding_day(r["qty B"], r["mark B"], insts.get(r["sym_b"])))
    return pd.Series(out, dtype=float)


def market_beta(net_by_coin: pd.Series, betas: dict[str, float]) -> tuple[float, float]:
    """Dollar beta of the book to BTC (USDT gained per +1 USDT of BTC exposure) and its coverage.

    Returns (sum of net exposure x beta, share of gross exposure that had a beta).
    """
    if net_by_coin is None or net_by_coin.empty:
        return 0.0, 1.0
    known = [c for c in net_by_coin.index if c in betas and np.isfinite(betas[c])]
    dollar = float(sum(net_by_coin[c] * betas[c] for c in known))
    gross = float(net_by_coin.abs().sum())
    cover = float(net_by_coin[known].abs().sum() / gross) if gross else 1.0
    return dollar, cover


def beta_to(ret: np.ndarray, ret_mkt: np.ndarray) -> float:
    ok = np.isfinite(ret) & np.isfinite(ret_mkt)
    x, y = ret_mkt[ok], ret[ok]
    if x.size < 48 or x.var() <= 0:
        return math.nan
    return float(np.cov(y, x, ddof=1)[0, 1] / x.var(ddof=1))


def pnl_correlation(marks: pd.DataFrame, min_bars: int = 24) -> pd.DataFrame:
    """Correlation of hourly PnL between pairs that were in a position (from per-pair bar-close equity)."""
    if marks is None or marks.empty:
        return pd.DataFrame()
    eq = marks.pivot_table(index="bar_ts", columns="pair_id", values="equity", aggfunc="last").sort_index()
    pnl = eq.diff().iloc[1:]
    pnl = pnl.loc[:, pnl.abs().sum() > 0]  # pairs that never moved (flat throughout) have no correlation
    if pnl.shape[0] < min_bars or pnl.shape[1] < 2:
        return pd.DataFrame()
    return pnl.corr()


# --------------------------------------------------------- NAV and drawdowns
MINUTE, DAY = 60_000, 24 * 3_600_000
# Timeframe of the performance chart -> bar size. Every timeframe shows the whole history,
# from the start of paper trading; the timeframe only sets how much time one point covers.
TIMEFRAMES = {"1m": MINUTE, "5m": 5 * MINUTE, "15m": 15 * MINUTE, "1H": 3_600_000, "4H": 4 * 3_600_000, "1D": DAY}
MAX_BARS = 50_000  # beyond this a finer timeframe is coarsened (the chart stays fast)
LIVE_TOL = 90_000  # live marks come every 5 s (one a minute once older than a day): longer = the bot was down
HOURLY_TOL = 2 * 3_600_000 + MINUTE  # bar-close NAV, where no live marks are left


def nav_points(hourly: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    """Every NAV observation, by time: live marks and bar closes (at the close), with how long each
    one may stand for before the line counts as broken (the bot not running)."""
    parts = []
    if live is not None and not live.empty:
        parts.append(pd.DataFrame({"ts": live["ts"].astype("int64"), "nav": live["equity"].astype(float),
                                   "tol": LIVE_TOL}))
    if hourly is not None and not hourly.empty:
        parts.append(pd.DataFrame({"ts": (hourly["bar_ts"] + 3_600_000).astype("int64"),
                                   "nav": hourly["equity"].astype(float), "tol": HOURLY_TOL}))
    if not parts:
        return pd.DataFrame(columns=["ts", "nav", "tol"])
    p = pd.concat(parts, ignore_index=True).sort_values(["ts", "tol"], kind="stable")
    return p.drop_duplicates("ts", keep="first").reset_index(drop=True)  # a live mark wins over a bar close


def default_timeframe(points: pd.DataFrame, now_ms: int, target_bars: int = 1500) -> str:
    """The finest timeframe that shows the whole history in at most ``target_bars`` points."""
    span = now_ms - int(points["ts"].iloc[0]) if len(points) else 0
    return next((k for k, v in TIMEFRAMES.items() if span / v <= target_bars), "1D")


def timeframe_window(points: pd.DataFrame, tf: str, now_ms: int) -> tuple[int, int, int]:
    """(start, end, bar size) in ms: from the first NAV observation to now, in bars of ``tf``."""
    start = int(points["ts"].iloc[0]) if len(points) else now_ms
    step = max(TIMEFRAMES[tf], -(-(now_ms - start) // MAX_BARS))
    return start, now_ms, int(step)


def nav_grid(points: pd.DataFrame, start_ms: int, end_ms: int, step_ms: int, offset_ms: int = 0,
             capital: float | None = None) -> pd.DataFrame:
    """NAV on an even grid (the last observation at or before each bar time), NaN where the bot was down.

    Bars are labelled at their end; the last bar is "now" and moves with the latest mark.
    ``offset_ms`` aligns the bars to local time (the zone's UTC offset), so 4-hour and daily bars
    close on local hours and midnights. Two more columns come from every observation, not only
    the bar closes, so they are the same at any timeframe: ``hwm``, the high-water mark at the
    bar's end (``capital`` at least), and ``dd``, the deepest drawdown (%) inside the bar.
    """
    if points.empty:
        return pd.DataFrame(columns=["t", "nav", "hwm", "dd"])
    first = max(int(start_ms), int(points["ts"].iloc[0]))
    grid = np.arange((first + offset_ms) // step_ms * step_ms - offset_ms + step_ms, end_ms, step_ms, dtype=np.int64)
    grid = np.append(grid, np.int64(end_ms)) if not grid.size or grid[-1] < end_ms else grid
    ts, nav, tol = (points[c].to_numpy() for c in ("ts", "nav", "tol"))
    i = np.searchsorted(ts, grid, side="right") - 1
    ok = i >= 0
    j = np.where(ok, i, 0)
    stale = grid - ts[j] > np.maximum(tol[j], 1.5 * step_ms)  # silent longer than this source allows
    live = ok & ~stale
    v = np.where(live, nav[j], np.nan)
    peak = np.maximum.accumulate(np.maximum(nav, capital if capital is not None else -np.inf))
    obs_dd = 100 * (nav / peak - 1)  # every observation's drawdown from the peak reached so far
    bar = np.searchsorted(grid, ts, side="left")  # the bar each observation falls in
    worst = pd.Series(obs_dd).groupby(bar).min()
    dd = np.where(live, obs_dd[j], np.nan)  # a bar without observations keeps the last one's value
    inside = worst.index.to_numpy()
    keep = inside < grid.size
    dd[inside[keep]] = np.where(live[inside[keep]], worst.to_numpy()[keep], np.nan)
    return pd.DataFrame({"t": grid, "nav": v, "hwm": np.where(live, peak[j], np.nan), "dd": dd})


def high_water(points: pd.DataFrame, before_ms: int, default: float) -> float:
    """Highest NAV observed before ``before_ms`` (``default``, the starting capital, at least)."""
    past = points.loc[points["ts"] < before_ms, "nav"]
    return max(default, float(past.max())) if len(past) else default


def underwater(nav: np.ndarray, peak_before: float) -> tuple[np.ndarray, np.ndarray]:
    """(high-water mark, drawdown %) along a NAV path, continuing the peak reached before it."""
    v = np.asarray(nav, dtype=float)
    hwm = np.fmax.accumulate(np.where(np.isfinite(v), v, -np.inf))
    hwm = np.maximum(hwm, peak_before)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd = np.where(np.isfinite(v), 100 * (v / hwm - 1), np.nan)
    return hwm, dd


def range_max_drawdown(points: pd.DataFrame, start_ms: int, end_ms: int, peak_before: float) -> float:
    """Deepest drawdown (%) inside a range, from every observation, continuing the earlier peak."""
    p = points[(points["ts"] >= start_ms) & (points["ts"] <= end_ms)]
    if p.empty:
        return 0.0
    _, dd = underwater(p["nav"].to_numpy(dtype=float), peak_before)
    return float(min(0.0, np.nanmin(dd))) if np.isfinite(dd).any() else 0.0


def drawdown_episodes(points: pd.DataFrame, capital: float, min_depth_pct: float = 0.1) -> pd.DataFrame:
    """Every fall from a high-water mark and its recovery, deepest first.

    Columns: peak (ms), trough (ms), recovered (ms or NaN while still under water), depth %,
    days from peak to trough, and to recovery (or until now).
    """
    cols = ["peak", "trough", "recovered", "depth %", "fall h", "length h", "peak NAV"]
    if points.empty:
        return pd.DataFrame(columns=cols)
    ts, nav = points["ts"].to_numpy(dtype=np.int64), points["nav"].to_numpy(dtype=float)
    out = []
    peak_v, peak_t = capital, int(ts[0])
    cur = None  # the open episode
    for t, v in zip(ts, nav):
        if v >= peak_v:
            if cur is not None:
                cur["recovered"] = int(t)
                out.append(cur)
                cur = None
            peak_v, peak_t = v, int(t)
            continue
        depth = 100 * (v / peak_v - 1)
        if cur is None:
            cur = {"peak": peak_t, "trough": int(t), "depth %": depth, "peak NAV": peak_v, "recovered": np.nan}
        elif depth < cur["depth %"]:
            cur["trough"], cur["depth %"] = int(t), depth
    if cur is not None:
        out.append(cur)
    df = pd.DataFrame(out, columns=[c for c in cols if c not in ("fall h", "length h")])
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df[df["depth %"] <= -min_depth_pct].copy()
    end = df["recovered"].fillna(int(ts[-1]))
    df["fall h"] = (df["trough"] - df["peak"]) / 3_600_000
    df["length h"] = (end - df["peak"]) / 3_600_000
    return df.sort_values("depth %", ignore_index=True)[cols]

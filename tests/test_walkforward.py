"""Walk-forward of bot B's recipe (research/walkforward.py): the Sortino score, month-end segments that
chain into one continuous run, causality, the portfolio's books, and the leakage test of the selection."""

import math

import numpy as np
import pandas as pd
import pytest

from conftest import make_pair
from cryptopairs.config import CostConfig, StrategyConfig, load_config
from cryptopairs.core.signals import kalman_filter
from cryptopairs.core.strategy import PairParams
from cryptopairs.research.backtest import HOURS_PER_YEAR, sortino
from cryptopairs.research.calibrate import select_best
from cryptopairs.research.data import DataStore, PairData
from cryptopairs.research.intrabar import EntryRule, Minutes, run_intrabar
from cryptopairs.research.walkforward import (Portfolio, _csv_roundtrip, fold_config, full_book_folds, month_folds,
                                              select_fold, truncated_copy)

H, M = 3_600_000, 60_000
T0 = 1_780_000_000_000 // H * H
SIGMA = 0.01
NB = 400
START, END = T0 + 12 * H, T0 + (NB - 1) * H
PARAMS = PairParams(lookback=10, z_entry=2.0, z_exit=0.0, kalman_delta=None)
CFG = StrategyConfig(max_entry_z=10.0, catastrophe_stop_pct=0.35, min_edge_cost_multiple=1.5, cooldown_hours=6)
COSTS = CostConfig(taker_fee=5e-4, half_spread_bps=5, slippage_bps=5, fill_on="next_open", maker_legs=1,
                   maker_fee=2e-4, maker_adverse_bps=1.0)
HOURLY = EntryRule()  # bot B: entries at the hourly close, exits on minute closes


# ---------------------------------------------------------------------------- scores
def test_sortino_counts_every_hour_and_only_losses_below():
    r = np.array([0.01, -0.02, 0.03, 0.0])
    assert sortino(r) == pytest.approx(0.005 / 0.01 * math.sqrt(HOURS_PER_YEAR))
    assert math.isnan(sortino(np.array([0.01, 0.0, 0.02])))  # no losing hour


def test_sortino_dd_takes_the_best_rank_sum_and_breaks_ties_by_sortino():
    t = pd.DataFrame([
        {"z_entry": 1.0, "Sortino": 3.0, "MaxDD": -0.20, "Sharpe": 2.5, "Trades": 10},  # ranks 1 + 3
        {"z_entry": 1.5, "Sortino": 2.0, "MaxDD": -0.05, "Sharpe": 1.5, "Trades": 10},  # ranks 2 + 1: best
        {"z_entry": 2.0, "Sortino": 1.0, "MaxDD": -0.10, "Sharpe": 1.0, "Trades": 10},  # ranks 3 + 2
        {"z_entry": 3.0, "Sortino": 9.0, "MaxDD": -0.01, "Sharpe": 9.0, "Trades": 2},  # too few trades
    ])
    assert select_best(t, "sortino_dd", min_trades=5)["z_entry"] == 1.5
    assert select_best(t, "argmax", min_trades=5)["z_entry"] == 1.0  # today's rule: the best Sharpe
    tie = pd.DataFrame([
        {"z_entry": 1.0, "Sortino": 1.0, "MaxDD": -0.05, "Sharpe": 1.0, "Trades": 9},  # ranks 2 + 1
        {"z_entry": 2.0, "Sortino": 2.0, "MaxDD": -0.10, "Sharpe": 1.0, "Trades": 9},  # ranks 1 + 2: higher Sortino
    ])
    assert select_best(tie, "sortino_dd", min_trades=5)["z_entry"] == 2.0


def test_month_folds_cover_whole_months():
    f = month_folds("2022-10", "2022-12")
    assert [x.month for x in f] == ["2022-10", "2022-11", "2022-12"]
    assert pd.Timestamp(f[0].start_ms, unit="ms") == pd.Timestamp("2022-10-01")
    assert all(a.end_ms == b.start_ms for a, b in zip(f, f[1:]))


def test_the_full_book_starts_at_the_first_full_month(tmp_path):
    f = month_folds("2023-01", "2023-05")
    for fold, n in zip(f, [0, 3, 5, 3, 5]):
        (tmp_path / "folds" / fold.month).mkdir(parents=True)
        pd.DataFrame({"a": ["X"] * n, "b": ["Y"] * n}).to_csv(tmp_path / "folds" / fold.month / "chosen_sharpe.csv",
                                                              index=False)
    assert [x.month for x in full_book_folds(f, tmp_path, "sharpe")] == ["2023-03", "2023-04", "2023-05"]


# ------------------------------------------------------------------- the B replay
def _market(seed: int = 3) -> tuple[PairData, Minutes]:
    """A wandering spread on minute bars (B = 1, A = exp(Z * sigma)): entries, exits and stops happen."""
    rng = np.random.default_rng(seed)
    z = np.cumsum(rng.normal(0, 0.12, NB * 60))
    z = 2.0 * (z - pd.Series(z).rolling(600, min_periods=1).mean().to_numpy())
    ts = T0 + M * np.arange(z.shape[0], dtype=np.int64)
    close_a = np.exp(z * SIGMA)
    open_a = np.concatenate(([close_a[0]], close_a[:-1]))
    one = np.ones(z.shape[0])
    mins = Minutes(ts=ts, open_a=open_a, high_a=np.maximum(open_a, close_a), low_a=np.minimum(open_a, close_a),
                   close_a=close_a, open_b=one, high_b=one, low_b=one, close_b=one)
    g = lambda x: x.reshape(NB, 60)  # noqa: E731
    data = PairData(a="A", b="B", ts=T0 + H * np.arange(NB, dtype=np.int64), open_a=g(open_a)[:, 0],
                    high_a=g(mins.high_a).max(1), low_a=g(mins.low_a).min(1), close_a=g(close_a)[:, -1],
                    open_b=np.ones(NB), high_b=np.ones(NB), low_b=np.ones(NB), close_b=np.ones(NB),
                    funding_a=np.zeros(NB), funding_b=np.zeros(NB))
    return data, mins


def _fits():
    return np.zeros(NB), np.ones(NB), np.full(NB, SIGMA)


def _run(data, mins, start=START, end=END, equity=1000.0, **kw):
    return run_intrabar(data, mins, PARAMS, CFG, COSTS, start, end, equity, HOURLY, fits=_fits(), **kw)


def _key(trades):
    return [(t["entry_ts"], t.get("exit_ts"), t.get("reason"), round(t["pnl"], 9) if "pnl" in t else None)
            for t in trades]


def test_without_a_month_end_the_replay_is_unchanged():
    data, mins = _market()
    ref = _run(data, mins)
    got = _run(data, mins, entries_until_ms=END + 10 * H, cooldown_until=-1, deactivation_base=1000.0)
    assert len(ref.trades) >= 5
    np.testing.assert_array_equal(got.equity, ref.equity)
    assert _key(got.trades) == _key(ref.trades)


def test_no_entry_fills_after_the_month_end_and_an_open_position_runs_to_its_exit():
    data, mins = _market()
    ref = _run(data, mins)
    # a month end that falls inside a position of the unrestricted run
    t = next(t for t in ref.trades if "exit_ts" in t and t["exit_ts"] - t["entry_ts"] >= 4 * H)
    until = (t["entry_ts"] // H + 2) * H
    got = _run(data, mins, entries_until_ms=until)
    assert all(x["entry_ts"] < until for x in got.trades)
    assert _key(got.trades) == _key([x for x in ref.trades if x["entry_ts"] < until])
    assert got.trades[-1]["exit_ts"] == t["exit_ts"]  # managed to its exit after the month end
    n = got.equity.shape[0]
    assert data.ts[0] + H * 0 <= got.ts[-1] and got.ts[-1] >= t["exit_ts"] // H * H
    np.testing.assert_array_equal(got.equity, ref.equity[:n])  # the same path, cut at the first flat bar


def test_two_segments_chain_into_the_continuous_run():
    """Same parameters on both sides of a month end that falls inside a position: segment 1 runs to
    the exit, segment 2 starts on the next bar with segment 1's equity and cooldown."""
    data, mins = _market()
    ref = _run(data, mins)
    for t in [t for t in ref.trades if "exit_ts" in t and t["exit_ts"] - t["entry_ts"] >= 4 * H][:3]:
        until = (t["entry_ts"] // H + 2) * H
        s1 = _run(data, mins, entries_until_ms=until)
        nxt = int(s1.ts[-1]) + H
        s2 = _run(data, mins, start=nxt, equity=float(s1.equity[-1]), cooldown_until=s1.cooldown_until,
                  deactivation_base=1000.0)
        assert _key(s1.trades + s2.trades) == _key(ref.trades)
        np.testing.assert_allclose(np.concatenate([s1.equity, s2.equity]), ref.equity, rtol=1e-12)
        np.testing.assert_array_equal(np.concatenate([s1.ts, s2.ts]), ref.ts)


def test_later_prices_do_not_change_earlier_decisions():
    data, mins = _market()
    ref = _run(data, mins)
    cut = T0 + 250 * H
    rng = np.random.default_rng(11)
    later_h, later_m = data.ts >= cut, mins.ts >= cut
    f_h, f_m = np.exp(rng.normal(0, 0.05, later_h.sum())), np.exp(rng.normal(0, 0.05, later_m.sum()))
    for arr in (data.open_a, data.high_a, data.low_a, data.close_a):
        arr[later_h] *= f_h
    for arr in (mins.open_a, mins.high_a, mins.low_a, mins.close_a):
        arr[later_m] *= f_m
    data.log_a = np.log(data.close_a)
    got = _run(data, mins)
    early = ref.ts < cut - H
    np.testing.assert_array_equal(got.equity[early], ref.equity[early])
    done = lambda tr: [x for x in tr if "exit_ts" in x and x["exit_ts"] < cut - H]  # noqa: E731
    assert _key(done(got.trades)) == _key(done(ref.trades))


def test_the_kalman_filter_is_causal():
    rng = np.random.default_rng(5)
    lb = np.cumsum(rng.normal(0, 0.01, 3000))
    la = 0.1 + 1.2 * lb + rng.normal(0, 0.01, 3000)
    ref = kalman_filter(la, lb, 1e-6, warmup=200)
    la2 = la.copy()
    la2[2000:] += rng.normal(0, 0.3, 1000)
    got = kalman_filter(la2, lb, 1e-6, warmup=200)
    for r, g in zip(ref, got):
        np.testing.assert_array_equal(g[:2001], r[:2001])  # element t is the prior: it never sees bar t


def test_portfolio_books_each_segment_once_and_keeps_its_result():
    p = Portfolio(T0, T0 + 9 * H, 1000.0)
    p.add(np.array([T0 + 2 * H, T0 + 3 * H, T0 + 4 * H]), np.array([100.0, 104.0, 103.0]), 100.0)
    p.add(np.array([T0 + 4 * H, T0 + 6 * H]), np.array([10.0, 8.0]), 10.0)  # bar 5 missing: carried forward
    want = 1000.0 + np.array([0, 0, 0, 4, 3, 3, 1, 1, 1, 1])
    np.testing.assert_allclose(p.equity(), want)
    assert p.value_at(T0) == 1000.0 and p.value_at(T0 + 4 * H) == 1004.0  # the close of the bar 3h-4h


# ----------------------------------------------------------- leakage of the selection
def _synthetic_store(root, n_hours: int = 70 * 24) -> None:
    store = DataStore(root, "1h")
    for k, seed in enumerate((1, 2, 3)):
        ka, kb = make_pair(n=n_hours, seed=seed, start_ms=T0)
        for sym, kl in ((f"A{k}USDT", ka), (f"B{k}USDT", kb)):
            store.kline_path(sym).parent.mkdir(parents=True, exist_ok=True)
            kl.to_pickle(store.kline_path(sym))
            fu = pd.DataFrame({"ts": kl["ts"].to_numpy()[7::8] + H, "rate": np.full(len(kl["ts"].to_numpy()[7::8]), 1e-4)})
            store.funding_path(sym).parent.mkdir(parents=True, exist_ok=True)
            fu.to_pickle(store.funding_path(sym))
    (root / "meta").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"symbol": [], "coingecko_id": [], "chains": []}).to_csv(root / "meta" / "chains.csv", index=False)


def _small_cfg():
    cfg = load_config(None, None)
    rc = cfg.research
    rc.funnel_days, rc.calibration_days, rc.min_history_days = 40, 30, 45
    rc.same_chain, rc.min_corr_logp = False, 0.5
    rc.ic_lookbacks, rc.ic_horizons, rc.top_n = [24, 48], [1, 6, 24], 10
    rc.grid_lookback, rc.grid_z_entry, rc.grid_z_exit = [48], [1.0, 2.0], [0.0]
    rc.grid_kalman_delta, rc.min_train_trades, rc.workers = [1e-5, 1e-4], 1, 1
    cfg.strategy.estimator, cfg.strategy.kalman_warmup, cfg.strategy.cooldown_hours = "kalman", 48, 24
    cfg.strategy.leverage = 2.0
    return cfg


def _selection(cfg, root, start, end):
    res = select_fold(fold_config(cfg, start, end, root), None)
    return _csv_roundtrip(res["funnel"]), {k: _csv_roundtrip(v) for k, v in res["chosen"].items()}


def test_the_selection_is_identical_without_the_future(tmp_path):
    src = tmp_path / "data"
    _synthetic_store(src)
    cfg = _small_cfg()
    start = T0 + 60 * 24 * H
    end = start + 10 * 24 * H
    f_ref, c_ref = _selection(cfg, src, start, end)
    assert len(f_ref) >= 2 and all(len(c) >= 2 for c in c_ref.values())

    # 1. the future deleted
    cut = tmp_path / "cut"
    truncated_copy(src, cut, start - H)
    f_cut, c_cut = _selection(cfg, cut, start, end)
    assert f_cut.equals(f_ref)
    assert all(c_cut[k].equals(c_ref[k]) for k in c_ref)

    # 2. the future replaced by noise
    noisy = tmp_path / "noisy"
    truncated_copy(src, noisy, end + 100 * H)
    rng = np.random.default_rng(0)
    st = DataStore(noisy, "1h")
    for sym in st.symbols():
        kl = st.load_klines(sym)
        later = kl["ts"] >= start
        f = np.exp(rng.normal(0, 0.2, int(later.sum())))
        for c in ("open", "high", "low", "close"):
            kl.loc[later, c] *= f
        kl.to_pickle(st.kline_path(sym))
    f_noisy, c_noisy = _selection(cfg, noisy, start, end)
    assert f_noisy.equals(f_ref)
    assert all(c_noisy[k].equals(c_ref[k]) for k in c_ref)

    # 3. the check can fail: one training day less does change the result
    short = tmp_path / "short"
    truncated_copy(src, short, start - 25 * H)
    rng2 = np.random.default_rng(1)
    st2 = DataStore(short, "1h")
    for sym in st2.symbols():  # fill the last day again with other prices
        kl = st2.load_klines(sym)
        last = kl.iloc[-1]
        extra = pd.DataFrame({"ts": last["ts"] + H * np.arange(1, 25)})
        px = last["close"] * np.exp(np.cumsum(rng2.normal(0, 0.03, 24)))
        extra = extra.assign(open=px, high=px * 1.002, low=px * 0.998, close=px, quote_volume=1e6)
        pd.concat([kl, extra], ignore_index=True).to_pickle(st2.kline_path(sym))
    f_short, c_short = _selection(cfg, short, start, end)
    assert not (f_short.equals(f_ref) and all(c_short[k].equals(c_ref[k]) for k in c_ref))

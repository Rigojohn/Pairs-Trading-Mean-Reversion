import math

import numpy as np
import pytest

from cryptopairs.config import StrategyConfig
from cryptopairs.core.book import PairBook
from cryptopairs.core.signals import executable_zscores, fit_ols, ou_half_life, rolling_ols, zscore
from cryptopairs.core.sizing import (
    LotRules, SizingError, hedge_weights, round_to_lots, signed_quantities, target_quantities,
)
from cryptopairs.core.strategy import FLAT, LONG, SHORT, PairParams, PairStrategy
from tests.conftest import HOUR, ou_spread


# ------------------------------------------------------------------ signals
def test_rolling_ols_matches_single_window_fit():
    rng = np.random.default_rng(3)
    lb = np.cumsum(rng.normal(0, 0.01, 2000)) + 2.0
    la = 0.3 + 1.2 * lb + ou_spread(2000, seed=3)
    L = 240
    a, b, s = rolling_ols(la, lb, L)
    assert np.isnan(a[: L - 1]).all() and np.isfinite(a[L - 1 :]).all()
    for t in (L - 1, 777, 1999):
        fa, fb, fs = fit_ols(la[t - L + 1 : t + 1], lb[t - L + 1 : t + 1])
        assert a[t] == pytest.approx(fa, abs=1e-9)
        assert b[t] == pytest.approx(fb, abs=1e-9)
        assert s[t] == pytest.approx(fs, rel=1e-8)
        resid = la[t - L + 1 : t + 1] - fa - fb * lb[t - L + 1 : t + 1]
        assert fs == pytest.approx(np.std(resid, ddof=1), rel=1e-10)


def test_degenerate_window_gives_nan():
    a, b, s = rolling_ols(np.ones(50), np.ones(50), 10)
    assert np.isnan(b).all()
    assert all(math.isnan(v) for v in fit_ols(np.ones(10), np.ones(10)))


def test_executable_zscores():
    assert executable_zscores(1.0, 1.0, 0.5, 0.5, 0.1, 1.2, 0.02) == pytest.approx(
        (zscore(1.0, 0.5, 0.1, 1.2, 0.02),) * 2)
    zb, zs = executable_zscores(math.log(0.999), math.log(1.001), math.log(1.999), math.log(2.001), 0.0, 1.0, 0.01)
    assert zb > zs  # buying the spread costs more than selling it


def test_half_life_of_ar1():
    hl = ou_half_life(ou_spread(20000, phi=0.97, seed=1))
    assert hl == pytest.approx(-math.log(2) / math.log(0.97), rel=0.3)
    assert ou_half_life(np.cumsum(np.ones(100))) == math.inf


# ------------------------------------------------------------------- sizing
def test_target_quantities_notional_ratio():
    qa, qb = target_quantities(1000.0, 5.0, 1.5, 20.0, 4.0)
    assert qa * 20.0 + qb * 4.0 == pytest.approx(5000.0)
    assert (qb * 4.0) / (qa * 20.0) == pytest.approx(1.5)
    assert signed_quantities(LONG, qa, qb) == (qa, -qb)
    assert signed_quantities(SHORT, qa, qb) == (-qa, qb)


def test_negative_beta_is_rejected():
    with pytest.raises(SizingError):
        hedge_weights(-0.8)


def test_round_to_lots():
    ra, rb = round_to_lots(10.37, 3.06, 2.0, 7.0, LotRules(0.1, 0.1, 5.0), LotRules(0.01, 0.01, 5.0), beta=1.03)
    assert ra == pytest.approx(10.3) and rb == pytest.approx(3.06)
    with pytest.raises(SizingError, match="notional"):
        round_to_lots(1.0, 1.0, 2.0, 2.0, LotRules(0.1, 0, 5.0), LotRules(0.1, 0, 5.0), beta=1.0)
    with pytest.raises(SizingError, match="hedge ratio"):  # coarse lot destroys the 1:beta hedge
        round_to_lots(10.0, 1.4, 1.0, 10.0, LotRules(0.1), LotRules(1.0), beta=1.4)


def test_book_pnl_and_funding():
    book = PairBook(cash=1000.0)
    book.open(10.0, -5.0, 100.0, 50.0, fee=1.0)
    assert book.equity(110.0, 50.0) == pytest.approx(999.0 + 100.0)
    paid = book.apply_funding(0.001, 100.0, 0.001, 50.0)  # long pays 1.0, short receives 0.25
    assert paid == pytest.approx(0.75)
    assert book.close(110.0, 40.0, fee=1.0) == pytest.approx(999.0 + 100.0 + 50.0 - 0.75 - 1.0)
    assert not book.in_position


# ----------------------------------------------------------------- strategy
def _strat(**cfg_kw) -> PairStrategy:
    cfg = StrategyConfig(**cfg_kw)
    s = PairStrategy(PairParams(100, 1.5, 0.0), cfg, 1000.0)
    s.set_fit(0.0, 1.0, 0.01, 0)
    return s


def test_entry_and_exit_rules():
    s = _strat()
    assert s.check_entry(0, 0, -1.4, -1.4) == FLAT
    assert s.check_entry(0, 0, -1.6, -1.6) == LONG
    assert s.check_entry(0, 0, 1.6, 1.6) == SHORT
    s.on_opened(LONG, 0, 0, 1000.0, -1.6, -1.6)
    assert s.check_exit(HOUR, 1, 1000.0, -0.5, -0.5) is None
    assert s.check_exit(2 * HOUR, 2, 1000.0, 0.1, 0.1) == "signal"


def test_short_exit_uses_buy_side_z():
    s = _strat()
    s.on_opened(SHORT, 0, 0, 1000.0, 1.7, 1.6)
    assert s.check_exit(HOUR, 1, 1000.0, z_buy=0.2, z_sell=-0.1) is None  # must pay the ask to exit
    assert s.check_exit(2 * HOUR, 2, 1000.0, z_buy=-0.05, z_sell=-0.2) == "signal"


def test_stop_loss_starts_cooldown():
    s = _strat(cooldown_hours=10)
    s.on_opened(LONG, 0, 0, 1000.0, -2.0, -2.0)
    assert s.stop_level() == pytest.approx(800.0)
    assert s.check_exit(HOUR, 1, 799.0, -3.0, -3.0) == "stop_loss"
    s.on_closed("stop_loss", HOUR)
    s.set_fit(0.0, 1.0, 0.01, 2)
    assert s.check_entry(2 * HOUR, 2, -3.0, -3.0) == FLAT  # cooling down
    s.set_fit(0.0, 1.0, 0.01, 11)
    assert s.check_entry(11 * HOUR, 11, -3.0, -3.0) == LONG


@pytest.mark.parametrize("mode,equities,expect", [
    ("peak_drawdown", [1000, 1300, 1050], False),
    ("peak_drawdown", [1000, 1300, 1030], True),   # 20.8% below the 1300 peak
    ("equity_floor", [1000, 400, 300], False),
    ("equity_floor", [1000, 400, 199], True),      # Algorithm 1: E < 0.2 * E0
])
def test_deactivation_modes(mode, equities, expect):
    s = _strat(deactivation_mode=mode)
    for e in equities:
        s.observe_equity(e)
    assert s.deactivated is expect
    assert (s.check_entry(0, 0, -3.0, -3.0) == FLAT) is expect


def test_peak_drawdown_must_exceed_stop_loss():
    with pytest.raises(ValueError, match="first stop-loss"):
        StrategyConfig(deactivation_mode="peak_drawdown", deactivation_threshold=0.2).validate()
    StrategyConfig(deactivation_mode="peak_drawdown", deactivation_threshold=0.4).validate()


def test_beta_guard_blocks_negative_hedge():
    s = _strat()
    s.set_fit(0.0, -0.8, 0.01, 0)
    assert s.check_entry(0, 0, -5.0, -5.0) == FLAT


def test_refit_schedule_and_freshness():
    s = PairStrategy(PairParams(100, 1.5, 0.0), StrategyConfig(refit_every=24), 1000.0, anchor=10)
    assert s.wants_refit(10) and s.wants_refit(34) and not s.wants_refit(11)
    s.set_fit(0.0, 1.0, 0.01, 10)
    assert s.fit_is_fresh(33) and not s.fit_is_fresh(34)


def test_cost_gate():
    s = PairStrategy(PairParams(100, 1.0, 0.0), StrategyConfig(min_edge_cost_multiple=1.5), 1000.0,
                     round_trip_cost=0.003)
    s.set_fit(0.0, 1.0, 0.004, 0)  # edge at z=-1.1: 0.004/2*1.1 = 0.0022 < 1.5*0.003
    assert s.check_entry(0, 0, -1.1, -1.1) == FLAT
    assert s.check_entry(0, 0, -2.5, -2.5) == LONG  # 0.005 >= 0.0045


def test_funding_filters():
    hourly = 1e-5  # ~ 0.008% per 8h
    s = PairStrategy(PairParams(100, 1.5, 0.0), StrategyConfig(funding_filter="short_positive"), 1000.0)
    s.set_fit(0.0, 1.0, 0.02, 0)
    assert s.check_entry(0, 0, -2.0, -2.0, funding_a=hourly, funding_b=-hourly) == FLAT  # would short B, B negative
    assert s.check_entry(0, 0, -2.0, -2.0, funding_a=-hourly, funding_b=hourly) == LONG
    assert s.check_entry(0, 0, 2.0, 2.0, funding_a=hourly, funding_b=-hourly) == SHORT   # shorts A, A positive
    assert s.check_entry(0, 0, -2.0, -2.0) == LONG  # unknown funding: no filtering

    # net_cost: edge 0.02/2*(2-0)=0.02; paying 1e-4/h on both legs for 120h costs 0.012 + 0.012... per weights
    cfg = StrategyConfig(funding_filter="net_cost", funding_hold_hours=120)
    s = PairStrategy(PairParams(100, 1.5, 0.0), cfg, 1000.0)
    s.set_fit(0.0, 1.0, 0.02, 0)
    assert s.check_entry(0, 0, -2.0, -2.0, funding_a=4e-4, funding_b=-4e-4) == FLAT   # long A pays, short B pays
    assert s.check_entry(0, 0, -2.0, -2.0, funding_a=-4e-4, funding_b=4e-4) == LONG   # both legs receive
    spot = PairStrategy(PairParams(100, 1.5, 0.0), cfg, 1000.0, long_pays_funding=False)
    spot.set_fit(0.0, 1.0, 0.02, 0)
    assert spot.check_entry(0, 0, -2.0, -2.0, funding_a=4e-4, funding_b=0.0) == LONG  # spot long pays nothing


def test_z_stop_and_half_life_time_stop():
    cfg = StrategyConfig(z_stop_offset=2.0, stop_loss_pct=0.0, time_stop_halflives=3.0)
    s = PairStrategy(PairParams(100, 1.5, 0.0), cfg, 1000.0)
    s.set_fit(0.0, 1.0, 0.01, 0)
    s.on_opened(LONG, 0, 0, 1000.0, -1.6, -1.6, half_life=10.0)
    assert s.hold_limit == 30
    assert s.check_exit(HOUR, 1, 100.0, -3.4, -3.4) is None        # equity stop disabled, z above -3.5
    assert s.check_exit(2 * HOUR, 2, 1000.0, -3.4, -3.6) == "stop_z"  # exit-side z beyond -(1.5 + 2)
    s.on_closed("stop_z", 2 * HOUR)
    assert s.cooldown_until > 2 * HOUR and s.hold_limit == -1

    s = PairStrategy(PairParams(100, 1.5, 0.0), cfg, 1000.0)
    s.set_fit(0.0, 1.0, 0.01, 0)
    s.on_opened(SHORT, 0, 0, 1000.0, 1.6, 1.6, half_life=2.0)  # 3 x 2 = 6 bars, floored at 24
    assert s.check_exit(23 * HOUR, 23, 1000.0, 1.0, 1.0) is None
    assert s.check_exit(24 * HOUR, 24, 1000.0, 1.0, 1.0) == "time_stop"
    snap = s.snapshot()
    assert snap["hold_limit"] == 24


def test_smoothed_selection_prefers_a_robust_plateau():
    import pandas as pd
    from cryptopairs.research.calibrate import select_best

    rows = []
    for L in (100, 200, 300):
        for ze in (1.0, 2.0, 3.0):
            sharpe = 1.0 if L == 300 else 0.0   # plateau at L=300
            if (L, ze) == (100, 1.0):
                sharpe = 3.0                    # isolated spike
            rows.append({"lookback": L, "z_entry": ze, "z_exit": 0.0, "Sharpe": sharpe, "Trades": 10})
    t = pd.DataFrame(rows)
    assert select_best(t, "argmax")["lookback"] == 100
    assert select_best(t, "smoothed")["lookback"] == 300


# ------------------------------------------------------------------- Kalman
def _coint(n=6000, beta=1.1, seed=11, beta2=None, switch=None):
    rng = np.random.default_rng(seed)
    lb = np.cumsum(rng.normal(0, 0.01, n)) + 2.0
    b = np.full(n, beta)
    if beta2 is not None:
        b[switch:] = beta2
    la = 0.3 + b * lb + ou_spread(n, seed=seed, vol=0.005)
    return la, lb


def test_kalman_recovers_constant_beta_and_warmup_contract():
    from cryptopairs.core.signals import kalman_filter

    la, lb = _coint()
    a, b, s = kalman_filter(la, lb, delta=1e-6, warmup=720)
    assert np.isnan(a[:720]).all() and np.isfinite(a[720:]).all() and np.isfinite(s[720:]).all()
    assert b[-1] == pytest.approx(1.1, abs=0.05)
    z = (la - a - b * lb) / s
    assert 0.5 < np.nanstd(z) < 2.0  # sigma is on the scale of the spread


def test_kalman_tracks_a_beta_change_faster_than_long_ols():
    from cryptopairs.core.signals import kalman_filter

    la, lb = _coint(n=9000, beta=1.0, beta2=1.4, switch=6000)
    _, bk, _ = kalman_filter(la, lb, delta=1e-5, warmup=720)
    _, bo, _ = rolling_ols(la, lb, 4320)
    t = 6000 + 24 * 30  # one month after the change
    assert abs(bk[t] - 1.4) < abs(bo[t] - 1.4)


def test_kalman_value_at_t_does_not_use_bar_t():
    from cryptopairs.core.signals import kalman_filter

    la, lb = _coint(n=3000)
    a1, b1, s1 = kalman_filter(la, lb, 1e-6, 720)
    la2 = la.copy()
    la2[2000] += 0.5
    a2, b2, s2 = kalman_filter(la2, lb, 1e-6, 720)
    for x, y in ((a1, a2), (b1, b2), (s1, s2)):
        assert np.array_equal(x[:2001], y[:2001], equal_nan=True)  # up to and including bar 2000
        assert not np.array_equal(x[2001:], y[2001:], equal_nan=True)  # bar 2000 is used from 2001 on


def test_kalman_ignores_an_outlier_spike():
    from cryptopairs.core.signals import kalman_filter

    la, lb = _coint(n=3000)
    la2 = la.copy()
    la2[2000] += 1.0  # a crash wick: +100% in log terms for one bar
    _, b_robust, _ = kalman_filter(la2, lb, 1e-5, 720, outlier_z=4.0)
    _, b_naive, _ = kalman_filter(la2, lb, 1e-5, 720, outlier_z=None)
    _, b_clean, _ = kalman_filter(la, lb, 1e-5, 720, outlier_z=4.0)
    _, _, s_robust = kalman_filter(la2, lb, 1e-5, 720, outlier_z=4.0)
    _, _, s_clean = kalman_filter(la, lb, 1e-5, 720, outlier_z=4.0)
    for t in (2001, 2200):
        assert abs(b_robust[t] - b_clean[t]) < 0.1 * abs(b_naive[t] - b_clean[t])
    assert s_robust[2100] < 1.5 * s_clean[2100]  # the wick did not blow up sigma


def test_backtest_and_params_dispatch_to_kalman():
    from cryptopairs.config import CostConfig
    from cryptopairs.core.signals import estimate_fits, kalman_filter
    from cryptopairs.research.backtest import run_backtest
    from cryptopairs.research.data import make_pair_data
    from tests.conftest import make_pair

    ka, kb = make_pair(n=3000, seed=4)
    data = make_pair_data("AUSDT", "BUSDT", ka, kb)
    direct = kalman_filter(data.log_a, data.log_b, 1e-6, 720)
    via = estimate_fits(data.log_a, data.log_b, 720, 1e-6)
    assert all(np.array_equal(x, y, equal_nan=True) for x, y in zip(direct, via))
    p = PairParams(720, 1.5, 0.0, kalman_delta=1e-6)
    res = run_backtest(data, p, StrategyConfig(), CostConfig(), int(data.ts[720]), int(data.ts[-1]), 1e4)
    assert len(res.trades) > 3 and res.equity[-1] > 0
    with pytest.raises(ValueError):
        PairParams(720, 1.5, 0.0, kalman_delta=0.0)


def test_kalman_sigma_does_not_collapse_on_a_persistent_divergence():
    """Regression (2026-10-01): clipped one-sided innovations made sigma -> 0 and |Z| explode (ALICE/T Z=37)."""
    from cryptopairs.core.signals import kalman_filter

    la, lb = _coint(n=6000)
    la = la.copy()
    la[3000:] += 0.5  # the relationship breaks: a permanent 50% shift in the spread
    a, b, s = kalman_filter(la, lb, 1e-7, 720, outlier_z=4.0)
    z = (la - a - b * lb) / s
    s_before = s[2999]
    assert s[-1] > s_before  # the divergence raises sigma ...
    assert np.median(np.abs(z[-720:])) < 4.0  # ... so Z does not stay at absurd levels
    assert np.nanmin(s[3000:]) > 0.5 * s_before  # and sigma never collapses


def test_frozen_price_and_extreme_z_never_trigger_entries():
    from cryptopairs.core.signals import kalman_filter

    _, lb = _coint(n=3000)
    la = 0.3 + 1.1 * lb  # a spread that never moves: sigma would be ~0 and Z infinite
    _, _, s = kalman_filter(la, lb, 1e-6, 720)
    assert np.isnan(s).all()  # no sigma, so no Z and no entries

    st = PairStrategy(PairParams(100, 1.5, 0.0), StrategyConfig(max_entry_z=10.0), 1000.0)
    st.set_fit(0.0, 1.0, 0.01, 0)
    assert st.check_entry(0, 0, -37.0, -37.0) == FLAT
    assert st.check_entry(0, 0, -3.0, -3.0) == LONG

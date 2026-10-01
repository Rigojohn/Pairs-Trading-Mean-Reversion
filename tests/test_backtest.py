import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cryptopairs.config import CostConfig, StrategyConfig
from cryptopairs.core.strategy import PairParams
from cryptopairs.research.backtest import run_backtest
from cryptopairs.research.data import make_pair_data
from tests.conftest import HOUR, T0, make_pair

LEGACY = Path(__file__).resolve().parents[1] / "legacy"


def _pair(**kw):
    ka, kb = make_pair(**kw)
    return make_pair_data("AUSDT", "BUSDT", ka, kb), ka, kb


def _legacy_backtester():
    pytest.importorskip("statsmodels")
    if not (LEGACY / "trade.py").exists():
        pytest.skip("legacy code not present")
    sys.path.insert(0, str(LEGACY))
    try:
        import trade  # noqa: PLC0415
        import utils  # noqa: PLC0415
    finally:
        sys.path.remove(str(LEGACY))
    # The legacy fit runs an ADF test on every refit even with cointegration off;
    # it never affects trades in that mode, so skip it to keep the test fast.
    utils.adf_pvalue = lambda series: 0.0
    return trade


@pytest.mark.parametrize("L,R,ze,zx,cool", [(240, 24, 1.5, 0.0, 5.0), (168, 24, 1.0, 0.5, 0.0), (300, 1, 2.0, 0.0, 2.0)])
def test_parity_with_legacy_backtester(L, R, ze, zx, cool):
    """With legacy-compatible settings the new engine reproduces the old one bar for bar."""
    trade = _legacy_backtester()
    data, ka, kb = _pair(n=2600, seed=7, break_at=1800)
    merged = pd.DataFrame({
        "open_time": pd.to_datetime(ka["ts"], unit="ms"),
        "close_1": ka["close"], "close_2": kb["close"],
        "log_close_1": np.log(ka["close"]), "log_close_2": np.log(kb["close"]),
    })
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bt = trade.CointBacktester(
            trade.Exchange(merged), merged["open_time"].iloc[0], merged["open_time"].iloc[-1],
            train_len=L, reset_len=R, require_cointegration=False, entry_k=ze, exit_k=zx,
            stop_loss_pct=0.20, stop_loss_cooling_days=cool, initial_balance=100_000.0,
            trade_frac=1.0, margin_rate=0.2, fee_rate=5e-4, deactivate_equity_ratio=0.2,
        )
        daily, _ = bt.run()

    cfg = StrategyConfig(leverage=5.0, refit_every=R, allow_same_bar_reentry=True, stop_loss_pct=0.20,
                         cooldown_hours=cool * 24, deactivation_mode="equity_floor",
                         deactivation_threshold=0.2, beta_min=1e-9, beta_max=1e9)
    res = run_backtest(data, PairParams(L, ze, zx), cfg, CostConfig(taker_fee=5e-4),
                       int(data.ts[L]), int(data.ts[-1]), 100_000.0)

    legacy_eq = daily["pnl"].to_numpy()[L:]
    assert res.equity.shape == legacy_eq.shape
    np.testing.assert_allclose(res.equity, legacy_eq, rtol=1e-8)
    legacy_entries = pd.to_datetime(daily.loc[daily["entry_flag"], "time"]).dt.as_unit("ms").astype("int64")
    assert [t["entry_ts"] for t in res.trades] == legacy_entries.tolist()
    assert len(res.trades) >= 5  # the scenario must actually trade
    assert any(t.get("reason") == "stop_loss" for t in res.trades)  # and exercise the stop


def test_negative_beta_pair_is_not_traded():
    """Legacy bug: abs(beta) hedged the wrong way and bled money. The new core refuses to trade."""
    data, _, _ = _pair(n=2000, beta=-0.8, seed=2)
    res = run_backtest(data, PairParams(240, 1.5, 0.0), StrategyConfig(), CostConfig(),
                       int(data.ts[240]), int(data.ts[-1]), 10_000.0)
    assert res.trades == []
    assert res.equity[-1] == pytest.approx(10_000.0)


def test_costs_reduce_pnl():
    data, _, _ = _pair(n=3000, seed=4)
    args = (data, PairParams(240, 1.5, 0.0), StrategyConfig())
    win = (int(data.ts[240]), int(data.ts[-1]), 10_000.0)
    free = run_backtest(*args, CostConfig(taker_fee=0.0), *win)
    paper = run_backtest(*args, CostConfig(), *win)
    hard = run_backtest(*args, CostConfig(half_spread_bps=5, slippage_bps=5, fill_on="next_open"), *win)
    assert len(free.trades) > 5
    assert free.equity[-1] > paper.equity[-1] > hard.equity[-1]
    assert paper.fees > 0


def test_funding_is_charged_to_the_long_leg():
    ka, kb = make_pair(n=1500, seed=5)
    fund_a = pd.DataFrame({"ts": ka["ts"] + HOUR, "rate": 0.0002})  # every hour, A longs pay
    data = make_pair_data("AUSDT", "BUSDT", ka, kb, fund_a, None)
    res = run_backtest(data, PairParams(240, 1.5, 0.0), StrategyConfig(), CostConfig(funding=True),
                       int(data.ts[240]), int(data.ts[-1]), 10_000.0)
    closed = [t for t in res.trades if "funding" in t]
    longs = [t for t in closed if t["side"] == 1]
    shorts = [t for t in closed if t["side"] == -1]
    assert longs and shorts
    assert all(t["funding"] > 0 for t in longs)   # long A pays
    assert all(t["funding"] < 0 for t in shorts)  # short A receives


def test_intrabar_stop_uses_the_wick():
    ka, kb = make_pair(n=1200, seed=6, wick=0.0)
    data_plain = make_pair_data("AUSDT", "BUSDT", ka, kb)
    base = run_backtest(data_plain, PairParams(240, 1.5, 0.0), StrategyConfig(), CostConfig(),
                        int(data_plain.ts[240]), int(data_plain.ts[-1]), 10_000.0)
    first = base.trades[0]
    i = int(np.searchsorted(data_plain.ts, first["entry_ts"])) + 1
    # A violent wick against the position on the next bar, recovering by the close.
    ka2 = ka.copy()
    if first["side"] == 1:
        ka2.loc[i, "low"] = ka2.loc[i, "close"] * 0.5
    else:
        ka2.loc[i, "high"] = ka2.loc[i, "close"] * 1.5
    data_wick = make_pair_data("AUSDT", "BUSDT", ka2, kb)
    plain = run_backtest(data_wick, PairParams(240, 1.5, 0.0), StrategyConfig(), CostConfig(),
                         int(data_wick.ts[240]), int(data_wick.ts[-1]), 10_000.0)
    hard = run_backtest(data_wick, PairParams(240, 1.5, 0.0), StrategyConfig(), CostConfig(intrabar_stop=True),
                        int(data_wick.ts[240]), int(data_wick.ts[-1]), 10_000.0)
    assert plain.trades[0].get("reason") != "stop_loss_intrabar"
    assert hard.trades[0]["reason"] == "stop_loss_intrabar"
    assert hard.trades[0]["exit_ts"] == int(data_wick.ts[i])


def test_catastrophe_stop_exits_at_its_level():
    ka, kb = make_pair(n=1200, seed=6, wick=0.0)
    base = make_pair_data("AUSDT", "BUSDT", ka, kb)
    args = (PairParams(240, 1.5, 0.0),)
    first = run_backtest(base, *args, StrategyConfig(), CostConfig(), int(base.ts[240]), int(base.ts[-1]), 1e4).trades[0]
    i = int(np.searchsorted(base.ts, first["entry_ts"])) + 1
    ka2 = ka.copy()
    if first["side"] == 1:
        ka2.loc[i, "low"] = ka2.loc[i, "close"] * 0.5
    else:
        ka2.loc[i, "high"] = ka2.loc[i, "close"] * 1.5
    data = make_pair_data("AUSDT", "BUSDT", ka2, kb)
    cfg = StrategyConfig(catastrophe_stop_pct=0.35)
    t = run_backtest(data, *args, cfg, CostConfig(), int(data.ts[240]), int(data.ts[-1]), 1e4).trades[0]
    assert t["reason"] == "stop_catastrophe" and t["exit_ts"] == int(data.ts[i])
    entry_equity = t["equity_before"] - (t["fees"] - t["fees"] / 2)  # roughly equity after entry fees
    assert t["equity_after"] == pytest.approx(0.65 * entry_equity, rel=0.01)  # exits at the level, not the wick


def test_position_closed_when_data_ends_early():
    data, _, _ = _pair(n=1500, seed=8)
    end_beyond = int(data.ts[-1]) + 500 * HOUR  # contract "delisted" before the window ends
    res = run_backtest(data, PairParams(240, 0.8, 0.0), StrategyConfig(), CostConfig(),
                       int(data.ts[240]), end_beyond, 10_000.0)
    assert res.trades and res.trades[-1].get("exit_ts") is not None


def test_window_needs_warmup():
    data, _, _ = _pair(n=300, seed=9)
    res = run_backtest(data, PairParams(240, 1.5, 0.0), StrategyConfig(), CostConfig(), T0, T0 + 299 * HOUR, 1.0)
    assert res.ts[0] == data.ts[239]  # first bar with a full 240-bar window

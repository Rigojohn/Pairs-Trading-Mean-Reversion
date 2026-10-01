import numpy as np
import pandas as pd

from cryptopairs.config import ResearchConfig
from cryptopairs.research.data import DataStore
from cryptopairs.research.funnel import information_coefficient, run_funnel
from tests.conftest import HOUR, T0, _klines, ou_spread


def test_ic_is_negative_for_mean_reverting_spread_and_zero_for_noise():
    rng = np.random.default_rng(0)
    lb = np.cumsum(rng.normal(0, 0.01, 6000))
    la = 1.1 * lb + ou_spread(6000, seed=0)
    ic = information_coefficient(la, lb, 720, [1, 24, 72])
    assert ic[24] < -0.15 and ic[72] < ic[1]

    noise_a = np.cumsum(rng.normal(0, 0.01, 6000))
    ic0 = information_coefficient(noise_a, lb, 720, [24])
    assert abs(ic0[24]) < abs(ic[24]) / 2


def _write(store: DataStore, symbol: str, ts: np.ndarray, logp: np.ndarray) -> None:
    path = store.kline_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    _klines(ts, np.exp(logp), 0.001).to_pickle(path)


def test_funnel_end_to_end(tmp_path):
    n = 24 * 1200
    ts = T0 + HOUR * np.arange(n, dtype=np.int64)
    rng = np.random.default_rng(1)
    market = np.cumsum(rng.normal(0, 0.008, n))
    store = DataStore(tmp_path)
    # AAA/BBB: cointegrated on the same chain. CCC: same chain, unrelated.
    # DDD: tied to AAA but on another chain. EEE: listed too late (Filter 1).
    _write(store, "AAAUSDT", ts, 2.0 + market + ou_spread(n, seed=2, vol=0.004))
    _write(store, "BBBUSDT", ts, 1.0 + market)
    _write(store, "CCCUSDT", ts, 3.0 + np.cumsum(rng.normal(0, 0.01, n)))
    _write(store, "DDDUSDT", ts, 0.5 + market + ou_spread(n, seed=3, vol=0.004))
    _write(store, "EEEUSDT", ts[-24 * 300 :], (1.5 + market)[-24 * 300 :])

    cfg = ResearchConfig(
        test_start=str(pd.Timestamp(int(ts[-24 * 60]), unit="ms")),
        test_end=str(pd.Timestamp(int(ts[-1]), unit="ms")),
        funnel_days=600, min_history_days=1000, ic_lookbacks=[168, 720], ic_horizons=[1, 24],
    )
    chains = {"AAAUSDT": {"ethereum"}, "BBBUSDT": {"ethereum", "base"}, "CCCUSDT": {"ethereum"},
              "DDDUSDT": {"solana"}, "EEEUSDT": {"ethereum"}}
    res = run_funnel(store, cfg, chains)

    assert res.counts["filter1_symbols"] == 4  # EEE dropped
    assert res.counts["filter2_pairs"] == 3    # AAA-BBB, AAA-CCC, BBB-CCC (DDD is on solana)
    pairs = {tuple(sorted((r.a, r.b))) for r in res.table.itertuples()}
    assert pairs == {("AAAUSDT", "BBBUSDT")}
    row = res.table.iloc[0]
    assert row.best_ic < -0.1 and row.chains == "ethereum"


def test_select_pairs_caps_coins():
    from cryptopairs.research.funnel import select_pairs

    t = pd.DataFrame({"a": ["X", "X", "X", "Y"], "b": ["A", "B", "C", "Z"], "best_ic": [-0.5, -0.4, -0.3, -0.2]})
    assert len(select_pairs(t, 10)) == 4
    capped = select_pairs(t, 10, max_per_symbol=2)
    assert list(capped["b"]) == ["A", "B", "Z"]  # the third X pair is skipped

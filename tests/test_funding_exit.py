"""Funding exit: close when the funding over the next hours outweighs what the position still earns."""

import math

import pytest

from cryptopairs.config import StrategyConfig
from cryptopairs.core.strategy import LONG, SHORT, PairParams, PairStrategy
from cryptopairs.research.experiments import VARIANTS
from tests.conftest import T0

RT = 0.002  # round trip cost, per unit gross notional


def _open(mode: str, side: int = SHORT, half_life: float = 40.0, **cfg) -> PairStrategy:
    s = PairStrategy(PairParams(100, 2.0, 0.0), StrategyConfig(funding_exit=mode, **cfg), 100.0, round_trip_cost=RT)
    s.set_fit(0.0, 1.0, 0.05, 10)
    s.on_opened(side, T0, 10, 100.0, 2.0, 2.0, half_life)
    return s


def test_off_never_exits():
    assert not _open("off").check_funding_exit(100.0, 0.5, 0.5, 0.01, 0.01)


def test_a_position_that_earns_funding_is_never_closed():
    s = _open("edge", side=SHORT)  # short A: A's positive rate is received
    assert not s.check_funding_exit(100.0, 0.5, 0.5, 0.001, 0.0)


def test_edge_closes_a_winner_close_to_its_exit_and_keeps_a_loser_far_from_it():
    # SHORT, beta 1: the position pays when B (long) has a positive rate. 0.0005/h x 8 h x 0.5 = 0.2%.
    near = _open("edge")
    far = _open("edge")
    assert near.check_funding_exit(110.0, 0.2, 0.2, 0.0, 0.0005)  # Z 0.2 from the exit: earns < 0.2% + 0.1%
    assert not far.check_funding_exit(90.0, 3.0, 3.0, 0.0, 0.0005)  # Z 3 away: earns ~0.4% over 8 h


def test_edge_needs_a_finite_half_life():
    assert not _open("edge", half_life=math.inf).check_funding_exit(100.0, 0.2, 0.2, 0.0, 0.0005)


def test_a_long_position_measures_the_distance_to_its_own_exit():
    s = _open("edge", side=LONG)  # long A pays A's positive rate
    assert s.check_funding_exit(100.0, -0.1, -0.1, 0.0005, 0.0)
    assert not s.check_funding_exit(100.0, -3.0, -3.0, 0.0005, 0.0)


def test_profit_only_closes_winners_and_keeps_losers():
    s = _open("profit_only")
    assert s.check_funding_exit(101.0, 3.0, 3.0, 0.0, 0.0005)  # in profit and paying more than the exit costs
    assert not s.check_funding_exit(99.0, 0.2, 0.2, 0.0, 0.0005)  # a loser is held however much it pays
    assert not s.check_funding_exit(101.0, 3.0, 3.0, 0.0, 0.00005)  # funding below the closing cost


def test_unknown_funding_does_nothing():
    assert not _open("edge").check_funding_exit(100.0, 0.2, 0.2, math.nan, 0.0005)


def test_a_funding_exit_starts_a_short_cooldown():
    s = _open("edge", funding_exit_cooldown_hours=24)
    s.on_closed("funding_exit", T0 + 3_600_000)
    assert s.cooldown_until == T0 + 25 * 3_600_000


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        StrategyConfig(funding_exit="sometimes").validate()


def test_variants_change_only_the_exit():
    k1, f1, f2 = (VARIANTS[n] for n in ("K1 Kalman filter", "F1 funding exit: expected earnings vs funding",
                                        "F2 funding exit: winners only"))
    assert f1["calibrate_with"] == f2["calibrate_with"] == k1["strategy"]
    assert f1["strategy"]["funding_exit"] == "edge" and f2["strategy"]["funding_exit"] == "profit_only"

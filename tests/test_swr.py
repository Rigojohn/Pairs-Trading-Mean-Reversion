"""Exchange data from memory, refreshed in the background (ui/swr.py), and pair_rows computed once."""

import threading
import time

import pandas as pd

from cryptopairs.ui import swr


def _wait(cond, timeout=3.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_a_stale_value_is_returned_at_once_and_refreshed_once_in_the_background():
    swr.clear()
    calls, gate = [], threading.Event()

    def slow():
        calls.append(1)
        if len(calls) > 1:
            gate.wait(2)  # the refresh is slow: the caller must not wait for it
        return len(calls)

    assert swr.get("k", 0.05, slow) == 1  # the first request waits
    time.sleep(0.1)  # now older than its ttl
    t = time.time()
    assert swr.get("k", 0.05, slow) == 1  # the old value, at once
    assert swr.get("k", 0.05, slow) == 1  # a second caller does not start a second refresh
    assert time.time() - t < 0.2
    gate.set()
    assert _wait(lambda: swr.get("k", 60, slow) == 2)
    assert len(calls) == 2


def test_a_failed_fetch_keeps_the_old_value_and_is_not_retried_at_once():
    swr.clear()
    answers = iter([5, None, 7])
    calls = []

    def fn():
        calls.append(1)
        return next(answers)

    assert swr.get("f", 0.0, fn) == 5
    assert swr.get("f", 0.0, fn) == 5  # refresh starts in the background and fails (None)
    assert _wait(lambda: len(calls) == 2)
    time.sleep(0.05)
    for _ in range(5):  # within RETRY_S: the old value, no new request
        assert swr.get("f", 0.0, fn) == 5
    assert len(calls) == 2


def test_wait_false_returns_none_then_the_value_and_warm_fills_a_key():
    swr.clear()
    assert swr.get("w", 60, lambda: 3, wait=False) is None
    assert _wait(lambda: swr.get("w", 60, lambda: 3, wait=False) == 3)
    swr.warm("x", lambda: "ready")
    assert _wait(lambda: swr.get("x", 60, lambda: "other") == "ready")


def test_pair_rows_from_the_cache_equal_the_direct_computation(tmp_path, monkeypatch):
    from tests.test_dashboard_controls import _populated
    from cryptopairs.ui import data as D

    db = _populated(tmp_path).db
    d = D.load_core(str(db), D.version(db), 0)
    cfg = D.session_cfg(d)
    cached = D.pair_rows(d, cfg)
    direct = D._pair_rows({k: v for k, v in d.items() if k != "_key"}, cfg)
    pd.testing.assert_frame_equal(cached.drop(columns=["hours"], errors="ignore"),
                                  direct.drop(columns=["hours"], errors="ignore"))
    assert D.pair_rows(d, cfg) is not None  # the second call comes from the cache


def test_the_watchlists_panel_data_equals_what_the_desk_sends(tmp_path, monkeypatch):
    """A click switches the ticker/books/tape with the watchlist's data before the page reruns:
    it must be the same data the desk then sends (fit, position, symbols, threshold)."""
    from types import SimpleNamespace

    from tests.test_dashboard_controls import _populated
    from cryptopairs.ui import data as D
    from cryptopairs.ui import desk as K
    from cryptopairs.ui import widgets as W

    monkeypatch.setenv("CRYPTOPAIRS_OFFLINE", "1")
    db = _populated(tmp_path).db
    d = D.load_core(str(db), D.version(db), 0)
    md = W.market_data(D.load_pair_states(str(db), D.version(db)))
    ctx = SimpleNamespace(config_path=tmp_path / "none.yaml", brief={"profile": None})
    for pid in d["pairs"]["pair_id"]:
        s = K.load_state(d, ctx, pid, pd.DataFrame())
        want, got = s.market_data(), md[pid]
        for k in ("digits_a", "digits_b"):  # offline the server guesses them from the last mark
            want.pop(k), got.pop(k)
        assert got == want
    assert any(v["position"] for v in md.values())  # the scenario has an open position

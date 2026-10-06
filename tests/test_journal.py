"""The trade journal (ui/data.journal): exit Z, exit reasons, open trades."""

import math

import numpy as np
import pandas as pd

from cryptopairs.ui import data as D

H = 3_600_000
T0 = 1_790_000_000_000 // H * H


def _trade(i, pair, side, entry, exit_=None, reason=None, px=(1.0, 1.0), pnl=None, entry_reason="signal"):
    return {"id": i, "pair_id": pair, "session_id": "s", "side": side, "entry_ts": entry, "exit_ts": exit_,
            "reason": reason, "z_entry": -2.0 * side, "alpha": 0.1, "beta": 0.8, "sigma": 0.05, "expected_ret": 0.0,
            "qty_a": 1.0, "qty_b": -1.0, "entry_px_a": 1.0, "entry_px_b": 1.0, "exit_px_a": px[0] if exit_ else None,
            "exit_px_b": px[1] if exit_ else None, "equity_before": 100.0, "equity_after": None, "pnl": pnl,
            "fees": 0.2, "funding": -0.05 if exit_ else None, "entry_reason": entry_reason}


def test_journal_reasons_exit_z_and_open_trades():
    trades = pd.DataFrame([
        _trade(1, "A_B", 1, T0, T0 + 5 * H, "signal", (1.2, 1.1), 3.0),                # hourly exit
        _trade(2, "A_B", -1, T0 + 6 * H, T0 + 7 * H + 1234, "signal", (1.0, 1.0), -1.0),  # instant exit
        _trade(3, "C_D", 1, T0 + 2 * H, T0 + 3 * H + 99, "stop_loss", (1.0, 1.3), -20.0),
        _trade(4, "C_D", -1, T0 + 8 * H, entry_reason="manual"),                          # open
    ])
    events = pd.DataFrame({"ts": [T0 + 7 * H + 1234], "pair_id": ["A_B"], "kind": ["realtime_exit"]})
    rows = pd.DataFrame({"pair": ["C_D"], "side": [-1], "unrealized": [1.5], "Z now": [0.7]})
    j = D.journal({"trades": trades, "events": events}, rows).set_index("id")

    assert list(j.index) == [4, 2, 3, 1]  # newest entry first
    assert j.loc[1, "exit reason"] == "exit signal · hourly"
    assert j.loc[2, "exit reason"] == "exit signal · instant"
    assert j.loc[3, "exit reason"] == "stop-loss"
    want = (math.log(1.2) - 0.1 - 0.8 * math.log(1.1)) / 0.05
    assert abs(j.loc[1, "Z exit / now"] - want) < 1e-9
    assert j.loc[1, "fees"] == -0.2 and j.loc[1, "funding"] == 0.05  # + received, − paid
    assert j.loc[1, "PnL %"] == 3.0 and j.loc[1, "hours"] == 5.0
    o = j.loc[4]
    assert o["status"] == "open" and o["exit reason"] == "open" and o["origin"] == "manual"
    assert o["PnL"] == 1.5 and o["Z exit / now"] == 0.7 and pd.isna(o["exit"])


def test_journal_of_a_bot_without_trades_is_empty():
    j = D.journal({"trades": pd.DataFrame(), "events": pd.DataFrame()}, pd.DataFrame())
    assert j.empty and "exit reason" in j.columns

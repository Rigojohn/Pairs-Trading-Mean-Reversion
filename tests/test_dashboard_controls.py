"""Live views, manual controls (commands) and the real-time dashboard."""

import asyncio
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cryptopairs.config import AppConfig
from cryptopairs.core.strategy import FLAT, LONG, PairParams
from cryptopairs.runtime.commands import CommandProcessor
from cryptopairs.runtime.risk import RiskManager
from cryptopairs.runtime.store import StateStore, submit_command
from tests.conftest import HOUR
from tests.test_persistence import L, Rig, open_then_crash, spec

APP = Path(__file__).resolve().parents[1] / "cryptopairs" / "dashboard.py"


# ------------------------------------------------------------- engine side
def test_paused_pair_does_not_enter_but_still_exits(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        eng, feed, store = rig.engine([spec()])
        eng.set_paused("AAA_BBB", True)
        await rig.step(eng, feed, rig.bar)  # a clear entry signal ...
        assert eng.pairs[0].strat.side == FLAT  # ... is ignored while paused
        eng.set_paused("AAA_BBB", False)
        await rig.step(eng, feed, rig.bar)
        assert eng.pairs[0].strat.side == LONG
        eng.set_entries_paused(True)  # a global pause never blocks exits
        bar2 = rig.add_bar(+0.03)
        await rig.step(eng, feed, bar2)
        assert eng.pairs[0].strat.side == FLAT
        store.close()

    asyncio.run(go())
    store = StateStore(rig.db)
    assert store.kv_get("entries_paused") is True and store.kv_get("paused_pairs") == []


def test_commands_from_the_dashboard(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)  # the pair holds a long position

    async def go():
        eng, feed, store = rig.engine([spec()])
        await feed.backfill()
        rig.quote(feed)
        risk = RiskManager(eng.cfg.risk)
        risk.kill_switch = True
        proc = CommandProcessor(store, eng, risk, max_age_s=120)
        now = feed.now_ms()
        ids = [submit_command(rig.db, "pause_pair", "AAA_BBB"),
               submit_command(rig.db, "close_pair", "AAA_BBB"),
               submit_command(rig.db, "close_pair", "NOPE"),
               submit_command(rig.db, "reset_kill_switch")]
        store.conn.execute("UPDATE commands SET ts=?", (now,))  # the fake clock vs wall clock
        old = submit_command(rig.db, "flatten_all")
        store.conn.execute("UPDATE commands SET ts=? WHERE id=?", (now - 600_000, old))  # clicked 10 min ago
        assert await proc.poll() == 5
        res = {r["id"]: r for r in store.query("SELECT * FROM commands")}
        assert [res[i]["status"] for i in ids] == ["done", "done", "rejected", "done"]
        assert res[old]["status"] == "rejected" and "expired" in res[old]["result"]
        assert eng.pairs[0].strat.side == FLAT and "AAA_BBB" in eng.paused_pairs
        assert risk.kill_switch is False and store.kv_get("risk.kill_switch") is False
        trades = store.query("SELECT reason FROM trades")
        assert trades[-1]["reason"] == "manual_close"
        assert (await eng.close_pair("AAA_BBB"))[0] is False  # nothing left to close

    asyncio.run(go())


def test_param_override_on_a_flat_pair_survives_restarts_until_recalibration(tmp_path):
    rig = Rig(tmp_path)

    async def boot(file_z_entry=2.0):
        eng, feed, store = rig.engine([spec(z_entry=file_z_entry)])
        await feed.backfill()
        rig.quote(feed)
        return eng, feed, store

    async def go():
        eng, feed, store = await boot()
        ok, msg = await eng.set_params("AAA_BBB", PairParams(L - 10, 8.0, 0.5))
        assert ok and "in force now" in msg and "refitted" in msg
        pr = eng.pairs[0]
        assert pr.strat.params == PairParams(L - 10, 8.0, 0.5) and pr.strat.fit is not None
        await rig.step(eng, feed, rig.bar)  # |Z| ~ 6: an entry at 2.0, but not at 8.0
        assert pr.strat.side == FLAT
        assert store.load_pairs()["AAA_BBB"].z_entry == 8.0
        store.close()

        eng, feed, store = await boot()  # restart with the same pairs file: the override stays
        assert eng.pairs[0].spec.params.z_entry == 8.0
        assert store.kv_get("param_overrides")["AAA_BBB"]["base"]["z_entry"] == 2.0
        ok, msg = await eng.set_params("AAA_BBB", None)  # back to the calibration
        assert ok and eng.pairs[0].strat.params == PairParams(L, 2.0, 0.0) and store.kv_get("param_overrides") == {}
        assert (await eng.set_params("AAA_BBB", PairParams(L, 2.0, 0.0, 1e-6)))[0] is False  # OLS -> Kalman
        assert (await eng.set_params("AAA_BBB", PairParams(L, 3.0, 0.5)))[0] is True
        store.close()

        eng, feed, store = await boot(file_z_entry=2.5)  # recalibrated file: it supersedes the override
        assert eng.pairs[0].spec.params == PairParams(L, 2.5, 0.0) and store.kv_get("param_overrides") == {}
        store.close()

    asyncio.run(go())


def test_param_override_in_a_position_waits_until_it_closes(tmp_path):
    rig = Rig(tmp_path)
    open_then_crash(rig)  # long spread, entered with z_entry 2.0 / z_exit 0.0

    async def go():
        eng, feed, store = rig.engine([spec()])
        await feed.backfill()
        rig.quote(feed)
        proc = CommandProcessor(store, eng, RiskManager(eng.cfg.risk), max_age_s=120)
        ok_id = submit_command(rig.db, "set_params", "AAA_BBB", lookback=L, z_entry=3.0, z_exit=0.5, kalman_delta=None)
        bad_id = submit_command(rig.db, "set_params", "AAA_BBB", lookback=L, z_entry=1.0, z_exit=1.5)
        store.conn.execute("UPDATE commands SET ts=?", (feed.now_ms(),))
        assert await proc.poll() == 2
        res = {r["id"]: r for r in store.query("SELECT * FROM commands")}
        assert res[ok_id]["status"] == "done" and "when the open position closes" in res[ok_id]["result"]
        assert res[bad_id]["status"] == "rejected" and "invalid parameters" in res[bad_id]["result"]
        pr = eng.pairs[0]
        assert pr.strat.params.z_entry == 2.0 and pr.next_params == PairParams(L, 3.0, 0.5)
        await rig.step(eng, feed, rig.add_bar(+0.03))  # reverts: closes on the old exit rule
        assert pr.strat.side == FLAT and pr.strat.params == PairParams(L, 3.0, 0.5) and pr.next_params is None
        store.close()

    asyncio.run(go())


def test_live_rows_and_fit_history(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        eng, feed, store = rig.engine([spec()])
        await rig.step(eng, feed, rig.bar)
        rows = eng.live_rows(feed.now_ms())
        store.record_live(rows)
        r = rows[0]
        assert r["side"] == LONG and r["z_buy"] < -2 and r["spread_u"] < 0 and r["hs_bps_a"] == pytest.approx(1.0, rel=1e-3)
        hist = eng.fit_rows(rig.bar, history_bars=10)
        store.record_fit_history(hist)
        assert len(hist) == 1 and hist[0]["kf_beta"] == pytest.approx(eng.pairs[0].strat.fit.beta)  # OLS: L bars only
        n_eq = store.query("SELECT COUNT(*) AS n FROM equity_live")[0]["n"]
        assert store.prune_live(feed.now_ms() + 1) == 1
        assert store.query("SELECT COUNT(*) AS n FROM equity_live")[0]["n"] == n_eq  # one mark a minute is kept
        store.prune_live(feed.now_ms() + 1, feed.now_ms() + 1)
        assert store.query("SELECT COUNT(*) AS n FROM equity_live")[0]["n"] == 0
        store.close()

    asyncio.run(go())


# ------------------------------------------------------------- dashboard
def _populated(tmp_path) -> Rig:
    rig = Rig(tmp_path)
    open_then_crash(rig)
    bar2 = rig.add_bar(-0.07)  # still diverged: the position stays open

    async def go():
        eng, feed, store = rig.engine([spec()])
        await rig.step(eng, feed, bar2)
        now = feed.now_ms()
        store.record_equity(bar2, eng.total_equity(), eng.open_pairs(), False)
        store.record_live_equity(now, eng.total_equity(), eng.open_pairs())
        store.record_live(eng.live_rows(now))
        store.record_marks(bar2, eng.marks_rows())
        store.record_fit_history(eng.fit_rows(bar2, history_bars=5))
        store.event("execution", "AAA_BBB", reason="long_spread", order_kind="open", urgent=False,
                    seconds=42.0, maker_share=0.57, cost_bps=4.2, fee_bps=3.3)
        store.close()

    asyncio.run(go())
    return rig


PAGES = ["overview", "pair", "backtest", "executions", "controls", "logs"]
LOG = """2026-10-01 20:51:59,036 INFO    cryptopairs.runtime.app: restored state: 1 open position(s)
2026-10-01 20:52:03,468 WARNING cryptopairs.runtime.feed: stale quote for AAA/USDT:USDT
2026-10-01 20:52:04,001 ERROR   cryptopairs.runtime.commands: command 3 failed
Traceback (most recent call last):
  ValueError: boom
"""


def _archive(tmp_path) -> None:
    """A synthetic local archive for AAA/BBB (mean-reverting spread) covering the out-of-sample window."""
    ts = np.arange(pd.Timestamp("2025-12-01", tz="UTC").value // 10**6,
                   pd.Timestamp("2026-09-30", tz="UTC").value // 10**6, HOUR, dtype=np.int64)
    rng = np.random.default_rng(1)
    lb = np.log(5.0) + np.cumsum(rng.normal(0, 0.01, ts.size))
    u = np.zeros(ts.size)
    for i in range(1, ts.size):
        u[i] = 0.97 * u[i - 1] + rng.normal(0, 0.004)
    for sym, lp in (("AAAUSDT", 0.1 + lb + u), ("BBBUSDT", lb)):
        px = np.exp(lp)
        path = tmp_path / "data" / "klines" / "1h" / f"{sym}.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"ts": ts, "open": px, "high": px * 1.001, "low": px * 0.999, "close": px,
                      "quote_volume": 1e6}).to_pickle(path)


def _app(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("CRYPTOPAIRS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CRYPTOPAIRS_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CRYPTOPAIRS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CRYPTOPAIRS_CONFIG", str(tmp_path / "no-config.yaml"))  # defaults of the stored profile
    monkeypatch.setenv("CRYPTOPAIRS_OFFLINE", "1")  # no Binance / CoinGecko calls from the pages
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.session_state["lab_extend"] = False  # no network in tests
    return at


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders(tmp_path, monkeypatch, page):
    pytest.importorskip("streamlit")
    _populated(tmp_path)
    _archive(tmp_path)
    (tmp_path / "paper.log").write_text(LOG)
    at = _app(tmp_path, monkeypatch).run()
    if page != "overview":
        at.switch_page(f"ui/views/{page}.py").run()
    assert not at.exception, at.exception
    labels = {m.label for m in at.metric}
    expected = {
        "overview": {"NAV", "Today", "Since inception", "Sharpe 7d", "Max DD 7d", "Gross exposure", "BTC-equivalent",
                     "Funding carry", "Open positions", "Hit rate"},
        "pair": {"Position", "Unrealized", "Z now", "β in use"},
        "backtest": {"Sharpe · calibrated", "Total return · calibrated", "Trades · calibrated"},
        "executions": {"Executions", "Maker share"},
        "controls": {"Leverage", "Stop-loss", "Max open pairs"},
        "logs": {"Lines shown", "Warnings", "Errors"},
    }[page]
    assert expected <= labels, labels
    if page == "executions":
        # the scenario's taker entry (0% maker) plus the injected maker execution (57%)
        assert next(m for m in at.metric if m.label == "Maker share").value == "28%"
    if page == "logs":
        assert next(m for m in at.metric if m.label == "Errors").value == "1"


def test_what_if_reaches_the_lab_and_is_applied_through_a_command(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    rig = _populated(tmp_path)
    _archive(tmp_path)
    at = _app(tmp_path, monkeypatch).run()
    assert at.session_state["focus"] == "AAA_BBB"
    at.switch_page("ui/views/backtest.py").run()  # the what-if panel sits in the lab and on the Pair desk
    at.slider(key="wi_ze_AAA_BBB").set_value(3.0)
    at.slider(key="wi_zx_AAA_BBB").set_value(0.5)
    next(b for b in at.button if b.label == "Recalculate").click().run()
    assert not at.exception, at.exception
    assert {"Sharpe · what-if", "Trades · what-if"} <= {m.label for m in at.metric}

    at.switch_page("ui/views/pair.py").run()
    assert at.button(key="apply_AAA_BBB").disabled  # needs the confirmation first
    at.checkbox(key="conf_apply_AAA_BBB").check().run()
    at.button(key="apply_AAA_BBB").click().run()
    assert not at.exception, at.exception
    cmd = StateStore(rig.db).pending_commands()
    assert [(c["kind"], c["pair_id"]) for c in cmd] == [("set_params", "AAA_BBB")]
    assert json.loads(cmd[0]["payload"]) == {"lookback": L, "z_entry": 3.0, "z_exit": 0.5, "kalman_delta": None}


def test_pair_button_queues_a_command(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    rig = _populated(tmp_path)
    at = _app(tmp_path, monkeypatch).run()
    at.switch_page("ui/views/pair.py").run()
    at.button(key="pause_AAA_BBB").click().run()
    assert not at.exception, at.exception
    store = StateStore(rig.db)
    cmd = store.pending_commands()
    assert [(c["kind"], c["pair_id"]) for c in cmd] == [("pause_pair", "AAA_BBB")]
    # the ticket cannot send anything while the bot is down (its snapshots are years old here)
    assert at.button(key="tk_send_close_AAA_BBB").disabled


def _alive_with_books(rig, monkeypatch):
    """The bot looks alive (a fresh live snapshot) and Binance answers with a deep book."""
    from cryptopairs.ui import data as D
    from cryptopairs.ui import market as M

    store = StateStore(rig.db)
    store.record_live_equity(D.now_ms(), 100.0, 1)
    live = store.query("SELECT * FROM pair_live ORDER BY ts DESC LIMIT 1")[0]
    store.close()
    books = {sym: {"bids": [[px * 0.9999, 1e9]], "asks": [[px * 1.0001, 1e9]], "ts": 0}
             for sym, px in (("AAA/USDT:USDT", live["mid_a"]), ("BBB/USDT:USDT", live["mid_b"]))}
    monkeypatch.setattr(M, "order_books", lambda symbols, limit=50: books)


def test_order_ticket_closes_part_of_the_position_and_sets_an_exit_plan(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    rig = _populated(tmp_path)  # a long spread is open
    _alive_with_books(rig, monkeypatch)
    at = _app(tmp_path, monkeypatch).run()
    at.switch_page("ui/views/pair.py").run()
    assert not at.exception, at.exception
    at.segmented_control(key="tk_frac_AAA_BBB").set_value("50%").run()
    assert {"PnL realized", "Est. cost", "Left open"} <= {m.label for m in at.metric}
    send = at.button(key="tk_send_close_AAA_BBB")
    assert send.disabled  # needs the confirmation first
    at.checkbox(key="tk_close_AAA_BBB_0").check().run()
    at.button(key="tk_send_close_AAA_BBB").click().run()
    assert not at.exception, at.exception
    at.number_input(key="tk_zx_AAA_BBB").set_value(0.5).run()
    at.button(key="tk_send_exit_AAA_BBB").click().run()
    cmds = StateStore(rig.db).pending_commands()
    assert [(c["kind"], json.loads(c["payload"])) for c in cmds] == [
        ("close_pair", {"fraction": 0.5, "execution": "market"}),
        ("set_exit_plan", {"z_exit": 0.5, "stop_loss_pct": 0.2})]
    assert not at.checkbox(key="tk_close_AAA_BBB_1").value  # the confirmation resets after a send


def test_order_ticket_opens_a_flat_pair(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    rig = Rig(tmp_path)

    async def go():  # a flat pair with a fit and live marks
        eng, feed, store = rig.engine([spec(z_entry=50.0)])  # no automatic entry
        await rig.step(eng, feed, rig.bar)
        store.record_live(eng.live_rows(feed.now_ms()))
        store.record_fit_history(eng.fit_rows(rig.bar, history_bars=5))
        store.close()

    asyncio.run(go())
    _alive_with_books(rig, monkeypatch)
    at = _app(tmp_path, monkeypatch).run()
    at.switch_page("ui/views/pair.py").run()
    assert not at.exception, at.exception
    at.segmented_control(key="tk_side_AAA_BBB").set_value("Short spread").run()
    at.segmented_control(key="tk_ex_open_AAA_BBB").set_value("Maker-first").run()
    assert {"Z at entry", "Gross notional", "Est. cost", "Stop / catastrophe"} <= {m.label for m in at.metric}
    at.checkbox(key="tk_open_AAA_BBB_0").check().run()
    at.button(key="tk_send_open_AAA_BBB").click().run()
    assert not at.exception, at.exception
    cmds = StateStore(rig.db).pending_commands()
    lev = AppConfig().strategy.leverage
    assert [(c["kind"], json.loads(c["payload"])) for c in cmds] == [
        ("open_pair", {"side": -1, "leverage": lev, "execution": "maker"})]


def test_heartbeat_marks_a_stale_bot(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    _populated(tmp_path)  # snapshots were written with the test's fake clock (years ago)
    at = _app(tmp_path, monkeypatch).run()
    assert any("The bot is not running" in h.proto.body for h in at.get("html"))
    assert any("BOT DOWN" in h.proto.body for h in at.get("html"))


def test_watchlist_puts_open_positions_first_and_a_pick_moves_the_focus(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from cryptopairs.ui.widgets import watchlist_rows

    rows = pd.DataFrame([
        {"pair": "F1", "a": "A", "b": "B", "side": 0, "Z now": 1.9, "Z entry": 2.0, "to entry": 0.1, "PnL": 1.0,
         "unrealized": 0.0, "status": "", "paused": False, "override": False, "exit plan": False},
        {"pair": "O1", "a": "C", "b": "D", "side": -1, "Z now": 2.5, "Z entry": 2.0, "to entry": np.nan, "PnL": -1.0,
         "unrealized": -1.5, "status": "", "paused": False, "override": False, "exit plan": True},
        {"pair": "F2", "a": "E", "b": "F", "side": 0, "Z now": 0.1, "Z entry": 2.0, "to entry": 1.9, "PnL": 0.0,
         "unrealized": 0.0, "status": "", "paused": True, "override": False, "exit plan": False},
    ])
    wl = watchlist_rows(rows, pending={"O1"})
    assert [r["id"] for r in wl] == ["O1", "F1", "F2"] and wl[0]["busy"] and wl[0]["plan"] and wl[2]["paused"]
    assert wl[1]["to_entry"] == 0.1 and wl[1]["z"] == 1.9

    _populated(tmp_path)
    at = _app(tmp_path, monkeypatch)
    at.session_state["_focus_pick"] = "AAA_BBB"  # what a click in the watchlist leaves for the next run
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["focus"] == "AAA_BBB" and "_focus_pick" not in at.session_state


def test_a_click_in_the_watchlist_opens_that_pairs_desk(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    _populated(tmp_path)
    at = _app(tmp_path, monkeypatch)
    at.run()  # the default page: Overview
    assert not at.exception, at.exception
    assert "pd_view" not in at.session_state
    at.session_state["_focus_pick"] = "AAA_BBB"  # what a click in the watchlist leaves for the next run
    at.session_state["_open_desk"] = True
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["focus"] == "AAA_BBB" and "_open_desk" not in at.session_state
    assert "pd_view" in at.session_state  # the Pair desk ran (its chart selector exists)


def test_log_reader_keeps_tracebacks_with_their_line(tmp_path):
    from cryptopairs.ui.data import read_log

    p = tmp_path / "bot.log"
    p.write_text(LOG)
    log = read_log.__wrapped__(str(p), 0.0)
    assert log["level"].tolist() == ["INFO", "WARNING", "ERROR"]
    assert log["source"].iloc[2] == "runtime.commands" and "ValueError: boom" in log["message"].iloc[2]


def test_what_if_signals_follow_the_strategy_rules():
    from cryptopairs.ui.lab import simulate_signals

    t = pd.Series(pd.date_range("2026-01-01", periods=8, freq="h"))
    z = np.array([0.0, -2.5, -1.0, 0.1, 0.5, 2.6, 0.4, -0.2])
    sig = simulate_signals(t, z, z_entry=2.0, z_exit=0.0)
    assert sig["kind"].tolist() == ["long", "exit", "short", "exit"]
    assert sig["time"].tolist() == [t[1], t[3], t[5], t[7]]


def test_live_lines_break_where_the_bot_was_down():
    from cryptopairs.ui.charts import _with_gaps

    df = pd.DataFrame({"ts": [0, 5_000, 10_000, 100_000, 105_000], "equity": [1.0, 2.0, 3.0, 4.0, 5.0],
                       "side": [1, 1, 1, -1, -1], "session_id": ["s"] * 5})  # int and text columns as in the DB
    out = _with_gaps(df)
    assert out["equity"].isna().sum() == 1 and out["ts"].is_monotonic_increasing
    assert out.loc[out["equity"].isna(), "ts"].item() == 99_999

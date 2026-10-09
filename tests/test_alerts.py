"""Telegram alerts: coin shocks, stops from one coin, a bot that stopped, funding, a bad day, the summary."""

import logging
import sqlite3

from cryptopairs.runtime.news import NewsItem, coins_in, parse_binance, parse_bybit, parse_okx, parse_rss, parse_upbit
from cryptopairs.runtime.alerts import AlertConfig, AlertMonitor, Move, Telegram, coin_of, day_start_ms, read_bot
from tests.test_dashboard_controls import _populated

MIN, HOUR = 60_000, 3_600_000
ALIVE = AlertConfig(down_after_min=10**6)  # the test bot writes no live equity while the clock moves on


def _monitor(rig, moves=None, now=0, cfg=None, rates=None, news=None):
    sent: list[str] = []
    clock = {"now": now}
    mon = AlertMonitor(rig.db.parent, cfg or AlertConfig(), lambda m: sent.append(m) or True,
                       lambda coins: dict(moves or {}), lambda coin: None, now=lambda: clock["now"],
                       funding=(lambda: dict(rates)) if rates is not None else None,
                       news=(lambda: list(news)) if news is not None else None)
    return mon, sent, clock


def _heartbeat(rig) -> int:
    return read_bot(rig.db, 0).heartbeat_ms


def test_coin_names():
    assert [coin_of(s) for s in ("OGN/USDT:USDT", "OGNUSDT", "1000SHIBUSDT", "1000SHIB/USDT:USDT")] == \
        ["OGN", "OGN", "1000SHIB", "1000SHIB"]


def test_a_shock_names_the_positions_and_waits_out_its_cooldown(tmp_path):
    rig = _populated(tmp_path)  # bot "A" (paper.db) holds a long spread: long AAA, short BBB
    hb = _heartbeat(rig)
    mon, sent, clock = _monitor(rig, {"AAA": Move(0.152, 39.0), "BBB": Move(-0.02)}, now=hb + MIN, cfg=ALIVE)
    assert len(mon.check()) == 1
    msg = sent[0]
    assert "AAA +15,2% σε 60 λεπτά" in msg and "όγκος 39×" in msg
    assert "A: long σε 1 ζεύγος · ανοιχτά" in msg and "BBB" not in msg  # a 2% move is no shock
    assert msg.endswith("Risk &amp; controls: https://cryptobot.tail5610d8.ts.net/controls")

    clock["now"] += HOUR  # still moving an hour later: no second message within the cooldown ...
    assert mon.check() == [] and len(sent) == 1
    again, sent2, clock2 = _monitor(rig, {"AAA": Move(0.2)}, now=clock["now"], cfg=ALIVE)  # ... not even after a restart
    assert again.check() == []
    clock2["now"] = hb + MIN + 4 * HOUR  # but after 4 hours, yes
    assert len(again.check()) == 1 and "AAA +20,0%" in sent2[0]


def test_two_stops_from_the_same_coin(tmp_path):
    rig = _populated(tmp_path)
    hb = _heartbeat(rig)
    con = sqlite3.connect(rig.db)
    with con:  # two stops where the AAA leg lost (long AAA fell), one where BBB did
        for ts, exit_a, exit_b in ((hb - HOUR, 0.80, 0.99), (hb, 0.85, 0.99), (hb, 1.0, 1.3)):
            con.execute("INSERT INTO trades (pair_id, side, entry_ts, exit_ts, reason, qty_a, qty_b, entry_px_a, "
                        "entry_px_b, exit_px_a, exit_px_b, pnl) VALUES ('AAA_BBB', 1, ?, ?, 'stop_loss', 10, -10, "
                        "1.0, 1.0, ?, ?, -20)", (ts - 5 * HOUR, ts, exit_a, exit_b))
    con.close()
    mon, sent, clock = _monitor(rig, now=hb + MIN, cfg=ALIVE)
    blocks = mon.check()
    assert len(blocks) == 1 and blocks[0].startswith("🛑 <b>AAA: 2 stops στο A</b> τις τελευταίες 6 ώρες (-40,0 USDT)")
    clock["now"] += 30 * MIN
    assert mon.check() == []


def test_a_bot_that_stops_and_comes_back(tmp_path, caplog):
    rig = _populated(tmp_path)
    hb = _heartbeat(rig)
    mon, sent, clock = _monitor(rig, now=hb + 11 * MIN)
    assert mon.check() == ["🔴 <b>Το bot A δεν τρέχει</b> (καμία ενημέρωση εδώ και 11 λεπτά)"]
    clock["now"] += 5 * MIN
    assert mon.check() == []  # said once
    con = sqlite3.connect(rig.db)
    with con:
        con.execute("INSERT INTO equity_live (ts, equity, open_pairs) VALUES (?, 100, 1)", (clock["now"],))
    con.close()
    assert mon.check() == ["🟢 Το bot A τρέχει ξανά"]

    tries: list[str] = []  # a send that fails (returns None) ...
    failing = AlertMonitor(rig.db.parent, ALIVE, tries.append, lambda coins: {"AAA": Move(0.3)}, now=lambda: hb)
    assert failing.check() == [] and failing.check() == [] and len(tries) == 2  # ... is tried again every round
    with caplog.at_level(logging.WARNING):  # without a token the message is only written to the log
        assert Telegram(None, None).send("hello") is False
    assert "not set up" in caplog.text and "hello" in caplog.text


def test_an_archived_database_is_not_a_bot(tmp_path):
    rig = _populated(tmp_path)
    mon, sent, _ = _monitor(rig, {"AAA": Move(0.5)}, now=_heartbeat(rig) + 8 * 24 * HOUR)
    assert mon.check() == [] and sent == []


def _write(rig, sql, *args):
    con = sqlite3.connect(rig.db)
    with con:
        con.execute(sql, args)
    con.close()


def test_expensive_funding_names_the_pair_once_a_day(tmp_path):
    rig = _populated(tmp_path)  # long AAA (~260 USDT), short BBB (~237 USDT)
    hb = _heartbeat(rig)
    cheap, sent, _ = _monitor(rig, now=hb + MIN, cfg=ALIVE, rates={"AAA": (0.0001, 8.0), "BBB": (0.0001, 8.0)})
    assert cheap.check() == []
    # long AAA pays 0.3% every 8 h; short BBB pays 0.05% every 4 h: ~3.06 USDT a day, 0.61% of ~497 USDT
    mon, sent, clock = _monitor(rig, now=hb + MIN, cfg=ALIVE, rates={"AAA": (0.003, 8.0), "BBB": (-0.0005, 4.0)})
    blocks = mon.check()
    assert len(blocks) == 1 and blocks[0].startswith("💸 <b>Ακριβό funding στο A</b>")
    assert "AAA/BBB: πληρώνει 3,06 USDT την ημέρα (0,61% της θέσης 497 USDT), κυρίως το long AAA" in blocks[0]
    clock["now"] += 23 * HOUR
    assert mon.check() == []
    clock["now"] += HOUR
    assert len(mon.check()) == 1


def test_a_bad_day_alerts_at_each_step_of_3_percent(tmp_path):
    rig = _populated(tmp_path)  # equity 97.08 at the start of the day
    hb = _heartbeat(rig)
    mon, sent, clock = _monitor(rig, now=hb + MIN, cfg=ALIVE)
    assert mon.check() == []
    _write(rig, "INSERT INTO equity_live (ts, equity, open_pairs) VALUES (?, ?, 1)", hb + MIN, 97.08 * 0.965)
    assert mon.check() == ["📉 <b>Το bot A έχασε 3,5% σήμερα</b> (-3,4 USDT, equity 94)"]
    assert mon.check() == []  # the same step: said once
    _write(rig, "INSERT INTO equity_live (ts, equity, open_pairs) VALUES (?, ?, 1)", hb + 2 * MIN, 97.08 * 0.93)
    assert mon.check()[0].startswith("📉 <b>Το bot A έχασε 7,0% σήμερα</b>")


def test_near_the_kill_switch_and_on_it(tmp_path):
    rig = _populated(tmp_path)
    hb = _heartbeat(rig)
    _write(rig, "INSERT OR REPLACE INTO kv (key, value) VALUES ('risk.peak_equity', '130')")
    mon, sent, clock = _monitor(rig, now=hb + MIN, cfg=ALIVE)
    assert mon.check() == ["🟠 <b>Το bot A πλησιάζει το kill switch</b>: πτώση 25,3% από την κορυφή (130 USDT), "
                           "το όριο είναι 25%"]
    _write(rig, "INSERT OR REPLACE INTO kv (key, value) VALUES ('risk.kill_switch', 'true')")
    assert mon.check()[0].startswith("⛔ <b>Kill switch στο A</b>: πτώση 25,3% από την κορυφή")
    assert mon.check() == []


def test_the_daily_summary_at_23(tmp_path):
    rig = _populated(tmp_path)
    hb = _heartbeat(rig)  # 03:00 in Athens
    late = day_start_ms(hb) + 23 * HOUR
    mon, sent, clock = _monitor(rig, now=hb + MIN, cfg=ALIVE, rates={"AAA": (0.0001, 8.0), "BBB": (0.0001, 8.0)})
    assert mon.check() == []  # not before 23:00
    clock["now"] = late
    [summary] = mon.check()
    assert summary.startswith("📊 <b>Σύνοψη ημέρας 17/11</b>\n<b>A</b>: equity 97 USDT, σήμερα +0,0 (+0,00%)")
    assert "κανένα κλείσιμο · ανοίγματα 1 · ανοιχτές θέσεις 1" in summary
    assert "fees σήμερα 0,25 USDT · funding τώρα -0,01 USDT/ημέρα" in summary
    clock["now"] += 30 * MIN
    assert mon.check() == []  # once a day
    clock["now"] = late + 24 * HOUR
    assert mon.check()[0].startswith("📊 <b>Σύνοψη ημέρας 18/11</b>")


def test_headlines_name_coins_by_ticker_or_project_name():
    coins = ["OGN", "1000PEPE", "ARB", "T", "HFT", "BAND"]
    assert coins_in("Binance Will Delist OGN, BAND on 2026-10-15", coins) == ["OGN", "BAND"]
    assert coins_in("Origin Protocol buyback", coins) == ["OGN"]
    assert coins_in("Binance Futures Will Launch USDⓈ-Margined ARBUSDC and 1000PEPEUSDT", coins) == ["1000PEPE", "ARB"]
    assert coins_in("$PEPE pumps", coins) == ["1000PEPE"]
    assert coins_in("HFT firms cross the threshold; ARBITRAGE desks band together", coins) == []
    assert coins_in("ARB Price Prediction: Can Arbitrum Break Above $1?", coins) == []  # opinion, not news


def test_news_feeds_are_parsed():
    rss = b"""<rss><channel><title>Feed</title><item><title><![CDATA[Cartesi rallies]]></title>
        <link>https://x.test/a</link><pubDate>Fri, 09 Oct 2026 10:44:22 +0000</pubDate></item>
        <item><title>no date</title><link>https://x.test/b</link></item></channel></rss>"""
    [it] = parse_rss("CoinDesk", rss)
    assert (it.source, it.title, it.link, it.ts) == ("CoinDesk", "Cartesi rallies", "https://x.test/a", 1791542662000)
    raw = (b'{"data": {"catalogs": [{"catalogId": 161, "articles": [{"title": "Binance Will Delist OGN", '
           b'"code": "abc", "releaseDate": 5}]}, {"catalogId": 93, "articles": [{"title": "Win prizes", '
           b'"code": "d", "releaseDate": 6}]}]}}')
    assert parse_binance(raw) == [NewsItem("Binance", "Binance Will Delist OGN",
                                           "https://www.binance.com/en/support/announcement/detail/abc", 5)]
    assert parse_bybit(b'{"result": {"list": [{"title": "Delisting of OGNUSDT", "url": "https://by.test/1", '
                       b'"dateTimestamp": 1, "publishTime": 2}]}}') == [NewsItem("Bybit", "Delisting of OGNUSDT",
                                                                               "https://by.test/1", 2)]
    assert parse_okx(b'{"data": [{"details": [{"title": "OKX to list OGN", "url": "https://ok.test/1", '
                     b'"pTime": "3"}]}]}') == [NewsItem("OKX", "OKX to list OGN", "https://ok.test/1", 3)]
    [up] = parse_upbit('{"data": {"notices": [{"id": 6636, "first_listed_at": "2026-10-02T16:00:00+09:00", '
                       '"title": "샌드박스(SAND) 거래 유의 종목 지정 해제 안내"}]}}'.encode())
    assert up.title == "τέλος προειδοποίησης: 샌드박스(SAND) 거래 유의 종목 지정 해제 안내"
    assert (up.link, up.ts) == ("https://upbit.com/service_center/notice?id=6636", 1790924400000)
    assert coins_in(up.title, ["SAND"]) == ["SAND"]


def test_a_headline_about_a_coin_of_the_list_is_sent_once(tmp_path):
    rig = _populated(tmp_path)  # pairs AAA/BBB: long AAA
    hb = _heartbeat(rig)
    news = [NewsItem("Binance", "Binance Will Delist AAA & co", "https://b.test/1", hb - HOUR),
            NewsItem("CoinDesk", "Bitcoin is flat", "https://c.test/2", hb - HOUR),
            NewsItem("CoinDesk", "BBB news of yesterday", "https://c.test/3", hb - 24 * HOUR)]
    mon, sent, clock = _monitor(rig, now=hb + MIN, cfg=ALIVE, news=news)
    assert mon.check() == ['📰 <b>AAA</b> · Binance: <a href="https://b.test/1">Binance Will Delist AAA &amp; co</a>'
                           "\nA: long σε 1 ζεύγος · ανοιχτά -2,7 USDT"]
    clock["now"] += 10 * MIN
    assert mon.check() == []  # seen

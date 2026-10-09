"""Free crypto headlines for the alerts: news sites' RSS and exchanges' announcements, matched to coins.

Sources: the large crypto sites, the sites that write mostly about altcoins, and the announcements of
Binance, Bybit, OKX and Upbit (Korea: its listings and "warning" designations move altcoins the most).

No API key and no AI: the headline goes to the trader as written, only when it names a coin of the
bots' pairs (the dashboard's watchlist), held or not. A coin is named by its ticker in capitals
("OGN", "$OGN") or by its project name ("Origin Protocol"); names that are everyday words are left
out on purpose, or a headline about "the threshold of" would be news about T.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) CryptoPairsBot/1.0"
RSS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
    # mostly altcoins
    "U.Today": "https://u.today/rss",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "CryptoPotato": "https://cryptopotato.com/feed/",
    "NewsBTC": "https://www.newsbtc.com/feed/",
    "Bitcoinist": "https://bitcoinist.com/feed/",
    "The Defiant": "https://thedefiant.io/api/feed",
    "CoinGape": "https://coingape.com/feed/",
    "BeInCrypto": "https://beincrypto.com/feed/",
    "AMBCrypto": "https://ambcrypto.com/feed/",
    "Coinpedia": "https://coinpedia.org/feed/",
}
BINANCE = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20"
BINANCE_CATALOGS = {48, 49, 161}  # new listings (spot and futures), latest news, delistings
BINANCE_LINK = "https://www.binance.com/en/support/announcement/detail/"
BYBIT = "https://api.bybit.com/v5/announcements/index?locale=en-US&limit=20"
OKX = "https://www.okx.com/api/v5/support/announcements?page=1"
UPBIT = "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade"
UPBIT_LINK = "https://upbit.com/service_center/notice?id="
# Upbit writes in Korean: what each notice means, most specific first
UPBIT_KINDS = [("유의 종목 지정 해제", "τέλος προειδοποίησης"), ("유의 종목 지정 기간 연장", "παράταση προειδοποίησης"),
               ("유의 종목 지정", "προειδοποίηση (κίνδυνος delisting)"), ("거래지원 종료", "delisting"),
               ("신규 거래지원", "νέο listing"), ("디지털 자산 추가", "νέες αγορές")]

# Project names besides the ticker, for the coins of the bots' pairs (2026-10). Not every coin needs one.
NAMES = {
    "ALICE": ["My Neighbor Alice"], "ANKR": ["Ankr"], "ARB": ["Arbitrum"], "ASTR": ["Astar"],
    "ATOM": ["Cosmos"], "BAND": ["Band Protocol"], "BLUR": ["Blur"], "C98": ["Coin98"], "CELR": ["Celer"],
    "CHR": ["Chromia"], "COMP": ["Compound"], "CTSI": ["Cartesi"], "ENS": ["Ethereum Name Service"],
    "FET": ["Fetch.ai", "Artificial Superintelligence Alliance", "ASI Alliance"], "GMT": ["STEPN"],
    "GRT": ["The Graph"], "GTC": ["Gitcoin"], "HFT": ["Hashflow"], "HOT": ["Holochain", "Holo"],
    "IMX": ["Immutable"], "JASMY": ["JasmyCoin", "Jasmy"], "JOE": ["Trader Joe"], "KNC": ["Kyber"],
    "LQTY": ["Liquity"], "LRC": ["Loopring"], "MAGIC": ["TreasureDAO", "Treasure DAO"], "MANA": ["Decentraland"],
    "OGN": ["Origin Protocol"], "ONE": ["Harmony"], "OP": ["Optimism"], "RLC": ["iExec"],
    "ROSE": ["Oasis Network", "Oasis"], "RSR": ["Reserve Rights"], "SAND": ["The Sandbox"],
    "SFP": ["SafePal"], "SKL": ["SKALE"], "SNX": ["Synthetix"], "SSV": ["SSV Network"],
    "SUSHI": ["SushiSwap", "Sushi"], "T": ["Threshold Network"], "TLM": ["Alien Worlds"],
    "WOO": ["WOO X", "WOO Network"], "YFI": ["Yearn"], "ZIL": ["Zilliqa"], "ZRX": ["0x Protocol"],
    "PEPE": ["Pepe"], "SHIB": ["Shiba Inu"],
}
# Tickers that are not the coin when written in capitals: HFT = high-frequency trading, T = a letter.
NO_TICKER = {"HFT", "T"}
# Opinion, not news: the altcoin sites publish several of these a day for the large coins.
NOT_NEWS = re.compile(r"price (prediction|analysis|forecast)|technical analysis|\bto watch\b", re.I)


@dataclass
class NewsItem:
    source: str
    title: str
    link: str
    ts: int  # published, epoch ms


def base_coin(coin: str) -> str:
    """'1000PEPE' -> 'PEPE': the futures contract of 1000 coins is the same coin in the news."""
    return re.sub(r"^1000+", "", coin)


def _pattern(coin: str) -> re.Pattern:
    c = base_coin(coin)
    words = [re.escape(n) for n in NAMES.get(c, [])]
    if c not in NO_TICKER and len(c) >= 2:
        words.append(r"\$?(?:1000)*" + re.escape(c) + r"(?:USDT|USDC)?")  # Binance writes ARBUSDT, 1000PEPEUSDT
    return re.compile(r"(?<![\w$])(?:%s)(?!\w)" % "|".join(words)) if words else re.compile(r"(?!x)x")


def coins_in(title: str, coins: list[str], _cache: dict = {}) -> list[str]:  # noqa: B006 (a module cache)
    """The coins a headline names, in the order of ``coins`` (none for a price prediction)."""
    if NOT_NEWS.search(title):
        return []
    out = []
    for c in coins:
        if c not in _cache:
            _cache[c] = _pattern(c)
        if _cache[c].search(title):
            out.append(c)
    return out


def _fetch(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_rss(source: str, xml: bytes) -> list[NewsItem]:
    out = []
    for it in ET.fromstring(xml).iter("item"):
        title, link, date = ((it.findtext(k) or "").strip() for k in ("title", "link", "pubDate"))
        try:
            ts = int(parsedate_to_datetime(date).timestamp() * 1000)
        except (TypeError, ValueError):
            continue
        if title and link:
            out.append(NewsItem(source, title, link, ts))
    return out


def parse_binance(raw: bytes) -> list[NewsItem]:
    out = []
    for cat in (json.loads(raw).get("data") or {}).get("catalogs") or []:
        if cat.get("catalogId") not in BINANCE_CATALOGS:
            continue
        for a in cat.get("articles") or []:
            if a.get("title") and a.get("code") and a.get("releaseDate"):
                out.append(NewsItem("Binance", a["title"], BINANCE_LINK + a["code"], int(a["releaseDate"])))
    return out


def parse_bybit(raw: bytes) -> list[NewsItem]:
    return [NewsItem("Bybit", a["title"], a["url"], int(a.get("publishTime") or a["dateTimestamp"]))
            for a in ((json.loads(raw).get("result") or {}).get("list") or [])
            if a.get("title") and a.get("url") and (a.get("publishTime") or a.get("dateTimestamp"))]


def parse_okx(raw: bytes) -> list[NewsItem]:
    return [NewsItem("OKX", a["title"], a["url"], int(a["pTime"]))
            for page in (json.loads(raw).get("data") or []) for a in (page.get("details") or [])
            if a.get("title") and a.get("url") and a.get("pTime")]


def parse_upbit(raw: bytes) -> list[NewsItem]:
    out = []
    for n in ((json.loads(raw).get("data") or {}).get("notices") or []):
        try:
            ts = int(datetime.fromisoformat(n.get("first_listed_at") or n["listed_at"]).timestamp() * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        kind = next((g for k, g in UPBIT_KINDS if k in n.get("title", "")), None)
        title = f"{kind}: {n['title']}" if kind else n.get("title", "")
        if title and n.get("id"):
            out.append(NewsItem("Upbit", title, UPBIT_LINK + str(n["id"]), ts))
    return out


def fetch_news() -> list[NewsItem]:
    """Headlines of every source; a source that fails is skipped this round."""
    out: list[NewsItem] = []
    for source, url in RSS.items():
        try:
            out += parse_rss(source, _fetch(url))
        except Exception as exc:
            log.debug("news from %s: %s", source, exc)
    for source, url, parse in (("Binance", BINANCE, parse_binance), ("Bybit", BYBIT, parse_bybit),
                               ("OKX", OKX, parse_okx), ("Upbit", UPBIT, parse_upbit)):
        try:
            out += parse(_fetch(url))
        except Exception as exc:
            log.debug("news from %s: %s", source, exc)
    return out

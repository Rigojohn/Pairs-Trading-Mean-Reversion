"""TradingView Lightweight Charts and live Binance market panels (Streamlit components v2).

``chart(spec, key, rev)`` shows a chart built by ``ui/tvcharts.py``; reruns with the same key
update it in place (only series whose data hash changed are redrawn, the zoom is kept until
``rev`` changes). ``market(view, ...)`` shows the order books, the trade tape or the live
ticker of a pair, streamed by the browser from Binance's public WebSocket. Both share one
connection per page (``ui/js/common.js``).

The library is vendored in ``ui/vendor`` (Apache-2.0, TradingView attribution logo kept) and
served by Streamlit's static file serving; the CDN is the fallback.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import streamlit as st

from cryptopairs.ui import data as D

HERE = Path(__file__).resolve().parent
JS_DIR, VENDOR = HERE / "js", HERE / "vendor"
STATIC = HERE.parent / "static"  # next to dashboard.py, as Streamlit requires
LWC_FILE = "lightweight-charts.standalone.production.js"
LWC_VERSION = "5.2.1"

CSS = """
.cp-tv { position: relative; width: 100%; }
.cp-tv-box { width: 100%; }
.cp-tv-legend { position: absolute; top: 2px; left: 6px; z-index: 3; display: flex; flex-wrap: wrap; gap: 2px 14px;
  font-size: 11px; color: #8b93a5; pointer-events: none; font-variant-numeric: tabular-nums; }
.cp-leg i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 5px; vertical-align: 0; }
.cp-leg b { color: #d7dce5; font-weight: 600; margin-left: 3px; }
.cp-tv-status { position: absolute; inset: 40% 0 auto 0; text-align: center; color: #8b93a5; font-size: 12px; }
.cp-tv-status:empty { display: none; }
.cp-tv-older { position: absolute; left: 6px; bottom: 30px; z-index: 3; font-size: 11px; color: #8b93a5;
  background: rgba(11,14,20,.75); border-radius: 4px; padding: 1px 6px; pointer-events: none; }
.cp-tv-older:empty { display: none; }

.cp-mk { font-variant-numeric: tabular-nums; color: #d7dce5; font-size: 12px; }
.cp-mk .sub { color: #8b93a5; font-size: 11px; font-weight: 400; }
.cp-mk .pos { color: #2ebd85; } .cp-mk .neg { color: #f6465d; }
.cp-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #4b5468; margin-right: 6px; }
.cp-dot.ok { background: #2ebd85; box-shadow: 0 0 6px rgba(46,189,133,.7); }
.cp-dot.warn { background: #f0b429; } .cp-dot.bad { background: #f6465d; }
.cp-ticker { display: flex; flex-wrap: wrap; align-items: stretch; gap: 8px; }
.cp-tk { background: #131925; border: 1px solid #232b3a; border-radius: 8px; padding: 7px 12px; min-width: 118px;
  display: flex; flex-direction: column; gap: 1px; }
.cp-tk .k { color: #8b93a5; font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase; }
.cp-tk b { font-size: 19px; font-weight: 650; line-height: 1.25; }
.cp-z.warm { color: #f0b429; } .cp-z.hot { color: #f6465d; }
.cp-feed { margin-left: auto; align-self: center; text-align: right; color: #8b93a5; font-size: 11.5px; line-height: 1.6; }
.cp-feedline { color: #8b93a5; font-size: 11.5px; margin: 0 0 6px 2px; }
.cp-books { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
@media (max-width: 1500px) { .cp-books { grid-template-columns: 1fr; } }
.cp-book { background: #131925; border: 1px solid #232b3a; border-radius: 8px; padding: 6px 8px 8px 8px; }
.cp-book-h { font-weight: 650; font-size: 12.5px; margin: 0 0 4px 2px; display: flex; gap: 8px; align-items: center; }
.cp-book-h .sub { margin-left: auto; }
.cp-own { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 999px; letter-spacing: .05em; }
.cp-own.long { color: #2ebd85; background: rgba(46,189,133,.12); } .cp-own.short { color: #f6465d; background: rgba(246,70,93,.12); }
.cp-book table, .cp-tape table { width: 100%; border-collapse: collapse; }
.cp-book th, .cp-tape th { color: #4b5468; font-weight: 500; font-size: 10.5px; text-align: right; padding: 0 4px 2px 4px; }
.cp-book td, .cp-tape td { text-align: right; padding: 1px 4px; line-height: 1.35; position: relative; }
.cp-book th:first-child, .cp-book td:first-child, .cp-tape td:first-child, .cp-tape th:first-child { text-align: left; }
.cp-book tr.ask td.px, .cp-tape tr.ask td.px { color: #f6465d; }
.cp-book tr.bid td.px, .cp-tape tr.bid td.px { color: #2ebd85; }
.cp-book td.bar { width: 26%; padding: 0; }
.cp-book td.bar i { display: block; height: 12px; border-radius: 2px; margin-left: auto; }
.cp-book tr.ask td.bar i { background: rgba(246,70,93,.22); } .cp-book tr.bid td.bar i { background: rgba(46,189,133,.22); }
.cp-book tr.mid td { border-top: 1px solid #232b3a; border-bottom: 1px solid #232b3a; color: #8b93a5; padding: 3px 4px;
  text-align: left; font-size: 11px; }
.cp-book tr.mid td.px { color: #d7dce5; font-weight: 650; font-size: 12.5px; }
.cp-fund { display: flex; flex-direction: column; gap: 1px; margin-top: 6px; color: #8b93a5; font-size: 11px; }
.cp-fund b { color: #d7dce5; font-weight: 600; }
.cp-tape { background: #131925; border: 1px solid #232b3a; border-radius: 8px; padding: 6px 8px; max-height: 330px;
  overflow: hidden; }
.cp-empty { color: #8b93a5; padding: 10px 4px; text-align: center !important; }

.wl { font-variant-numeric: tabular-nums; font-size: 12.5px; color: #d7dce5; user-select: none; }
.wl-h { display: flex; justify-content: space-between; color: #8b93a5; font-size: 10.5px; letter-spacing: .07em;
  text-transform: uppercase; margin: 10px 2px 4px 2px; }
.wl-row { display: grid; grid-template-columns: 18px 1fr 74px 52px; align-items: center; gap: 6px; padding: 5px 6px;
  border-radius: 6px; cursor: pointer; border-left: 2px solid transparent; }
.wl-row:hover { background: #161c29; }
.wl-row.on { background: #1a2131; border-left-color: #f0b429; }
.wl-side { font-size: 10px; font-weight: 800; text-align: center; border-radius: 4px; padding: 1px 0; }
.wl-side.long { color: #2ebd85; background: rgba(46,189,133,.14); }
.wl-side.short { color: #f6465d; background: rgba(246,70,93,.14); }
.wl-side.flat { color: #4b5468; }
.wl-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
.wl-name i { color: #4b5468; font-style: normal; margin: 0 2px; }
.wl-name em { color: #f0b429; font-style: normal; font-size: 10px; margin-left: 4px; }
.wl-z { text-align: right; position: relative; padding-bottom: 4px; }
.wl-z.near { color: #f0b429; font-weight: 700; } .wl-z.sig { color: #f6465d; font-weight: 700; }
.wl-bar { position: absolute; left: 6px; right: 0; bottom: 0; height: 2px; background: #232b3a; border-radius: 2px; }
.wl-bar i { position: absolute; left: 0; top: 0; bottom: 0; background: #a78bfa; border-radius: 2px; }
.wl-bar u { position: absolute; top: -2px; width: 1px; height: 6px; background: #f6465d; }
.wl-pnl { text-align: right; } .wl-pnl.pos { color: #2ebd85; } .wl-pnl.neg { color: #f6465d; }
@media (pointer: coarse) { .wl-row { padding: 10px 6px; font-size: 14px; } .wl-h { padding-top: 14px; } }
.wl-empty { color: #4b5468; padding: 2px 8px; }
.wl-row.kb { background: #1a2131; box-shadow: inset 0 0 0 1px #3a4458; }
.wl-q { position: relative; margin: 6px 0 2px 0; }
.wl-q input { width: 100%; box-sizing: border-box; background: #131925; border: 1px solid #232b3a; border-radius: 6px;
  color: #d7dce5; font: inherit; font-size: 12.5px; padding: 6px 28px 6px 9px; outline: none; user-select: text; }
.wl-q input::placeholder { color: #4b5468; }
.wl-q input:focus { border-color: #f0b429; }
.wl-q kbd, .wl-q button { position: absolute; right: 6px; top: 50%; transform: translateY(-50%); }
.wl-q kbd { color: #4b5468; border: 1px solid #232b3a; border-radius: 4px; font-family: inherit; font-size: 10.5px; line-height: 1; padding: 2px 5px;
  pointer-events: none; }
.wl-q button { display: none; background: none; border: 0; color: #8b93a5; font-size: 16px; line-height: 1;
  padding: 2px 4px; cursor: pointer; }
.wl-q button:hover { color: #d7dce5; }
.wl-q.has kbd, .wl-q input:focus ~ kbd { display: none; }
.wl-q.has button { display: block; }
.wl-none { color: #8b93a5; padding: 12px 8px 4px 8px; }
@media (pointer: coarse) { .wl-q input { font-size: 16px; padding: 9px 34px 9px 10px; } .wl-q kbd { display: none; } }
.wl-foot { color: #4b5468; font-size: 10.5px; line-height: 1.45; margin: 10px 2px 0 2px; }
"""

_renderers: dict[tuple[int, str], tuple[object, Callable]] = {}


def _source(name: str) -> str:
    common = (JS_DIR / "common.js").read_text().replace("__LWC_VERSION__", LWC_VERSION)
    return common + "\n" + (JS_DIR / f"{name}.js").read_text()


def _renderer(name: str) -> Callable:
    """A mount command per Streamlit runtime (tests start several)."""
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager

    manager = get_bidi_component_manager()
    entry = _renderers.get((id(manager), name))
    if entry is None or entry[0] is not manager:
        mount = st.components.v2.component(f"cryptopairs_{name}", js=_source(name), css=CSS, isolate_styles=False)
        entry = _renderers[(id(manager), name)] = (manager, mount)
    return entry[1]


def ensure_static_files() -> None:
    """Copy the chart library next to the app for static serving (once, or after an upgrade)."""
    src, target = VENDOR / LWC_FILE, STATIC / LWC_FILE
    if not target.exists() or target.stat().st_size != src.stat().st_size:
        STATIC.mkdir(exist_ok=True)
        shutil.copyfile(src, target)


def _trigger(key: str, event: str):
    """A component's trigger value, from session state (inside its ``on_<event>_change`` callback)."""
    try:
        v = st.session_state.get(key)
        return v.get(event) if hasattr(v, "get") else getattr(v, event, None)
    except Exception:  # not there yet
        return None


def chart(spec: dict, key: str, rev: str | None = None, on_older: Callable | None = None) -> None:
    """Show a chart spec (see ui/tvcharts.py); reruns with the same key update it in place.
    ``on_older(ask)``: called before the rerun when the chart asks for older bars (``spec["older"]``)."""
    kw = {"on_older_change": lambda: on_older(_trigger(key, "older"))} if on_older else {}
    _renderer("chart")(key=key, data={"spec": spec, "rev": rev or key, "tz": D.TZ_NAME}, width="stretch", **kw)


def watchlist(rows: list[dict], focus: str | None, key: str = "watchlist") -> str | None:
    """The sidebar's pair list; returns the pair just clicked (None otherwise)."""
    res = _renderer("watchlist")(key=key, data={"rows": rows, "focus": focus}, on_pick_change=lambda: None,
                                 width="stretch")  # clicks normally go through pick_relay; this is the fallback
    return getattr(res, "pick", None)


def pick_relay(md: dict | None = None, key: str = "pick_relay") -> str | None:
    """Receives the watchlist's clicks (js/relay.js); mount it outside any fragment. Returns the pair clicked.
    ``md``: per pair, the live panels' data, so a click switches them in the browser at once."""
    res = _renderer("relay")(key=key, data={"md": md or {}}, on_pick_change=lambda: None, width="content")
    return getattr(res, "pick", None)


def pulse(key: str, url: str, every_ms: int, version: float) -> None:
    """Smart live refresh (js/pulse.js): reruns the fragment it sits in when the bot's database is newer
    than ``version`` (the one the fragment was drawn with); checked every ``every_ms``."""
    _renderer("pulse")(key=key, data={"url": url, "every_ms": int(every_ms), "v": float(version)},
                       on_tick_change=lambda: None, width="content")


def market(view: str, key: str, **data) -> None:
    """A live panel ("ticker", "book" or "tape") for the pair in ``data`` (a, b, labels, digits, fit, position)."""
    _renderer("market")(key=key, data={"view": view, "tz": D.TZ_NAME, **data}, width="stretch")

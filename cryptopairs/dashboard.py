"""Real-time dashboard of the bot (Streamlit). Start: python -m cryptopairs dashboard

A multi-page web app on top of the bot's state database (read-only):
  Overview        KPIs (equity, daily PnL, Sharpe, drawdown, Z), equity curve, open book, signal monitor, exposure
  Pair monitor    normalised prices, Z with thresholds and BUY/SELL/CLOSE markers, model, legs, controls
  Backtest lab    the sidebar what-if vs the calibrated parameters on real data up to the last hour
  Executions      maker share, cost and time per execution
  Risk & controls global switches, per-pair controls, parameter overrides, command history
  System logs     live tail of the bot's log with filters, and the event journal

The sidebar picks the bot, the focus pair, the pairs in view, the history window, the refresh
rate and the what-if parameters ("Recalculate" applies them to the charts and the lab).
The dashboard never trades: buttons only queue a command that the bot validates and runs
within a couple of seconds (runtime/commands.py). Paths can be set with the CRYPTOPAIRS_*
environment variables or the arguments below.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import threading
import time
from pathlib import Path

import streamlit as st

# Modules the pages use, in dependency order. Streamlit re-reads the page files when they change
# (runOnSave), but keeps already-imported modules; reloading them as well keeps both in step,
# so a code update never leaves a running dashboard half old, half new.
CODE = ["cryptopairs.runtime.store", "cryptopairs.ui.theme", "cryptopairs.ui.data", "cryptopairs.ui.lab",
        "cryptopairs.ui.live", "cryptopairs.ui.charts", "cryptopairs.ui.widgets"]
_reload_lock = threading.Lock()


def _reload_updated_code() -> None:
    import cryptopairs.ui as pkg

    with _reload_lock:
        loaded_at = getattr(pkg, "_loaded_at", None)
        if loaded_at is None:  # first run of this process: anything newer than its start is stale
            try:
                loaded_at = os.stat(f"/proc/{os.getpid()}").st_mtime
            except OSError:
                loaded_at = time.time()
        mods = [sys.modules[m] for m in CODE if m in sys.modules]
        if any(Path(m.__file__).stat().st_mtime > loaded_at for m in mods):
            for m in mods:
                importlib.reload(m)
        pkg._loaded_at = time.time()


_reload_updated_code()

from cryptopairs.ui import live, theme  # noqa: E402 - after the reload above
from cryptopairs.ui.widgets import sidebar  # noqa: E402

VIEWS = Path(__file__).resolve().parent / "ui" / "views"
PAGES = [  # (file, title, icon, url)
    ("overview.py", "Overview", ":material/dashboard:", "overview"),
    ("pair.py", "Pair monitor", ":material/candlestick_chart:", "pair"),
    ("backtest.py", "Backtest lab", ":material/science:", "lab"),
    ("executions.py", "Executions", ":material/receipt_long:", "executions"),
    ("controls.py", "Risk & controls", ":material/tune:", "controls"),
    ("logs.py", "System logs", ":material/terminal:", "logs"),
]


def _args() -> argparse.Namespace:
    env = os.environ.get
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir", default=env("CRYPTOPAIRS_STATE_DIR", "state"))
    p.add_argument("--db", default=env("CRYPTOPAIRS_DB"))
    p.add_argument("--log-dir", default=env("CRYPTOPAIRS_LOG_DIR", "logs"))
    p.add_argument("--config", default=env("CRYPTOPAIRS_CONFIG", "config/config.yaml"))
    p.add_argument("--data-dir", default=env("CRYPTOPAIRS_DATA_DIR", "data"))
    args, _ = p.parse_known_args()
    return args


@st.cache_resource(show_spinner=False)
def _static_files() -> bool:
    live.ensure_plotly_js()
    return True


def main() -> None:
    st.set_page_config(page_title="CryptoPairs Desk", page_icon="📈", layout="wide", initial_sidebar_state="expanded")
    theme.apply()
    _static_files()
    args = _args()
    ctx = sidebar(Path(args.state_dir), Path(args.log_dir), Path(args.config), Path(args.data_dir), args.db)
    pages = [st.Page(VIEWS / f, title=t, icon=i, url_path=u, default=(k == 0)) for k, (f, t, i, u) in enumerate(PAGES)]
    page = st.navigation(pages, position="top")
    if ctx is None:
        st.stop()
    st.session_state["ctx"] = ctx
    page.run()


main()

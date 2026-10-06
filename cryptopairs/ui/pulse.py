"""The bot database's version as a small static file, for the smart live refresh (js/pulse.js).

A live part of a page used to rerun every second, ~half a CPU core per open tab, while the bot
writes new data only every ~5 s. Here one thread per dashboard process checks each state database
four times a second and writes its version (``data.version``) to ``static/ver/<db>.json`` when it
changes; the browser polls that file (served by Streamlit's static file serving, no script run) and
reruns a fragment only when the bot has written something new.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import streamlit as st

from cryptopairs.ui import data as D

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
VER_DIR = HERE.parent / "static" / "ver"
URL = "app/static/ver/{name}.json"


def url(db_path: Path) -> str:
    return URL.format(name=Path(db_path).stem)


def _write(name: str, version: float) -> None:
    VER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = VER_DIR / f".{name}.json.tmp"
    tmp.write_text(json.dumps({"v": version}))
    os.replace(tmp, VER_DIR / f"{name}.json")  # never a half-written file


def _loop(state_dir: Path, every_s: float) -> None:
    seen: dict[str, float] = {}
    while True:
        try:
            for db in state_dir.glob("*.db"):
                v = D.version(db)
                if seen.get(db.stem) != v:
                    _write(db.stem, v)
                    seen[db.stem] = v
        except Exception as exc:  # a database being created or removed: try again next time
            log.warning("pulse: %s", exc)
        time.sleep(every_s)


@st.cache_resource(show_spinner=False)
def start(state_dir: str, every_s: float = 0.25) -> bool:
    """Start the writer thread once per dashboard process (and state directory)."""
    threading.Thread(target=_loop, args=(Path(state_dir), every_s), daemon=True, name="pulse").start()
    return True

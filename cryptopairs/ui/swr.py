"""Stale-while-revalidate: exchange data from memory, refreshed in the background.

A click on the dashboard reruns the page, and every Binance request made during that run is
time the user waits (it was 5-8 s per pair switch on the server). Here a value is returned at
once from memory; when it is older than its ``ttl`` a background thread fetches a new one for
the next run. Only the very first request for a key waits (or, with ``wait=False``, returns None
and fills the key in the background).

``fn`` returns None on failure (the market.py convention): that is never stored, the old value
stays, and the key is not retried for ``RETRY_S`` seconds so an outage does not slow every run.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Hashable

log = logging.getLogger(__name__)

RETRY_S = 10.0
MAX_KEYS = 2000

_lock = threading.Lock()
_values: dict[Hashable, tuple[Any, float]] = {}  # key -> (value, fetched at)
_failed: dict[Hashable, float] = {}  # key -> time of the last failed fetch
_busy: set[Hashable] = set()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="swr")


def _fetch(key: Hashable, fn: Callable[[], Any]) -> Any:
    try:
        val = fn()
    except Exception as exc:  # fn should not raise; be safe anyway
        log.warning("background fetch %s failed: %s", key, exc)
        val = None
    with _lock:
        _busy.discard(key)
        if val is None:
            _failed[key] = time.time()
        else:
            _failed.pop(key, None)
            _values[key] = (val, time.time())
            if len(_values) > MAX_KEYS:  # forget the oldest
                for k, _ in sorted(_values.items(), key=lambda kv: kv[1][1])[: len(_values) - MAX_KEYS]:
                    _values.pop(k, None)
    return val


def _start(key: Hashable, fn: Callable[[], Any]) -> None:
    """Fetch in the background unless that key is already being fetched (call with the lock held)."""
    if key in _busy:
        return
    _busy.add(key)
    _pool.submit(_fetch, key, fn)


def get(key: Hashable, ttl: float, fn: Callable[[], Any], wait: bool = True) -> Any:
    """The last value of ``key`` (refreshed in the background when older than ``ttl`` seconds)."""
    now = time.time()
    with _lock:
        hit = _values.get(key)
        recently_failed = now - _failed.get(key, 0.0) < RETRY_S
        if hit is not None:
            if now - hit[1] > ttl and not recently_failed:
                _start(key, fn)
            return hit[0]
        if recently_failed:
            return None
        if not wait:
            _start(key, fn)
            return None
        if key in _busy:  # someone else is fetching it: do not send a second request
            busy = True
        else:
            _busy.add(key)
            busy = False
    if busy:
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(0.05)
            with _lock:
                if key not in _busy:
                    hit = _values.get(key)
                    return hit[0] if hit else None
        return None
    return _fetch(key, fn)


def warm(key: Hashable, fn: Callable[[], Any]) -> None:
    """Fill ``key`` in the background if it is not in memory yet."""
    with _lock:
        if key not in _values:
            _start(key, fn)


def clear() -> None:
    """Forget everything (tests)."""
    with _lock:
        _values.clear()
        _failed.clear()

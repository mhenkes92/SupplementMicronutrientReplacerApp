"""In-process cache and background prefetch for SuppSwipe's LLM generations.

Meal plans, benefit comparisons and Ask AI answers depend only on their prompt,
so a finished answer is reused instead of regenerated (re-opening a popover,
re-running the same scan, or another visitor making the same swaps). Only
non-empty answers are stored, so a transient Blockbrain failure is retried next
time instead of being cached.

`submit()` starts a generation on a small worker pool so a likely-needed answer
(the default 3-meal plan) is already being written while the user is still
looking at their results. Workers only do HTTP work — never Streamlit calls;
a streaming worker reports its text so far with `set_partial()`, which the UI
polls with `partial()` to show the answer as it is written.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

_MAX_ENTRIES = 256


def _keep(name: str, make: Callable[[], Any]) -> Any:
    """What this module already holds under `name`, else a new one.

    A redeploy reloads this module in place (see _load_current in app.py). Fresh
    objects would orphan the cache, the lock, the in-flight bookkeeping and the
    worker pool that still-running background jobs use, and the app would start the
    same generation a second time."""
    return globals()[name] if name in globals() else make()


_lock = _keep("_lock", threading.Lock)
_cache: "OrderedDict[str, str]" = _keep("_cache", OrderedDict)
_inflight: dict[str, Future] = _keep("_inflight", dict)
_partial: dict[str, str] = _keep("_partial", dict)
# Text that must never be served or stored as an answer (set by the app to
# "is this a Blockbrain error?"). Also guards entries written by older code.
_reject: Callable[[str], bool] | None = _keep("_reject", lambda: None)
# As many workers as the app allows concurrent Blockbrain calls (BLOCKBRAIN_MAX_CONCURRENT, default 8): with fewer, the 5th
# visitor's meal plan waits behind a whole other generation.
_executor = _keep("_executor", lambda: ThreadPoolExecutor(max_workers=8, thread_name_prefix="suppswipe-llm"))

# Process-wide ledger of LLM calls (all sessions). It lives HERE and not in an st.cache_resource: any visitor can send
# Streamlit's `clear_cache` message over the websocket, which empties every st.cache_* store - and with it a quota kept there.
_usage_lock = _keep("_usage_lock", threading.Lock)
_usage_times: list[float] = _keep("_usage_times", list)


def set_reject(fn: Callable[[str], bool] | None) -> None:
    global _reject
    _reject = fn


def _rejected(text: str) -> bool:
    try:
        return bool(_reject and _reject(text))
    except Exception:
        return False


def make_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(key: str) -> str | None:
    with _lock:
        value = _cache.get(key)
        if value is not None and _rejected(value):
            _cache.pop(key, None)
            return None
        if value is not None:
            _cache.move_to_end(key)
        return value


def put(key: str, text: str) -> None:
    text = str(text or "").strip()
    if not text or _rejected(text):
        return
    with _lock:
        _cache[key] = text
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def inflight(key: str) -> Future | None:
    with _lock:
        future = _inflight.get(key)
    if future is not None and future.done():
        return None
    return future


def submit(key: str, fn: Callable[[], str]) -> Future | None:
    """Run fn() in the background and cache its result. Deduplicated per key;
    returns None when the answer is already cached."""
    if get(key) is not None:
        return None
    with _lock:
        existing = _inflight.get(key)
        if existing is not None and not existing.done():
            return existing

        def _job() -> str:
            try:
                text = str(fn() or "").strip()
            except Exception:
                text = ""
            put(key, text)
            with _lock:
                _inflight.pop(key, None)
                _partial.pop(key, None)
            return text

        future = _executor.submit(_job)
        _inflight[key] = future
        return future


def set_partial(key: str, text: str) -> None:
    """Record the text a background generation has written so far."""
    with _lock:
        if key in _inflight:
            _partial[key] = str(text or "")


def partial(key: str) -> str:
    """Text written so far by a running background generation ("" if none)."""
    with _lock:
        return _partial.get(key, "")


def clear() -> None:
    with _lock:
        _cache.clear()
        _inflight.clear()
        _partial.clear()


def drop(key: str) -> None:
    """Forget a cached answer (e.g. the user asked for different meal ideas)."""
    with _lock:
        _cache.pop(key, None)


def consume_global(now: float, window_s: float, hourly_limit: int, daily_limit: int) -> bool:
    """Record one LLM call in the process-wide ledger; False (and nothing recorded) when the hourly or the daily cap is used up."""
    with _usage_lock:
        day = [t for t in _usage_times if now - t < 86400.0]
        in_window = sum(1 for t in day if now - t < window_s)
        if in_window >= hourly_limit or len(day) >= daily_limit:
            _usage_times[:] = day
            return False
        day.append(now)
        _usage_times[:] = day
        return True


def reset_global_usage() -> None:
    with _usage_lock:
        _usage_times.clear()

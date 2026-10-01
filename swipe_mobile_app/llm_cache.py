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

_lock = threading.Lock()
_cache: "OrderedDict[str, str]" = OrderedDict()
_inflight: dict[str, Future] = {}
_partial: dict[str, str] = {}
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="suppswipe-llm")


def make_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(key: str) -> str | None:
    with _lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
        return value


def put(key: str, text: str) -> None:
    text = str(text or "").strip()
    if not text:
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

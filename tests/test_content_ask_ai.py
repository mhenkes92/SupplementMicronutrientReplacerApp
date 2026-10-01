"""Faster Ask AI (UXL-12): the Knowledge Bot runs in a background thread and
gets a bounded wait; when it is too slow the agent answer is streamed instead
and the late bot reply is ignored. Caching, quota, dose context and history
are unchanged."""
from __future__ import annotations

import threading
import time

import pytest

import blockbrain.app as bb
import llm_cache


class Box:
    def __init__(self):
        self.renders = []

    def markdown(self, text):
        self.renders.append(text)

    def info(self, text):
        self.renders.append(text)

    def empty(self):
        self.renders.append(None)


@pytest.fixture(autouse=True)
def _fresh_cache():
    llm_cache.clear()
    yield
    llm_cache.clear()


def test_default_wait_is_15_seconds(sw):
    assert sw._ASK_AI_BOT_WAIT_S == 15.0


def test_fast_bot_answer_is_used_and_cached(sw, monkeypatch):
    calls = []

    def fake_bot(message, bot_id=None, timeout=None):
        calls.append(threading.current_thread().name)
        return "Bot answer"

    monkeypatch.setattr(bb, "call_blockbrain_bot", fake_bot)
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: pytest.fail("agent not needed"))
    assert sw._answer_ask_ai_question("Zinc", "Safe long-term?", dose_label="10 mg") == ("Bot answer", "")
    assert calls == ["suppswipe-ask-bot"]  # ran in the background thread
    assert sw._answer_ask_ai_question("Zinc", "Safe long-term?", dose_label="10 mg") == ("Bot answer", "")
    assert len(calls) == 1  # cached


def test_slow_bot_falls_back_to_the_streamed_agent(sw, monkeypatch):
    release = threading.Event()
    bot_done = threading.Event()

    def slow_bot(message, bot_id=None, timeout=None):
        release.wait(5)
        bot_done.set()
        return "Late bot answer"

    seen = {}

    def fake_text(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        seen["user"] = user
        seen["history"] = history
        on_text and on_text("Agent")
        return "Agent answer"

    monkeypatch.setattr(sw, "_ASK_AI_BOT_WAIT_S", 0.2)
    monkeypatch.setattr(bb, "call_blockbrain_bot", slow_bot)
    monkeypatch.setattr(bb, "call_blockbrain_text", fake_text)
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    box = Box()
    history = [{"role": "user", "content": "Is 10 mg zinc a lot?"}, {"role": "assistant", "content": "No."}]
    started = time.monotonic()
    answer, _sources = sw._answer_ask_ai_question("Zinc", "And for vegans?", history=history, placeholder=box, dose_label="10 mg")
    elapsed = time.monotonic() - started
    assert answer == "Agent answer"
    assert elapsed < 2.0  # did not wait for the bot
    assert "Dose in the user's supplement: 10 mg" in seen["user"] and seen["history"] == history
    assert box.renders[-1] == "Agent answer"
    # The late bot reply is ignored: nothing changes once it arrives.
    release.set()
    assert bot_done.wait(5)
    time.sleep(0.05)
    first_key = llm_cache.make_key("ask_ai", "zinc", "10 mg", "and for vegans?")
    assert llm_cache.get(first_key) is None


def test_slow_bot_on_a_first_question_caches_the_agent_answer(sw, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(sw, "_ASK_AI_BOT_WAIT_S", 0.1)
    monkeypatch.setattr(bb, "call_blockbrain_bot", lambda *a, **k: release.wait(5) and "Late bot answer")
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: "Agent answer")
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    try:
        assert sw._answer_ask_ai_question("Iron", "Best food source?")[0] == "Agent answer"
        key = llm_cache.make_key("ask_ai", "iron", "", "best food source?")
        assert llm_cache.get(key) == "Agent answer"
    finally:
        release.set()


def test_quota_still_applies_to_the_agent_fallback(sw, monkeypatch):
    # Final review (LLM F2): an exhausted quota stops the Knowledge Bot too.
    monkeypatch.setattr(sw, "_ASK_AI_BOT_WAIT_S", 0.1)
    monkeypatch.setattr(bb, "call_blockbrain_bot", lambda *a, **k: pytest.fail("bot called with the quota used up"))
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: pytest.fail("quota exhausted"))
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: False)
    monkeypatch.setattr(sw, "_cached_rag_chunks", lambda: [])
    box = Box()
    assert sw._answer_ask_ai_question("Iron", "Best food source?", placeholder=box) == (None, "")
    assert box.renders == [sw._QUOTA_MESSAGE]


def test_bot_errors_fall_back_without_waiting(sw, monkeypatch):
    def broken_bot(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bb, "call_blockbrain_bot", broken_bot)
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: "Agent answer")
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    started = time.monotonic()
    assert sw._answer_ask_ai_question("Iron", "Best food source?")[0] == "Agent answer"
    assert time.monotonic() - started < 2.0

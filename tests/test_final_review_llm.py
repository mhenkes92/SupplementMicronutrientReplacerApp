"""Final review, LLM security / deploy: quotas cover the Knowledge Bot and the
URL extraction plus a process-wide backstop (F2), no background meal plan
for pregnancy / religious / health diets (F3), report lines without free text
(F5), image and camera size limits (F6), vision refusals are not cached
(F7), the configured agent is retried first (F8) and URL fetches connect to
the vetted address within a deadline (F9)."""
from __future__ import annotations

import pytest

import blockbrain.app as bb
import llm_cache


@pytest.fixture(autouse=True)
def _fresh_cache():
    llm_cache.clear()
    yield
    llm_cache.clear()


@pytest.fixture
def session(sw, monkeypatch):
    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._global_llm_usage.clear()
    yield state
    sw._global_llm_usage.clear()


# --- F2: quotas -----------------------------------------------------------------------------

def test_each_ask_ai_question_counts_once_even_with_the_agent_fallback(sw, session, monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_MAX_GENERATIONS_PER_HOUR", "2")
    monkeypatch.setattr(sw, "_ASK_AI_BOT_WAIT_S", 0.5)
    bot_calls, agent_calls = [], []
    monkeypatch.setattr(bb, "call_blockbrain_bot", lambda *a, **k: bot_calls.append(1) or "")
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: agent_calls.append(1) or "Agent answer")
    monkeypatch.setattr(sw, "_cached_rag_chunks", lambda: [])
    assert sw._answer_ask_ai_question("Iron", "Q1?")[0] == "Agent answer"
    assert sw._answer_ask_ai_question("Iron", "Q2?")[0] == "Agent answer"
    assert sw._answer_ask_ai_question("Iron", "Q3?") == (None, "")  # quota used up: no bot, no agent
    assert len(bot_calls) == 2 and len(agent_calls) == 2
    # A cached first question is still answered for free.
    assert sw._answer_ask_ai_question("Iron", "Q1?")[0] == "Agent answer"
    assert len(bot_calls) == 2


def test_url_extraction_asks_before_its_llm_call(monkeypatch):
    monkeypatch.setattr(bb, "fetch_clean_page_text", lambda url: "Some product page without a facts table " * 5)
    monkeypatch.setattr(bb, "extract_supplement_text_from_page_text_local", lambda text: "")
    monkeypatch.setattr(bb, "_text_llm_available", lambda: True)
    calls = []
    monkeypatch.setattr(bb, "call_text_llm", lambda *a, **k: calls.append(1) or "Vitamin C 80 mg\nZinc 10 mg")
    assert bb.extract_supplement_text_from_url("https://example.com/p", llm_allowed=lambda: False) == ""
    assert calls == [] and "quota" in bb.LAST_URL_PARSE_REASON
    bb.extract_supplement_text_from_url("https://example.com/p", llm_allowed=lambda: True)
    assert calls == [1]


def test_url_path_passes_the_session_quota(sw, session, monkeypatch):
    seen = []

    def fake_extract(url, llm_allowed=None):
        seen.append(llm_allowed())
        return "Vitamin C 80 mg"

    monkeypatch.setattr(bb, "extract_supplement_text_from_url", fake_extract)
    monkeypatch.setenv("SUPPSWIPE_MAX_GENERATIONS_PER_HOUR", "1")
    sw._cached_extract_from_url.clear()
    sw._cached_extract_from_url("https://example.com/a", lambda: sw._consume_llm_quota("generate"))
    sw._cached_extract_from_url("https://example.com/b", lambda: sw._consume_llm_quota("generate"))
    assert seen == [True, False]
    sw._cached_extract_from_url.clear()


def test_process_wide_backstop_survives_new_sessions(sw, monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_MAX_LLM_CALLS_PER_HOUR_GLOBAL", "3")
    sw._global_llm_usage.clear()
    results = []
    for _session in range(4):  # a reload = a fresh session state
        monkeypatch.setattr(sw.st, "session_state", {})
        results.append(sw._consume_llm_quota("generate"))
    assert results == [True, True, True, False]
    sw._global_llm_usage.clear()

"""Ask AI: the Blockbrain model answers (through blockbrain_llm_client.py), first questions are cached, follow-ups carry
the chat history, every question counts once against the session's allowance, and the local research index answers
when the model cannot."""
from __future__ import annotations

import pytest

import blockbrain.app as bb
import llm_cache


class Box:
    def __init__(self):
        self.renders = []

    def markdown(self, text, **_kwargs):
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


def test_the_model_answers_and_the_first_question_is_cached(sw, monkeypatch):
    calls = []
    seen = {}

    def fake_text(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        calls.append(1)
        seen["user"] = user
        return "Model answer"

    monkeypatch.setattr(bb, "call_blockbrain_text", fake_text)
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    assert sw._answer_ask_ai_question("Zinc", "Safe long-term?", dose_label="10 mg") == ("Model answer", sw._SOURCE_AGENT)
    assert "Dose in the user's supplement: 10 mg" in seen["user"]
    assert sw._answer_ask_ai_question("Zinc", "Safe long-term?", dose_label="10 mg") == ("Model answer", sw._SOURCE_AGENT)
    assert len(calls) == 1  # cached


def test_a_follow_up_sends_the_history_and_is_cached_per_history(sw, monkeypatch):
    seen = []
    monkeypatch.setattr(
        bb, "call_blockbrain_text",
        lambda system, user, history=None, **k: seen.append(history) or "Model answer",
    )
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    history = [{"role": "user", "content": "Is 10 mg zinc a lot?"}, {"role": "assistant", "content": "No."}]
    box = Box()
    assert sw._answer_ask_ai_question("Zinc", "And for vegans?", history=history, placeholder=box)[0] == "Model answer"
    assert seen == [history] and box.renders[-1] == "Model answer"
    assert sw._answer_ask_ai_question("Zinc", "And for vegans?", history=history)[0] == "Model answer"
    assert len(seen) == 1  # the same question after the same chat: cached
    other = history + [{"role": "user", "content": "And pregnant?"}]
    assert sw._answer_ask_ai_question("Zinc", "And for vegans?", history=other)[0] == "Model answer"
    assert seen[-1] == other and len(seen) == 2  # another chat: asked again


def test_quota_stops_the_model_and_falls_back_to_the_local_index(sw, monkeypatch):
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: pytest.fail("quota exhausted"))
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: False)
    monkeypatch.setattr(sw, "_cached_rag_chunks", lambda: [])
    box = Box()
    assert sw._answer_ask_ai_question("Iron", "Best food source?", placeholder=box) == (None, "")
    assert box.renders == [sw._QUOTA_MESSAGE]


def test_a_failing_model_falls_back_to_the_local_index(sw, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    monkeypatch.setattr(sw, "_cached_rag_chunks", lambda: [{"source": "guide.pdf", "text": "Iron: red meat, lentils."}])
    monkeypatch.setattr(bb, "answer_rag_question", lambda q, chunks: ("Local answer", ["guide.pdf"], {}))
    for model in (broken, lambda *a, **k: ""):
        monkeypatch.setattr(bb, "call_blockbrain_text", model)
        answer, sources = sw._answer_ask_ai_question("Iron", "Best food source?")
        assert answer == "Local answer" and "guide.pdf" in sources

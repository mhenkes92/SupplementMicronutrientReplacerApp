"""Adapter wiring: streaming on_text, model only on cortex, Ask AI on the knowledge-base bot, English meal plans."""
import types

import blockbrain.app as bb
import llm_cache
import pytest


@pytest.fixture
def session(sw, monkeypatch):
    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state, raising=False)
    llm_cache.reset_global_usage()
    return state


class _FakeClient:
    def __init__(self, route="agentic", pieces=("Hello ", "world"), sources=()):
        self.text_route, self.text_model = route, ""
        self.pieces, self.sources, self.calls = list(pieces), list(sources), []

    def chat(self, prompt, **kw):
        self.calls.append(("chat", kw))
        return types.SimpleNamespace(text="".join(self.pieces), via=self.text_route, model="m", usage={}, sources=self.sources)

    def chat_stream(self, prompt, **kw):
        self.calls.append(("stream", kw))
        yield from self.pieces


def test_on_text_streams_growing_text_and_the_final_answer(monkeypatch):
    client = _FakeClient(pieces=["a" * 10] * 5)
    monkeypatch.setattr(bb, "_client", lambda: client)
    monkeypatch.setattr(bb, "_STREAM_PUSH_S", 0.0)
    seen = []
    assert bb.call_blockbrain_text("sys", "user", on_text=seen.append) == "a" * 50
    assert client.calls[0][0] == "stream"
    assert len(seen) >= 3 and seen[-1] == "a" * 50 and all(len(a) <= len(b) for a, b in zip(seen, seen[1:]))


def test_without_on_text_the_plain_chat_is_used(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(bb, "_client", lambda: client)
    assert bb.call_blockbrain_text("sys", "user") == "Hello world"
    assert client.calls[0][0] == "chat"


def test_model_is_passed_on_cortex_only(monkeypatch):
    agentic, cortex = _FakeClient("agentic"), _FakeClient("cortex")
    monkeypatch.setattr(bb, "_client", lambda: agentic)
    bb.call_blockbrain_text("s", "u", model="azure-gpt-41-nano")
    assert "model" not in agentic.calls[0][1]
    monkeypatch.setattr(bb, "_client", lambda: cortex)
    bb.call_blockbrain_text("s", "u", model="azure-gpt-41-nano")
    assert cortex.calls[0][1]["model"] == "azure-gpt-41-nano"


def test_platform_error_text_is_never_an_answer(monkeypatch):
    monkeypatch.setattr(bb, "_client", lambda: _FakeClient(pieces=["### ERROR something broke"]))
    assert bb.call_blockbrain_text("s", "u", on_text=lambda t: None) == ""


def test_kb_ask_returns_answer_and_sources(monkeypatch):
    made = {}

    class _Mod:
        READ_TIMEOUT = 0

        @staticmethod
        def Blockbrain(bot_id=None):
            made["bot"] = bot_id
            return _FakeClient("cortex", pieces=["K2: natto 939 mcg"], sources=["examine-k2.pdf"])

    monkeypatch.setenv("BLOCKBRAIN_KB_BOT_ID", "kb-bot")
    monkeypatch.setattr(bb, "_bbc_now", lambda: _Mod)
    text, sources = bb.call_blockbrain_ask("sys", "How much K2?")
    assert text == "K2: natto 939 mcg" and sources == ["examine-k2.pdf"] and made["bot"] == "kb-bot"


def test_kb_ask_without_bot_id_does_nothing(monkeypatch):
    monkeypatch.delenv("BLOCKBRAIN_KB_BOT_ID", raising=False)
    monkeypatch.setattr(bb, "_sync_blockbrain_env_from_secrets", lambda: None)
    assert bb.call_blockbrain_ask("sys", "q") == ("", [])


def test_ask_ai_uses_the_kb_bot_and_labels_its_source(sw, session, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_KB_BOT_ID", "kb-bot")
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    monkeypatch.setattr(bb, "call_blockbrain_ask", lambda *a, **k: ("From the KB: 939 mcg", ["examine.pdf"]))
    general = []
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: general.append(1) or "general")
    answer, source = sw._answer_ask_ai_question("Vitamin K2", "How much per day? kb-test-1")
    assert answer == "From the KB: 939 mcg" and source == sw._SOURCE_KB and not general


def test_ask_ai_falls_back_to_the_general_model_when_the_kb_fails(sw, session, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_KB_BOT_ID", "kb-bot")
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    monkeypatch.setattr(bb, "call_blockbrain_ask", lambda *a, **k: ("", []))
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: "General answer")
    answer, source = sw._answer_ask_ai_question("Zinc", "Best time? kb-test-2")
    assert answer == "General answer" and source == sw._SOURCE_AGENT


def test_meal_plan_prompt_asks_for_english(sw):
    item = {"decision": "replace", "component": "zinc", "dose_value": 10, "dose_unit": "mg",
            "selected_food": {"food_description": "Seeds, pumpkin", "amount_per_100g": 7.8, "unit": "mg"}}
    system, _user, _key = sw._meal_plan_prompts([item], "No restriction", 3, pregnant=False)
    assert "in English" in system and "German-supermarket" in system


def test_per_feature_model_changes_the_cache_key(sw, monkeypatch):
    item = {"decision": "replace", "component": "zinc", "dose_value": 10, "dose_unit": "mg",
            "selected_food": {"food_description": "Seeds, pumpkin", "amount_per_100g": 7.8, "unit": "mg"}}
    monkeypatch.delenv("BLOCKBRAIN_MODEL_MEAL", raising=False)
    k1 = sw._meal_plan_prompts([item], "No restriction", 3, pregnant=False)[2]
    monkeypatch.setenv("BLOCKBRAIN_MODEL_MEAL", "azure-gpt-41-nano")
    k2 = sw._meal_plan_prompts([item], "No restriction", 3, pregnant=False)[2]
    assert k1 != k2

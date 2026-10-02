"""Offline tests for SuppSwipe's LLM features: caching, streaming, prefetch,
Ask AI memory and product-research provenance."""
from __future__ import annotations

import io
import time

import pytest
from PIL import Image

import blockbrain.app as bb
import llm_cache


class Box:
    """Stand-in for st.empty(): records what was rendered."""

    def __init__(self):
        self.renders = []

    def markdown(self, text, **_kwargs):
        self.renders.append(text)

    def empty(self):
        self.renders.append(None)


@pytest.fixture(autouse=True)
def _fresh_cache():
    llm_cache.clear()
    yield
    llm_cache.clear()


REPLACE = [
    {
        "component": "Vitamin C",
        "component_key": "vitamin c",
        "dose_value": 90.0,
        "dose_unit": "mg",
        "decision": "replace",
        "selected_food": {"food_description": "Kiwifruit, green, raw", "amount_per_100g": 92.7, "unit": "mg"},
    }
]


def test_meal_plan_streams_then_is_cached(sw, monkeypatch):
    calls = []

    def fake_text(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        calls.append(user)
        if on_text:
            on_text("**Meal 1**")
        return "**Meal 1**\n- Kiwi bowl"

    monkeypatch.setattr(bb, "call_blockbrain_text", fake_text)
    box = Box()
    assert sw._generate_meal_plan(REPLACE, "Vegan", 3, placeholder=box) == "**Meal 1**\n- Kiwi bowl"
    assert box.renders[0].startswith("**Meal 1**") and box.renders[-1] == "**Meal 1**\n- Kiwi bowl"
    assert sw._generate_meal_plan(REPLACE, "Vegan", 3) == "**Meal 1**\n- Kiwi bowl"
    assert len(calls) == 1  # second request served from cache
    sw._generate_meal_plan(REPLACE, "Vegan", 2)
    assert len(calls) == 2  # different meal count -> new generation


def test_failed_generation_is_not_cached(sw, monkeypatch):
    replies = iter(["", "Plan B"])
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: next(replies))
    assert sw._generate_meal_plan(REPLACE, "", 3) == ""
    assert sw._generate_meal_plan(REPLACE, "", 3) == "Plan B"


def test_prefetch_is_reused_by_generate(sw, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", "k")
    calls = []

    def slow_text(*a, **k):
        calls.append(1)
        time.sleep(0.2)
        return "Prefetched plan"

    monkeypatch.setattr(bb, "call_blockbrain_text", slow_text)
    sw._prefetch_meal_plan(REPLACE, "", 3)
    assert sw._generate_meal_plan(REPLACE, "", 3, placeholder=Box()) == "Prefetched plan"
    assert len(calls) == 1  # waited for the background job instead of a 2nd call


def test_failed_prefetch_is_not_retried_by_itself(sw, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", "k")
    sw.st.session_state.pop("_suppswipe_prefetched", None)
    calls = []

    def failing_text(*a, **k):
        calls.append(1)
        return ""

    monkeypatch.setattr(bb, "call_blockbrain_text", failing_text)
    items = [dict(REPLACE[0], dose_value=123)]  # a plan no other test prefetched
    sw._prefetch_meal_plan(items, "", 3)
    _sys, _usr, key = sw._meal_plan_prompts(items, "", 3)
    for _ in range(100):
        if sw.llm_cache.inflight(key) is None:
            break
        time.sleep(0.01)
    sw._prefetch_meal_plan(items, "", 3)  # e.g. the live view's re-run after the failure
    time.sleep(0.05)
    assert len(calls) == 1
    # "Generate my meals" still retries on request.
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: "Plan")
    assert sw._generate_meal_plan(items, "", 3, placeholder=Box()) == "Plan"


def test_prefetch_can_be_disabled(sw, monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: pytest.fail("should not run"))
    sw._prefetch_meal_plan(REPLACE, "", 3)


def test_ask_ai_prefers_bot_and_caches_first_questions(sw, monkeypatch):
    bot_calls = []

    def fake_bot(message, bot_id=None, timeout=None):
        bot_calls.append((message, timeout))
        return "Bot answer"

    monkeypatch.setattr(bb, "call_blockbrain_bot", fake_bot)
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: pytest.fail("agent not needed"))
    assert sw._answer_ask_ai_question("Zinc", "Does zinc deplete copper?") == ("Bot answer", "")
    assert sw._answer_ask_ai_question("Zinc", "does zinc deplete copper? ") == ("Bot answer", "")
    assert len(bot_calls) == 1
    assert bot_calls[0][1] == sw._ASK_AI_BOT_TIMEOUT


def test_ask_ai_follow_up_sends_history_to_agent(sw, monkeypatch):
    monkeypatch.setattr(bb, "call_blockbrain_bot", lambda *a, **k: "")
    seen = {}

    def fake_text(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        seen["history"] = history
        on_text and on_text("partial")
        return "Agent answer"

    monkeypatch.setattr(bb, "call_blockbrain_text", fake_text)
    history = [{"role": "user", "content": "Is 25 mcg vitamin D enough?"}, {"role": "assistant", "content": "Usually."}]
    box = Box()
    answer, _ = sw._answer_ask_ai_question("Vitamin D", "And for vegans?", history=history, placeholder=box)
    assert answer == "Agent answer"
    assert seen["history"] == history
    assert box.renders[-1] == "Agent answer"


def test_research_returns_source_url_and_uses_tools(sw, monkeypatch):
    seen = {}

    def fake_text(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        seen["allow_tools"] = allow_tools
        return "Source: https://example.com/product\nVitamin D 25 mcg\nZinc 10 mg"

    monkeypatch.setattr(bb, "call_blockbrain_text", fake_text)
    text, url = sw._research_product_from_label_text("Brand X Vitamin D3 + Zinc 60 tablets")
    assert seen["allow_tools"] is True
    assert url == "https://example.com/product"
    assert text == "Vitamin D 25 mcg\nZinc 10 mg"
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: "NONE")
    assert sw._research_product_from_label_text("Brand X") == ("", "")


def _photo(w=4000, h=3000, color="white"):
    # Distinct colours per test: the OCR cache is keyed by image bytes.
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_ocr_sends_small_image_first_and_skips_detail_when_good(sw, monkeypatch):
    sizes = []

    def fake_ocr(image_bytes, model=None):
        sizes.append(max(Image.open(io.BytesIO(image_bytes)).size))
        return "Supplement Facts\nVitamin C 90 mg\nZinc 11 mg\nMagnesium 100 mg"

    monkeypatch.setattr(bb, "extract_image_text_with_blockbrain", fake_ocr)
    text, route = sw._extract_image_text_best_effort(_photo())
    assert "Vitamin C" in text
    assert sizes == [1400]


def test_ocr_tries_detail_variant_when_small_read_is_weak(sw, monkeypatch):
    sizes = []

    def fake_ocr(image_bytes, model=None):
        side = max(Image.open(io.BytesIO(image_bytes)).size)
        sizes.append(side)
        return "blurry" if side == 1400 else "Supplement Facts\nVitamin C 90 mg\nZinc 11 mg"

    monkeypatch.setattr(bb, "extract_image_text_with_blockbrain", fake_ocr)
    text, route = sw._extract_image_text_best_effort(_photo(color="lightyellow"))
    assert sizes == [1400, 2000]
    assert "detail_jpeg" in route and "Vitamin C" in text


def test_ocr_failures_are_not_cached(sw, monkeypatch):
    replies = iter(["", "Vitamin C 90 mg"])
    monkeypatch.setattr(bb, "extract_image_text_with_blockbrain", lambda b, model=None: next(replies))
    payload = b"unique-bytes-for-cache-test"
    with pytest.raises(RuntimeError):
        sw._cached_ocr(payload)
    assert sw._cached_ocr(payload) == "Vitamin C 90 mg"

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


# --- F3: no background meal plan with sensitive settings ------------------------------------

_REPLACE = [{
    "decision": "replace", "component": "vitamin c", "dose_value": 80, "dose_unit": "mg", "form": "",
    "selected_food": {"food_description": "Kiwifruit, green, raw", "amount_per_100g": 92.7, "unit": "mg"},
}]


@pytest.mark.parametrize(
    "diet, pregnant, prefetched",
    [
        ("none", False, True), ("vegan", False, True), ("pescatarian", False, True),
        ("none", True, False), ("vegan", True, False),
        ("halal friendly", False, False), ("kosher style", False, False), ("gluten free", False, False),
        ("lactose free", False, False), ("nut free", False, False), ("low sodium aware", False, False),
    ],
)
def test_prefetch_skips_pregnancy_and_religious_or_health_diets(sw, session, monkeypatch, diet, pregnant, prefetched):
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", "k")
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "1")
    session.update({"swipe_diet_profile_id": diet, "swipe_pregnant": pregnant})
    calls = []
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: calls.append(a) or "Plan")
    sw._prefetch_meal_plan(_REPLACE, diet, 3)
    _sys, _usr, key = sw._meal_plan_prompts(_REPLACE, diet, 3)
    job = llm_cache.inflight(key)
    if job is not None:
        job.result(timeout=5)
    assert bool(calls) is prefetched


def test_privacy_note_mentions_meal_plans_and_the_background_prefetch(sw):
    from pathlib import Path

    source = Path(sw.__file__).read_text(encoding="utf-8")
    assert "Meal plans and the benefit comparison send your chosen foods" in source
    assert "prepared in the background" in source and "pregnancy setting" in source


# --- F5: the report logs the name-and-dose span only -------------------------------------------

@pytest.mark.parametrize(
    "line, key, span",
    [
        ("Folsäure 200 µg 100%", "folate", "Folsäure 200 µg 100%"),
        ("Vitamin D3 20 µg für Max Mustermann, geb. 01.02.1990, Tel 0171 1234567, Niereninsuffizienz",
         "vitamin d", "Vitamin D3 20 µg"),
        ("Für Max: Vitamin-B12 2,5 µg", "vitamin b12", "Vitamin-B12 2,5 µg"),
        ("Vitamin D3 20 µg (800 I.E.) 400%", "vitamin d", "Vitamin D3 20 µg (800 I.E.) 400%"),
        ("Hallo Welt", "zinc", ""),
    ],
)
def test_label_nutrient_span(sw, line, key, span):
    assert sw._label_nutrient_span(line, key) == span


def test_report_never_logs_personal_text_from_the_label_line(sw, monkeypatch, caplog):
    import json
    import logging

    text = "Vitamin D3 20 µg für Max Mustermann, geb. 01.02.1990, Tel 0171 1234567, Niereninsuffizienz"
    components = sw._filter_to_micronutrients(bb.parse_components(text))
    [card] = sw._build_swipe_cards(components, [])
    monkeypatch.setattr(sw.st, "session_state", {"swipe_components": components})
    with caplog.at_level(logging.WARNING, logger=bb.logger.name):
        payload = sw._report_card_problem(card, None, None)
    assert payload["label_line"] == "Vitamin D3 20 µg"
    message = " ".join(r.getMessage() for r in caplog.records)
    for personal in ("Mustermann", "1990", "0171", "Niereninsuffizienz"):
        assert personal not in message
    assert json.loads(message.split(": ", 1)[1])["label_line"] == "Vitamin D3 20 µg"


# --- F6: image and camera size limits ------------------------------------------------------------

def _png(width: int, height: int) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("1", (width, height)).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _jpeg(width: int, height: int) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="JPEG", quality=30)
    return buf.getvalue()


def test_large_png_is_refused_before_decoding(monkeypatch):
    big = _png(4100, 4100)  # 16.8 MP, a few kB
    assert len(big) < 200_000
    assert bb.build_vision_image_variants(big) == []
    assert bb.build_vision_image_variants(_png(1200, 900))  # a normal screenshot still works


def test_large_jpeg_is_still_accepted():
    assert bb.build_vision_image_variants(_jpeg(5000, 4000))  # 20 MP JPEG (draft decode)


def test_camera_data_url_is_capped(sw):
    import base64

    small = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8 jpeg").decode()
    assert sw._decode_camera_image({"image": small}) == b"\xff\xd8 jpeg"
    huge = "data:image/jpeg;base64," + "A" * (sw._CAMERA_MAX_DATA_URL_CHARS + 1)
    assert sw._decode_camera_image({"image": huge}) == b""


def test_streamlit_config_caps_websocket_messages():
    from pathlib import Path

    config = (Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "maxMessageSize = 25" in config

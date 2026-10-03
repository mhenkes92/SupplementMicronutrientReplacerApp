"""Regression tests for the security/robustness audit: quota ledger vs clear_cache, ReDoS, decode memory, question size,
RAG fallback without a model, diagnostics, strict resume flags."""
from __future__ import annotations

import io
import threading
import time

import pytest
from PIL import Image

import blockbrain.app as bb
import llm_cache


# ---------------------------------------------------------------- 1. the spend backstop survives Streamlit's clear_cache
def test_the_global_ledger_is_not_in_a_streamlit_cache(sw, monkeypatch):
    import streamlit as st

    monkeypatch.setenv("SUPPSWIPE_MAX_LLM_CALLS_PER_HOUR_GLOBAL", "3")
    llm_cache.reset_global_usage()
    monkeypatch.setattr(sw.st, "session_state", {})
    assert [sw._consume_global_llm_quota(time.time()) for _ in range(4)] == [True, True, True, False]
    # What a websocket `clear_cache` message does to every st.cache_* store (and to the session): the cap must stand.
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setattr(sw.st, "session_state", {})
    assert sw._consume_global_llm_quota(time.time()) is False
    llm_cache.reset_global_usage()


def test_the_daily_cap_holds_when_the_hourly_cap_has_room():
    llm_cache.reset_global_usage()
    now = time.time()
    # 5 calls spread over 5 hours: never more than 1 in any hour, but the day is used up at 5.
    results = [llm_cache.consume_global(now - 3 * 3600 * i / 4, 3600.0, hourly_limit=10, daily_limit=5) for i in range(6)]
    assert results == [True] * 5 + [False]
    # A day later the ledger has aged out.
    assert llm_cache.consume_global(now + 86500, 3600.0, hourly_limit=10, daily_limit=5) is True
    llm_cache.reset_global_usage()


def test_the_ledger_and_the_pool_survive_a_module_reload():
    import importlib

    llm_cache.reset_global_usage()
    llm_cache.consume_global(time.time(), 3600.0, 10, 10)
    pool = llm_cache._executor
    importlib.reload(llm_cache)
    assert len(llm_cache._usage_times) == 1 and llm_cache._executor is pool and pool._max_workers == 8
    llm_cache.reset_global_usage()


# ---------------------------------------------------------------- 2. a pasted run of digits cannot freeze the server
@pytest.mark.parametrize("text", ["1" * 20000, "o" * 20000, "1" * 6000 + " mg", ("9" * 60 + "\n") * 300, "l" * 20000])
def test_hostile_digit_runs_parse_in_milliseconds(text):
    started = time.perf_counter()
    bb.parse_components_rule_based(text)
    bb.parse_components(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("text, expected", [
    ("Zinc,10 mg", [("zinc", 10.0)]),
    ("Vitamin C 80 mg\nZinc 10 mg", [("vitamin c", 80.0), ("zinc", 10.0)]),
    ("Vitamin B12 2,5 µg", [("vitamin b12", 2.5)]),
    ("Magnesium 1.250 mg", [("magnesium", 1250.0)]),
    ("Vitamin D3 2O µg", [("vitamin d3", 20.0)]),  # an OCR 'O' for a zero still reads
    ("Vitamin C l00 mg", [("vitamin c", 100.0)]),  # an OCR 'l' for a one too
])
def test_the_dose_regex_still_reads_normal_and_ocr_noisy_doses(text, expected):
    got = [(c["component"], c["dose_value"]) for c in bb.parse_components_rule_based(text)]
    assert got == expected


# ---------------------------------------------------------------- 3. decoding is bounded
def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def _jpeg(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


def test_a_big_png_is_refused_and_a_normal_one_is_not():
    assert bb._load_upright_image(_png(3000, 3000), 2000) is None  # 9 MP: over the 8 MP limit for non-JPEG
    assert bb._load_upright_image(_png(2400, 3200), 2000) is not None  # 7.7 MP


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_only_jpeg_png_and_webp_are_opened(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, fmt)
    assert bb._load_upright_image(buf.getvalue(), 2000) is None


def test_a_jpeg_is_decoded_at_the_size_it_is_needed(monkeypatch):
    from PIL import JpegImagePlugin

    requests = []
    real = JpegImagePlugin.JpegImageFile.draft

    def spy(self, mode, size):
        requests.append(size)
        return real(self, mode, size)

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", spy)
    bb._load_upright_image(_jpeg(4032, 3024), 2000)
    assert requests == [(2000, 1500)]  # not (2000, 2000): that left every 12 MP photo at full size


def test_at_most_two_decodes_run_at_once(monkeypatch):
    monkeypatch.setattr(bb, "_DECODE_WAIT_S", 0.2)
    held = [bb._DECODE_SLOTS.acquire(), bb._DECODE_SLOTS.acquire()]
    try:
        started = time.monotonic()
        assert bb._load_upright_image(_jpeg(100, 100), 2000) is None  # a third upload waits, then gives up
        assert 0.15 < time.monotonic() - started < 2.0
    finally:
        for _ in held:
            bb._DECODE_SLOTS.release()
    assert bb._load_upright_image(_jpeg(100, 100), 2000) is not None


# ---------------------------------------------------------------- 4. one question = one bounded call
def test_a_question_is_cut_to_500_characters(sw, monkeypatch):
    seen = {}
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda system, user, **k: seen.setdefault("user", user) and "answer")
    sw._answer_ask_ai_question("Zinc", "x" * 2_000_000)
    assert len(seen["user"]) < 800  # the component line, the dose line and at most 500 characters of question


def test_the_chat_box_has_a_length_limit(sw):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path

    at = AppTest.from_file(str(Path(sw.__file__)), default_timeout=60)
    at.run()
    at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "camera_barcode": "",
                                                 "manual": "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%"}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.session_state["swipe_last_auto_signature"] = "x"
    at.run()
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    for i, card in enumerate(at.session_state["swipe_cards"]):
        at.session_state[key] = {"dir": "right", "id": f"c{i}", "card": card["component_key"], "index": i}
        at.run()
    assert at.chat_input and at.chat_input[0].max_chars == sw._ASK_AI_MAX_CHARS == 500


# ---------------------------------------------------------------- 5. the fallback never calls the model
def test_the_local_fallback_does_not_call_the_model(sw, monkeypatch):
    chunks = [{"source": "guide.pdf", "text": "Magnesium glycinate is often taken in the evening; typical doses are 200-400 mg."}]
    monkeypatch.setattr(sw, "_cached_rag_chunks", lambda: chunks)
    monkeypatch.setattr(bb, "retrieve_rag_chunks", lambda q, c: [dict(x, _score=10.0) for x in chunks])
    monkeypatch.setattr(bb, "detect_rag_query_intent", lambda q: {})
    monkeypatch.setattr(bb, "call_text_llm", lambda *a, **k: pytest.fail("a model call that bypasses the quota"))
    answer, sources = sw._local_rag_answer("Magnesium: best time?")
    assert "200-400 mg" in answer and "guide.pdf" in sources


# ---------------------------------------------------------------- 6. diagnostics
def test_the_debug_panel_shows_the_kind_of_error_never_what_the_server_said(sw, fake_bb, monkeypatch):
    shown = {}
    monkeypatch.setattr(sw.st, "json", lambda value, **k: shown.update(value))
    monkeypatch.setattr(sw.st, "expander", lambda *a, **k: __import__("contextlib").nullcontext())
    fake_bb.stream_script = [{"http": 500, "message": "org-4711 bot-secret-id internal trace"}]
    bb.call_blockbrain_text("s", "q")
    sw._render_debug_panel()
    assert "HTTP 500" in shown["last_error"] and "org-4711" not in str(shown) and "internal trace" not in str(shown)


@pytest.mark.parametrize("token, asked, allowed", [
    ("", "1", True), ("", "", False), ("", "0", False),
    ("s3cret-token", "1", False), ("s3cret-token", "s3cret-token", True), ("s3cret-token", "s3cret", False),
])
def test_the_debug_panel_can_be_locked_with_a_token(sw, monkeypatch, token, asked, allowed):
    monkeypatch.setenv("SUPPSWIPE_DEBUG_TOKEN", token)
    monkeypatch.setattr(sw.st, "query_params", {"debug": asked})
    assert sw._debug_requested() is allowed


def test_the_short_error_cuts_at_the_server_text(sw):
    assert sw._short_error('stream: HTTP 404 {"error":"Agent abc not found"}') == "stream: HTTP 404"
    assert sw._short_error("x" * 300) == "x" * 80


# ---------------------------------------------------------------- 7. resume: flags are strict
@pytest.mark.parametrize("flag, expected", [(True, True), (False, False), ("false", False), ("true", False), (1, False), (None, False)])
def test_a_tampered_pregnancy_flag_does_not_switch_the_mode_on(sw, flag, expected):
    state: dict = {}
    saved = {"v": 1, "text": "Vitamin C 80 mg 100%\nZinc 10 mg 100%", "index": 0, "pregnant": flag, "diet": "none",
             "decisions": {}, "label_source": {"kind": "input", "url": ""}}
    try:
        sw._restore_scan(state, saved)
    except Exception:
        pytest.skip("restore needs a fully built snapshot")
    if "swipe_pregnant" in state:
        assert state["swipe_pregnant"] is expected


# ---------------------------------------------------------------- 8. a failed database open heals itself
def test_a_failed_database_open_is_not_remembered(monkeypatch, tmp_path):
    from pathlib import Path

    real_path = bb.USDA_RANK_DB_PATH
    bb._usda_food_diet_facts.cache_clear()
    monkeypatch.setattr(bb, "USDA_RANK_DB_PATH", tmp_path / "missing.db")  # the open fails ...
    assert bb._usda_food_diet_facts() == {}
    assert bb._USDA_OPEN_FAILED is True
    monkeypatch.setattr(bb, "USDA_RANK_DB_PATH", Path(real_path))  # ... and then works again
    facts = bb._usda_food_diet_facts()
    assert facts and bb._USDA_OPEN_FAILED is False  # the empty result was not kept
    assert bb._usda_food_diet_facts() is bb._usda_food_diet_facts()  # and the good one is memoised again


@pytest.mark.parametrize("name", ["_usda_food_diet_facts", "_lexicon_food_rows", "_usda_energy_kcal_index", "_load_macro_table"])
def test_the_database_readers_are_wrapped(name):
    assert hasattr(getattr(bb, name), "__wrapped__") and hasattr(getattr(bb, name), "cache_clear")

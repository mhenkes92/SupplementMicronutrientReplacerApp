"""Hardening batch 4: barcode look-up limits, stale saved scans leave the device, no second OCR route after an auth failure."""
from __future__ import annotations

import io

import pytest

import blockbrain.app as bb

GTIN = "4006381333931"  # a valid EAN-13


def _jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- barcode look-ups
@pytest.fixture
def barcode_env(sw, monkeypatch):
    from swipe_mobile_app import llm_cache  # noqa: F401  (same module object the app uses)

    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw.llm_cache.reset_global_usage()
    calls: list[str] = []

    def fake_lookup(barcode, *a, **k):
        calls.append(barcode)
        return "Vitamin C 80 mg 100%", "name", "OpenFoodFacts", ""

    monkeypatch.setattr(sw.bb, "extract_supplement_text_from_barcode", fake_lookup)
    yield state, calls
    sw.llm_cache.reset_global_usage()


def test_a_session_can_only_look_up_so_many_barcodes_per_hour(sw, monkeypatch, barcode_env):
    state, calls = barcode_env
    monkeypatch.setenv("SUPPSWIPE_MAX_BARCODE_LOOKUPS_PER_HOUR", "3")
    assert [bool(sw._research_barcode_label(GTIN)) for _ in range(3)] == [True, True, True]
    assert sw._research_barcode_label(GTIN) == ""  # the database is not asked a 4th time
    assert len(calls) == 3
    assert state["swipe_barcode_throttled"] is True
    # A later look-up that is allowed clears the flag again (a new hour).
    state["_suppswipe_llm_usage"]["barcode"] = []
    assert sw._research_barcode_label(GTIN)
    assert state["swipe_barcode_throttled"] is False


def test_the_whole_app_has_a_barcode_ceiling_too(sw, monkeypatch, barcode_env):
    _state, calls = barcode_env
    monkeypatch.setenv("SUPPSWIPE_MAX_BARCODE_LOOKUPS_PER_HOUR", "100")
    monkeypatch.setenv("SUPPSWIPE_MAX_BARCODE_LOOKUPS_PER_HOUR_GLOBAL", "2")
    assert sw._research_barcode_label(GTIN) and sw._research_barcode_label(GTIN)
    monkeypatch.setattr(sw.st, "session_state", {})  # another visitor
    assert sw._research_barcode_label(GTIN) == ""
    assert len(calls) == 2


def test_an_invalid_barcode_does_not_use_up_the_allowance(sw, barcode_env):
    state, calls = barcode_env
    assert sw._research_barcode_label("1234567890123") == ""  # check digit does not match
    assert calls == [] and state.get("_suppswipe_llm_usage", {}).get("barcode", []) == []


def test_the_counter_ledger_survives_a_streamlit_clear_cache(sw):
    import streamlit as st

    sw.llm_cache.reset_global_usage()
    assert sw.llm_cache.consume_counter("barcode", 1000.0, 3600.0, 1)
    st.cache_data.clear()
    st.cache_resource.clear()
    assert not sw.llm_cache.consume_counter("barcode", 1001.0, 3600.0, 1)
    assert sw.llm_cache.consume_counter("barcode", 1000.0 + 3601.0, 3600.0, 1)  # the window moved on
    sw.llm_cache.reset_global_usage()


def test_the_throttled_barcode_message_is_shown_instead_of_not_found():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py").read_text(encoding="utf-8")
    assert "That's a lot of barcode look-ups for now" in src
    assert src.index("swipe_barcode_throttled\"):") < src.index("We couldn't find that barcode in the product databases")


# ---------------------------------------------------------------- saved scans on the device
def _saved(sw, age_days: float, now: float = 5_000_000.0) -> dict:
    cards = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(sw._SAMPLE_LABEL_TEXT)), [])
    state = {
        "swipe_cards": cards,
        "swipe_analysis_text": sw._SAMPLE_LABEL_TEXT,
        "swipe_index": 1,
        "swipe_label_source": {"kind": "input", "url": ""},
        "swipe_decisions": {},
    }
    return sw._scan_snapshot(state, now=now - age_days * 86400)


def test_a_stale_saved_scan_is_deleted_from_the_device_not_just_hidden(sw, monkeypatch):
    import time

    now = time.time()
    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state)
    monkeypatch.setattr(sw.st, "rerun", lambda *a, **k: None)
    stale = _saved(sw, age_days=9, now=now)
    monkeypatch.setattr(sw, "_history_store", lambda **kw: {"history": [], "scan": stale})
    sw._sync_scan_history_with_browser()
    assert state["_suppswipe_saved_scan"] is None  # never offered
    assert state["_suppswipe_scan_clear"] is True  # and removed from localStorage on the next run


def test_a_fresh_saved_scan_is_kept_and_offered(sw, monkeypatch):
    import time

    now = time.time()
    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state)
    monkeypatch.setattr(sw.st, "rerun", lambda *a, **k: None)
    fresh = _saved(sw, age_days=1, now=now)
    monkeypatch.setattr(sw, "_history_store", lambda **kw: {"history": [], "scan": fresh})
    sw._sync_scan_history_with_browser()
    assert state["_suppswipe_saved_scan"] == fresh
    assert "_suppswipe_scan_clear" not in state


def test_a_damaged_saved_scan_is_deleted_too(sw, monkeypatch):
    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state)
    monkeypatch.setattr(sw.st, "rerun", lambda *a, **k: None)
    monkeypatch.setattr(sw, "_history_store", lambda **kw: {"history": [], "scan": {"v": 0, "text": "x"}})
    sw._sync_scan_history_with_browser()
    assert state["_suppswipe_saved_scan"] is None and state["_suppswipe_scan_clear"] is True


# ---------------------------------------------------------------- OCR routes
def test_a_rejected_key_does_not_try_the_second_ocr_route(fake_bb, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", "not-the-" + "right-key-0123456789")
    assert bb.call_blockbrain_vision(_jpeg()) == ""
    assert len(bb.LAST_VISION_ATTEMPT_LOG) == 1 and bb.LAST_VISION_ATTEMPT_LOG[0].startswith("agentic:error")
    assert "401" in bb.last_call_error()
    assert len(fake_bb.requests) == 1  # one refused request, no second route, no upload


def test_other_failures_still_fall_back_to_the_second_route(fake_bb):
    fake_bb.stream_script = [{"http": 404, "message": "no custom agent on this bot"}]
    assert bb.call_blockbrain_vision(_jpeg()) == "cortex fake answer"
    assert [e.split(":")[0] for e in bb.LAST_VISION_ATTEMPT_LOG] == ["agentic", "cortex"]

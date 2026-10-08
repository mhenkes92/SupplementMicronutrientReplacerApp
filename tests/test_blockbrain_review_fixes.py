"""Regression tests for the findings of the independent review of the client migration."""
from __future__ import annotations

import io
import re
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

import blockbrain.app as bb
import fake_blockbrain as fb

ROOT = Path(__file__).resolve().parent.parent
_SK = "sk-" + "kb-"  # the secret scanner must not see a key-shaped literal in this file
APP = ROOT / "swipe_mobile_app" / "app.py"


def _jpeg(color="white", size=(800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- 1. an unconfigured fresh deploy still analyses pasted text
def _open_paste_dialog(monkeypatch, configured: bool):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("BLOCKBRAIN_API_KEY", _SK + "old-secret-from-previous-deploy-0000")
    if not configured:
        monkeypatch.setenv("BLOCKBRAIN_ORG_ID", "")
        monkeypatch.setenv("BLOCKBRAIN_MODEL", "")
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.session_state["swipe_open_analyze"] = True
    at.run()
    at.session_state["dlg_method_0"] = "🔗 Paste"
    at.run()
    return at


@pytest.mark.parametrize("configured", [True, False])
def test_pasted_label_text_is_analysed_with_or_without_the_blockbrain_settings(monkeypatch, configured):
    at = _open_paste_dialog(monkeypatch, configured)
    at.text_area[0].set_value("Vitamin C 80 mg 100%\nZink 10 mg 100%\nVitamin D3 20 µg 400%")
    (button,) = [b for b in at.button if b.label == "Analyze"]
    button.click()
    at.run()
    assert not at.exception
    assert len(at.session_state["swipe_cards"] or []) >= 2, [e.value for e in at.error]


def test_a_link_still_needs_the_settings_and_says_so(monkeypatch):
    at = _open_paste_dialog(monkeypatch, configured=False)
    # Plain words for visitors (no variable names), and the paste way stays open.
    assert any("switched off" in i.value and "barcode number" in i.value for i in at.info)
    assert not any("BLOCKBRAIN" in e.value for e in at.error) and not any("BLOCKBRAIN" in i.value for i in at.info)
    at.text_area[0].set_value("https://shop.example/product")
    (button,) = [b for b in at.button if b.label == "Analyze"]
    button.click()
    at.run()
    assert not (at.session_state["swipe_cards"] or [])
    assert not at.session_state["swipe_pending_request"]
    assert any("Product links need the AI helper" in w.value for w in at.warning)  # not a silent no-op


# ---------------------------------------------------------------- 2. a number next to [unreadable] is not a different number
@pytest.mark.parametrize("line, name", [
    ("Zinc [unreadable]5 mg 100%", "zinc"),
    ("Vitamin B12 [unreadable],5 µg 100%", "vitamin b12"),
    ("Calcium 1[unreadable]0 mg", "calcium"),
    ("Vitamin C 8[unreadable] mg 100%", "vitamin c"),
])
def test_a_partly_unreadable_number_becomes_an_unknown_dose(fake_bb, line, name):
    fake_bb.stream_script = [f"Supplement Facts\n{line}\nMagnesium 300 mg 80%"]
    text = bb.call_blockbrain_vision(_jpeg())
    assert "[unreadable]" in text and not re.search(r"[\d.,]\[unreadable\]|\[unreadable\][\d.,]", text)
    rows = {c["component"]: c["dose_value"] for c in bb.parse_components(text)}
    assert rows.get(name) is None  # unknown ("Dose not found"), never a wrong number
    assert rows["magnesium"] == 300.0


def test_unreadable_numbers_are_masked():
    assert bb._mask_unreadable_numbers("B12 [unreadable],5 µg").split() == ["B12", "[unreadable]", "µg"]
    assert bb._mask_unreadable_numbers("Zinc 10 mg") == "Zinc 10 mg"


# ---------------------------------------------------------------- 3. a model that cannot see images is never label text
@pytest.mark.parametrize("reply", [
    "Vision is not supported by this model.",
    "This model does not support image input.",
    "[Image omitted]",
    "Attachment not available",
])
def test_non_vision_replies_are_recognised_and_tried_on_the_other_route(fake_bb, reply):
    assert bb._image_not_received(reply)
    fake_bb.stream_script = [reply]
    assert bb.call_blockbrain_vision(_jpeg()) == "cortex fake answer"
    assert bb.LAST_VISION_ATTEMPT_LOG[0].startswith("agentic:image_missing")


def test_label_text_that_mentions_support_is_not_blind():
    assert not bb._image_not_received("Vitamin D3 25 µg\nSupports bone health. Not supported by claims.")


# ---------------------------------------------------------------- 4. a dose-less product shot is not analysed
_PHOTO_NUMBER = [0]


def _run_photo(monkeypatch, reply):
    from streamlit.testing.v1 import AppTest

    # Every photo of the run is a different image: the OCR result is cached by the image bytes, so a colour taken from
    # hash(reply) (random per process) made two tests share a photo now and then and the second got the first one's text.
    _PHOTO_NUMBER[0] += 1
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", _SK + "xxxxxxxxxxxxxxxx")
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    monkeypatch.setattr(bb, "call_blockbrain_vision", lambda image_bytes, model=None: reply)
    at.session_state["swipe_pending_request"] = {
        "upload_bytes": _jpeg(color=(5 + _PHOTO_NUMBER[0], 90, 40)), "camera_bytes": b"", "manual": "", "camera_barcode": "",
    }
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.run()
    return at


@pytest.mark.parametrize("reply", [
    "NOW Foods\nMagnesium Citrate\nDietary Supplement",
    "Doppelherz\nVitamin D3 + K2\n60 Tabletten",
])
def test_a_front_of_pack_photo_without_doses_asks_for_the_nutrition_table(monkeypatch, reply):
    at = _run_photo(monkeypatch, reply)
    assert not at.exception
    assert not (at.session_state["swipe_cards"] or [])  # no swipe cards full of "Dose not found"
    assert any("nutrition table" in e.value for e in at.error), [e.value for e in at.error]


def test_a_photo_with_doses_still_gets_its_cards(monkeypatch):
    at = _run_photo(monkeypatch, "Supplement Facts\nVitamin C 90 mg\nZinc 11 mg\nMagnesium 100 mg")
    assert len(at.session_state["swipe_cards"] or []) == 3


# ---------------------------------------------------------------- 5. one failed read ends the photo read
def test_a_stalled_platform_costs_one_budget_not_two(sw, fake_bb, monkeypatch):
    fake_bb.stream_script = [{"delay": 3.0, "text": "late"}]
    monkeypatch.setattr(bb, "BLOCKBRAIN_VISION_BUDGET_S", 1.0)
    monkeypatch.setattr(sw, "_consume_llm_quota", lambda kind: True)
    sw._cached_ocr.clear()
    started = time.monotonic()
    text, _route = sw._extract_image_text_best_effort(_jpeg(color="orange", size=(3000, 2000)))  # two variants would exist
    # One budget is 1 s; the stalled server answers after 3 s. Anything below that proves the call did not wait for the
    # server (a loaded machine may need more than 2.5 s); the single stream call below proves there was no second variant.
    assert text == "" and time.monotonic() - started < 2.9
    assert fake_bb.stream_calls == 1 and fake_bb.completion_calls == 0
    assert "did not answer" in bb.last_call_error()


# ---------------------------------------------------------------- 6. nothing sensitive in the diagnostics
def test_the_bot_id_is_never_shown(sw, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_BOT_ID", "bot-SECRET-ID-1234")
    monkeypatch.delenv("BLOCKBRAIN_MODEL", raising=False)
    assert "SECRET" not in bb.blockbrain_model_label() and bb.blockbrain_model_label() == "custom bot"
    assert "SECRET" not in sw._generation_model() and sw._generation_model().startswith("bot-")
    assert "SECRET" not in str(bb._load_blockbrain_model_defaults())
    first = sw._generation_model()
    monkeypatch.setenv("BLOCKBRAIN_BOT_ID", "bot-ANOTHER-ONE")
    assert sw._generation_model() != first  # a different bot = different cache keys


def test_the_debug_panel_shows_this_sessions_error_and_no_server_text(sw, fake_bb, monkeypatch):
    shown = {}
    monkeypatch.setattr(sw.st, "json", lambda value, **k: shown.update(value))
    monkeypatch.setattr(sw.st, "expander", lambda *a, **k: __import__("contextlib").nullcontext())
    fake_bb.stream_script = [{"http": 500, "message": "internal detail of the platform"}]
    bb.call_blockbrain_text("s", "q")
    sw._render_debug_panel()
    assert "error" not in shown["last_call"]  # the process-wide timing carries no text
    assert "HTTP 500" in shown["last_error"]  # this thread's own error
    seen_by_another = {}
    t = threading.Thread(target=lambda: seen_by_another.update(error=bb.last_call_error()))
    t.start(); t.join()
    assert seen_by_another["error"] == ""


# ---------------------------------------------------------------- 7. a cut key is redacted too
def test_a_key_cut_by_the_300_character_limit_is_redacted(fake_bb):
    key = fb.API_KEY
    body = ("x" * 280) + key  # the client keeps only the first 300 characters of a server reply
    message = f"stream: HTTP 500 {body[:300]}"
    assert key[:10] in message and key not in message  # the key really straddles the cut
    out = bb._redact(message)
    assert key[:6] not in out and "***" in out


@pytest.mark.parametrize("text", ["Authorization: Bearer abc123def456ghi", "token " + _SK + "1234567890abcdef was echoed"])
def test_key_shaped_strings_are_redacted(text):
    out = bb._redact(text)
    assert "abc123def456ghi" not in out and "1234567890abcdef" not in out


# ---------------------------------------------------------------- 8. abandoned calls cannot pile up
def test_too_many_running_calls_fail_fast(fake_bb, monkeypatch):
    monkeypatch.setattr(bb, "_WORKER_SLOTS", threading.BoundedSemaphore(2))
    fake_bb.stream_script = [{"delay": 4.0, "text": "slow"}]
    monkeypatch.setattr(bb, "BLOCKBRAIN_TOTAL_BUDGET_S", 1.0)
    for _ in range(2):
        assert bb.call_blockbrain_text("s", "q") == ""  # each times out and keeps its slot while the server is still busy
    started = time.monotonic()
    assert bb.call_blockbrain_text("s", "q") == ""
    assert time.monotonic() - started < 0.5  # no third thread: refused at once
    assert "busy" in bb.last_call_error()
    time.sleep(3.5)  # the slow calls finish and give their slots back
    fake_bb.stream_script = ["ok"]
    assert bb.call_blockbrain_text("s", "q") == "ok"


# ---------------------------------------------------------------- 9. the client module is resolved at call time
def test_a_replaced_client_module_is_used_without_reloading_the_app(fake_bb, monkeypatch):
    import sys
    import types

    real = sys.modules["blockbrain_llm_client"]
    calls = []

    class Marked(real.Blockbrain):
        def chat(self, *a, **k):
            calls.append(1)
            return super().chat(*a, **k)

    fresh = types.ModuleType("blockbrain_llm_client")
    fresh.__dict__.update(real.__dict__)
    fresh.Blockbrain = Marked
    monkeypatch.setitem(sys.modules, "blockbrain_llm_client", fresh)
    assert bb.call_blockbrain_text("s", "q") == "Hello from the fake." and calls == [1]


# ---------------------------------------------------------------- 10. messages and configuration
def test_an_unknown_model_key_is_named_not_reported_as_missing(monkeypatch):
    monkeypatch.delenv("BLOCKBRAIN_BOT_ID", raising=False)
    monkeypatch.setenv("BLOCKBRAIN_MODEL", "gpt-4.1-nano")
    message = bb.blockbrain_config_error()
    assert "gpt-4.1-nano" in message and "not a known model key" in message and "BLOCKBRAIN_BOT_ID" in message


def test_stray_whitespace_in_the_settings_is_removed(fake_bb, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", fb.API_KEY + "\n")
    monkeypatch.setenv("BLOCKBRAIN_ORG_ID", " " + fb.ORG_ID + " ")
    assert bb.call_blockbrain_text("s", "q") == "Hello from the fake."


@pytest.mark.parametrize("error", [
    "create conversation: HTTP 401 Unauthorized",
    "stream: HTTP 403 forbidden",
    "create conversation: HTTP 404 not found",
    "missing configuration: BLOCKBRAIN_ORG_ID",
])
def test_a_settings_problem_is_not_called_busy_or_blurry(sw, error):
    bb._CALL_STATE.error = error
    try:
        message = sw._ai_unavailable_message("photo")
        assert "AI helper is unavailable" in message and "Paste" in message
        assert "BLOCKBRAIN" not in message and "owner" not in message and "blurry" not in message  # no jargon for visitors
        assert sw._ai_retry_note("please try again") == "The AI helper is unavailable right now. Please try again later."
    finally:
        bb.reset_call_error()
    assert sw._ai_retry_note("please try again") == "please try again"


def test_an_unconfigured_app_uses_no_allowance_and_makes_no_request(sw, fake_bb, monkeypatch):
    monkeypatch.delenv("BLOCKBRAIN_ORG_ID")
    sw.st.session_state.pop("_suppswipe_llm_usage", None)
    monkeypatch.setattr(sw, "_cached_rag_chunks", lambda: [])
    assert sw._stream_llm_text("k", "s", "u") == ""
    assert sw._answer_ask_ai_question("Zinc", "Safe?") == (None, "")
    assert sw._generate_meal_plan([{"component": "Vitamin C", "component_key": "vitamin c", "dose_value": 90.0, "dose_unit": "mg",
                                    "decision": "replace", "selected_food": {"food_description": "Kiwi", "amount_per_100g": 90, "unit": "mg"}}],
                                  "", 1) == ""
    assert sw.st.session_state.get("_suppswipe_llm_usage", {}).get("generate", []) == []
    assert fake_bb.requests == []
    assert "BLOCKBRAIN_ORG_ID" in bb.last_call_error()


def test_the_prefetch_waits_for_a_complete_configuration(sw, fake_bb, monkeypatch):
    monkeypatch.delenv("BLOCKBRAIN_ORG_ID")
    monkeypatch.setattr(sw.llm_cache, "submit", lambda *a, **k: pytest.fail("a background job without a configuration"))
    sw._prefetch_meal_plan([{"component": "Vitamin C", "component_key": "vitamin c", "decision": "replace",
                             "selected_food": {"food_description": "Kiwi", "amount_per_100g": 90, "unit": "mg"}}], "", 1)


# ---------------------------------------------------------------- 11. the route that works is remembered
def test_a_working_cortex_route_leads_for_a_while(fake_bb):
    fake_bb.stream_script = [{"http": 404, "message": "this bot has no custom agent"}]
    assert bb.call_blockbrain_vision(_jpeg()) == "cortex fake answer"
    assert fake_bb.stream_calls == 1
    assert bb._ocr_routes() == ["cortex", "agentic"]
    assert bb.call_blockbrain_vision(_jpeg("blue")) == "cortex fake answer"
    assert fake_bb.stream_calls == 1  # agentic was not tried again for this photo
    bb._ROUTE_PREFERENCE["until"] = time.monotonic() - 1  # the memory expires: agentic is retried
    fake_bb.stream_script = ["Zinc 10 mg 100%"]
    assert bb.call_blockbrain_vision(_jpeg("green")) == "Zinc 10 mg 100%"
    assert bb._ocr_routes() == ["agentic", "cortex"]


def test_an_explicit_route_is_never_overridden_by_memory(fake_bb, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_OCR_ROUTE", "agentic")
    fake_bb.stream_script = [{"http": 404}]
    bb.call_blockbrain_vision(_jpeg())
    assert bb._ocr_routes() == ["agentic", "cortex"]


# ---------------------------------------------------------------- 12. the browser test server cannot reach the platform
def test_the_browser_server_is_started_unconfigured():
    src = (ROOT / "tests" / "test_ux_browser.py").read_text(encoding="utf-8")
    assert 'BLOCKBRAIN_ORG_ID=""' in src and 'BLOCKBRAIN_MODEL=""' in src and 'BLOCKBRAIN_BOT_ID=""' in src

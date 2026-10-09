"""UX audit fixes (mobile walk-through): AI-off states, one clear error, no lost paste, sample label, copy and layout hooks."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "swipe_mobile_app" / "app.py")


def _unconfigured(monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_ORG_ID", "")
    monkeypatch.setenv("BLOCKBRAIN_MODEL", "")
    monkeypatch.setenv("BLOCKBRAIN_BOT_ID", "")


def _analyse(at, manual: str):
    at.session_state["swipe_pending_request"] = {
        "upload_bytes": b"", "camera_bytes": b"", "camera_barcode": "", "manual": manual,
    }
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.session_state["swipe_last_auto_signature"] = "x"
    at.run()


def _finish_swipes(at, direction="left"):
    cards = at.session_state["swipe_cards"]
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    for i, card in enumerate(cards):
        at.session_state[key] = {"dir": direction, "id": f"c{i}", "card": card["component_key"], "index": i}
        at.run()
    assert at.session_state["swipe_index"] == len(cards)


# ---------------------------------------------------------------- the Analyze dialog while the AI is off
def test_the_dialog_starts_on_paste_with_a_plain_note_when_the_ai_is_off(monkeypatch):
    _unconfigured(monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.session_state["swipe_open_analyze"] = True
    at.run()
    assert at.session_state["dlg_method_0"] == "🔗 Paste"  # not a camera that cannot be read
    notes = [i.value for i in at.info]
    assert any("switched off" in n and "barcode number" in n for n in notes)
    assert not any(re.search(r"BLOCKBRAIN|environment|secrets", n + " ".join(e.value for e in at.error)) for n in notes)


def test_the_dialog_starts_on_the_camera_when_the_ai_is_on():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.session_state["swipe_open_analyze"] = True
    at.run()
    assert at.session_state["dlg_method_0"] == "📷 Camera"
    assert not any("switched off" in i.value for i in at.info)


# ---------------------------------------------------------------- AI surfaces are explained, not offered
def test_the_results_offer_no_ai_button_while_the_ai_is_off(monkeypatch):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _analyse(at, "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%")
    _unconfigured(monkeypatch)
    _finish_swipes(at, "right")  # replace everything: the meal and comparison surfaces are there
    assert not at.exception
    labels = [b.label for b in at.button]
    assert "Generate my meals" not in labels and "Show the comparison" not in labels
    assert not at.chat_input  # no chat box that can only end in an error
    captions = " ".join(c.value for c in at.caption)
    assert "AI meal plan is switched off" in captions and "AI answers are switched off" in captions
    at.button(key="planbtn_food_0").click().run()  # the comparison lives in the food's options window
    assert "Show the comparison" not in [b.label for b in at.button]
    assert "AI comparison is switched off" in " ".join(c.value for c in at.caption)


def test_the_ai_buttons_are_there_when_the_ai_is_on(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")  # no background plan: the button is what the visitor sees
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _analyse(at, "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%")
    _finish_swipes(at, "right")
    labels = [b.label for b in at.button]
    assert "Generate my meals" in labels and "Show the comparison" not in labels  # that one is in the food's window
    assert at.chat_input
    at.button(key="planbtn_food_0").click().run()
    assert "Show the comparison" in [b.label for b in at.button]


def test_the_cards_ask_ai_tool_is_not_offered_while_the_ai_is_off_and_a_forged_tap_opens_nothing(monkeypatch):
    """The card's Ask AI button exists only with a configured AI (a button that only ends in an error is no button); a forged
    "ask" event opens no sheet either. With the AI on, the same tap opens the Ask AI sheet."""
    from card_tools import card_args, sheet_titles, tap_tool

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _analyse(at, "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%")
    assert card_args(at)["canAsk"] is True  # configured (the tests' fake key): the button is there
    _unconfigured(monkeypatch)
    at.run()
    assert card_args(at)["canAsk"] is False and card_args(at)["canSwap"] is True  # only Ask AI goes
    tap_tool(at, "ask")
    assert not at.exception and sheet_titles(at) == [] and at.session_state["swipe_sheet"] is None
    assert not at.chat_input


def test_no_empty_assistant_bubble_when_asking_fails(sw, monkeypatch):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _analyse(at, "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%")
    _finish_swipes(at, "right")
    import blockbrain.app as bb

    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: "")  # the model fails ...
    monkeypatch.setattr(sw, "_cached_rag_chunks", lambda: [])  # ... and the local index has nothing
    at.chat_input[0].set_value("Is zinc safe?").run()
    assert not at.exception
    assert not [m for m in at.chat_message if m.name == "assistant"]  # no avatar with nothing in it
    assert any("unavailable" in e.value for e in at.error)


# ---------------------------------------------------------------- errors: one message, and the paste comes back
def test_an_invalid_barcode_gives_one_message_and_no_analyzing_chip():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _analyse(at, "1234567890123")
    assert len(at.error) == 1 and "check digit" in at.error[0].value
    assert not any("Analyzing" in m.value for m in at.markdown)  # the pill is gone with the progress
    assert not at.warning  # no second, stacked message


def test_nothing_parsed_says_what_to_paste():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _analyse(at, "hello world asdf qwerty")
    assert len(at.error) == 1
    assert "Supplement Facts" in at.error[0].value and "Vitamin D3 20" in at.error[0].value


def test_a_failed_paste_is_still_in_the_box_when_the_dialog_reopens():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _analyse(at, "hello world asdf qwerty")
    assert at.session_state["swipe_paste_draft"] == "hello world asdf qwerty"
    at.session_state["swipe_open_analyze"] = True
    at.run()
    at.session_state["dlg_method_0"] = "🔗 Paste"
    at.run()
    assert at.text_area[0].value == "hello world asdf qwerty"
    assert "swipe_paste_draft" not in at.session_state  # used once


def test_text_input_does_not_say_preparing_ai():
    src = Path(APP).read_text(encoding="utf-8")
    assert "Preparing AI analysis" not in src and "Preparing your analysis" in src


# ---------------------------------------------------------------- the sample label
def test_the_sample_label_is_marked_english_and_not_saved_as_a_scan():
    import importlib.util

    spec = importlib.util.spec_from_file_location("suppswipe_app_for_sample", APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not re.search(r"\b(Zink|Folsäure|Selen\b|Nährwert)", module._SAMPLE_LABEL_TEXT)  # the app speaks English

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="appbar_scan").click().run()  # the sample button lives in the Scan sheet
    at.button(key="swipe_try_sample").click().run()
    assert at.session_state["swipe_label_source"]["kind"] == "sample"
    assert any("Sample label" in c.value for c in at.caption)  # on the cards ...
    _finish_swipes(at)
    assert at.session_state["suppswipe_scan_history"] == []  # ... never in "Recent scans"
    assert any("Sample label" in c.value for c in at.caption)  # ... and on the results


# ---------------------------------------------------------------- copy and layout hooks
@pytest.mark.parametrize("pct, phrase", [
    (45, "45% of the daily need"), (100, "100% of the daily need"), (199, "199% of the daily need"),
    (230, "2.3× the daily need"), (1100, "11× the daily need"),
])
def test_the_bonus_phrase_reads_right_for_percent_and_times(sw, pct, phrase):
    assert sw._format_need_phrase(pct) == phrase


def test_css_and_copy_follow_the_audit():
    src = Path(APP).read_text(encoding="utf-8")
    assert "padding-bottom: calc(5rem + env(safe-area-inset-bottom" in src  # room for the "Manage app" pill
    assert re.search(r'\[data-baseweb="tab"\]\s*\{\s*min-height: 44px', src.replace(",\n            [data-baseweb", ", [data-baseweb"))
    assert "prefers-reduced-motion: reduce" in src and ".plan-dots i" in src
    assert "Approximate German discounter prices" in src and "2025" not in src.split("Approximate German discounter prices")[0][-80:]
    assert "Analyze my Supplement" not in src


def test_the_swipe_frame_only_grows_and_the_camera_fits_a_short_phone():
    swipe = (ROOT / "swipe_mobile_app" / "swipe_component" / "index.html").read_text(encoding="utf-8")
    assert "maxHeight" in swipe and "Math.max(HEIGHT, Math.min(780, contentHeight + actionsHeight + 10), maxHeight)" in swipe
    camera = (ROOT / "swipe_mobile_app" / "camera_component" / "index.html").read_text(encoding="utf-8")
    assert "height: var(--cam-h, 300px)" in camera and "parentHeight * 0.34" in camera

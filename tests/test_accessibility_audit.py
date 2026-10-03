"""Accessibility audit fixes: heading semantics, contrast, keyboard focus, screen-reader announcements, camera component."""
from __future__ import annotations

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "swipe_mobile_app" / "app.py")
APP_SRC = (ROOT / "swipe_mobile_app" / "app.py").read_text(encoding="utf-8")
SWIPE = (ROOT / "swipe_mobile_app" / "swipe_component" / "index.html").read_text(encoding="utf-8")
CAMERA = (ROOT / "swipe_mobile_app" / "camera_component" / "index.html").read_text(encoding="utf-8")


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


# ---------------------------------------------------------------- the page and results
def test_the_app_headings_are_real_headings_for_screen_readers():
    assert "class=\"brand\" role=\"heading\" aria-level=\"1\"" in APP_SRC
    assert "class='hero-title' role='heading' aria-level='2'" in APP_SRC
    assert "class='plan-title' role='heading' aria-level='2'" in APP_SRC
    assert "class='plan-warn-h' role='heading' aria-level='3'" in APP_SRC
    # Every section title of the results carries the role (none was left behind as a plain div).
    assert not re.search(r"class='plan-h'(?! role=)", APP_SRC)
    assert len(re.findall(r"class='plan-h' role='heading' aria-level='3'", APP_SRC)) >= 7


def test_captions_are_not_dimmed_and_the_keyboard_focus_is_visible():
    css = APP_SRC
    assert re.search(r'\[data-testid="stCaptionContainer"\] p \{[^}]*opacity: 1 !important', css, re.S)
    assert "button:focus-visible" in css and "outline: 3px solid #1d4ed8" in css
    assert '[data-baseweb="tab"]:focus-visible' in css
    assert re.search(r'stTooltipContent"\] \{[^}]*max-width: min\(320px, 88vw\)', css, re.S)


def test_the_results_state_the_medical_disclaimer_right_under_the_summary():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _analyse(at, "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%")
    _finish_swipes(at, "right")
    captions = [c.value for c in at.caption]
    assert any("not medical advice" in c and "pregnant" in c for c in captions)


# ---------------------------------------------------------------- the swipe card component
def test_the_swipe_card_keeps_focus_and_announces_changes():
    assert "lastFocusId" in SWIPE  # focus goes back to the pressed button after a redraw
    assert 'aria-label="Keep pill"' in SWIPE or "'Keep pill'" in SWIPE or '"Keep pill"' in SWIPE
    assert 'role="note"' in SWIPE or "role', 'note'" in SWIPE or 'role", "note"' in SWIPE
    assert "aria-disabled" in SWIPE  # the buttons are not removed from the tab order mid-commit
    assert "var announce" in SWIPE and "announce + " in SWIPE  # "Kept the pill." is said with the next card


def test_the_swipe_card_text_is_not_tiny_and_wraps_at_large_text_sizes():
    sizes = [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)rem", SWIPE)]
    assert sizes and min(sizes) >= 0.7  # nothing smaller than 0.7rem anywhere
    assert "overflow-wrap" in SWIPE and "flex-wrap" in SWIPE
    assert "prefers-reduced-motion" in SWIPE


# ---------------------------------------------------------------- the camera component
def test_the_camera_view_is_labelled_and_keyboard_friendly():
    assert re.search(r'<video id="video"[^>]*aria-label="Live camera view', CAMERA)
    assert 'id="processing" role="status"' in CAMERA  # "Sending photo for analysis…" is announced
    assert "button:focus-visible" in CAMERA
    assert "prefers-reduced-motion" in CAMERA
    assert 'alt="The photo you just took' in CAMERA
    # Keyboard focus follows the visible state instead of staying on a button that was just hidden.
    assert 'focusSoon("use")' in CAMERA and 'focusSoon("snap")' in CAMERA
    # Hint text meets the 4.5:1 contrast on the page background.
    assert ".hint { color: #475569;" in CAMERA

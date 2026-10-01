"""Final review, UX journey: dialogs survive stray reruns (UXJ-F1), scroll to
the top when a scan starts / ends / fails (UXJ-F2), resume keeps the
pregnancy toggle (UXJ-F4), display names in the "not included" caption
(UXJ-F5), the meal count survives editing a card (UXJ-F6) and the privacy
note says what Start over deletes (UXJ-F7). Browser checks are in
test_ux_browser.py."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "swipe_mobile_app" / "app.py")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


@pytest.fixture(scope="module")
def sample_cards(sw):
    import blockbrain.app as bb

    return sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(sw._SAMPLE_LABEL_TEXT)), [])


# --- UXJ-F1: the dialog flag is cleared by the dialog, not by the run that opens it -------

def test_analyze_dialog_stays_open_across_reruns_until_cancelled():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.session_state["swipe_open_analyze"] = True
    at.run()
    assert at.session_state["swipe_open_analyze"] is True
    at.run()  # a stray rerun (nothing clicked) keeps the dialog's flag
    assert at.session_state["swipe_open_analyze"] is True
    cancel = next(b for b in at.button if b.label == "Cancel")
    cancel.click().run()
    assert at.session_state["swipe_open_analyze"] is False


def test_close_callbacks_clear_the_flags(sw, monkeypatch):
    state: dict = {"swipe_open_analyze": True, "swipe_confirm_restart": True}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._close_analyze_dialog()
    sw._close_restart_dialog()
    assert state == {"swipe_open_analyze": False, "swipe_confirm_restart": False}


def test_history_component_sends_its_data_once_per_session():
    html = (ROOT / "swipe_mobile_app" / "history_component" / "index.html").read_text(encoding="utf-8")
    assert "sessionStorage" in html and "args.session" in html
    source = Path(APP).read_text(encoding="utf-8")
    assert "session=token" in source


# --- UXJ-F2: scroll to the top ------------------------------------------------------------

def test_staging_a_scan_requests_a_scroll_to_the_top(sw, monkeypatch):
    state: dict = {}
    monkeypatch.setattr(sw.st, "session_state", state)
    assert sw._stage_analysis_from_inputs(b"", b"", "Vitamin C 80 mg")
    assert state["_suppswipe_scroll_top"] is True


def test_scroll_script_targets_the_main_container(sw, monkeypatch):
    state: dict = {}
    rendered: list[str] = []
    monkeypatch.setattr(sw.st, "session_state", state)
    monkeypatch.setattr(sw.components, "html", lambda html, height=0, **_k: rendered.append(html))
    sw._scroll_to_top()
    sw._scroll_to_top()
    assert len(rendered) == 2 and rendered[0] != rendered[1]  # a fresh iframe each time
    assert "[data-testid=stMain]" in rendered[0] and "scrollTo" in rendered[0]


def test_a_failed_analysis_scrolls_to_its_error():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "manual": "Hello there", "camera_barcode": ""}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.run()
    assert any("No micronutrients could be parsed" in e.value for e in at.error)
    # The scroll was rendered in that same run (the flag is consumed).
    assert "_suppswipe_scroll_top" not in at.session_state


# --- UXJ-F4: resume keeps the pregnancy toggle ---------------------------------------------

def test_snapshot_and_restore_keep_the_pregnancy_toggle(sw, sample_cards):
    state = {
        "swipe_cards": sample_cards,
        "swipe_analysis_text": sw._SAMPLE_LABEL_TEXT,
        "swipe_index": 1,
        "swipe_decisions": {},
        "swipe_pregnant": True,
    }
    snap = sw._scan_snapshot(state)
    assert snap["pregnant"] is True
    restored: dict = {}
    assert sw._restore_scan(restored, snap)
    assert restored["swipe_pregnant"] is True and restored["swipe_pregnant_toggle"] is True
    # An older snapshot without the field resumes with the toggle off.
    restored = {}
    assert sw._restore_scan(restored, {k: v for k, v in snap.items() if k != "pregnant"})
    assert restored["swipe_pregnant"] is False


# --- UXJ-F5: display names ---------------------------------------------------------------------

def test_excluded_caption_uses_display_names(sw, monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(sw.st, "caption", lambda text, **_k: shown.append(text))
    excluded = [{"component": "vitamin d3"}, {"component": "vitamin b12"}, {"component": "zink"}]
    sw._excluded_swaps_caption(excluded, "Vegan")
    assert shown == ["Not included until you choose another food: Vitamin D3, Vitamin B12, Zinc (doesn't fit Vegan)."]


# --- UXJ-F6: the meal count is mirrored into a plain key -------------------------------------

def test_meal_count_choice_is_mirrored(sw, monkeypatch):
    state: dict = {"swipe_meal_count": 1}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._on_meal_count_change()
    assert state["swipe_meal_count_choice"] == 1


# --- UXJ-F7: privacy note -----------------------------------------------------------------------

def test_privacy_note_says_what_start_over_deletes():
    source = Path(APP).read_text(encoding="utf-8")
    assert "*Clear history* \"\n            \"deletes both, *Start over* deletes the scan in progress." in source
    assert "*Clear history* or *Start over* deletes them" not in source

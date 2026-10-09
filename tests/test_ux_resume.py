"""Resume the last scan after a refresh: snapshot, age check, deterministic
rebuild from the saved text and re-applied decisions."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "swipe_mobile_app" / "app.py")
DAY = 24 * 3600


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


@pytest.fixture(scope="module")
def sample_cards(sw):
    import blockbrain.app as bb

    return sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(sw._SAMPLE_LABEL_TEXT)), [])


def _scan_state(sw, cards, **extra):
    food = cards[0]["foods"][1]
    state = {
        "swipe_cards": cards,
        "swipe_analysis_text": sw._SAMPLE_LABEL_TEXT,
        "swipe_index": 2,
        "swipe_diet_profile_id": "vegetarian",
        "swipe_label_source": {"kind": "input", "url": ""},
        "swipe_decisions": {
            cards[0]["component_key"]: sw._decision_record(cards[0], 0, "replace", food),
            cards[1]["component_key"]: sw._decision_record(cards[1], 1, "keep", None),
        },
    }
    state.update(extra)
    return state


def test_snapshot_holds_text_decisions_diet_and_index(sw, sample_cards):
    snap = sw._scan_snapshot(_scan_state(sw, sample_cards), now=1000.0)
    assert snap["text"] == sw._SAMPLE_LABEL_TEXT
    assert snap["diet"] == "vegetarian" and snap["index"] == 2 and snap["total"] == len(sample_cards)
    assert snap["ts"] == 1000.0 and snap["v"] == 1
    first = sample_cards[0]["component_key"]
    assert snap["decisions"][first] == {
        "decision": "replace", "food_description": sample_cards[0]["foods"][1]["food_description"],
    }
    assert snap["decisions"][sample_cards[1]["component_key"]] == {"decision": "keep", "food_description": ""}
    import json

    json.dumps(snap)  # must be storable in localStorage


def test_snapshot_mid_edit_resumes_on_the_results(sw, sample_cards):
    snap = sw._scan_snapshot(_scan_state(sw, sample_cards, swipe_index=0, swipe_edit_return=True))
    assert snap["index"] == len(sample_cards)


def test_no_snapshot_without_cards(sw):
    assert sw._scan_snapshot({"swipe_cards": [], "swipe_analysis_text": "x"}) is None
    assert sw._scan_snapshot({"swipe_cards": [{}], "swipe_analysis_text": " "}) is None


def test_resumable_only_for_a_week(sw, sample_cards):
    snap = sw._scan_snapshot(_scan_state(sw, sample_cards), now=100 * DAY)
    assert sw._resumable_scan(snap, now=100 * DAY + 6 * DAY) is snap
    assert sw._resumable_scan(snap, now=100 * DAY + 8 * DAY) is None
    assert sw._resumable_scan(dict(snap, v=0), now=100 * DAY) is None
    assert sw._resumable_scan(dict(snap, text=""), now=100 * DAY) is None
    assert sw._resumable_scan("junk") is None and sw._resumable_scan(None) is None


def test_resume_detail_says_what_comes_back_and_when(sw, sample_cards):
    snap = sw._scan_snapshot(_scan_state(sw, sample_cards), now=1_000_000.0)
    n = len(sample_cards)
    assert sw._resume_detail(snap, now=1_000_000.0 + 60) == f"2 of {n} cards done · saved today"
    assert sw._resume_detail(snap, now=1_000_000.0 + 30 * 3600) == f"2 of {n} cards done · saved yesterday"
    assert sw._resume_detail(snap, now=1_000_000.0 + 3 * DAY) == f"2 of {n} cards done · saved 3 days ago"
    assert sw._resume_detail(dict(snap, decisions={f"k{i}": {} for i in range(n)}), now=1_000_000.0) == f"All {n} cards done · saved today"
    assert sw._resume_detail(dict(snap, label_source={"kind": "sample"}), now=1_000_000.0).startswith("2 of")  # a sample is never saved: no special case
    assert sw._resume_detail(dict(snap, ts="junk"), now=1_000_000.0).endswith("saved today")  # a damaged stamp never raises


def test_restore_rebuilds_cards_and_reapplies_decisions(sw, sample_cards):
    snap = sw._scan_snapshot(_scan_state(sw, sample_cards))
    state: dict = {}
    assert sw._restore_scan(state, snap) is True
    assert [c["component_key"] for c in state["swipe_cards"]] == [c["component_key"] for c in sample_cards]
    first, second = sample_cards[0]["component_key"], sample_cards[1]["component_key"]
    assert state["swipe_decisions"][first]["decision"] == "replace"
    assert state["swipe_decisions"][first]["selected_food"]["food_description"] == sample_cards[0]["foods"][1]["food_description"]
    assert state["swipe_decisions"][first]["card_index"] == 0
    assert state["swipe_decisions"][second]["decision"] == "keep"
    assert state["swipe_index"] == 2
    assert state["swipe_diet_profile_id"] == "vegetarian"
    assert "swipe_diet_pills" not in state  # the chips live in the Diet sheet only: the mirror is what the sheet is seeded from
    assert state["swipe_last_auto_signature"]


def test_restore_drops_a_swap_whose_food_is_gone(sw, sample_cards):
    snap = sw._scan_snapshot(_scan_state(sw, sample_cards))
    first = sample_cards[0]["component_key"]
    snap["decisions"][first]["food_description"] = "Unobtainium, raw"
    state: dict = {}
    assert sw._restore_scan(state, snap)
    assert first not in state["swipe_decisions"]


def test_restore_fails_cleanly_on_text_without_nutrients(sw):
    state: dict = {}
    assert sw._restore_scan(state, {"v": 1, "text": "hello world", "decisions": {}, "total": 1, "ts": 0}) is False
    assert state == {}


def test_history_component_keeps_a_second_key_for_the_scan():
    html = (ROOT / "swipe_mobile_app" / "history_component" / "index.html").read_text(encoding="utf-8")
    assert "suppswipe_scan_history_v1" in html and "suppswipe_current_scan_v1" in html
    assert "args.saveScan" in html and "args.clearScan" in html


def test_welcome_offers_resume_and_restores(sw, sample_cards):
    import time

    snap = sw._scan_snapshot(_scan_state(sw, sample_cards), now=time.time())
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = snap
    at.run()
    assert not [b for b in at.button if b.key == "swipe_resume_scan"]  # not on the page ...
    at.button(key="appbar_scan").click().run()  # ... but in the Scan sheet
    resume = [b for b in at.button if b.key == "swipe_resume_scan"]
    assert resume and resume[0].label == "Resume last scan"
    assert f"2 of {len(sample_cards)} cards done · saved today" in [c.value for c in at.caption]  # what it brings back, and when
    resume[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["swipe_index"] == 2
    assert len(at.session_state["swipe_decisions"]) == 2
    assert at.session_state["swipe_diet_profile_id"] == "vegetarian"


def test_old_saved_scan_is_not_offered(sw, sample_cards):
    snap = sw._scan_snapshot(_scan_state(sw, sample_cards), now=0.0)
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = snap
    at.run()
    at.button(key="appbar_scan").click().run()  # the offer lives in the Scan sheet: look there, or this proves nothing
    assert [b for b in at.button if b.key == "swipe_try_sample"]  # the sheet is open ...
    assert not [b for b in at.button if b.key == "swipe_resume_scan"]  # ... and offers no Resume


def test_saved_scan_args_only_restamp_on_change(sw, sample_cards):
    state = _scan_state(sw, sample_cards)
    first = sw._saved_scan_args(state)
    assert sw._saved_scan_args(state) is first  # unchanged: identical props
    state["swipe_index"] = 3
    assert sw._saved_scan_args(state)["index"] == 3


def test_clear_history_also_forgets_the_saved_scan(sw, sample_cards):
    import time

    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = sw._scan_snapshot(_scan_state(sw, sample_cards), now=time.time())
    at.session_state["suppswipe_scan_history"] = [{"ts": "2026-10-01 10:00", "diet": "", "kept": [], "replaced": []}]
    at.run()
    at.button(key="appbar_scan").click().run()
    assert [b for b in at.button if b.key == "swipe_resume_scan"]
    at.session_state["swipe_sheet"] = None
    at.button(key="appbar_scans").click().run()
    at.button(key="swipe_clear_history").click().run()  # asks first
    assert at.session_state["_suppswipe_saved_scan"] is not None
    at.button(key="swipe_clear_history_confirm").click().run()
    assert at.session_state["_suppswipe_saved_scan"] is None
    at.button(key="appbar_scan").click().run()
    assert [b for b in at.button if b.key == "swipe_try_sample"]  # the sheet is open ...
    assert not [b for b in at.button if b.key == "swipe_resume_scan"]  # ... and the forgotten scan is not offered


def test_cleared_scan_is_not_saved_again_until_it_changes(sw, sample_cards):
    state = _scan_state(sw, sample_cards)
    state["_suppswipe_scan_suppressed"] = sw._scan_content(sw._scan_snapshot(state))
    assert sw._saved_scan_args(state) is None
    assert sw._saved_scan_args(state) is None
    state["swipe_index"] = 3  # the next swipe saves it again
    assert sw._saved_scan_args(state)["index"] == 3
    assert "_suppswipe_scan_suppressed" not in state


def test_clear_history_on_the_results_does_not_resave_the_scan():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    # The sample label is a demo, not one of the visitor's scans: this scan is the visitor's own pasted label.
    at.session_state["swipe_pending_request"] = {
        "upload_bytes": b"", "camera_bytes": b"", "camera_barcode": "",
        "manual": "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%",
    }
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.session_state["swipe_last_auto_signature"] = "own-label"  # what _stage_analysis_from_inputs records for a real input
    at.run()
    cards = at.session_state["swipe_cards"]
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    for i, card in enumerate(cards):
        at.session_state[key] = {"dir": "left", "id": f"c{i}", "card": card["component_key"], "index": i}
        at.run()
    assert at.session_state["swipe_index"] == len(cards)
    assert at.session_state["suppswipe_scan_history"]  # the finished scan was recorded
    assert at.session_state["_suppswipe_scan_snapshot"]["index"] == len(cards)  # and saved for resuming
    at.button(key="appbar_scans").click().run()
    at.button(key="swipe_clear_history").click().run()
    at.button(key="swipe_clear_history_confirm").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["suppswipe_scan_history"] == []
    # The cards are still on screen, but nothing is handed to the browser again.
    assert "_suppswipe_scan_snapshot" not in at.session_state
    assert at.session_state["_suppswipe_scan_suppressed"]["index"] == len(cards)
    at.run()
    assert "_suppswipe_scan_snapshot" not in at.session_state


def test_forget_with_cards_on_screen_suppresses_them(sw, sample_cards, monkeypatch):
    state = _scan_state(sw, sample_cards, _suppswipe_saved_scan={"v": 1})
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._forget_saved_scan()  # Clear history on the results / mid-way through a scan
    assert state["_suppswipe_saved_scan"] is None and state["_suppswipe_scan_clear"] is True
    assert sw._saved_scan_args(state) is None


def test_start_over_clears_any_suppression(sw, monkeypatch):
    # Start over resets the cards first, so a fresh scan after it is saved even
    # if it looks like the cleared one.
    state = {"_suppswipe_scan_suppressed": {"text": "old"}}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._forget_saved_scan()
    assert "_suppswipe_scan_suppressed" not in state

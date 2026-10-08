"""Headless UI checks (Streamlit AppTest) for the content workstream: the
pregnancy toggle, the report button and the results-screen totals."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")
SAMPLE = "Vitamin B12 2,5 µg 100%\nFolsäure 200 µg 100%\nMagnesium 56 mg 15%"
ORGAN_WORDS = ("liver", "kidney", "heart", "gizzard", "sweetbread", "tongue")


def _run(at: AppTest) -> AppTest:
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    return at


def _scanned(pregnant: bool = False) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["swipe_pregnant"] = pregnant
    at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "manual": SAMPLE}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    return _run(at)


def _food_options(at: AppTest) -> list[str]:
    return list(at.selectbox[0].options)


def _toggle(at: AppTest):
    return next(t for t in at.toggle if "Pregnant or breastfeeding" in t.label)


def test_pregnancy_toggle_hides_organ_meats_and_persists():
    at = _scanned()
    assert at.session_state["swipe_cards"][0]["nutrient_key"] == "vitamin b12"
    assert any(w in _food_options(at)[0].lower() for w in ORGAN_WORDS)  # liver tops B12
    toggle = _toggle(at)
    assert toggle.value is False
    toggle.set_value(True)
    _run(at)
    assert at.session_state["swipe_pregnant"] is True
    assert _toggle(at).value is True
    options = _food_options(at)
    assert options and not any(w in o.lower() for o in options for w in ORGAN_WORDS)
    # Persisted like the diet filter: still on after the next rerun.
    _run(at)
    assert at.session_state["swipe_pregnant"] is True and _toggle(at).value is True


def test_report_button_logs_and_thanks(caplog):
    at = _scanned()
    report = next(b for b in at.button if b.label == "🚩 Report a problem with this card")
    with caplog.at_level(logging.WARNING, logger="blockbrain.app"):
        report.click()
        _run(at)
    lines = [r.getMessage() for r in caplog.records if "SuppSwipe card report" in r.getMessage()]
    assert len(lines) == 1 and '"nutrient": "Vitamin B12"' in lines[0]
    assert [t.value for t in at.toast] == ["Thanks — logged for review"]


def test_results_screen_shows_the_daily_totals(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")  # offline: no background meal plan
    at = _scanned()
    cards = at.session_state["swipe_cards"]
    decisions = {}
    for i, card in enumerate(cards):
        decisions[card["component_key"]] = {
            "component_key": card["component_key"],
            "component": card["component"],
            "dose_label": card["dose_label"],
            "dose_value": card["dose_value"],
            "dose_unit": card["dose_unit"],
            "form": card["form"],
            "decision": "replace",
            "selected_food": card["foods"][-1],
            "card_index": i,
        }
    at.session_state["swipe_decisions"] = decisions
    at.session_state["swipe_index"] = len(cards)
    _run(at)
    text = " ".join(m.value for m in at.markdown)
    # The results hero shows the daily food amount and energy as stat tiles.
    assert "food / day" in text and "kcal / day" in text
    labels = " ".join(b.label for b in at.button)
    assert "for Vitamin B12" in labels and "for Folic acid" in labels  # the plan's food rows


@pytest.mark.parametrize("pregnant", [False, True])
def test_first_load_with_the_toggle(pregnant):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["swipe_pregnant"] = pregnant
    _run(at)
    assert _toggle(at).value is pregnant

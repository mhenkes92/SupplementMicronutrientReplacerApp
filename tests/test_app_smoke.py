"""Headless end-to-end smoke tests of the real Streamlit script (AppTest)."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")

US_LABEL = """Supplement Facts
Serving Size 1 Tablet
Vitamin C (as ascorbic acid) 90 mg
Vitamin D3 (as cholecalciferol) 25 mcg (1000 IU)
Zinc (as zinc oxide) 11 mg
Magnesium (as magnesium oxide) 100 mg
"""


def _run(at: AppTest) -> AppTest:
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_first_load_renders_welcome():
    at = _run(AppTest.from_file(APP, default_timeout=60))
    text = " ".join(m.value for m in at.markdown)
    assert "Analyze my supplement" in " ".join(b.label for b in at.button)
    assert "swipe" in text.lower()


def test_pasted_label_builds_cards():
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "manual": US_LABEL}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True  # skip the paint-only first pass
    _run(at)
    cards = at.session_state["swipe_cards"]
    names = [str(c.get("component_key", "")) for c in cards]
    assert cards, "no swipe cards were built"
    assert any("vitamin c" in n for n in names)
    assert any("zinc" in n for n in names)

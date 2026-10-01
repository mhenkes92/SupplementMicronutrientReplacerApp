"""Results screen layout: tabs instead of a stack of action popovers, with the
Ask AI chat, the Athlete RDA guide and the per-item / back buttons kept."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")
TABS = ["🍽️ Meals", "🛒 Cost", "💊 Kept pills", "📤 Share", "🌱 Why food"]
OLD_POPOVERS = ["🍽️ Meal plan", "🛒 Grocery cost", "💊 Cheapest combo", "📤 Share", "🌱 Pill vs whole-food benefits"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


def _labels(node, kind):
    out = []
    for child in getattr(node, "children", {}).values():
        if getattr(child, "type", "") == kind:
            out.append(child.proto.popover.label if kind == "popover" else getattr(child, "label", ""))
        out += _labels(child, kind)
    return out


def _results_app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="swipe_try_sample").click().run()
    cards = at.session_state["swipe_cards"]
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    for i, card in enumerate(cards):
        at.session_state[key] = {"dir": "right" if i % 2 else "left", "id": f"t{i}", "card": card["component_key"], "index": i}
        at.run()
    assert at.session_state["swipe_index"] == len(cards)
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_results_use_tabs_not_popovers():
    at = _results_app()
    assert [t.label for t in at.tabs] == TABS
    popovers = _labels(at.main, "popover")
    assert not set(OLD_POPOVERS) & set(popovers), popovers
    # Ask AI and the Athlete RDA guide stay reachable below the tabs.
    assert "💬 Ask AI" in popovers and "\U0001F3C3 Athlete RDA guide" in popovers


def test_results_keep_back_and_per_item_buttons():
    at = _results_app()
    keys = {b.key for b in at.button}
    assert "final_back_last" in keys
    cards = at.session_state["swipe_cards"]
    assert all(f"final_keep_{c['component_key']}" in keys or f"final_repl_{c['component_key']}" in keys for c in cards)
    at.button(key="final_back_last").click().run()
    assert at.session_state["swipe_index"] == len(cards) - 1
    assert not at.session_state["swipe_edit_return"]


def test_share_tab_lists_the_swaps():
    at = _results_app()
    share = " ".join(c.value for c in at.code)
    assert "SuppSwipe — my results" in share and "Replaced with whole foods" in share


def test_build_tag_in_about_popover(sw):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert f"Build {sw.BUILD_TAG}" in [c.value for c in at.caption]

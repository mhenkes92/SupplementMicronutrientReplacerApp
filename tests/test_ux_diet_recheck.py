"""Diet filter visibility on the card and the re-check of earlier swaps when the
filter changes (they are flagged and left out of meals / cost / share)."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")

SALMON = {"food_description": "Fish, salmon, Atlantic, wild, raw", "food_category": "Finfish and Shellfish Products",
          "amount_per_100g": 3.2, "unit": "UG"}
SOY_MILK = {"food_description": "Soy milk, unsweetened, plain, shelf stable", "food_category": "Legumes and Legume Products",
            "amount_per_100g": 1.1, "unit": "UG"}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


def _profile(sw, pid):
    _ids, by_id = sw._dietary_profile_lookup()
    return by_id[pid]


def test_active_diet_label(sw):
    assert sw._active_diet_label(_profile(sw, "vegan")) == "Vegan"
    assert sw._active_diet_label(_profile(sw, "none")) == ""
    assert sw._active_diet_label(None) == ""


def test_food_fit_uses_the_dropdown_filter(sw):
    vegan = _profile(sw, "vegan")
    assert sw._food_fits_diet(SALMON, vegan) is False
    assert sw._food_fits_diet(SOY_MILK, vegan) is True
    assert sw._food_fits_diet(SALMON, _profile(sw, "none")) is True
    assert sw._food_fits_diet(SALMON, _profile(sw, "pescatarian")) is True
    assert sw._food_fits_diet(None, vegan) is True  # nothing to check


def test_split_replacements_by_diet(sw):
    fish = {"component": "Vitamin B12", "decision": "replace", "selected_food": SALMON}
    soy = {"component": "Vitamin D", "decision": "replace", "selected_food": SOY_MILK}
    fits, misfits = sw._split_replacements_by_diet([fish, soy], _profile(sw, "vegan"))
    assert fits == [soy] and misfits == [fish]
    fits, misfits = sw._split_replacements_by_diet([fish, soy], _profile(sw, "none"))
    assert fits == [fish, soy] and misfits == []


def _results_app(diet: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    cards = [
        {"component": "Vitamin B12", "component_key": "vitamin b12", "dose_label": "2.5 mcg", "dose_value": 2.5,
         "dose_unit": "mcg", "form": "", "foods": [SALMON, SOY_MILK]},
        {"component": "Vitamin D", "component_key": "vitamin d", "dose_label": "20 mcg", "dose_value": 20,
         "dose_unit": "mcg", "form": "", "foods": [SOY_MILK]},
    ]
    at.session_state["swipe_cards"] = cards
    at.session_state["swipe_index"] = 2
    at.session_state["swipe_decisions"] = {
        "vitamin b12": {"component_key": "vitamin b12", "component": "Vitamin B12", "dose_label": "2.5 mcg", "dose_value": 2.5,
                        "dose_unit": "mcg", "form": "", "decision": "replace", "selected_food": SALMON, "card_index": 0},
        "vitamin d": {"component_key": "vitamin d", "component": "Vitamin D", "dose_label": "20 mcg", "dose_value": 20,
                      "dose_unit": "mcg", "form": "", "decision": "replace", "selected_food": SOY_MILK, "card_index": 1},
    }
    at.session_state["swipe_diet_profile_id"] = diet
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_results_flag_swaps_that_no_longer_fit_and_exclude_them():
    at = _results_app("vegan")
    flagged = [b for b in at.button if b.key == "final_misfit_vitamin b12"]
    assert flagged and "doesn't fit Vegan — tap to choose another" in flagged[0].label
    share = " ".join(c.value for c in at.code)
    assert "Soy milk" in share and "salmon" not in share.lower()
    captions = " ".join(c.value for c in at.caption)
    assert "Not included until you choose another food: Vitamin B12" in captions
    # Tapping the flag reopens that card in edit mode.
    flagged[0].click().run()
    assert at.session_state["swipe_index"] == 0 and at.session_state["swipe_edit_return"] is True


def test_no_flags_without_a_restriction():
    at = _results_app("none")
    assert not [b for b in at.button if str(b.key).startswith("final_misfit_")]
    share = " ".join(c.value for c in at.code)
    assert "salmon" in share.lower()


def test_card_shows_the_active_filter_line():
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["swipe_cards"] = [
        {"component": "Vitamin D", "component_key": "vitamin d", "dose_label": "20 mcg", "dose_value": 20,
         "dose_unit": "mcg", "form": "", "foods": [SOY_MILK]},
    ]
    at.session_state["swipe_diet_profile_id"] = "vegan"
    at.run()
    assert "Filter: Vegan" in [c.value for c in at.caption]
    at.session_state["swipe_diet_profile_id"] = "none"
    at.session_state["swipe_diet_pills"] = "none"
    at.run()
    assert not [c.value for c in at.caption if c.value.startswith("Filter:")]


def test_stale_meal_plan_is_not_shared():
    at = _results_app("vegan")
    # A plan written before the filter changed (for other swaps) is not shared.
    at.session_state["swipe_meal_plan"] = "**Salmon bowl** with 100 g salmon"
    at.session_state["swipe_meal_plan_key"] = "an-older-plan-key"
    at.run()
    share = " ".join(c.value for c in at.code)
    assert "Salmon bowl" not in share

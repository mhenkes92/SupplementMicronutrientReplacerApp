"""Safety follow-ups: portion counts and notes never contradict the upper
limit (Brazil nuts), omega-3-6-9 blends are not an EPA+DHA card, nicotinic
acid named as the nutrient keeps its flushing-form limit, and form hints
("50% as beta-carotene", "natürliches Vitamin E") reach the IU conversions."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


def _cards(sw, text: str) -> list[dict]:
    return sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])


def _brazil_nut(card: dict) -> dict:
    return next(f for f in card["foods"] if f["food_description"] == "Nuts, brazilnuts, raw")


# --- Brazil nuts vs the selenium upper limit ------------------------------------

def test_selenium_200_mcg_suggests_2_nuts_not_3(sw):
    [card] = _cards(sw, "Selenium 200 mcg")
    brazil = _brazil_nut(card)
    portion = sw._portion_for_target(brazil, card["dose_value"], card["dose_unit"], card["component"], card["form"])
    assert portion == "~10 g (~2 Brazil nuts)"
    note = sw._bioavailability_note(card["component_key"], card["form"], card["dose_value"], card["dose_unit"])
    assert "~2 nuts that match this dose" in note
    assert "3 or more nuts a day can pass the 255 µg/day safe upper limit" in note
    assert "one nut a day is plenty" not in note
    assert sw._upper_limit_warning(card["component_key"], card["dose_value"], card["dose_unit"]) == ""


@pytest.mark.parametrize("dose", [10, 55, 100, 150, 200, 230, 240, 250, 255])
def test_nut_count_shown_for_a_dose_within_the_ul_stays_within_the_ul(sw, dose):
    [card] = _cards(sw, "Selenium 200 mcg")
    brazil = _brazil_nut(card)
    portion = sw._portion_for_target(brazil, dose, "mcg", "selenium")
    nuts = float(portion.split("(~", 1)[1].split()[0])
    assert nuts * 5 * brazil["amount_per_100g"] / 100 <= 255


def test_selenium_note_for_low_and_over_limit_doses(sw):
    assert "one nut a day is plenty" in sw._bioavailability_note("selenium", "", 55, "mcg")
    assert "one nut a day is plenty" in sw._bioavailability_note("selenium")
    over = sw._bioavailability_note("selenium", "", 400, "mcg")
    assert "~4 nuts, more than is safe" in over


def test_unit_counts_round_to_the_nearest_half():
    assert "~2 Brazil nuts" in bb.estimate_whole_food_units("Nuts, brazilnuts, raw", 10.4)  # 2.09
    assert "~2.5 Brazil nuts" in bb.estimate_whole_food_units("Nuts, brazilnuts, raw", 13.0)  # 2.6
    assert "~3 bananas" in bb.estimate_whole_food_units("Bananas, raw", 118 * 2.8)

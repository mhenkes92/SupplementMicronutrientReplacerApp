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
    assert "3 or more nuts a day can pass the 255 µg/day upper intake level" in note
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


# --- Omega 3-6-9 blends ----------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Omega 3-6-9 1200 mg",
        "Omega-3/6/9 1000 mg",
        "Omega-3, -6 und -9 1000 mg",
        "Omega 3 + Omega 6 1000 mg",
        "Omega 3 6 9 1200 mg",
    ],
)
def test_omega_3_6_9_blend_is_not_an_epa_dha_card(sw, text):
    assert not sw._is_micronutrient(text.rsplit(" ", 2)[0])
    assert _cards(sw, text) == []


@pytest.mark.parametrize("text", ["Omega-3 1000 mg", "Omega 3 1000 mg", "Omega-3 fatty acids 1000 mg"])
def test_plain_omega_3_still_is_a_card(sw, text):
    [card] = _cards(sw, text)
    assert card["nutrient_key"] == "omega 3" and card["dose_label"] == "1000 mg"


# --- A line named after the form keeps it -------------------------------------------

@pytest.mark.parametrize("text", ["Nicotinic acid 20 mg", "Nicotinsäure 20 mg", "Niacin (als Nicotinsäure) 20 mg"])
def test_nicotinic_acid_line_keeps_the_flushing_form_limit(sw, text):
    [card] = _cards(sw, text)
    assert card["nutrient_key"] == "niacin"
    assert "upper intake level for niacin as nicotinic acid (10 mg/day" in sw._upper_limit_warning(
        card["component_key"], card["dose_value"], card["dose_unit"], card["form"]
    )


def test_nicotinamide_line_is_not_held_to_the_nicotinic_acid_limit(sw):
    [card] = _cards(sw, "Nicotinamide 20 mg")
    assert card["form"] == "nicotinamide"
    assert sw._upper_limit_warning(card["component_key"], card["dose_value"], card["dose_unit"], card["form"]) == ""


def test_natural_vitamin_e_named_as_the_line_uses_the_natural_iu_factor(sw):
    [card] = _cards(sw, "d-alpha-Tocopherol 400 IU")
    assert card["nutrient_key"] == "vitamin e" and "d alpha" in card["form"]
    assert bb._iu_unit_to_mg_for_component(card["component"], card["form"]) == 0.67


@pytest.mark.parametrize(
    "form, factor",
    [("natürliches Vitamin E", 0.67), ("natürlichem d-alpha-Tocopherol", 0.67), ("natural vitamin E", 0.67), ("", 0.45)],
)
def test_german_natural_vitamin_e_hint(form, factor):
    assert bb._iu_unit_to_mg_for_component("Vitamin E", form) == factor


# --- "(50% as beta-carotene)" -----------------------------------------------------

def test_partial_beta_carotene_share_is_weighted_in_the_iu_conversion(sw):
    [card] = _cards(sw, "Vitamin A 5000 IU (50% as beta-carotene)")
    assert card["form"] == "50% beta carotene"
    # 2500 IU x 0.3 + 2500 IU x 0.15 = 1125 µg RAE (not 1500 as all-retinyl).
    assert sw._dose_in_unit(card["component"], 5000, "iu", "mcg", card["form"]) == pytest.approx(1125)
    assert bb.vitamin_a_form_kind(card["component"], card["form"]) == ""  # mixed


def test_partial_beta_carotene_share_ul_counts_only_the_preformed_part(sw):
    assert sw._upper_limit_warning("vitamin a", 12000, "iu", "50% beta carotene") == ""  # 1800 µg preformed
    assert sw._upper_limit_warning("vitamin a", 30000, "iu", "50% beta carotene").startswith(
        "⚠️ 30000 IU (4500 mcg preformed vitamin A) is above the upper intake level for vitamin A (3000 mcg/day"
    )
    assert sw._upper_limit_warning("vitamin a", 4000, "mcg", "25% beta carotin") == ""  # 3000 µg = UL
    assert sw._upper_limit_warning("vitamin a", 4000, "mcg", "beta carotene") == ""  # no UL for beta-carotene


def test_vitamin_k_spaced_2_is_k2():
    assert [(c["component"], c["dose_value"]) for c in bb.parse_components("Vitamin K 2 100 µg")] == [("vitamin k2", 100.0)]


# --- RDA lookup ------------------------------------------------------------------

@pytest.mark.parametrize(
    "name, display",
    [
        ("Iodine (as potassium iodide)", "Iodine"),
        ("Vitamin B-12", "Vitamin B12"),
        ("Cholecalciferol", "Vitamin D"),
        ("Folsäure", "Vitamin B9 (Folate)"),
        ("ascorbic", "Vitamin C"),  # partial name: whole-word fallback
        ("folic", "Vitamin B9 (Folate)"),
    ],
)
def test_rda_lookup_via_lexicon_and_fallback(sw, name, display):
    assert sw._rda_for_component(name)["display"] == display


def test_omega_blend_has_no_epa_dha_rda_entry(sw):
    assert sw._rda_for_component("Omega 3-6-9") is None

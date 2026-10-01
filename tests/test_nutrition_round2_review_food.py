"""Round-2 confirmation review, swipe-card food choice: portions past a food's
own realistic daily amount read in grams (never "~0 kg/day"), plant-based B12
cards pre-select a food their own Replace soft-block accepts (any non-algae
plant food with B12 is fortified), vitamin D cards never default to a UV
mushroom when a common fish is listed, and seaweed (iodine unknown, possibly
far above the limit) is warned about and never the default when another food
exists."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


@pytest.fixture(scope="module")
def profiles(sw):
    return sw._dietary_profile_lookup()[1]


def _card(sw, text: str) -> dict:
    [card] = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])
    return card


def _shown(sw, card: dict, profile: dict | None = None) -> list[dict]:
    """The dropdown as _render_card builds it."""
    foods = bb.apply_food_filters(card["foods"], profile, use_llm_adjudication=False)[: sw.SWIPE_CARD_DROPDOWN_MAX]
    return sw._with_fortified_options(foods, card, profile)


def _default(sw, text: str, profile: dict | None = None) -> tuple[dict, dict, list[dict]]:
    card = _card(sw, text)
    foods = _shown(sw, card, profile)
    return foods[sw._default_food_index(foods, card, profile)], card, foods


# --- R1-KG-ROUNDING: grams past a food's own daily maximum, never "~0 kg" ------

def _yeast(sw) -> dict:
    return next(f for f in bb.fortified_food_options("vitamin b12") if "yeast" in f["food_description"])


@pytest.mark.parametrize("target, grams", [(4.0, "40 g"), (6.0, "60 g"), (2.9, "29 g")])
def test_portion_past_own_daily_maximum_reads_in_grams(sw, target, grams):
    text = sw._portion_for_target(_yeast(sw), target, "mcg", "vitamin b12")
    if target <= 3.0:
        assert text == f"~{grams}"
    else:
        assert text == f"not practical from food alone (~{grams}/day; realistic max ~30 g/day)"
    assert "kg" not in text


def test_portion_over_a_kilogram_keeps_the_kg_wording(sw):
    assert sw._portion_for_target(_yeast(sw), 500, "mcg", "vitamin b12") == "not practical from food alone (~5 kg/day)"
    drink = next(f for f in bb.fortified_food_options("vitamin b12") if "soy drink" in f["food_description"])
    assert sw._portion_for_target(drink, 6, "mcg", "vitamin b12") == "not practical from food alone (~1.6 kg/day)"


@pytest.mark.parametrize("diet", ["vegan", "vegetarian"])
@pytest.mark.parametrize("text", ["Vitamin B12 6 mcg", "Vitamin B12 2.5 mcg", "Vitamin-B12 2,5 µg 100%"])
def test_plant_based_b12_card_never_shows_zero_kilograms(sw, profiles, diet, text):
    food, card, _foods = _default(sw, text, profiles[diet])
    rda = sw._rda_for_component(card["component_key"])
    shown = [
        sw._portion_for_target(food, card["dose_value"], card["dose_unit"], card["component"], card["form"]),
        sw._portion_for_target(food, rda["athlete"], rda["unit"], rda["display"], note=False),
    ]
    assert all(s and "~0 kg" not in s and "~0.1 kg" not in s for s in shown), shown

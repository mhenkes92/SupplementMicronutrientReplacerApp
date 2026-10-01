"""Final review, nutrition safety: vegan EPA/DHA cards (no plant food supplies
EPA+DHA; Replace soft-blocked), plant-based B12 sized at the EU fortification
level (US-fortified USDA plant drinks dropped, "check the label" note)."""
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


def _cards(sw, text: str) -> list[dict]:
    return sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])


def _shown(sw, card: dict, profile: dict | None = None, pregnant: bool = False) -> list[dict]:
    """The dropdown as _render_card builds it."""
    foods = sw._card_food_options(card["foods"], profile, pregnant)
    return sw._with_fortified_options(foods, card, profile)


# --- F1: vegan omega-3 / DHA ------------------------------------------------------

_ALGAE_LABEL = "Vegan Omega-3 Algenöl Kapseln\nOmega-3-Fettsäuren 500 mg\ndavon DHA 300 mg\ndavon EPA 150 mg"
_PRENATAL_LABEL = (
    "Schwangerschaftsvitamine\nNährwertangaben pro Tagesdosis %NRV\nFolsäure 400 µg 200%\nJod 150 µg 100%\n"
    "Vitamin D3 20 µg 400%\nDHA 200 mg\nEisen 30 mg 214%\nVitamin B12 3,5 µg 140%"
)


@pytest.mark.parametrize("key", ["omega 3", "fish oil", "epa", "dha"])
def test_epa_dha_pools_hold_no_plant_food(key):
    rows = bb._build_local_food_rows_for_component(key, 250)
    assert rows, key
    for row in rows:
        assert not bb._PLANT_FOOD_CATEGORY_RE.search(row["food_category"]), row
        assert "quinoa" not in row["food_description"].lower() and "seaweed" not in row["food_description"].lower()


def test_ala_keeps_its_plant_foods():
    rows = bb._build_local_food_rows_for_component("ala", 50)
    assert any(bb._PLANT_FOOD_CATEGORY_RE.search(r["food_category"]) for r in rows)


@pytest.mark.parametrize("text", [_ALGAE_LABEL, _PRENATAL_LABEL, "DHA 200 mg", "Omega-3 (EPA+DHA) 1000 mg", "Algenöl 1000 mg"])
@pytest.mark.parametrize("pregnant", [False, True])
def test_vegan_epa_dha_card_offers_no_quinoa_or_seaweed_and_blocks_replace(sw, profiles, text, pregnant):
    vegan = profiles["vegan"]
    cards = [c for c in _cards(sw, text) if c["nutrient_key"] in sw._OMEGA3_LONG_CHAIN_KEYS]
    assert cards, text
    for card in cards:
        foods = _shown(sw, card, vegan, pregnant)
        assert not any(
            "quinoa" in f["food_description"].lower() or sw._algae_with_unknown_iodine(f, card["component"])
            or "seaweed" in f["food_description"].lower()
            for f in foods
        ), [f["food_description"] for f in foods]
        if foods:  # nothing should be left, but a default must never be accepted
            assert sw._replace_block_reason(card, foods[sw._default_food_index(foods, card, vegan)], vegan)
        assert "algal oil" in sw._replace_block_reason(card, None, vegan)
        warn = sw._card_warning_text(card["component_key"], card["dose_value"], card["dose_unit"], card["form"], vegan)
        assert "On a vegan diet no whole food supplies EPA+DHA" in warn


def test_omnivore_and_pescatarian_epa_dha_cards_are_not_blocked(sw, profiles):
    [card] = [c for c in _cards(sw, _ALGAE_LABEL) if c["nutrient_key"] in sw._OMEGA3_LONG_CHAIN_KEYS]
    for diet in ("none", "pescatarian"):
        foods = _shown(sw, card, profiles[diet])
        food = foods[sw._default_food_index(foods, card, profiles[diet])]
        assert food["food_category"].startswith("Finfish")
        assert sw._replace_block_reason(card, food, profiles[diet]) == ""
        assert "EPA+DHA" not in sw._diet_specific_warning(card["component_key"], profiles[diet])


def test_vegetarian_epa_dha_card_points_to_algal_oil(sw, profiles):
    warn = sw._card_warning_text("dha", 200, "mg", "", profiles["vegetarian"])
    assert "only eggs give a little DHA" in warn and "algal oil" in warn


@pytest.mark.parametrize("diet", ["vegan", "vegetarian"])
def test_plant_based_pregnancy_keeps_the_dha_supplement(sw, profiles, diet):
    info = sw._card_extra_info("dha", 200, "mg", "", profiles[diet], pregnant=True)
    assert "200 mg DHA" in info
    assert "200 mg DHA" not in sw._card_extra_info("dha", 200, "mg", "", profiles[diet], pregnant=False)
    assert "200 mg DHA" not in sw._card_extra_info("dha", 200, "mg", "", profiles["none"], pregnant=True)


# --- F2: plant-based B12 at the EU fortification level ------------------------------

@pytest.mark.parametrize("diet", ["vegan", "vegetarian"])
@pytest.mark.parametrize("dose", ["2.5", "3,1", "3,5", "4", "5", "6", "10", "13", "25"])
@pytest.mark.parametrize("pregnant", [False, True])
def test_plant_based_b12_never_preselects_us_fortified_soy_milk(sw, profiles, diet, dose, pregnant):
    [card] = _cards(sw, f"Vitamin B12 {dose} µg 140%")
    foods = _shown(sw, card, profiles[diet], pregnant)
    assert not any(sw._is_us_fortified_b12_row(f) for f in foods)
    assert not any(f["food_description"].lower().startswith("soy milk") for f in foods)
    food = foods[sw._default_food_index(foods, card, profiles[diet])]
    assert "fortified" in sw._food_label(food).lower(), food
    assert sw._replace_block_reason(card, food, profiles[diet]) == ""


def test_b12_pool_drops_us_fortified_plant_rows_for_every_diet(sw):
    [card] = _cards(sw, "Vitamin B12 2.5 µg")
    assert not any(sw._is_us_fortified_b12_row(f) for f in card["foods"])
    assert not any(f["food_description"].lower().startswith("soy milk") for f in card["foods"])


def test_fortified_b12_portion_says_check_the_label(sw):
    drink = next(f for f in bb.fortified_food_options("vitamin b12") if "soy drink" in f["food_description"])
    text = sw._portion_for_target(drink, 2.0, "mcg", "vitamin b12")
    assert "check the label" in text and "Bio/organic" in text
    # Not on other cards, and not for a whole food.
    assert "check the label" not in sw._portion_for_target(drink, 2.0, "mcg", "calcium")
    beef = {"food_description": "Beef, liver, raw", "food_category": "Beef Products", "amount_per_100g": 59.3, "unit": "mcg"}
    assert "check the label" not in sw._portion_for_target(beef, 2.5, "mcg", "vitamin b12")

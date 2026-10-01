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


# --- R1-B12-DEFAULT-BLOCKED: plant-based B12 pre-selects a food Replace accepts -

_B12_CARD = {"component": "vitamin b12", "component_key": "vitamin b12", "nutrient_key": "vitamin b12",
             "dose_value": 6.0, "dose_unit": "mcg", "form": ""}
# USDA rows as the merged (ws-diet) pool shows them on vegan / vegetarian cards.
_USDA_SOY_MILK = {"food_description": "Soy milk, sweetened, plain, refrigerated", "food_category": "Legumes and Legume Products",
                  "amount_per_100g": 1.327, "unit": "mcg", "source_db": "USDA Local DB"}
_USDA_EGG_YOLK = {"food_description": "Egg, yolk, raw, fresh", "food_category": "Dairy and Egg Products",
                  "amount_per_100g": 1.95, "unit": "mcg", "source_db": "USDA Local DB"}


def _merged_shape_b12_list(sw, profile, extra=()) -> list[dict]:
    return sw._with_fortified_options([*extra, _USDA_SOY_MILK], _B12_CARD, profile)


@pytest.mark.parametrize("diet", ["vegan", "vegetarian"])
def test_usda_soy_milk_counts_as_fortified_and_is_preselected(sw, profiles, diet):
    profile = profiles[diet]
    extra = (_USDA_EGG_YOLK,) if diet == "vegetarian" else ()
    foods = _merged_shape_b12_list(sw, profile, extra)
    assert sw._replace_block_reason(_B12_CARD, _USDA_SOY_MILK, profile) == ""
    # 6 µg: ~450 g soy milk beats 60 g of flakes (past their ~30 g/day) and,
    # on a vegetarian card, ~308 g of egg yolks that Replace would refuse.
    default = foods[sw._default_food_index(foods, _B12_CARD, profile)]
    assert default is _USDA_SOY_MILK
    assert sw._replace_block_reason(_B12_CARD, default, profile) == ""
    if diet == "vegetarian":
        assert sw._replace_block_reason(_B12_CARD, _USDA_EGG_YOLK, profile)


@pytest.mark.parametrize(
    "food, fortified",
    [
        (_USDA_SOY_MILK, True),
        ({"food_description": "B12-fortified oat drink", "food_category": "Fortified foods", "amount_per_100g": 0.38,
          "unit": "mcg", "fortified": True}, True),
        ({"food_description": "Cereals ready-to-eat, fortified", "food_category": "Breakfast Cereals",
          "amount_per_100g": 6.0, "unit": "mcg"}, True),
        ({"food_description": "Tempeh", "food_category": "Legumes and Legume Products", "amount_per_100g": 0.08,
          "unit": "mcg"}, False),
        ({"food_description": "Mushrooms, white, raw", "food_category": "Vegetables and Vegetable Products",
          "amount_per_100g": 0.04, "unit": "mcg"}, False),
        ({"food_description": "Seaweed, laver, raw", "food_category": "Vegetables and Vegetable Products",
          "amount_per_100g": 32.0, "unit": "mcg"}, False),
        (_USDA_EGG_YOLK, False),
        ({"food_description": "Beef, liver, raw", "food_category": "Beef Products", "amount_per_100g": 59.3, "unit": "mcg"}, False),
    ],
)
def test_is_b12_fortified_food(sw, food, fortified):
    assert sw._is_b12_fortified_food(food) is fortified


@pytest.mark.parametrize("diet", ["vegan", "vegetarian"])
@pytest.mark.parametrize("text", ["Vitamin B12 6 mcg", "Vitamin B12 2.5 mcg", "Vitamin B12 25 mcg", "Vitamin B12 1000 µg"])
def test_plant_based_b12_default_is_never_soft_blocked(sw, profiles, diet, text):
    food, card, _foods = _default(sw, text, profiles[diet])
    assert sw._replace_block_reason(card, food, profiles[diet]) == "", food


def test_omnivore_b12_default_is_a_whole_food_not_a_fortified_plant_food(sw, profiles):
    foods = [_USDA_SOY_MILK, {"food_description": "Fish, tuna, fresh, bluefin, raw", "food_category": "Finfish and Shellfish Products",
                              "amount_per_100g": 9.43, "unit": "mcg"}]
    foods.sort(key=lambda f: f["amount_per_100g"], reverse=True)
    assert "tuna" in foods[sw._default_food_index(foods, _B12_CARD, profiles["none"])]["food_description"]


# --- R1-SEAWEED-IODINE: seaweed of unknown iodine is warned about and demoted ----

_WAKAME = {"food_description": "Seaweed, wakame, raw", "food_category": "Vegetables and Vegetable Products",
           "amount_per_100g": 186.0, "unit": "mg"}


def test_seaweed_without_an_iodine_value_is_flagged_on_other_cards(sw):
    assert bb.food_nutrient_amount("Seaweed, wakame, raw", "iodine") is None
    assert sw._algae_with_unknown_iodine(_WAKAME, "omega-3")
    assert sw._food_exceeds_a_limit(_WAKAME, 10.0, "omega-3")
    warning = sw._selected_food_warning(_WAKAME, 1000, "mg", "omega-3")
    assert "iodine" in warning and "600 mcg/day" in warning
    # Not on the iodine card itself (its portion is sized to the iodine dose).
    assert not sw._algae_with_unknown_iodine(_WAKAME, "iodine")
    # Not for a land plant.
    assert not sw._algae_with_unknown_iodine({"food_description": "Seeds, chia seeds, dried"}, "omega-3")


def test_seaweed_is_the_default_only_when_nothing_else_is_listed(sw):
    card = {"component": "omega-3", "nutrient_key": "omega 3", "dose_value": 1000.0, "dose_unit": "mg", "form": ""}
    quinoa = {"food_description": "Quinoa, uncooked", "food_category": "Cereal Grains and Pasta", "amount_per_100g": 47.0, "unit": "mg"}
    assert sw._default_food_index([_WAKAME, quinoa], card) == 1
    assert sw._default_food_index([_WAKAME], card) == 0


@pytest.mark.parametrize("diet", ["none", "pescatarian", "vegetarian", "vegan"])
@pytest.mark.parametrize("text", ["Eisen 14 mg", "Vitamin B2 1.4 mg", "Kalium 500 mg", "Omega-3 1000 mg"])
def test_default_food_is_not_seaweed_when_another_food_exists(sw, profiles, diet, text):
    food, _card_, foods = _default(sw, text, profiles[diet])
    assert len(foods) > 1
    assert "seaweed" not in food["food_description"].lower(), food

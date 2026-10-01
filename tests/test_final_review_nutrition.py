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


# --- F4: realistic daily maxima and energy, not weight alone -----------------------

def _row(name: str, category: str, amount: float, unit: str) -> dict:
    return {"food_description": name, "food_category": category, "amount_per_100g": amount, "unit": unit}


_CHIA = _row("Chia seeds, dry, raw", "Cereal Grains and Pasta", 631.0, "mg")
_YOLK = _row("Egg, yolk, raw, fresh", "Dairy and Egg Products", 0.125, "g")
_PISTACHIO = _row("Nuts, pistachio nuts, raw", "Nut and Seed Products", 1.7, "mg")
_PEANUTS = _row("Peanuts, all types, raw", "Legumes and Legume Products", 12.07, "mg")
_GARLIC = _row("Garlic, raw", "Vegetables and Vegetable Products", 1.235, "mg")
_ROE = _row("Fish, roe, mixed species, raw", "Finfish and Shellfish Products", 7.0, "mg")


@pytest.mark.parametrize(
    "food, max_g",
    [(_CHIA, 15.0), (_YOLK, 51.0), (_PISTACHIO, 70.0), (_PEANUTS, 70.0), (_GARLIC, 10.0), (_ROE, 50.0),
     (_row("Egg, whole, raw, fresh", "Dairy and Egg Products", 2.0, "mcg"), 200.0),
     (_row("Seeds, sunflower seed kernels, dried", "Nut and Seed Products", 1.1, "mg"), 70.0)],
)
def test_foods_have_a_realistic_daily_maximum(sw, food, max_g):
    assert sw._food_max_daily_g(food) == max_g
    assert sw._portion_practicality(max_g * 0.9, food) in ("ok", "large")
    assert sw._portion_practicality(max_g * 1.5, food) == "impractical"


@pytest.mark.parametrize(
    "food",
    [_row("Nuts, coconut water", "Nut and Seed Products", 1.0, "mg"), _row("Spinach, raw", "Vegetables and Vegetable Products", 79.0, "mg"),
     _row("Fish, salmon, Atlantic, wild, raw", "Finfish and Shellfish Products", 3.2, "mcg"),
     _row("Noodles, egg, cooked", "Cereal Grains and Pasta", 1.0, "mg")],
)
def test_other_foods_keep_the_weight_thresholds(sw, food):
    assert sw._food_max_daily_g(food) == 0.0


@pytest.mark.parametrize(
    "food, dose, unit, component, shown",
    [
        (_CHIA, 1200, "mg", "calcium", "not practical from food alone (~190 g/day; realistic max ~15 g/day)"),
        (_PISTACHIO, 6, "mg", "vitamin b6", "not practical from food alone (~353 g/day; realistic max ~70 g/day)"),
        (_GARLIC, 2.8, "mg", "vitamin b6", "not practical from food alone (~227 g/day; realistic max ~10 g/day)"),
        (_ROE, 24, "mg", "vitamin e", "not practical from food alone (~343 g/day; realistic max ~50 g/day)"),
    ],
)
def test_reviewer_portions_are_flagged(sw, food, dose, unit, component, shown):
    assert sw._portion_for_target(food, dose, unit, component) == shown


def test_egg_yolk_portion_past_three_yolks_is_not_practical(sw):
    text = sw._portion_for_target(_YOLK, 375, "mg", "omega-3")
    assert text.startswith("not practical from food alone (~300 g/day") and "realistic max ~51 g/day" in text


def test_energy_dense_portion_is_a_lot_of_food(sw):
    rice = bb.fortified_food_options("vitamin b12")  # curated rows have no USDA energy
    assert sw._portion_kcal(100, rice[0]) is None
    black_rice = _row("Rice, black, unenriched, raw", "Cereal Grains and Pasta", 8.28, "mg")
    kcal = sw._portion_kcal(300, black_rice)
    assert kcal and kcal > 600
    assert sw._portion_practicality(300, black_rice) == "large"
    text = sw._portion_for_target(black_rice, 25, "mg", "niacin")
    assert text.startswith("a lot of food (~302 g/day, ~") and text.endswith(" kcal)")


def test_brazil_nuts_past_their_maximum_still_warn_about_selenium(sw):
    brazil = _row("Nuts, brazilnuts, raw", "Nut and Seed Products", 376.0, "mg")
    warning = sw._selected_food_warning(brazil, 400, "mg", "magnesium")
    assert "~70 g of this food also gives" in warning and "selenium" in warning


@pytest.mark.parametrize(
    "text, diet, banned",
    [
        ("Calcium 1200 mg", "vegetarian", "chia"),
        ("Vitamin B6 6 mg", "none", "pistachio"),
        ("Niacin 50 mg", "vegan", "peanut"),
        ("Riboflavin 2,8 mg", "none", "almond"),
        ("Pantothensäure 12 mg", "none", "sunflower"),
        ("Vitamin B6 2,8 mg", "nut free", "garlic"),
        ("Vitamin E 24 mg", "nut free", "roe"),
        ("Biotin 100 µg", "nut free", "yolk"),
    ],
)
def test_reviewer_defaults_are_no_longer_impractical_foods(sw, profiles, text, diet, banned):
    [card] = _cards(sw, text)
    foods = _shown(sw, card, profiles[diet])
    food = foods[sw._default_food_index(foods, card, profiles[diet])]
    assert banned not in food["food_description"].lower(), food


# --- F7 / UXJ-F8: pregnancy mode and fish / shellfish -------------------------------

_PREGNANCY_TEXT = "Vitamin B12 2,5 µg\nZink 10 mg\nOmega-3 1000 mg\nNiacin 50 mg\nSelen 55 µg\nKupfer 1 mg"


@pytest.mark.parametrize("name", [
    "Fish, tuna, fresh, bluefin, raw", "Fish, roughy, orange, raw", "Fish, swordfish, raw",
    "Fish, shark, mixed species, raw", "Fish, mackerel, king, raw", "Fish, tilefish, raw",
])
def test_high_mercury_fish_are_recognised(sw, name):
    assert sw._is_high_mercury_fish({"food_description": name})


@pytest.mark.parametrize("name", [
    "Crustaceans, crab, alaska king, raw", "Fish, mackerel, Atlantic, raw", "Fish, tuna, fresh, yellowfin, raw",
    "Fish, salmon, Atlantic, wild, raw",
])
def test_everyday_fish_are_not_high_mercury(sw, name):
    assert not sw._is_high_mercury_fish({"food_description": name})


def test_pregnancy_hides_high_mercury_fish_and_demotes_shellfish_roe_and_tuna(sw):
    for card in _cards(sw, _PREGNANCY_TEXT):
        pregnant_foods = sw._card_food_options(card["foods"], None, True)
        assert not any(sw._is_high_mercury_fish(f) or sw._is_organ_meat(f) for f in pregnant_foods), card["component"]
        food = pregnant_foods[sw._default_food_index(pregnant_foods, card, None, pregnant=True)]
        assert not sw._pregnancy_caution_food(food), (card["component"], food["food_description"])
    # Not pregnant: the same cards still default to oysters / tuna where those rank first.
    b12 = next(c for c in _cards(sw, _PREGNANCY_TEXT) if c["nutrient_key"] == "vitamin b12")
    foods = sw._card_food_options(b12["foods"], None, False)
    assert any(sw._is_high_mercury_fish(f) for f in foods)
    assert sw._pregnancy_caution_food(foods[sw._default_food_index(foods, b12, None, pregnant=False)])


def test_pregnancy_notes_for_shellfish_roe_and_tuna(sw):
    assert "well cooked" in sw._pregnancy_food_note({"food_description": "Mollusks, oyster, eastern, wild, raw"})
    assert "well cooked" in sw._pregnancy_food_note({"food_description": "Fish, roe, mixed species, raw"})
    assert "twice a week" in sw._pregnancy_food_note({"food_description": "Fish, tuna, fresh, yellowfin, raw"})
    assert sw._pregnancy_food_note({"food_description": "Fish, herring, Atlantic, raw"}) == ""
    oyster = {"component": "zinc", "selected_food": {"food_description": "Mollusks, oyster, eastern, wild, raw",
              "food_category": "Finfish and Shellfish Products", "amount_per_100g": 39.3, "unit": "mg"},
              "dose_value": 10, "dose_unit": "mg"}
    assert any("well cooked" in w for w in sw._pregnancy_food_warnings([oyster], pregnant=True))
    assert sw._pregnancy_food_warnings([oyster], pregnant=False) == []


def test_pregnancy_meal_plan_never_asks_for_liver_or_swordfish(sw):
    liver = {"component": "vitamin b12", "selected_food": {"food_description": "Beef, variety meats and by-products, liver, raw",
             "food_category": "Beef Products", "amount_per_100g": 59.3, "unit": "mcg"}, "dose_value": 2.5, "dose_unit": "mcg"}
    sword = {"component": "selenium", "selected_food": {"food_description": "Fish, swordfish, raw",
             "food_category": "Finfish and Shellfish Products", "amount_per_100g": 48.1, "unit": "mcg"},
             "dose_value": 55, "dose_unit": "mcg"}
    _sys, user, _key = sw._meal_plan_prompts([liver, sword], "No restriction", 3, pregnant=True)
    assert "pregnancy-safe food rich in Vitamin B12 instead of" in user
    assert "pregnancy-safe food rich in Selenium instead of" in user
    assert "per week" not in user  # no organ-meat portion
    _sys, user, _key = sw._meal_plan_prompts([liver, sword], "No restriction", 3, pregnant=False)
    assert "pregnancy-safe" not in user and "per week" in user
    warnings = sw._pregnancy_food_warnings([liver, sword], pregnant=True)
    assert len(warnings) == 2 and all("isn't advised in pregnancy" in w for w in warnings)


# --- F5: an over-the-limit pill matched from food is over the limit too -------------

_OYSTER = _row("Mollusks, oyster, eastern, wild, raw", "Finfish and Shellfish Products", 39.3, "mg")


def _decision(component: str, food: dict, dose: float, unit: str) -> dict:
    return {"decision": "replace", "component": component, "component_key": component, "dose_value": dose,
            "dose_unit": unit, "form": "", "selected_food": food}


def test_matching_an_over_limit_zinc_pill_from_food_warns_and_points_to_the_target(sw):
    warning = sw._selected_food_warning(_OYSTER, 50, "mg", "zinc")
    assert "Matching this dose from food is also above the 25 mg/day safe upper limit for zinc" in warning
    assert "aim for the daily target (~38 g) instead" in warning
    decision = _decision("zinc", _OYSTER, 50, "mg")
    # Still on the results after Replace (the pill is no longer "kept").
    assert any("also above the 25 mg/day" in w for w in sw._final_food_warnings([decision]))
    assert sw._meal_plan_amount(decision).startswith("eat ~38 g (the daily target")
    assert sw._swap_grams(decision) == pytest.approx(15 / 39.3 * 100)


def test_a_dose_within_the_limit_has_no_own_limit_warning(sw):
    assert "Matching this dose" not in sw._selected_food_warning(_OYSTER, 10, "mg", "zinc")
    decision = _decision("zinc", _OYSTER, 10, "mg")
    assert sw._meal_plan_amount(decision) == sw._amount_to_match_dose(decision)
    assert sw._swap_grams(decision) == pytest.approx(sw._grams_to_match_dose(decision))


def test_iodine_over_the_limit_from_food(sw):
    haddock = _row("Fish, haddock, raw", "Finfish and Shellfish Products", 300.0, "mcg")
    warning = sw._selected_food_warning(haddock, 1000, "mcg", "iodine")
    assert "also above the 600 mcg/day safe upper limit for iodine" in warning
    # A portion over 1 kg a day is not eaten, so there is nothing to warn about.
    cod = _row("Fish, cod, Atlantic, raw", "Finfish and Shellfish Products", 99.0, "mcg")
    assert "Matching this dose" not in sw._selected_food_warning(cod, 1000, "mcg", "iodine")


# --- F11: totals and basket agree with the card ------------------------------------------

def test_impractical_yeast_flakes_are_not_counted_in_the_totals_or_priced(sw):
    yeast = next(f for f in bb.fortified_food_options("vitamin b12") if "yeast" in f["food_description"])
    decision = _decision("vitamin b12", yeast, 25, "mcg")
    totals = sw._swap_totals([decision])
    assert totals["foods"] == [] and totals["grams"] == 0
    assert [name for name, _g in totals["impractical"]] == [sw._food_name(yeast)]
    lines = sw._swap_totals_lines([decision])
    assert lines == [("caption", f"Not counted, not practical from food: {sw._food_name(yeast)} (~250 g/day).")]
    basket = sw._basket_cost_breakdown([decision])
    assert basket["rows"] == [] and [name for name, _g in basket["impractical"]] == [sw._food_name(yeast)]
    assert sw._meal_plan_amount(decision) == sw._MEAL_PLAN_NORMAL_PORTION


def test_the_same_food_under_two_usda_names_counts_once(sw):
    nuts = _row("Nuts, almonds", "Nut and Seed Products", 1.14, "mg")
    plain = _row("Almonds", "Nut and Seed Products", 57.0, "mcg")
    items = [_decision("riboflavin", nuts, 0.68, "mg"), _decision("biotin", plain, 23, "mcg")]  # ~60 g and ~40 g
    totals = sw._swap_totals(items)
    assert [name for name, _g, _k in totals["foods"]] == ["Almonds"]
    assert totals["grams"] == pytest.approx(0.68 / 1.14 * 100)
    basket = sw._basket_cost_breakdown(items)
    assert [name for name, _c in basket["rows"]] == ["Almonds"]

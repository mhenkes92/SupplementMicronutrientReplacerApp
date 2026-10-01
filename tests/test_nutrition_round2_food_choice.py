"""Round-2 food choice on the swipe card: the pre-selected food is an everyday
choice (no liver when another food works, D3 fish before UV mushrooms, a
practical portion first), co-nutrient upper limits (liver vitamin A on every
portion the card shows, Brazil-nut selenium, ...), algae out of the B12 list,
B12-fortified options and soft-blocked Replace for plant-based B12 / vegan
iodine, the winter vitamin D note, diet-aware notes, dose ranges and unit
hints below 0.1 unit."""
from __future__ import annotations

import datetime
import time
from pathlib import Path

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


# --- Default food: no organ meat when another food works ---------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Folic acid 400 mcg", "Folsäure 200 µg", "Folate 400 mcg", "Vitamin B2 1.4 mg", "Riboflavin 1.6 mg",
        "Vitamin B12 2.5 mcg", "Vitamin B12 25 mcg", "Copper 1 mg", "Kupfer 2 mg", "Vitamin A 800 µg",
        "Pantothensäure 6 mg", "Selen 55 µg",
    ],
)
def test_default_food_is_not_an_organ_meat_when_another_food_works(sw, profiles, text):
    for diet in ("none", "pescatarian", "halal friendly"):
        food, card, foods = _default(sw, text, profiles[diet])
        assert not bb.food_is_organ_meat(food["food_description"], food.get("food_category", "")), (diet, food)
        assert sw._portion_practicality(sw._food_portion_grams(food, card["dose_value"], card["dose_unit"], card["component"], card["form"])) == "ok"
        assert sw._selected_food_warning(food, card["dose_value"], card["dose_unit"], card["component"], card["form"]) == ""
    # Liver stays in the full list for whoever wants it.
    _food, card, foods = _default(sw, text)
    assert foods == _shown(sw, card)


def test_default_food_is_never_liver_past_the_vitamin_a_limit(sw):
    food, card, foods = _default(sw, "Vitamin B12 500 mcg")
    assert "liver" not in food["food_description"].lower()
    assert any("liver" in f["food_description"].lower() for f in foods)


def test_organ_meat_is_the_default_only_when_strictly_more_practical(sw):
    card = {"component": "vitamin b12", "nutrient_key": "vitamin b12", "dose_value": 2.5, "dose_unit": "mcg", "form": ""}
    heart = {"food_description": "Turkey, all classes, heart, raw", "food_category": "Poultry Products", "amount_per_100g": 13.3, "unit": "mcg"}
    tuna = {"food_description": "Fish, tuna, fresh, bluefin, raw", "food_category": "Finfish and Shellfish Products", "amount_per_100g": 9.43, "unit": "mcg"}
    tiny = {"food_description": "Beans, kidney, red, raw", "food_category": "Legumes and Legume Products", "amount_per_100g": 0.1, "unit": "mcg"}
    assert sw._default_food_index([heart, tuna], card) == 1  # tuna ~27 g is practical too
    assert sw._default_food_index([heart, tiny], card) == 0  # 2.5 kg of beans is not
    assert not bb.food_is_organ_meat("Beans, kidney, red, raw", "Legumes and Legume Products")
    assert not bb.food_is_organ_meat("Hearts of palm, raw", "Vegetables and Vegetable Products")


def test_default_prefers_a_practical_portion(sw):
    card = {"component": "magnesium", "nutrient_key": "magnesium", "dose_value": 400, "dose_unit": "mg", "form": ""}
    big = {"food_description": "Spinach, raw", "food_category": "Vegetables and Vegetable Products", "amount_per_100g": 79.0, "unit": "mg"}
    seeds = {"food_description": "Seeds, pumpkin seeds (pepitas), raw", "food_category": "Nut and Seed Products", "amount_per_100g": 499.7, "unit": "mg"}
    assert sw._default_food_index([big, seeds], card) == 1


def test_default_skips_a_food_that_breaks_a_co_nutrient_limit(sw):
    card = {"component": "magnesium", "nutrient_key": "magnesium", "dose_value": 400, "dose_unit": "mg", "form": ""}
    brazil = {"food_description": "Nuts, brazilnuts, raw", "food_category": "Nut and Seed Products", "amount_per_100g": 376.0, "unit": "mg"}
    cashew = {"food_description": "Nuts, cashew nuts, raw", "food_category": "Nut and Seed Products", "amount_per_100g": 292.0, "unit": "mg"}
    assert sw._default_food_index([brazil, cashew], card) == 1


# --- Default food for vitamin D: D3 fish / eggs before UV mushrooms ------------------

@pytest.mark.parametrize("diet", ["none", "pescatarian"])
def test_vitamin_d_default_is_an_everyday_d3_fish_not_a_uv_mushroom(sw, profiles, diet):
    food, card, foods = _default(sw, "Vitamin D3 25 µg", profiles[diet])
    assert sw._EVERYDAY_VITAMIN_D3_RE.search(food["food_description"]), food
    assert "mushroom" not in food["food_description"].lower()
    # The UV mushrooms stay in the list, clearly labelled.
    uv = next(f for f in foods if sw._is_uv_mushroom(f))
    assert sw._portion_for_target(uv, 25, "mcg", "vitamin d3").endswith(
        "(only UV-treated mushrooms — regular mushrooms contain almost no vitamin D)"
    )


def test_high_dose_vitamin_d_default_is_still_a_fish(sw, profiles):
    # 100 µg: mackerel would be ~620 g, but another fish (~365 g) beats ~313 g of UV mushrooms.
    food, _card_, _foods = _default(sw, "Vitamin D3 100 µg", profiles["none"])
    assert food["food_description"].startswith("Fish,") and "mushroom" not in food["food_description"].lower()


def test_vegan_vitamin_d_default_is_a_labelled_uv_mushroom(sw, profiles):
    food, card, _foods = _default(sw, "Vitamin D3 25 µg", profiles["vegan"])
    assert sw._is_uv_mushroom(food)
    portion = sw._portion_for_target(food, card["dose_value"], card["dose_unit"], card["component"], card["form"])
    assert "only UV-treated mushrooms" in portion
    # The athlete-target line on the card does not repeat the note.
    assert "UV" not in sw._portion_for_target(food, 25, "mcg", "Vitamin D", note=False)
    # Not on other cards, and not on a "not practical" portion.
    assert "UV" not in sw._portion_for_target(food, 50, "mcg", "biotin")
    assert "UV" not in sw._portion_for_target(food, 1000, "mcg", "vitamin d3")


def test_mushroom_stays_default_when_eggs_would_take_25_yolks(sw, profiles):
    food, _card_, _foods = _default(sw, "Vitamin D3 25 µg", profiles["vegetarian"])
    assert sw._is_uv_mushroom(food)


# --- Liver vitamin A: every portion the card shows, pregnancy ------------------------

def test_liver_warning_covers_the_athlete_target_portion(sw):
    card = _card(sw, "Folsäure 100 µg")
    duck = next(f for f in card["foods"] if f["food_description"] == "Duck, domesticated, liver, raw")
    # The pill portion is ~23 g, the athlete-target portion ~81 g (~9,700 µg vitamin A).
    assert sw._portion_for_target(duck, card["dose_value"], card["dose_unit"], card["component"], card["form"]) == "~23 g"
    warning = sw._selected_food_warning(duck, card["dose_value"], card["dose_unit"], card["component"], card["form"])
    assert warning.startswith("⚠️ ~81 g of this liver also gives ~97")
    assert "avoid liver during pregnancy" in warning
    card = _card(sw, "Vitamin B2 0,7 mg")
    lamb = next(f for f in card["foods"] if f["food_description"].startswith("Lamb, New Zealand, imported, liver"))
    assert "~48 g of this liver" in sw._selected_food_warning(lamb, card["dose_value"], card["dose_unit"], card["component"], card["form"])


def test_folate_cards_always_warn_against_liver_in_pregnancy(sw):
    pike = {"food_description": "Fish, pike, northern, liver (Alaska Native)", "food_category": "American Indian/Alaska Native Foods",
            "amount_per_100g": 2000.0, "unit": "mcg"}  # tiny portion, little vitamin A
    warning = sw._selected_food_warning(pike, 200, "mcg", "folic acid", "folic acid")
    assert "avoid it during pregnancy" in warning
    assert sw._selected_food_warning(pike, 0.1, "mg", "riboflavin") == ""


def test_fish_liver_vitamin_a_comes_from_the_iu_row():
    # USDA lists fish livers only in IU: 3140 IU x 0.3 = 942 µg retinol.
    assert bb.food_preformed_vitamin_a("Fish, salmon, king, chinook, liver (Alaska Native)", "American Indian/Alaska Native Foods") == pytest.approx(942.0)
    assert bb.food_preformed_vitamin_a("Carrots, raw", "Vegetables and Vegetable Products") == 0.0
    assert bb.food_preformed_vitamin_a("Lamb, New Zealand, imported, liver, raw", "Lamb, Veal, and Game Products") == 15434.0


def test_liver_without_any_vitamin_a_value_is_still_flagged(sw):
    seal = {"food_description": "Sea lion, Steller, liver (Alaska Native)", "food_category": "American Indian/Alaska Native Foods",
            "amount_per_100g": 5.0, "unit": "mg"}
    assert "very rich in preformed vitamin A" in sw._selected_food_warning(seal, 1.4, "mg", "riboflavin")


# --- Co-nutrient upper limits ----------------------------------------------------------

@pytest.mark.parametrize("text", ["Magnesium 400 mg", "Copper 1 mg", "Phosphorus 300 mg"])
def test_brazil_nuts_on_another_card_warn_about_selenium(sw, text):
    card = _card(sw, text)
    brazil = next(f for f in card["foods"] if f["food_description"] == "Nuts, brazilnuts, raw")
    warning = sw._selected_food_warning(brazil, card["dose_value"], card["dose_unit"], card["component"], card["form"])
    assert "selenium — above the 255 mcg/day safe upper limit" in warning


def test_selenium_card_itself_has_no_co_nutrient_selenium_warning(sw):
    card = _card(sw, "Selenium 55 mcg")
    brazil = next(f for f in card["foods"] if f["food_description"] == "Nuts, brazilnuts, raw")
    assert sw._selected_food_warning(brazil, card["dose_value"], card["dose_unit"], card["component"], card["form"]) == ""


def test_no_co_nutrient_noise_for_a_portion_already_called_not_practical(sw):
    card = _card(sw, "Biotin 5000 mcg")
    almonds = card["foods"][0]
    assert sw._portion_for_target(almonds, 5000, "mcg", "biotin").startswith("not practical")
    assert sw._selected_food_warning(almonds, card["dose_value"], card["dose_unit"], card["component"], card["form"]) == ""


# --- Vegan / vegetarian B12, vegan iodine ---------------------------------------------

def test_algae_are_never_a_b12_source():
    pool = bb._build_local_food_rows_for_component("vitamin b12", limit=250)
    assert not [f for f in pool if bb._NUTRIENT_FOOD_EXCLUSIONS["vitamin b12"].search(f["food_description"])]
    assert bb.food_nutrient_amount("Seaweed, Canadian Cultivated EMI-TSUNOMATA, dry", "vitamin b12") is None
    # Only the B12 list excludes them (algae iron, iodine, ... are real).
    assert set(bb._NUTRIENT_FOOD_EXCLUSIONS) == {"vitamin b12"}


def test_b12_list_has_fortified_options_flagged(sw, profiles):
    for diet in ("vegan", "vegetarian"):
        foods = _shown(sw, _card(sw, "Vitamin B12 2.5 mcg"), profiles[diet])
        fortified = [f for f in foods if f.get("fortified")]
        assert len(fortified) == 3, diet
        # The name says so up front (short display names keep it).
        assert all(f["food_description"].startswith("B12-fortified ") for f in fortified)
        assert not any("seaweed" in f["food_description"].lower() for f in foods)
    # Omnivores get whole foods only.
    assert not [f for f in _shown(sw, _card(sw, "Vitamin B12 2.5 mcg"), profiles["none"]) if f.get("fortified")]
    assert not [f for f in _card(sw, "Vitamin B12 2.5 mcg")["foods"] if f.get("fortified")]


def test_plant_based_b12_defaults_to_a_fortified_food_and_only_it_can_replace(sw, profiles):
    for diet in ("vegan", "vegetarian"):
        food, card, foods = _default(sw, "Vitamin B12 2.5 mcg", profiles[diet])
        assert food.get("fortified"), diet
        assert sw._replace_block_reason(card, food, profiles[diet]) == ""
    egg = next(f for f in _shown(sw, _card(sw, "Vitamin B12 2.5 mcg"), profiles["vegetarian"]) if not f.get("fortified"))
    assert sw._replace_block_reason(_card(sw, "Vitamin B12 2.5 mcg"), egg, profiles["vegetarian"])
    # Omnivores: whole foods first, and Replace is never blocked.
    food, card, _foods = _default(sw, "Vitamin B12 2.5 mcg", profiles["none"])
    assert not food.get("fortified")
    assert sw._replace_block_reason(card, food, profiles["none"]) == ""


def test_fortified_food_portion_beyond_a_realistic_daily_amount_is_not_practical(sw, profiles):
    foods = _shown(sw, _card(sw, "Vitamin B12 2.5 mcg"), profiles["vegan"])
    yeast = next(f for f in foods if "yeast" in f["food_description"].lower())
    drink = next(f for f in foods if "soy drink" in f["food_description"].lower())
    assert yeast.get("fortified") and drink.get("fortified")
    assert sw._portion_for_target(yeast, 2.5, "mcg", "vitamin b12") == "~25 g"
    assert sw._portion_for_target(yeast, 25, "mcg", "vitamin b12").startswith("not practical from food alone")
    assert sw._portion_for_target(drink, 2.5, "mcg", "vitamin b12").startswith("a lot of food")
    assert sw._portion_for_target(drink, 4, "mcg", "vitamin b12").startswith("not practical from food alone")


def test_vegan_iodine_is_warned_and_soft_blocked(sw, profiles):
    card = _card(sw, "Iodine 150 mcg")
    vegan = profiles["vegan"]
    warn = sw._card_warning_text(card["component_key"], card["dose_value"], card["dose_unit"], card["form"], vegan)
    assert "On a vegan diet no food supplies iodine reliably" in warn and "iodised salt" in warn
    food = _shown(sw, card, vegan)[0]
    assert sw._replace_block_reason(card, food, vegan)
    assert sw._replace_block_reason(card, food, profiles["vegetarian"]) == ""
    assert "iodine reliably" not in sw._card_warning_text("iodine", 150, "mcg", "", profiles["none"])


def test_plant_based_b12_warning_points_to_fortified_foods(sw, profiles):
    warn = sw._card_warning_text("vitamin b12", 2.5, "mcg", "", profiles["vegan"])
    assert "keeping the supplement is recommended" in warn and "B12-fortified" in warn and "seaweed" in warn


# --- Winter vitamin D -------------------------------------------------------------------

@pytest.mark.parametrize("month, shown", [(10, True), (12, True), (1, True), (3, True), (4, False), (7, False), (9, False)])
def test_vitamin_d_winter_note(sw, month, shown):
    today = datetime.date(2026, month, 15)
    warn = sw._card_warning_text("vitamin d3", 20, "mcg", "", None, today=today)
    assert ("October–March the sun in Germany is too weak" in warn) is shown
    assert "October–March" not in sw._card_warning_text("vitamin c", 80, "mg", "", None, today=today)


# --- Diet-aware notes -------------------------------------------------------------------

def test_notes_never_point_a_vegan_at_animal_foods(sw, profiles):
    vegan, none = profiles["vegan"], profiles["none"]
    assert "Only animal foods" not in sw._bioavailability_note("vitamin b12", profile=vegan)
    assert "fortified" in sw._bioavailability_note("vitamin b12", profile=vegan)
    assert "Heme iron from meat" not in sw._bioavailability_note("iron", profile=vegan)
    assert "non-heme" in sw._bioavailability_note("iron", profile=vegan)
    assert "Liver" not in sw._bioavailability_note("vitamin a", profile=vegan)
    assert "Algal oil" in sw._bioavailability_note("omega 3", profile=profiles["vegetarian"])
    assert "iodised salt" in sw._bioavailability_note("iodine", profile=vegan)
    assert "Eggs" in sw._bioavailability_note("choline", profile=profiles["vegetarian"])
    # Omnivores keep the general notes.
    assert sw._bioavailability_note("vitamin b12", profile=none) == sw._bioavailability_note("vitamin b12")
    assert "Heme iron" in sw._bioavailability_note("iron", profile=none)
    # Notes that do not depend on the diet are unchanged.
    assert sw._bioavailability_note("selenium", profile=vegan) == sw._bioavailability_note("selenium")


# --- Ranges and unit hints ----------------------------------------------------------------

def test_dose_range_card_label_portion_and_upper_limit(sw):
    card = _card(sw, "Magnesium 200–400 mg")
    assert card["dose_label"] == "200–400 mg" and card["dose_value"] == 200.0 and card["dose_max"] == 400.0
    warn = sw._card_warning_text(card["component_key"], card["dose_value"], card["dose_unit"], card["form"], None, dose_max=card["dose_max"])
    assert "400 mg is above the safe upper limit for magnesium" in warn
    kept = [{"component": "magnesium", "dose_value": 200.0, "dose_max": 400.0, "dose_unit": "mg", "form": ""}]
    assert "400 mg is above the safe upper limit" in sw._final_upper_limit_warnings(kept)[0]
    card = _card(sw, "Vitamin C 100-200 mg")
    acerola = card["foods"][0]
    assert sw._portion_for_target(acerola, card["dose_value"], card["dose_unit"], card["component"]) == sw._portion_for_target(acerola, 100, "mg", "vitamin c")


def test_unit_hint_below_a_tenth_of_a_unit_is_dropped(sw):
    banana = {"food_description": "Bananas, raw", "amount_per_100g": 27.0, "unit": "mg"}
    assert sw._portion_for_target(banana, 1, "mg", "magnesium") == "~3.7 g"
    assert bb.estimate_whole_food_units("Bananas, raw", 5) == ""
    assert "~0.3 bananas" in bb.estimate_whole_food_units("Bananas, raw", 30)


# --- The live card ---------------------------------------------------------------------------

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")


def _run_app(manual: str, diet: str):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "manual": manual}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.session_state["swipe_diet_profile_id"] = diet
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_live_card_preselects_the_everyday_food():
    at = _run_app("Vitamin D3 25 µg", "none")
    assert "mackerel" in str(at.selectbox[0].value).lower()
    at = _run_app("Folsäure 200 µg", "none")
    assert "liver" not in str(at.selectbox[0].value).lower()


def test_live_vegan_b12_card_preselects_a_fortified_food():
    at = _run_app("Vitamin B12 25 µg", "vegan")
    assert "fortified" in str(at.selectbox[0].value).lower()


def test_default_choice_is_fast(sw):
    card = _card(sw, "Vitamin B12 25 mcg")
    foods = _shown(sw, card)
    sw._default_food_index(foods, card)  # warm the caches
    start = time.perf_counter()
    for _ in range(20):
        sw._default_food_index(foods, card)
    assert (time.perf_counter() - start) / 20 < 0.1

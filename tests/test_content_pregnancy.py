"""Pregnancy & medication guardrails: organ meats hidden in pregnancy mode,
"keep as a supplement" notes, pregnancy food-safety rules in the meal plan and
short medication notes on the relevant cards."""
from __future__ import annotations

import pytest

import blockbrain.app as bb

LIVER = {"food_description": "Lamb, New Zealand, imported, liver, raw", "food_category": "Lamb, Veal, and Game Products", "amount_per_100g": 59.0, "unit": "UG"}
CLAMS = {"food_description": "Mollusks, clam, mixed species, raw", "food_category": "Finfish and Shellfish Products", "amount_per_100g": 11.3, "unit": "UG"}


@pytest.mark.parametrize(
    "desc, category, organ",
    [
        ("Chicken, liver, all classes, raw", "Poultry Products", True),
        ("Lamb, New Zealand, imported, kidney, raw", "Lamb, Veal, and Game Products", True),
        ("Turkey, all classes, heart, raw", "Poultry Products", True),
        ("Beef, New Zealand, imported, sweetbread, raw", "Beef Products", True),
        ("Turkey, gizzard, all classes, raw", "Poultry Products", True),
        ("Fish oil, cod liver", "Fats and Oils", True),
        ("Braunschweiger (a liver sausage), pork", "Sausages and Luncheon Meats", True),
        ("Chicken, liver, all classes, raw", "", True),  # curated rows may have no category
        ("Beans, kidney, red, mature seeds, raw", "Legumes and Legume Products", False),
        ("Beans, kidney, red, mature seeds, raw", "", False),
        ("Hearts of palm, raw", "Vegetables and Vegetable Products", False),
        ("Hearts of palm, raw", "", False),
        ("Custard-apple, (bullock's-heart), raw", "Fruits and Fruit Juices", False),
        ("Mollusks, clam, mixed species, raw", "Finfish and Shellfish Products", False),
    ],
)
def test_is_organ_meat(sw, desc, category, organ):
    assert sw._is_organ_meat({"food_description": desc, "food_category": category}) is organ


def test_pregnancy_mode_removes_organ_meats_from_the_options(sw):
    pool = list(bb._build_local_food_rows_for_component("vitamin b12", limit=sw.SWIPE_CARD_FOOD_POOL))
    normal = sw._card_food_options(pool, None, pregnant=False)
    pregnant = sw._card_food_options(pool, None, pregnant=True)
    assert any(sw._is_organ_meat(f) for f in normal)  # liver is a top B12 food
    assert pregnant and not any(sw._is_organ_meat(f) for f in pregnant)
    assert len(pregnant) <= sw.SWIPE_CARD_DROPDOWN_MAX
    # Kidney beans stay for iron in pregnancy mode.
    iron = sw._card_food_options(list(bb._build_local_food_rows_for_component("iron", limit=sw.SWIPE_CARD_FOOD_POOL)), None, pregnant=True)
    assert not any(sw._is_organ_meat(f) for f in iron)


def _profiles(sw):
    _ids, by_id = sw._dietary_profile_lookup()
    return by_id


@pytest.mark.parametrize("component", ["folic acid", "Folsäure", "folate", "iodine", "Jod", "vitamin d3", "iron"])
def test_pregnancy_note_on_the_usual_supplements(sw, component):
    assert sw._PREGNANCY_NOTE in sw._card_extra_info(component, None, "", "", None, pregnant=True)
    assert sw._PREGNANCY_NOTE not in sw._card_extra_info(component, None, "", "", None, pregnant=False)


def test_pregnancy_note_for_b12_only_on_plant_based_diets(sw):
    profiles = _profiles(sw)
    assert sw._pregnancy_note("vitamin b12", profiles["vegan"]) == sw._PREGNANCY_NOTE
    assert sw._pregnancy_note("vitamin b12", profiles["vegetarian"]) == sw._PREGNANCY_NOTE
    assert sw._pregnancy_note("vitamin b12", profiles["none"]) == ""
    assert sw._pregnancy_note("vitamin c", profiles["vegan"]) == ""
    assert sw._pregnancy_note("magnesium") == ""
    assert "midwife" in sw._PREGNANCY_NOTE


def test_medication_notes_always_show_on_their_cards(sw):
    for component in ("vitamin k", "Vitamin K2 (as menaquinone-7)", "phylloquinone"):
        note = sw._card_extra_info(component, 75, "mcg", "", None, pregnant=False)
        assert "warfarin" in note and "Marcumar" in note and "steady" in note
    potassium = sw._card_extra_info("potassium", 200, "mg", "", None, pregnant=False)
    assert "Kidney disease" in potassium and "blood-pressure drugs" in potassium
    iodine = sw._card_extra_info("iodine", 150, "mcg", "", None, pregnant=False)
    assert "Thyroid" in iodine and sw._PREGNANCY_NOTE not in iodine
    both = sw._card_extra_info("iodine", 150, "mcg", "", None, pregnant=True)
    assert "Thyroid" in both and sw._PREGNANCY_NOTE in both
    assert sw._medication_note("vitamin c") == ""
    # The curated bioavailability note itself is unchanged (the extra info is appended).
    assert "warfarin" not in sw._bioavailability_note("vitamin k")


def test_meal_plan_adds_pregnancy_food_safety_rules(sw):
    items = [{"decision": "replace", "component": "vitamin b12", "dose_value": 2.5, "dose_unit": "mcg", "form": "", "selected_food": CLAMS}]
    normal_system, _u, normal_key = sw._meal_plan_prompts(items, "", 3, pregnant=False)
    system, _u, key = sw._meal_plan_prompts(items, "", 3, pregnant=True)
    assert "pregnan" not in normal_system.lower()
    for rule in ("no liver", "no raw or undercooked meat, fish or eggs", "unpasteurised", "high-mercury fish"):
        assert rule in system
    assert key != normal_key  # a plan written without the rules is never reused
    # Without the argument the toggle decides (off outside the app).
    assert sw._meal_plan_prompts(items, "", 3)[0] == normal_system


def test_pregnancy_toggle_state_survives_a_swipe_reset(sw, monkeypatch):
    state = {"swipe_pregnant": True, "swipe_diet_profile_id": "vegan", "swipe_cards": [{"component": "zinc"}]}
    monkeypatch.setattr(sw.st, "session_state", state)
    assert sw._pregnancy_mode() is True
    sw._reset_swipe_state()
    assert state["swipe_pregnant"] is True and state["swipe_diet_profile_id"] == "vegan"
    assert state["swipe_cards"] == []
    state["swipe_pregnant_toggle"] = False
    sw._on_pregnancy_change()
    assert sw._pregnancy_mode() is False


def test_results_flag_an_organ_meat_picked_before_pregnancy_mode(sw):
    items = [
        {"component": "vitamin b12", "selected_food": LIVER},
        {"component": "zinc", "selected_food": CLAMS},
    ]
    assert sw._pregnancy_food_warnings(items, pregnant=False) == []
    warnings = sw._pregnancy_food_warnings(items, pregnant=True)
    # Clams stay allowed, with the cooked-only note (final review F7).
    assert warnings == [
        "🤰 Vitamin B12: Lamb liver isn't advised in pregnancy — tap it to pick another food.",
        "Zinc: 🤰 In pregnancy eat shellfish and fish roe only well cooked — never raw.",
    ]

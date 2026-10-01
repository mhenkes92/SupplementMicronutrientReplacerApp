"""Meal-plan portions (LLM-01): large / impractical match-dose amounts go into
the prompt as a normal portion, organ meats as at most ~50 g a week."""
from __future__ import annotations


def _swap(component, food, mg_per_100g, dose_mg, category=""):
    return {
        "decision": "replace",
        "component": component,
        "dose_value": dose_mg,
        "dose_unit": "mg",
        "form": "",
        "selected_food": {"food_description": food, "food_category": category, "amount_per_100g": mg_per_100g, "unit": "mg"},
    }


def _user_prompt(sw, items):
    return sw._meal_plan_prompts(items, "No restriction", 3, pregnant=False)[1]


def test_practical_amounts_keep_the_match_dose(sw):
    item = _swap("magnesium", "Broccoli, raw", 21.0, 42)  # 200 g
    assert sw._meal_plan_amount(item) == sw._amount_to_match_dose(item) == "eat ~200 g"
    assert "- Broccoli (eat ~200 g) for Magnesium" in _user_prompt(sw, [item])


def test_large_and_impractical_amounts_become_a_normal_portion(sw):
    large = _swap("magnesium", "Broccoli, raw", 21.0, 105)  # 500 g: "large"
    impractical = _swap("vitamin c", "Bananas, raw", 8.7, 200)  # ~2.3 kg: "impractical"
    assert sw._portion_practicality(sw._grams_to_match_dose(large)) == "large"
    assert sw._portion_practicality(sw._grams_to_match_dose(impractical)) == "impractical"
    prompt = _user_prompt(sw, [large, impractical])
    assert "- Broccoli (a normal portion (about 150 g); the full dose isn't practical from food) for Magnesium" in prompt
    assert "- Bananas (a normal portion (about 150 g); the full dose isn't practical from food) for Vitamin C" in prompt
    assert "kg" not in prompt and "500 g" not in prompt


def test_organ_meats_are_limited_to_one_small_portion_a_week(sw):
    liver = _swap("vitamin b12", "Lamb, New Zealand, imported, liver, raw", 0.059, 0.0025, "Lamb, Veal, and Game Products")
    prompt = _user_prompt(sw, [liver])
    assert "- Lamb liver (at most one small portion (~50 g) per week) for Vitamin B12" in prompt
    # Kidney beans are not an organ meat.
    beans = _swap("iron", "Beans, kidney, red, mature seeds, raw", 8.2, 8, "Legumes and Legume Products")
    assert "per week" not in sw._meal_plan_amount(beans)
    # Cod-liver oil is dosed in grams like any oil, not "a portion a week".
    oil = _swap("vitamin a", "Fish oil, cod liver", 30.0, 0.8, "Fats and Oils")
    assert "per week" not in sw._meal_plan_amount(oil)


def test_unknown_amounts_are_left_as_before(sw):
    item = {"decision": "replace", "component": "zinc", "dose_value": None, "dose_unit": "", "form": "", "selected_food": {"food_description": "Mollusks, oyster, eastern, wild, raw", "amount_per_100g": 39.3, "unit": "mg"}}
    assert sw._meal_plan_amount(item) == sw._amount_to_match_dose(item)

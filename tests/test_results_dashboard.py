"""Results dashboard helpers: instant, local data only (no LLM)."""
from __future__ import annotations

import pytest

import blockbrain.app as bb

MACKEREL = {"food_description": "Fish, mackerel, Atlantic, raw", "amount_per_100g": 16.1, "unit": "mcg"}


def test_food_bonus_lists_other_nutrients_as_percent_of_nrv(sw):
    bonus = sw._food_bonus(MACKEREL, 124, exclude="vitamin d")
    names = [name for name, _pct in bonus]
    assert names and len(bonus) <= 3
    assert all(pct >= sw._BONUS_MIN_PCT for _n, pct in bonus)
    assert bonus == sorted(bonus, key=lambda item: -item[1])
    # Mackerel is a top B12 source: 124 g give several times the 2.5 µg NRV.
    b12 = dict(bonus).get("Vitamin B12")
    assert b12 and b12 > 200
    assert "Vitamin D" not in names and "Vitamin D3" not in names


def test_food_bonus_handles_missing_data(sw):
    assert sw._food_bonus({}, 100) == []
    assert sw._food_bonus(MACKEREL, None) == []
    assert sw._food_bonus({"food_description": "Not a USDA food"}, 100) == []


@pytest.mark.parametrize("desc,expected", [
    ("Fish, mackerel, Atlantic, raw", "twice a week"),
    ("Nuts, brazilnuts, dried, unblanched", "snack"),
    ("Seeds, hemp seed, hulled", "sprinkled"),
    ("Cowpeas (blackeyes), mature seeds, raw", "curry"),
    ("Acerola, (west indian cherry), raw", "fresh"),
    ("Lamb, New Zealand, imported, liver, raw", "once a week"),
    ("Squash, winter, butternut, raw", "roasted"),
    ("Nuts, coconut meat, raw", "snack"),
])
def test_serving_ideas_by_food_type(sw, desc, expected):
    assert expected in sw._serving_idea({"food_description": desc})


def _decision(component, food, dose_value, dose_unit, index, decision="replace"):
    return {
        "component_key": component, "component": component, "dose_label": f"{dose_value} {dose_unit}",
        "dose_value": dose_value, "dose_unit": dose_unit, "form": "", "decision": decision,
        "selected_food": food, "card_index": index,
    }


def test_plan_rows_merge_a_food_chosen_for_two_nutrients(sw):
    items = [
        _decision("vitamin d", MACKEREL, 20.0, "mcg", 0),
        _decision("vitamin b12", dict(MACKEREL, amount_per_100g=8.71), 2.5, "mcg", 1),
    ]
    rows = sw._plan_rows(items)
    assert len(rows) == 1
    assert rows[0]["nutrients"] == ["Vitamin D", "Vitamin B12"] or set(rows[0]["nutrients"]) >= {"Vitamin B12"}
    # The larger daily amount wins (vitamin D needs more mackerel than B12).
    assert rows[0]["grams"] == pytest.approx(max(sw._grams_to_match_dose(d) for d in items))


def test_plan_grams_format(sw):
    assert sw._format_plan_grams(124.4) == "124 g"
    assert sw._format_plan_grams(2.94) == "2.9 g"
    assert sw._format_plan_grams(1530) == "1.5 kg"
    assert sw._format_plan_grams(None) == ""


@pytest.mark.parametrize("desc,icon", [
    ("Cowpeas, common (blackeyes, crowder, southern), mature seeds, raw", "🫘"),
    ("Lentils, mature seeds, cooked, boiled, without salt", "🫘"),
    ("Nuts, brazilnuts, dried, unblanched", "🥜"),
    ("Seeds, sunflower seed kernels, dried", "🌻"),
    ("Egg, whole, raw, fresh", "🥚"),
    ("Eggplant, raw", "🍆"),
    ("Spices, nutmeg, ground", "🌿"),
    ("Fish, salmon, Atlantic, farmed, cooked, dry heat", "🐟"),
    ("Fish oil, cod liver", "🐟"),
    ("Mollusks, oyster, eastern, wild, cooked", "🦪"),
    ("Beef, variety meats and by-products, liver, cooked, braised", "🥩"),
    ("Chicken, liver, all classes, cooked", "🍗"),
    ("Soymilk, original and vanilla, with added calcium, vitamins A and D", "🥛"),
    ("Tofu, raw, firm, prepared with calcium sulfate", "🫘"),
    ("Mushrooms, maitake, raw", "🍄"),
    ("Seaweed, kelp, raw", "🌿"),
    ("Spinach, raw", "🥬"),
    ("Kiwifruit, green, raw", "🥝"),
    ("Acerola, (west indian cherry), raw", "🍓"),
])
def test_food_icon_by_food_type(sw, desc, icon):
    # Whole words: "eggplant" is no egg, "nutmeg" no nut, a legume's
    # "mature seeds" no seed.
    assert sw._whole_food_icon_from_food({"food_description": desc}) == icon


def test_food_icon_falls_back_to_usda_category(sw):
    food = {"food_description": "Something unusual", "food_category": "Legumes and Legume Products"}
    assert sw._whole_food_icon_from_food(food) == "🫘"
    assert sw._whole_food_icon_from_food({"food_description": "Unknown"}) == sw.TITLE_WHOLE_FOOD_ICON
    assert sw._whole_food_icon_from_food(None) == sw.TITLE_WHOLE_FOOD_ICON


def test_background_generation_reports_partial_text(sw):
    import threading

    cache = sw.llm_cache
    key = cache.make_key("partial-test")
    cache.drop(key)
    gate = threading.Event()

    def job() -> str:
        cache.set_partial(key, "Breakfast: oats")
        gate.wait(5)
        return "Breakfast: oats with berries"

    future = cache.submit(key, job)
    for _ in range(100):
        if cache.partial(key):
            break
        threading.Event().wait(0.01)
    assert cache.partial(key) == "Breakfast: oats"
    gate.set()
    assert future.result(5) == "Breakfast: oats with berries"
    assert cache.partial(key) == ""
    assert cache.get(key) == "Breakfast: oats with berries"
    # No partial is recorded for a key that is not being generated.
    cache.set_partial("not-running", "x")
    assert cache.partial("not-running") == ""


def test_waiting_on_background_text_shows_partials(sw):
    import threading

    cache = sw.llm_cache
    key = cache.make_key("await-test")
    cache.drop(key)
    shown: list[str] = []

    class Box:
        def markdown(self, text: str) -> None:
            shown.append(text)

    def job() -> str:
        cache.set_partial(key, "Lunch")
        threading.Event().wait(0.6)
        return "Lunch: lentil curry"

    future = cache.submit(key, job)
    assert sw._await_background_text(key, future, Box()) == "Lunch: lentil curry"
    assert any(text.startswith("Lunch") for text in shown)


@pytest.mark.parametrize("pct,text", [(15, "15%"), (199, "199%"), (200, "2×"), (433, "4.3×"), (1142, "11×")])
def test_need_share_switches_to_multiples(sw, pct, text):
    assert sw._format_need_share(pct) == text


def test_card_ask_ai_suggestions_use_the_dose(sw):
    card = {"component": "vitamin c", "dose_label": "80 mg"}
    suggestions = sw._card_ask_ai_suggestions(card)
    assert suggestions[0] == "Is 80 mg a safe daily dose?" and len(suggestions) == 3
    assert sw._card_ask_ai_suggestions({"component": "zinc"})[0] == "How much Zinc do I need?"


def test_dialog_flags_are_cleared_by_their_close_handlers(sw, monkeypatch):
    state: dict = {"swipe_open_analyze": True, "swipe_confirm_restart": True}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._close_analyze_dialog()
    sw._close_restart_dialog()
    assert state == {}

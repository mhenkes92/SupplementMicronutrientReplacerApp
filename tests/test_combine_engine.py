"""The how-to-combine engine (swipe_mobile_app/combine.py): which swapped food and which kept pill goes into breakfast, lunch or dinner.
Deterministic, total, and every constraint that can be met is met. The plans are built from the real nutrient list and real USDA rows
(the app's own pools) with the app's own hooks (names, amounts, upper-limit check, pregnancy filter, USDA facts), so a change in the
app's portions or limits is seen here."""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import combine

ROOT = Path(__file__).resolve().parent.parent
TABLE = {r["id"]: i for i, r in enumerate(combine.RULES)}


# ------------------------------------------------------------------------------------------------------------------------ helpers
@pytest.fixture(scope="module")
def lab(sw):
    class Lab:
        pass

    lab = Lab()
    lab.sw, lab.bb = sw, sw.bb
    lab.pools = {}

    def pool(nutrient, contains=None):
        if nutrient not in lab.pools:
            lab.pools[nutrient] = [dict(r) for r in sw.bb._build_local_food_rows_for_component(nutrient, limit=150)]
        rows = lab.pools[nutrient]
        if contains is None:
            return rows
        return dict(next(r for r in rows if contains.lower() in r["food_description"].lower()))

    def keep(component, value, unit, form="", index=0):
        return {
            "component_key": sw.bb.normalize_lookup_key(component), "component": component,
            "dose_label": sw._dose_label({"dose_value": value, "dose_unit": unit}), "dose_value": value, "dose_max": None,
            "dose_unit": unit, "form": form, "decision": "keep", "selected_food": None, "card_index": index,
        }

    def replace(component, value, unit, food, form="", index=0):
        return {**keep(component, value, unit, form, index), "decision": "replace", "selected_food": food}

    def day(keep_items, replace_items=(), pregnant=False):
        return combine.plan_day(list(keep_items), list(replace_items), sw._combine_hooks(pregnant))

    lab.pool, lab.keep, lab.replace, lab.day = pool, keep, replace, day
    return lab


def where(day):
    """({pill label: [meal numbers]}, {food label: meal number}) of a day."""
    pills, foods = {}, {}
    for slot in day["slots"]:
        for it in slot["items"]:
            if it["kind"] == "pill":
                pills.setdefault(it["label"], []).append(slot["slot"])
            else:
                foods[it["label"]] = slot["slot"]
    return pills, foods


def rules_of(day):
    """{(meal number, rule id)} of the tips in the three meals."""
    return {(slot["slot"], t["rule_id"]) for slot in day["slots"] for t in slot["tips"]}


def tip(day, slot, rule_id):
    return next(t for t in day["slots"][slot - 1]["tips"] if t["rule_id"] == rule_id)


# ------------------------------------------------------------------------------------------------------------- worked examples
def test_nothing_chosen_is_an_empty_day_with_the_three_meals_in_place(lab):
    d = lab.day([], [])
    assert d["version"] == 1 and d["empty"] and not d["has_items"] and not d["needs_layout"] and d["medicine"] is None
    assert [(s["slot"], s["key"], s["label"], s["items"], s["tips"]) for s in d["slots"]] == [
        (1, "breakfast", "Breakfast", [], []), (2, "lunch", "Lunch", [], []), (3, "dinner", "Dinner", [], []),
    ]
    assert d["anytime"] == d["held"] == d["free_foods"] == d["directives"] == [] and combine.meal_layout([]) == []


def test_pills_only_iron_calcium_vitamin_d_magnesium_b12(lab):
    pills = [lab.keep("Iron", 14, "mg", index=0), lab.keep("Vitamin D3", 20, "mcg", index=1),
             lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=2), lab.keep("Magnesium", 56, "mg", "magnesium oxide", index=3),
             lab.keep("Vitamin B12", 2.5, "mcg", index=4)]
    d = lab.day(pills)
    breakfast, lunch, dinner = d["slots"]
    assert [i["text"] for i in breakfast["items"]] == ["Calcium, half of 1000 mg"]
    assert [i["text"] for i in lunch["items"]] == ["Iron 14 mg", "Vitamin D3 20 mcg"]
    assert [i["text"] for i in dinner["items"]] == ["Calcium, half of 1000 mg"]
    assert [t["rule_id"] for t in breakfast["tips"]] == ["iron-calcium-pill", "calcium-split-500"]  # no carbonate tip when it is split
    assert [t["rule_id"] for t in lunch["tips"]] == ["iron-vitc", "fat-vitamins-de", "iron-tea-coffee"] and dinner["tips"] == []
    assert tip(d, 2, "fat-vitamins-de")["extra"] == combine._HINTS["add_fat"]  # no fat food anywhere in the plan
    assert [i["text"] for i in d["anytime"]] == ["Magnesium 56 mg", "Vitamin B12 2.5 mcg"] and d["held"] == []
    assert d["medicine"]["rule_id"] == "medicine-spacing" and d["medicine"]["text"] == "On regular medicine? Ask your pharmacist about timing"
    assert not d["empty"] and d["has_items"] and not d["needs_layout"]
    assert tip(d, 1, "iron-calcium-pill")["items"] == ["pill:calcium", "pill:iron"]
    assert d["split_pills"]  # iron and calcium are kept apart: they may be ONE tablet, so the day says what to do then
    assert combine.day_text(d) == [
        "Breakfast: Calcium, half of 1000 mg", "Lunch: Iron 14 mg, Vitamin D3 20 mcg", "Dinner: Calcium, half of 1000 mg",
        "Any meal: Magnesium 56 mg, Vitamin B12 2.5 mcg", combine.ONE_TABLET_NOTE,
    ]


def test_the_nutrients_of_one_tablet_are_not_handed_over_as_a_schedule_without_a_word(lab):
    """The sample label is ONE tablet: D3, iron, calcium carbonate, zinc, magnesium, vitamin C. The engine keeps iron and calcium apart
    (it cannot know they are one tablet), so the day says so, in the tips and in the share lines."""
    tablet = [lab.keep("Vitamin D3", 20, "mcg", index=0), lab.keep("Iron", 14, "mg", index=1),
              lab.keep("Calcium", 120, "mg", "calcium carbonate", index=2), lab.keep("Zinc", 10, "mg", index=3),
              lab.keep("Magnesium", 56, "mg", index=4), lab.keep("Vitamin C", 80, "mg", index=5)]
    d = lab.day(tablet)
    assert d["split_pills"] and ("iron-calcium-pill" in {r for _, r in rules_of(d)})
    lines = combine.day_text(d)
    assert lines[-1] == combine.ONE_TABLET_NOTE and "Dinner: Calcium 120 mg" in lines
    shown = tip(d, next(s["slot"] for s in d["slots"] if any(t["rule_id"] == "iron-calcium-pill" for t in s["tips"])), "iron-calcium-pill")
    assert combine._ONE_TABLET in shown["caveats"][0]  # the opened tip says it too
    # nothing is kept apart (no calcium, no zinc beside a high-dose iron): no schedule to caveat, no note
    calm = lab.day([lab.keep("Vitamin D3", 20, "mcg"), lab.keep("Magnesium", 56, "mg", index=1), lab.keep("Iron", 14, "mg", index=2)])
    assert not calm["split_pills"] and combine.ONE_TABLET_NOTE not in combine.day_text(calm)
    # a calcium dose above 500 mg is split over two meals: also a schedule
    assert lab.day([lab.keep("Calcium", 1000, "mg", "calcium carbonate")])["split_pills"]
    assert not combine.plan_day([], [], None)["split_pills"] and combine.day_text({"slots": [], "split_pills": True}) == []


def test_calcium_alone_goes_to_a_meal_only_when_a_rule_asks_for_it(lab):
    d = lab.day([lab.keep("Calcium", 400, "mg", "calcium carbonate")])
    assert where(d)[0] == {"Calcium": [2]} and rules_of(d) == {(2, "calcium-carbonate-meal")}
    unknown = lab.day([lab.keep("Calcium", 400, "mg")])  # an unknown form shows no carbonate tip: calcium has no rule, so no meal
    assert where(unknown)[0] == {} and [i["text"] for i in unknown["anytime"]] == ["Calcium 400 mg"]
    assert unknown["empty"] and unknown["medicine"] is not None
    assert combine.day_text(unknown) == []  # nothing to say in the share text either
    citrate = lab.day([lab.keep("Calcium", 300, "mg", "calcium citrate")])
    assert citrate["empty"] and [i["label"] for i in citrate["anytime"]] == ["Calcium"]


def test_a_calcium_range_splits_on_its_upper_end_and_other_units_are_converted(lab):
    ranged = lab.keep("Calcium", 400, "mg")
    ranged["dose_max"] = 600
    assert where(lab.day([ranged]))[0] == {"Calcium": [2, 3]}  # "400-600 mg": the upper end counts, so it is split
    small = lab.keep("Calcium", 300, "mg")
    small["dose_max"] = 450
    assert where(lab.day([small]))[0] == {}  # a rule-less calcium has no meal
    with_iron = lab.day([ranged, lab.keep("Iron", 14, "mg", index=1)])
    assert where(with_iron)[0]["Calcium"] == [1, 3] and ("calcium-split-500" in {r for _, r in rules_of(with_iron)})
    grams = lab.day([lab.keep("Calcium", 1, "g", "calcium carbonate")])
    assert len(where(grams)[0]["Calcium"]) == 2  # 1 g is 1000 mg
    iu = lab.keep("Calcium", 1200, "IU", "calcium carbonate")  # an IU dose is no mg: not split, the carbonate tip stays
    assert where(lab.day([iu]))[0] == {"Calcium": [2]}
    edge = lab.day([lab.keep("Calcium", 500, "mg", "calcium carbonate")])
    assert where(edge)[0] == {"Calcium": [2]}  # exactly 500 mg is not "more than 500 mg"


def test_a_plant_iron_food_travels_with_its_vitamin_c_food_and_the_pills_follow_the_foods(lab):
    lentils, guava, salmon = lab.pool("iron", "Lentils, pink"), lab.pool("vitamin c", "Guavas"), lab.pool("vitamin d", "salmon, sockeye")
    foods = [lab.replace("Iron", 14, "mg", lentils, index=0), lab.replace("Vitamin C", 80, "mg", guava, index=1),
             lab.replace("Vitamin B12", 2.5, "mcg", salmon, index=2)]
    d = lab.day([lab.keep("Vitamin D3", 20, "mcg", index=3), lab.keep("Calcium", 400, "mg", "calcium carbonate", index=4)], foods)
    _, f = where(d)
    assert f == {"Red lentils (dry)": f["Guavas"], "Guavas": f["Guavas"]}  # the pair shares a meal; the salmon needs nobody: it is left out
    assert [i["label"] for i in d["free_foods"]] == ["Salmon, sockeye"]
    assert d["needs_layout"] and d["directives"] == []  # the partner is in the plan: nothing to add
    assert (f["Guavas"], "iron-vitc") in rules_of(d) and (f["Guavas"], "iron-tea-coffee") in rules_of(d)
    assert tip(d, f["Guavas"], "iron-vitc")["items"] == ["food:red lentils dry", "food:guavas"]
    # the fat-soluble D goes to the meal with the most placed fat foods (none: the heaviest meal); a fat food (the salmon) is in the plan,
    # so no "add some fat" hint. Calcium carbonate goes to the heaviest free meal.
    assert tip(d, where(d)[0]["Vitamin D3"][0], "fat-vitamins-de")["extra"] == ""
    hooks = lab.sw._combine_hooks(False)
    assert combine.meal_layout(foods, 3, hooks) == ["Meal 2 (lunch): Red lentils (dry) + Guavas"]
    assert combine.meal_layout(foods, 2, hooks) == ["Meal 1 (lunch): Red lentils (dry) + Guavas"]
    assert combine.meal_layout(foods, 1, hooks) == ["Meal 1: Red lentils (dry) + Guavas"]


def test_a_layout_names_only_the_foods_that_must_share_a_meal_never_the_whole_plan(lab):
    """The sample label's swaps plus the iron card swapped for lentils: seven foods, one pair. The plan writer is asked for the pair
    and nothing else (a layout that placed every food by grams gave 'Meal 1: five foods, Meal 3: one'), at 1, 2 and 3 meals."""
    pools = [("Iron", 14, "mg", "iron", "Lentils, pink"), ("Vitamin C", 80, "mg", "vitamin c", "Guavas"),
             ("Vitamin B12", 2.5, "mcg", "vitamin b12", "mackerel, Atlantic"), ("Zinc", 10, "mg", "zinc", "oyster, eastern, farmed"),
             ("Magnesium", 56, "mg", "magnesium", "hemp seed"), ("Selenium", 55, "mcg", "selenium", "brazilnuts"),
             ("Vitamin A", 800, "mcg", "vitamin a", "Duck, domesticated, liver")]
    swaps = [lab.replace(name, dose, unit, lab.pool(key, contains), index=i) for i, (name, dose, unit, key, contains) in enumerate(pools)]
    hooks = lab.sw._combine_hooks(False)
    day = lab.day([], swaps)
    foods = [i["label"] for s in day["slots"] for i in s["items"]] + [i["label"] for i in day["free_foods"]]
    assert len(foods) == len(set(foods)) >= 6
    assert [i["label"] for s in day["slots"] for i in s["items"]] == ["Red lentils (dry)", "Guavas"] and len(day["free_foods"]) == len(foods) - 2
    for n in (1, 2, 3):
        lines = combine.meal_layout(swaps, n, hooks)
        listed = [name for line in lines if line.startswith("Meal ") for name in line.split(": ", 1)[1].split(" + ")]
        assert sorted(listed) == ["Guavas", "Red lentils (dry)"], (n, lines)
        for line in lines:
            if line.startswith("Meal "):  # no meal holds more than ceil(foods / meals) + 1 foods
                assert len(line.split(": ", 1)[1].split(" + ")) <= -(-len(foods) // n) + 1, (n, line)


def test_a_vitamin_c_pill_goes_with_the_plant_iron_food_that_has_no_vitamin_c(lab):
    lentils = lab.pool("iron", "Lentils, pink")
    d = lab.day([lab.keep("Vitamin C", 80, "mg")], [lab.replace("Iron", 14, "mg", lentils)])
    p, f = where(d)
    assert p["Vitamin C"] == [f["Red lentils (dry)"]] and d["anytime"] == []
    assert set(tip(d, f["Red lentils (dry)"], "iron-vitc")["items"]) == {"food:red lentils dry", "pill:vitamin c"}
    alone = lab.day([lab.keep("Vitamin C", 80, "mg")])  # no iron anywhere: nothing for the vitamin C pill to do
    assert alone["anytime"][0]["label"] == "Vitamin C" and alone["empty"]


def test_a_plant_iron_food_with_no_partner_asks_the_plan_writer_for_one(lab):
    lentils = lab.pool("iron", "Lentils, pink")
    items = [lab.replace("Iron", 14, "mg", lentils)]
    d = lab.day([], items)
    assert d["needs_layout"] and d["directives"] == [{"kind": "add_vitc", "slot": 2, "foods": ["Red lentils (dry)"], "rule_id": "iron-vitc"}]
    assert (2, "iron-vitc") in rules_of(d) and (2, "iron-tea-coffee") in rules_of(d)
    hooks = lab.sw._combine_hooks(False)
    assert combine.meal_layout(items, 3, hooks) == [
        "Meal 2 (lunch): Red lentils (dry)",
        "  - serve the Red lentils (dry) with a vitamin C food (for example peppers, citrus fruit, kiwi, berries or a squeeze of lemon)",
    ]
    assert combine.meal_layout(items, 1, hooks)[0] == "Meal 1: Red lentils (dry)"


def test_carotenoid_vegetables_get_their_fat(lab):
    carrot, almond = lab.pool("beta carotene", "Carrots, raw"), lab.pool("vitamin e", "Nuts, almonds")
    both = lab.day([], [lab.replace("Beta carotene", 6, "mg", carrot, index=0), lab.replace("Vitamin E", 12, "mg", almond, index=1)])
    _, f = where(both)
    assert f["Carrots"] == f["Almonds"] and both["directives"] == [] and both["needs_layout"]
    assert tip(both, f["Carrots"], "fat-carotenoids")["items"] == ["food:carrots", "food:almonds"]
    alone = lab.day([], [lab.replace("Beta carotene", 6, "mg", carrot)])
    assert alone["directives"] == [{"kind": "add_fat", "slot": 2, "foods": ["Carrots"], "rule_id": "fat-carotenoids"}]
    assert combine.meal_layout([lab.replace("Beta carotene", 6, "mg", carrot)], 2, lab.sw._combine_hooks(False)) == [
        "Meal 1 (lunch): Carrots", "  - cook or dress the Carrots with a little oil (for example olive or rapeseed oil)",
    ]
    # a carrot chosen for another nutrient, or a vegetable that is not a carotenoid food, asks for nothing
    assert not lab.day([], [lab.replace("Vitamin K", 90, "mcg", carrot)])["needs_layout"]
    assert not lab.day([], [lab.replace("Beta carotene", 6, "mg", lab.pool("beta carotene", "Melons, cantaloupe"))])["needs_layout"]


def test_bundles_go_heaviest_first_into_the_meal_with_the_fewest_foods_and_a_savoury_bundle_is_never_a_breakfast(lab):
    sw = lab.sw
    rows = [lab.pool("iron", name) for name in ("Lentils, pink", "Beans, white", "Teff", "Amaranth grain")]
    items = [lab.replace("Iron", 14, "mg", row, index=i) for i, row in enumerate(rows)]  # four plant-iron foods, no vitamin C food: four bundles
    hooks = sw._combine_hooks(False)
    names = [sw._food_name(r) for r in rows]
    assert len(set(names)) == 4
    assert [combine._make_food("f", 0, "x", r, None, {"iron"}, hooks).breakfast for r in rows] == [False, False, True, True]  # legumes: no; grains: yes

    def foods_by_meal(grams):
        day = combine.plan_day([], items, dict(hooks, grams=lambda d: grams[d["card_index"]]))
        assert day["needs_layout"] and len(day["directives"]) >= 1
        return [where(day)[1][n] for n in names]

    assert foods_by_meal([100, 100, 100, 100]) == [2, 3, 1, 2]  # lunch, dinner, breakfast (the grains), then back to the emptiest (ties: lunch first)
    assert foods_by_meal([100, 100, 100, 300]) == [3, 3, 1, 2]  # the 300 g grain goes first (lunch); the legumes never go to breakfast
    assert foods_by_meal([50, 400, 300, 200]) == [3, 2, 3, 1]  # heaviest first; the next to the emptiest meal (by foods, then by grams)
    # the same plan, balanced by the number of foods: five directive foods spread 2 / 2 / 1, never 3 / 1 / 1 because one is heavy
    more = items + [lab.replace("Zinc", 10, "mg", lab.pool("zinc", "pumpkin seeds"), index=4)]  # pumpkin seeds: no partner needed, so not placed
    day = combine.plan_day([], more, dict(hooks, grams=lambda d: [900, 100, 100, 100, 100][d["card_index"]]))
    counts = sorted(len([i for i in s["items"]]) for s in day["slots"])
    assert counts == [1, 1, 2] and "Pumpkin seeds" not in where(day)[1]


def test_what_a_food_is_is_read_from_its_usda_name_and_category(lab):
    """The internal traits only decide WHICH meal a tip sits in, but a food in the wrong class would pair the wrong foods."""
    row = lambda nutrient, contains: lab.pool(nutrient, contains)  # noqa: E731

    def traits(nutrients, food, vitc100=None):
        return combine._food_traits(food["food_description"], food["food_category"], set(nutrients), None, vitc100)

    assert traits({"iron"}, row("iron", "Lentils, pink")) == {"plant_iron"}
    assert traits({"iron"}, row("iron", "Beans, white")) == {"plant_iron"}  # "mature seeds" in a bean's name is no seed food
    assert traits({"iron"}, row("iron", "Duck, domesticated, liver")) == frozenset()  # animal iron is not asked to meet vitamin C
    assert traits({"calcium"}, row("iron", "Lentils, pink")) == frozenset()  # a food chosen for another nutrient
    assert traits({"beta carotene"}, row("beta carotene", "Carrots, raw")) == {"carotenoid"}
    assert "carotenoid" in traits({"vitamin a"}, row("beta carotene", "Spinach, raw")) and "carotenoid" in traits({"beta carotene"}, row("beta carotene", "Kale, raw"))
    assert "carotenoid" not in traits({"beta carotene"}, row("zinc", "pumpkin seeds"))  # the seeds are a fat food, not a carotenoid vegetable
    assert "carotenoid" not in traits({"beta carotene"}, row("beta carotene", "Melons, cantaloupe"))
    assert traits({"vitamin e"}, row("vitamin e", "Nuts, almonds")) == {"fat"} and "fat" in traits({"vitamin d"}, row("vitamin d", "salmon, sockeye"))
    assert "fat" in traits({"vitamin e"}, row("vitamin e", "Eggs, Grade A, Large, egg yolk")) and "fat" in traits({"vitamin e"}, row("vitamin e", "Avocados"))
    assert "fat" not in traits({"iron"}, row("iron", "Soybeans, mature seeds, raw")) and "fat" not in traits({"vitamin k"}, row("vitamin k", "Kale, raw"))
    # vitamin C: by the USDA figure of the portion when there is one (20 mg), else by what the name says
    assert "vitc" in traits({"vitamin c"}, row("vitamin c", "Guavas"), 228.3) and "vitc" not in traits({"vitamin c"}, row("vitamin c", "Guavas"), 5.0)
    assert "vitc" in traits({"vitamin c"}, row("vitamin c", "Guavas")) and "vitc" in traits({"vitamin c"}, row("vitamin c", "Peppers, bell, red, raw"))
    assert "vitc" not in traits({"vitamin c"}, row("vitamin d", "Fish, salmon, sockeye")) and "vitc" not in traits({"iron"}, row("iron", "Lentils, pink"))
    assert "vitc" not in traits({"iron"}, {"food_description": "Fish, roughy, orange, raw", "food_category": "Finfish and Shellfish Products"})  # an orange FISH
    sweet = {"food_description": "Sweet potatoes, orange flesh, without skin, raw", "food_category": "Vegetables and Vegetable Products"}
    assert "vitc" not in traits({"beta carotene"}, sweet) and "carotenoid" in traits({"beta carotene"}, sweet)


def test_foods_that_need_no_partner_are_not_laid_out_at_all(lab):
    """Pumpkin seeds, kiwi, salmon: nothing to pair. The day lists them as free foods and the prompt keeps its old text."""
    items = [lab.replace("Zinc", 10, "mg", lab.pool("zinc", "pumpkin seeds"), index=0),
             lab.replace("Vitamin C", 80, "mg", lab.pool("vitamin c", "Kiwifruit"), index=1)]
    d = lab.day([], items)
    assert not d["needs_layout"] and [i["kind"] for i in d["free_foods"]] == ["food", "food"] and d["empty"] and d["has_items"]
    assert all(not s["items"] for s in d["slots"]) and combine.meal_layout(items, 3, lab.sw._combine_hooks(False)) == []


def test_iron_zinc_calcium_and_vitamin_c_pills_over_three_meals(lab):
    pills = [lab.keep("Iron", 30, "mg", index=0), lab.keep("Zinc", 15, "mg", index=1), lab.keep("Calcium", 600, "mg", "calcium citrate", index=2),
             lab.keep("Vitamin C", 500, "mg", index=3)]
    d = lab.day(pills)
    p, _ = where(d)
    assert p == {"Iron": [2], "Vitamin C": [2], "Calcium": [1, 3], "Zinc": [3]}  # vitamin C with the iron, everything else away from it
    assert rules_of(d) == {(1, "iron-calcium-pill"), (1, "calcium-split-500"), (2, "iron-vitc"), (2, "iron-tea-coffee"), (3, "zinc-iron-pills")}
    assert set(tip(d, 2, "iron-vitc")["items"]) == {"pill:iron", "pill:vitamin c"}
    below = lab.day([lab.keep("Iron", 24, "mg", index=0), lab.keep("Zinc", 15, "mg", index=1)])  # under 25 mg: the table has no rule
    assert [i["label"] for i in below["anytime"]] == ["Zinc"] and "zinc-iron-pills" not in {r for _, r in rules_of(below)}
    exact = lab.day([lab.keep("Iron", 25, "mg", index=0), lab.keep("Zinc", 15, "mg", index=1)])
    assert where(exact)[0]["Zinc"] != where(exact)[0]["Iron"] and "zinc-iron-pills" in {r for _, r in rules_of(exact)}


def test_a_dose_over_the_upper_level_is_invisible_to_every_rule_but_the_pharmacist_line(lab):
    d = lab.day([lab.keep("Iron", 60, "mg", index=0), lab.keep("Vitamin D3", 20, "mcg", index=1), lab.keep("Calcium", 400, "mg", "calcium carbonate", 2)])
    assert [i["label"] for i in d["held"]] == ["Iron"] and where(d)[0] == {"Vitamin D3": [2], "Calcium": [2]}
    assert {r for _, r in rules_of(d)} == {"fat-vitamins-de", "calcium-carbonate-meal"}  # no iron tip, no iron + calcium tip, no iron + vitamin C
    assert d["medicine"] is not None  # a medicine binds whatever the dose
    only = lab.day([lab.keep("Iron", 60, "mg", index=0)])
    assert only["empty"] and [i["label"] for i in only["held"]] == ["Iron"] and only["medicine"] is not None and combine.day_text(only) == []
    assert combine.card_tags("iron", dose_value=60, dose_unit="mg", over_limit=True) == []
    assert combine.item_tips("iron", dose_value=60, dose_unit="mg", over_limit=True) == []
    assert combine.item_tips("calcium", kept={"iron": 60}, dose_value=1000, dose_unit="mg") != []  # the caller decides who is "kept"
    assert combine.kept_pills([lab.keep("Iron", 60, "mg"), lab.keep("Zinc", 15, "mg")], lab.sw._combine_hooks(False)) == {"zinc": 15.0}


def test_every_pill_over_the_apps_upper_level_is_held_back_and_gets_no_tip(lab):
    """The engine uses the app's own check (the one behind the Heads-up box) for every nutrient that has an upper level."""
    sw = lab.sw
    checked = 0
    for key, entry in sw._UPPER_LIMITS.items():
        display = lab.bb._NUTRIENT_LEXICON[key]["display"] if key in lab.bb._NUTRIENT_LEXICON else entry["name"]
        over = lab.keep(display, round(entry["limit"] * 1.5, 1), entry["unit"], "", 0)
        at_limit = lab.keep(display, entry["limit"], entry["unit"], "", 0)
        if not sw._final_upper_limit_warnings([over]):
            continue  # the app has a limit but no warning for this form (folate: only folic acid is limited)
        assert not sw._final_upper_limit_warnings([at_limit]), key
        d = lab.day([over])
        assert [i["label"] for i in d["held"]] == [sw._nutrient_title(display)] and d["anytime"] == [] and d["empty"], key
        assert not any(s["items"] or s["tips"] for s in d["slots"]), key
        assert combine.card_tags(key, dose_value=over["dose_value"], dose_unit=entry["unit"], over_limit=bool(sw._final_upper_limit_warnings([over]))) == []
        assert (d["medicine"] is not None) == (key in combine._MINERAL_KEYS), key  # only the pharmacist line still counts it
        assert lab.day([at_limit])["held"] == []
        checked += 1
    assert checked >= 15, checked


def test_pregnancy_keeps_unsafe_foods_out_of_the_day_and_the_prompt_lines(lab):
    liver, lentils, carrot = lab.pool("vitamin a", "liver"), lab.pool("iron", "Lentils, pink"), lab.pool("beta carotene", "Carrots, raw")
    king = {"food_description": "Fish, mackerel, king, raw", "food_category": "Finfish and Shellfish Products", "amount_per_100g": 4.0, "unit": "mcg"}
    items = [lab.replace("Vitamin B12", 2.5, "mcg", liver, index=0), lab.replace("Iron", 14, "mg", lentils, index=1),
             lab.replace("Beta carotene", 6, "mg", carrot, index=2), lab.replace("Vitamin D", 15, "mcg", king, index=3)]
    d = lab.day([], items, pregnant=True)
    every = [i["label"] for s in d["slots"] for i in s["items"]] + [i["label"] for i in d["free_foods"]]
    assert not any("iver" in n or "ackerel" in n for n in every)  # neither the organ meat nor the high-mercury fish is in the day at all
    pregnant_lines = combine.meal_layout(items, 3, lab.sw._combine_hooks(True))
    assert not any("iver" in line or "ackerel" in line for line in pregnant_lines)
    assert any("Carrots" in line and "cook or dress the Carrots" in line for line in pregnant_lines)  # its fat partner is gone: oil is asked for instead
    normal = lab.day([], items)
    assert any("iver" in i["label"] for i in normal["free_foods"])  # without the flag the food is just a food (the app's own filter decides)
    normal_lines = combine.meal_layout(items, 3, lab.sw._combine_hooks(False))
    assert any("Carrots" in line and "ackerel" in line for line in normal_lines)  # the king mackerel is the fat the carrots are placed with
    assert not any("iver" in line for line in normal_lines)  # a food that needs no partner is never in the layout
    only_liver = lab.day([], items[:1], pregnant=True)
    assert not only_liver["has_items"] and only_liver["empty"]


def test_the_engine_has_no_diet_or_pregnancy_logic_of_its_own():
    """The app hands the engine the already diet-filtered swaps and a `skip_food` hook for the pregnancy filter; the engine reads
    nothing about a diet, a profile or pregnancy (no parameter, variable or text of it names one)."""
    import ast
    import inspect

    for fn in (combine.plan_day, combine.meal_layout, combine.item_tips, combine.card_tags, combine.table_rows, combine.kept_pills):
        assert not set(inspect.signature(fn).parameters) & {"profile", "diet", "pregnant", "diet_label"}, fn.__name__
    tree = ast.parse(inspect.getsource(combine))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {a.arg for a in ast.walk(tree) if isinstance(a, ast.arg)}
    assert not [n for n in names if re.search(r"diet|vegan|vegetarian|pregnan|profile", n, re.I)], names


# ---------------------------------------------------------------------------------------------------------------- tips of one item
def test_card_tags_are_few_short_and_only_positive(lab):
    sw = lab.sw
    hooks = sw._combine_hooks()
    one = lambda key, **kw: combine.card_tags(key, hooks=hooks, **kw)  # noqa: E731
    assert one("vitamin d3", dose_value=20, dose_unit="mcg") == [{"rule_id": "fat-vitamins-de", "icon": "\U0001F951", "text": "some fat"}]
    assert [t["text"] for t in one("vitamin e", dose_value=12, dose_unit="mg")] == ["some fat"]
    assert [t["text"] for t in one("Fish oil", dose_value=1000, dose_unit="mg")] == ["some fat"]
    assert [t["rule_id"] for t in one("omega-3 (epa+dha)")] == ["fat-omega3"]
    carbonate = one("calcium", dose_value=1000, dose_unit="mg", form="calcium carbonate")
    assert carbonate == [{"rule_id": "calcium-carbonate-meal", "icon": "\U0001F37D️", "text": "a meal"}]
    assert [t["text"] for t in one("calcium", dose_value=300, dose_unit="mg", form="Calciumcarbonat")] == ["a meal"]
    assert [t["text"] for t in one("calcium", component="Calcium carbonate", dose_value=300, dose_unit="mg")] == ["a meal"]
    for quiet in (
        one("calcium", dose_value=1000, dose_unit="mg"), one("calcium", dose_value=300, dose_unit="mg", form="calcium citrate"),  # unknown / other form
        one("iron", dose_value=14, dose_unit="mg"), one("magnesium", dose_value=56, dose_unit="mg"), one("zinc", dose_value=10, dose_unit="mg"),
        one("vitamin c", dose_value=1500, dose_unit="mg"), one("vitamin b12", dose_value=2.5, dose_unit="mcg"), one("selenium", dose_value=55, dose_unit="mcg"),
        one("vitamin a", dose_value=800, dose_unit="mcg"), one("vitamin k", dose_value=90, dose_unit="mcg"), one("folate", dose_value=200, dose_unit="mcg"),
        one("vitamin d3", dose_value=20, dose_unit="mcg", over_limit=True),
    ):
        assert quiet == []
    card_ids = {r["id"] for r in combine.RULES if "card" in r["surfaces"]}
    for key in sw.bb._NUTRIENT_LEXICON:  # every nutrient of the lexicon: at most two tags, short, from the card rules, positive
        tags = one(key, dose_value=1000, dose_unit="mg", form="oxide carbonate")
        assert len(tags) <= 2 and all(t["rule_id"] in card_ids and 0 < len(t["text"]) <= 18 for t in tags), key
        assert [t["rule_id"] for t in tags] == sorted({t["rule_id"] for t in tags}, key=[t["rule_id"] for t in tags].index)


def test_item_tips_of_a_kept_pill(lab):
    hooks = lab.sw._combine_hooks()
    tips = lambda key, **kw: combine.item_tips(key, hooks=hooks, **kw)  # noqa: E731
    ids = lambda ts: [t["rule_id"] for t in ts]  # noqa: E731
    assert ids(tips("iron", dose_value=14, dose_unit="mg")) == ["iron-vitc", "iron-tea-coffee", "medicine-spacing"]
    assert ids(tips("iron", dose_value=14, dose_unit="mg", kept={"calcium": 500})) == ["iron-calcium-pill", "iron-vitc", "iron-tea-coffee", "medicine-spacing"]
    assert ids(tips("iron", dose_value=30, dose_unit="mg", kept={"zinc": 15, "calcium": None}))[:2] == ["iron-calcium-pill", "zinc-iron-pills"]
    assert "zinc-iron-pills" not in ids(tips("iron", dose_value=24, dose_unit="mg", kept={"zinc": 15}))  # under 25 mg
    assert ids(tips("zinc", dose_value=10, dose_unit="mg", kept={"iron": 30})) == ["zinc-iron-pills", "medicine-spacing"]
    assert ids(tips("zinc", dose_value=10, dose_unit="mg", kept={"iron": 14})) == ["medicine-spacing"]
    assert ids(tips("zinc", dose_value=10, dose_unit="mg", kept={"iron"})) == ["medicine-spacing"]  # keys only: the iron dose is unknown
    assert ids(tips("calcium", dose_value=1000, dose_unit="mg", form="calcium carbonate", kept=["iron"])) == [
        "iron-calcium-pill", "calcium-split-500", "calcium-carbonate-meal", "calcium-vitamin-d", "medicine-spacing"]
    assert ids(tips("calcium", dose_value=300, dose_unit="mg")) == ["calcium-vitamin-d", "medicine-spacing"]
    assert ids(tips("vitamin d3", dose_value=20, dose_unit="mcg")) == ["fat-vitamins-de"]
    assert ids(tips("fish oil")) == ["fat-omega3"] and ids(tips("magnesium", dose_value=56, dose_unit="mg")) == ["medicine-spacing"]
    assert tips("selenium") == [] and tips("vitamin b12") == [] and tips("") == [] and tips("coenzyme q10") == []
    assert tips("iron", side="nonsense") == [] and tips("iron", dose_value=14, dose_unit="mg", over_limit=True) == []
    shape = tips("calcium", dose_value=1000, dose_unit="mg", form="calcium carbonate")[0]
    assert set(shape) == {"rule_id", "rel", "icon", "text", "short", "why", "caveats", "sources", "rank"}
    assert isinstance(shape["caveats"], list) and shape["sources"][0] == combine.rule(shape["rule_id"])["source"]
    ranks = [(t["rank"], TABLE[t["rule_id"]]) for t in tips("calcium", dose_value=1000, dose_unit="mg", form="calcium carbonate", kept={"iron"})]
    assert ranks == sorted(ranks)
    # the dialog of a kept magnesium pill shows the pharmacist line (the rule is about magnesium too): the table decides
    assert tips("magnesium", dose_value=56, dose_unit="mg")[0]["rel"] == "note"


def test_item_tips_of_a_swapped_food(lab):
    hooks = lab.sw._combine_hooks()
    tips = lambda key, food, **kw: combine.item_tips(key, "food", food=food, hooks=hooks, **kw)  # noqa: E731
    ids = lambda ts: [t["rule_id"] for t in ts]  # noqa: E731
    lentils, liver = lab.pool("iron", "Lentils, pink"), lab.pool("iron", "Duck, domesticated, liver")
    assert ids(tips("iron", lentils)) == ["iron-vitc", "iron-tea-coffee"]
    assert tips("iron", liver) == [] and tips("iron", None) == [] and tips("iron", {}) == [] and tips("iron", "x") == []  # heme iron: nothing
    assert ids(tips("beta carotene", lab.pool("beta carotene", "Carrots, raw"))) == ["fat-carotenoids"]
    assert ids(tips("vitamin a", lab.pool("beta carotene", "Carrots, raw"))) == ["fat-carotenoids"]
    assert tips("vitamin k", lab.pool("beta carotene", "Carrots, raw")) == []  # chosen for something else
    assert tips("beta carotene", lab.pool("beta carotene", "Melons, cantaloupe")) == []
    assert ids(tips("calcium", lab.pool("calcium", "Kale, raw"))) == ["calcium-vitamin-d"]
    assert tips("iron", lentils, over_limit=True) == []
    # a pill-only rule never appears on a food row, a food-only rule never on a pill row
    assert combine.item_tips("beta carotene", "pill", dose_value=6, dose_unit="mg", hooks=hooks) == []


def test_kept_pills_helper(lab):
    hooks = lab.sw._combine_hooks()
    got = combine.kept_pills([lab.keep("Vitamin D3", 20, "mcg"), lab.keep("Vitamin D", 10, "mcg"), lab.keep("Iron", 25, "mg"),
                              lab.keep("Coenzyme Q10", 100, "mg"), lab.keep("Calcium", 1, "g"), None, {}], hooks)
    assert got == {"vitamin d": 0.02, "iron": 25.0, "calcium": 1000.0} and combine.kept_pills(None) == {} and combine.kept_pills("x") == {}


# ---------------------------------------------------------------------------------------------------------------- the table view
def test_the_table_puts_the_visitors_own_rules_first(lab):
    hooks = lab.sw._combine_hooks()
    pills = [lab.keep("Iron", 14, "mg", index=0), lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=1), lab.keep("Vitamin D3", 20, "mcg", index=2)]
    rows = combine.table_rows(pills, [lab.replace("Beta carotene", 6, "mg", lab.pool("beta carotene", "Carrots, raw"))], hooks)
    assert len(rows) == 11 and [r["id"] for r in rows if r["in_plan"]] == [
        "iron-vitc", "fat-vitamins-de", "fat-carotenoids", "calcium-carbonate-meal", "calcium-vitamin-d", "iron-calcium-pill", "calcium-split-500",
        "iron-tea-coffee", "medicine-spacing",
    ]
    assert [r["in_plan"] for r in rows] == [True] * 9 + [False] * 2 and {r["id"] for r in rows[9:]} == {"zinc-iron-pills", "fat-omega3"}
    # a rule that needs its partner is "in your plan" only with both: Vitamin D helps calcium, but no vitamin D here
    calcium_only = combine.table_rows([lab.keep("Calcium", 300, "mg")], [], hooks)
    assert not next(r for r in calcium_only if r["id"] == "calcium-vitamin-d")["in_plan"]
    swapped_d = combine.table_rows([lab.keep("Calcium", 300, "mg")], [lab.replace("Vitamin D", 15, "mcg", lab.pool("vitamin d", "salmon, sockeye"))], hooks)
    assert next(r for r in swapped_d if r["id"] == "calcium-vitamin-d")["in_plan"]  # a food chosen for vitamin D counts as the partner
    # a plant-iron food swap puts the iron rules in the plan; heme iron does not
    lentil = combine.table_rows([], [lab.replace("Iron", 14, "mg", lab.pool("iron", "Lentils, pink"))], hooks)
    assert {r["id"] for r in lentil if r["in_plan"]} == {"iron-vitc", "iron-tea-coffee"}
    liver = combine.table_rows([], [lab.replace("Iron", 14, "mg", lab.pool("iron", "Duck, domesticated, liver"))], hooks)
    assert not any(r["in_plan"] for r in liver)
    # a pill over its upper level is invisible, the pharmacist line still counts it
    held = combine.table_rows([lab.keep("Iron", 60, "mg"), lab.keep("Calcium", 300, "mg")], [], hooks)
    assert {r["id"] for r in held if r["in_plan"]} == {"medicine-spacing"}  # not iron's rules, not the iron + calcium pair
    assert len(combine.table_rows(5, "x", {"canon": 3})) == 11 and len(combine.table_rows(None, None, None)) == 11


# ---------------------------------------------------------------------------------------------------------------- the prompt layout
def test_the_layout_is_the_same_for_every_set_of_kept_pills_and_names_no_pill(lab):
    hooks = lab.sw._combine_hooks(False)
    items = [lab.replace("Iron", 14, "mg", lab.pool("iron", "Lentils, pink"), index=0),
             lab.replace("Beta carotene", 6, "mg", lab.pool("beta carotene", "Carrots, raw"), index=1)]
    kept = [lab.keep("Vitamin D3", 20, "mcg", index=2), lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=3), lab.keep("Zinc", 15, "mg", index=4)]
    for n in (1, 2, 3):
        bare = combine.meal_layout(items, n, hooks)
        assert bare and combine.meal_layout(items, n, hooks) == bare
        day_with = lab.day(kept, items)
        day_without = lab.day([], items)
        assert where(day_with)[1] == where(day_without)[1] and day_with["directives"] == day_without["directives"]
    blob = " ".join(combine.meal_layout(items, 3, hooks)).lower()
    for word in ("vitamin d", "calcium", "zinc", "pill", "supplement", "mg", "take iron"):
        assert word not in blob, word
    for r in combine.RULES:
        assert r["takeaway"].lower() not in blob


def test_meal_count_is_clamped_and_garbage_means_three(lab):
    hooks = lab.sw._combine_hooks(False)
    items = [lab.replace("Iron", 14, "mg", lab.pool("iron", "Lentils, pink"))]
    three = combine.meal_layout(items, 3, hooks)
    for garbage in (None, "x", 0, -4, 99, float("nan"), "3", [], object(), 3.9):
        got = combine.meal_layout(items, garbage, hooks)
        assert got == (combine.meal_layout(items, 1, hooks) if garbage in (0, -4) else three), garbage
    assert combine.meal_layout(items, 2, hooks)[0].startswith("Meal 1 (lunch)") and combine.meal_layout(items, "2", hooks) == combine.meal_layout(items, 2, hooks)
    assert combine.meal_layout(items, hooks=hooks) == three


def test_two_meals_merge_the_breakfast_foods_into_lunch_and_merge_their_directives(lab):
    hooks = lab.sw._combine_hooks(False)
    items = [lab.replace("Iron", 14, "mg", lab.pool("iron", "Lentils, pink"), index=0),
             lab.replace("Beta carotene", 6, "mg", lab.pool("beta carotene", "Carrots, raw"), index=1),
             lab.replace("Vitamin C", 80, "mg", lab.pool("vitamin c", "Guavas"), index=2)]
    d = lab.day([], items)
    assert d["needs_layout"]
    for n, expected_meals in ((3, 3), (2, 2), (1, 1)):
        lines = combine.meal_layout(items, n, hooks)
        assert sum(1 for line in lines if line.startswith("Meal ")) <= expected_meals
        foods_in_lines = [name for line in lines if line.startswith("Meal ") for name in line.split(": ", 1)[1].split(" + ")]
        assert sorted(foods_in_lines) == sorted(i["label"] for s in d["slots"] for i in s["items"])  # every food once, none lost in the merge


# --------------------------------------------------------------------------------------------------------------- totality, speed
def test_the_garbage_the_app_could_never_send_is_still_answered(lab):
    bad_things = (None, 5, "x", [None], [{}], [{"selected_food": 3}], [{"component": None, "selected_food": {"food_description": None}}], {"a": 1},
                  [{"component": {"x": 1}, "component_key": ["y"], "dose_value": "abc", "dose_unit": 3, "dose_label": 5}],
                  [{"component": "Iron", "dose_value": float("nan"), "dose_max": float("inf"), "dose_unit": "mg"}])
    for bad in bad_things:
        d = combine.plan_day(bad, bad, bad)
        assert [s["slot"] for s in d["slots"]] == [1, 2, 3] and isinstance(d["empty"], bool)
        assert combine.meal_layout(bad, bad, bad) == [] and isinstance(combine.day_text(d), list)
        assert len(combine.table_rows(bad, bad, bad)) == 11
        assert isinstance(combine.card_tags(bad, bad, bad, bad, dose_max=bad, over_limit=bad, component=bad, hooks=bad), list)
        assert isinstance(combine.item_tips(bad, bad, form=bad, dose_value=bad, dose_unit=bad, food=bad, kept=bad, hooks=bad), list)
        assert isinstance(combine.kept_pills(bad, bad), dict)
    assert combine.day_text(None) == [] and combine.day_text({"slots": 5}) == [] and combine.rule([]) is None
    boom = lambda *a: 1 / 0  # noqa: E731
    hooks = {k: boom for k in ("canon", "title", "dose_text", "food_label", "food_icon", "grams", "over_limit", "skip_food", "fact")}
    pills = [{"component": "Iron", "component_key": "iron", "dose_value": 14, "dose_unit": "mg", "dose_label": "14 mg"}]
    food = {"component": "Iron", "component_key": "iron", "selected_food": {"food_description": "Lentils, raw", "food_category": "Legumes"}}
    d = combine.plan_day(pills, [food], hooks)  # every hook fails: it counts as absent, never an error
    assert [i["text"] for s in d["slots"] for i in s["items"] if i["kind"] == "pill"] == ["Iron 14 mg"]
    assert combine.card_tags("vitamin d", hooks=hooks)[0]["text"] == "some fat" and combine.item_tips("iron", hooks=hooks)[0]["rule_id"] == "iron-vitc"


def test_a_day_of_twelve_foods_and_twelve_pills_takes_a_few_milliseconds(lab):
    sw = lab.sw
    hooks = sw._combine_hooks(False)
    foods = [("Iron", "iron", "Lentils, pink"), ("Vitamin C", "vitamin c", "Guavas"), ("Beta carotene", "beta carotene", "Carrots, raw"),
             ("Vitamin E", "vitamin e", "Nuts, almonds"), ("Zinc", "zinc", "pumpkin seeds"), ("Vitamin D", "vitamin d", "salmon, sockeye"),
             ("Vitamin K", "vitamin k", "Kale, raw"), ("Calcium", "calcium", "Kale, raw"), ("Magnesium", "magnesium", "Nuts, almonds"),
             ("Selenium", "selenium", "Nuts, brazilnuts"), ("Folate", "folate", "Lentils, pink"), ("Vitamin B6", "vitamin b6", "Fish, salmon, sockeye")]
    replace = []
    for i, (title, key, contains) in enumerate(foods):
        try:
            row = lab.pool(key, contains)
        except StopIteration:
            continue
        replace.append(lab.replace(title, 10, "mg", row, index=i))
    kept = [lab.keep(n, v, u, f, index=20 + i) for i, (n, v, u, f) in enumerate([
        ("Iron", 30, "mg", ""), ("Calcium", 1000, "mg", "calcium carbonate"), ("Zinc", 15, "mg", ""), ("Vitamin C", 500, "mg", ""),
        ("Vitamin D3", 20, "mcg", ""), ("Vitamin E", 12, "mg", ""), ("Fish oil", 1000, "mg", ""), ("Magnesium", 56, "mg", ""),
        ("Vitamin B12", 2.5, "mcg", ""), ("Selenium", 55, "mcg", ""), ("Folic acid", 400, "mcg", ""), ("Biotin", 50, "mcg", "")])]
    assert len(kept) == 12 and len(replace) >= 8
    combine.plan_day(kept, replace, hooks)  # warm: the app's own caches (USDA vitamin C index, lexicon) are filled once
    best = min(_timed(lambda: combine.plan_day(kept, replace, hooks)) for _ in range(30))
    assert best < 0.005, f"plan_day took {best * 1000:.1f} ms for 12 foods + 12 pills"
    assert min(_timed(lambda: combine.meal_layout(replace, 3, hooks)) for _ in range(30)) < 0.005
    assert min(_timed(lambda: combine.table_rows(kept, replace, hooks)) for _ in range(30)) < 0.005


def _timed(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


# --------------------------------------------------------------------------------------------------------- properties of random plans
NS = [None, 0, 1, 2, 3, 4, "2", "x", -3, 10**9, 2.7, float("nan")]
UNITS = ["mg", "mcg", "g", "IU", "", "µg", "ug", "MG"]
FORMS = ["", "", "calcium carbonate", "calcium citrate", "magnesium oxide", "magnesium citrate", "cholecalciferol", "Calciumcarbonat"]
COMBOS = [("Iron", [8, 14, 25, 30], ""), ("Calcium", [200, 500, 600, 1000], "calcium carbonate"), ("Zinc", [10, 15], ""), ("Vitamin C", [80, 500, 1500], ""),
          ("Vitamin D3", [20], ""), ("Vitamin E", [12], ""), ("Vitamin K2", [100], ""), ("Fish oil", [1000], ""), ("Magnesium", [56], "")]


def _scenario(lab, rnd):
    names = [lab.bb._NUTRIENT_LEXICON[k]["display"] for k in lab.bb._NUTRIENT_LEXICON]
    keep, repl = [], []
    for i in range(rnd.randint(0, 9)):
        name = rnd.choice(names)
        dose = rnd.choice([None, 0, 0.5, 5, 10, 14, 20, 25, 30, 60, 80, 250, 500, 600, 1000, 1500])
        unit, form = rnd.choice(UNITS), rnd.choice(FORMS)
        pool = lab.pool(lab.bb.canonical_nutrient_key(name)) if lab.bb.canonical_nutrient_key(name) else []
        if rnd.random() < 0.5 and pool:
            repl.append(lab.replace(name, dose, unit, rnd.choice(pool), form, index=i))
        else:
            keep.append(lab.keep(name, dose, unit, form, index=i))
    if rnd.random() < 0.5:  # the combinations the rules are about
        for j, (name, doses, form) in enumerate(COMBOS):
            if rnd.random() < 0.5 and not any(d["component"] == name for d in keep + repl):
                keep.append(lab.keep(name, rnd.choice(doses), "mcg" if name in ("Vitamin D3", "Vitamin K2") else "mg", form, index=20 + j))
    if rnd.random() < 0.4:  # the foods the layout is about
        for j, (nut, key, contains) in enumerate([("Iron", "iron", "Lentils, pink"), ("Vitamin C", "vitamin c", "Guavas"), ("Beta carotene", "beta carotene", "Carrots, raw"),
                                                  ("Vitamin E", "vitamin e", "Nuts, almonds"), ("Vitamin D", "vitamin d", "salmon, sockeye"), ("Vitamin K", "vitamin k", "Kale, raw")]):
            if rnd.random() < 0.5 and not any(d["component"] == nut for d in keep + repl):
                repl.append(lab.replace(nut, rnd.choice([5, 14, 80]), "mg", lab.pool(key, contains), index=30 + j))
    if rnd.random() < 0.05:
        keep.append(rnd.choice([None, {}, "x", 3, {"component": None}]))
    if rnd.random() < 0.05:
        repl.append(rnd.choice([None, {}, {"selected_food": {}}, {"selected_food": {"food_description": None}}, "x"]))
    return keep, repl, rnd.random() < 0.3, rnd.random() < 0.3


MEALS = {
    3: [("Meal 1 (breakfast)", (1,)), ("Meal 2 (lunch)", (2,)), ("Meal 3 (dinner)", (3,))],
    2: [("Meal 1 (lunch)", (1, 2)), ("Meal 2 (dinner)", (3,))],
    1: [("Meal 1", (1, 2, 3))],
}


def _layout_of_day(day, n):
    """The prompt lines for n meals, rebuilt from a day's own placement of the foods (the spec's mapping of 3 slots to n meals)."""
    if not day["needs_layout"]:
        return []
    out = []
    for label, slots in MEALS[n]:
        names = [i["label"] for s in slots for i in day["slots"][s - 1]["items"] if i["kind"] == "food"]
        if not names:
            continue
        out.append(f"{label}: " + " + ".join(names))
        for kind in ("add_vitc", "add_fat"):
            asked = sorted({f for d in day["directives"] if d["kind"] == kind and d["slot"] in slots for f in d["foods"]}, key=str.lower)
            if asked:
                out.append("  - " + combine._DIRECTIVE_TEXT[kind].format(foods=" and ".join(asked)))
    return out


def _check_day(case, lab, keep, repl, pregnant, force_limits):
    sw = lab.sw
    hooks = sw._combine_hooks(pregnant)
    if force_limits:  # make "over the upper level" common: the app's own check hits a pill rarely
        hooks["over_limit"] = lambda d: sum(map(ord, str(d.get("component")))) % 5 == 0
    day = combine._plan_day(keep, repl, hooks)  # the inner function: an exception would show
    json.dumps(day)  # plain data only
    assert day == combine._plan_day(keep, repl, hooks) == combine.plan_day(keep, repl, hooks), ("not deterministic", case)
    assert [(s["slot"], s["key"], s["label"]) for s in day["slots"]] == [(1, "breakfast", "Breakfast"), (2, "lunch", "Lunch"), (3, "dinner", "Dinner")]
    pills = combine._pills(keep, hooks)
    foods = {f.uid: f for f in combine._foods(repl, hooks)}
    live = {p.key: p for p in pills if p.key and not p.over_limit}
    pill_slots: dict[str, list[int]] = {}
    for slot in day["slots"]:
        uids = [i["uid"] for i in slot["items"]]
        assert len(uids) == len(set(uids)), ("the same item twice in one meal", case)
        for it in slot["items"]:
            if it["kind"] == "pill":
                pill_slots.setdefault(it["uid"], []).append(slot["slot"])
    anytime, held = {i["uid"] for i in day["anytime"]}, {i["uid"] for i in day["held"]}
    for p in pills:  # every kept pill is in exactly one place (calcium: one or two portions of the one pill)
        places = len(pill_slots.get(p.uid, [])) + (p.uid in anytime) + (p.uid in held)
        assert places == 1 or (p.key == "calcium" and len(pill_slots.get(p.uid, [])) == 2 and places == 2), (case, p.uid)
        assert (p.uid in held) == p.over_limit
    assert not anytime & held and len(day["anytime"]) == len(anytime) and len(day["held"]) == len(held)
    iron, calcium, zinc, vit_c = (live.get(k) for k in ("iron", "calcium", "zinc", "vitamin c"))
    iron_slot = pill_slots[iron.uid][0] if iron else None
    if calcium:
        split = calcium.dose_mg is not None and calcium.dose_mg > combine.CALCIUM_PORTION_MG
        carbonate = "carbonat" in calcium.salt
        if split:
            assert len(pill_slots[calcium.uid]) == 2 and pill_slots[calcium.uid] == sorted(set(pill_slots[calcium.uid])), (case, pill_slots)
            assert (pill_slots[calcium.uid][0], "calcium-split-500") in rules_of(day)
            assert all(i["text"].startswith("Calcium, half of") for s in day["slots"] for i in s["items"] if i["uid"] == calcium.uid)
        elif carbonate or iron:
            assert len(pill_slots[calcium.uid]) == 1
            assert ((pill_slots[calcium.uid][0], "calcium-carbonate-meal") in rules_of(day)) == carbonate
        else:
            assert calcium.uid in anytime, case  # no rule, no meal
        if iron:
            assert iron_slot not in pill_slots[calcium.uid], ("iron and calcium share a meal", case)
            assert (pill_slots[calcium.uid][0], "iron-calcium-pill") in rules_of(day)
    if zinc:
        high_iron = iron is not None and iron.dose_mg is not None and iron.dose_mg >= combine.IRON_PILL_WITH_ZINC_MG
        assert (zinc.uid in anytime) == (not high_iron)
        if high_iron:
            assert pill_slots[zinc.uid][0] != iron_slot and (pill_slots[zinc.uid][0], "zinc-iron-pills") in rules_of(day)
    fat_pills = [p for p in pills if not p.over_limit and p.key in combine._FAT_PILL_RULE]
    placed_foods = {i["uid"]: s["slot"] for s in day["slots"] for i in s["items"] if i["kind"] == "food"}

    def load(s):
        return sum(f.grams for u, f in foods.items() if placed_foods.get(u) == s)

    def vitc(s):
        here = [f for u, f in foods.items() if placed_foods.get(u) == s and "vitc" in f.traits]
        return max(sum(f.vitc_mg for f in here), combine._VITC_MIN_MG) if here else 0.0

    if fat_pills:
        slots = {pill_slots[p.uid][0] for p in fat_pills}
        score = {s: (sum("fat" in f.traits for u, f in foods.items() if placed_foods.get(u) == s), load(s), -combine._PREF.index(s)) for s in (1, 2, 3)}
        assert slots == {max(score, key=score.get)}, ("fat pills are not in the meal with the most fat", case, score)
        extras = {t["extra"] for s in day["slots"] for t in s["tips"] if t["rule_id"] in combine._FAT_RULE_IDS}
        assert extras == {"" if any("fat" in f.traits for f in foods.values()) else combine._HINTS["add_fat"]}
    if iron:
        score = {s: (vitc(s), load(s), -combine._PREF.index(s)) for s in (1, 2, 3)}
        assert iron_slot == max(score, key=score.get), ("iron is not in the vitamin C meal", case, score)
        if vit_c and vitc(iron_slot) == 0:
            assert pill_slots[vit_c.uid] == [iron_slot]
    # the foods: once each; only a food that must share a meal with a partner (or needs something added) is placed, the rest is free; where
    # they go never depends on a pill
    placed_labels = [i["label"] for s in day["slots"] for i in s["items"] if i["kind"] == "food"]
    labels = placed_labels + [i["label"] for i in day["free_foods"]]
    assert len(labels) == len(set(labels)) == len(foods)
    assert bool(placed_labels) == day["needs_layout"]
    asked = {name for d in day["directives"] for name in d["foods"]}
    for s in day["slots"]:
        here = [i for i in s["items"] if i["kind"] == "food"]
        for i in here:
            assert i["label"] in asked or len(here) > 1, ("a food is placed that needs nobody", case, i["label"])  # a partner shares its meal, or it is asked for one
            assert s["slot"] != 1 or foods[i["uid"]].breakfast, ("a savoury food at breakfast", case, i["label"])
    bare = combine._plan_day([], repl, hooks)
    assert where(bare)[1] == where(day)[1] and bare["directives"] == day["directives"] and bare["free_foods"] == day["free_foods"], case
    for n in (1, 2, 3):
        lines = combine.meal_layout(repl, n, hooks)
        assert lines == combine.meal_layout(repl, n, hooks) and bool(lines) == day["needs_layout"], (case, n)
        assert lines == _layout_of_day(day, n), ("the prompt lines and the day place the foods differently", case, n)
        blob = "\n".join(lines).lower()
        for p in pills:  # no pill, dose or rule text reaches the prompt lines
            if p.title and p.title.lower() not in {f.label.lower() for f in foods.values()}:
                assert f"{p.title.lower()} " not in blob.replace("vitamin c food", "").replace("tea or coffee", ""), (case, p.title, blob)
        assert not any(r["takeaway"].lower() in blob for r in combine.RULES)
    # partners share a meal, or the plan writer is asked for one
    if day["needs_layout"]:
        for u, f in foods.items():
            if "plant_iron" in f.traits and "vitc" not in f.traits:
                partners = [g for g in foods.values() if "vitc" in g.traits]
                if partners:
                    assert any(placed_foods.get(g.uid) == placed_foods[u] for g in partners), case  # the others are free
                else:
                    assert any(d["kind"] == "add_vitc" and d["slot"] == placed_foods[u] and f.label in d["foods"] for d in day["directives"]), case
            if "carotenoid" in f.traits and "fat" not in f.traits:
                partners = [g for g in foods.values() if "fat" in g.traits]
                if partners:
                    assert any(placed_foods.get(g.uid) == placed_foods[u] for g in partners), case  # the others are free
                else:
                    assert any(d["kind"] == "add_fat" and d["slot"] == placed_foods[u] and f.label in d["foods"] for d in day["directives"]), case
    # the tips: the rules' own words, sorted, one per rule and meal, nothing about a held pill
    rule_ids = {r["id"] for r in combine.RULES}
    held_or_unseen = held
    for slot in day["slots"]:
        keys = [(t["rank"], TABLE[t["rule_id"]]) for t in slot["tips"]]
        assert keys == sorted(keys) and len({t["rule_id"] for t in slot["tips"]}) == len(slot["tips"])
        for t in slot["tips"] + ([day["medicine"]] if day["medicine"] else []):
            r = combine.rule(t["rule_id"])
            assert t["rule_id"] in rule_ids and t["text"] == r["takeaway"] and t["rel"] == r["rel"] and t["rank"] == r["rank"]
            assert set(t["also"]) <= combine._FAT_RULE_IDS and (not t["also"] or t["rule_id"] in combine._FAT_RULE_IDS)
            assert t["extra"] in ("", combine._HINTS["add_fat"]) and isinstance(t["caveats"], list) and t["sources"] and t["items"]
            assert all(s["name"] and s["url"].startswith("https://") for s in t["sources"])
            assert t["sources"][0] == r["source"] and len(t["sources"]) == 1 + len(t["also"])
            if t["rule_id"] != "medicine-spacing":
                assert not held_or_unseen & set(t["items"]), ("a tip about a pill over its upper level", case)
    assert (day["medicine"] is not None) == any(p.key in combine._MINERAL_KEYS for p in pills)
    assert day["empty"] == (not any(s["tips"] for s in day["slots"])) and day["has_items"] == bool(pills or foods)
    assert day["directives"] == [] or day["needs_layout"]
    combine.day_text(day)
    return day


def test_random_plans_keep_every_promise(lab):
    """1500 seeded random plans from the real nutrient list and real USDA rows, half of them with many pills over their upper level."""
    rnd = random.Random(20261015)
    seen = {"layout": 0, "held": 0, "tips": 0, "split": 0, "directive": 0, "zinc": 0}
    for case in range(1500):
        keep, repl, pregnant, force = _scenario(lab, rnd)
        day = _check_day(case, lab, keep, repl, pregnant, force)
        seen["layout"] += day["needs_layout"]
        seen["held"] += bool(day["held"])
        seen["tips"] += sum(len(s["tips"]) for s in day["slots"])
        seen["split"] += any(t["rule_id"] == "calcium-split-500" for s in day["slots"] for t in s["tips"])
        seen["directive"] += bool(day["directives"])
        seen["zinc"] += any(t["rule_id"] == "zinc-iron-pills" for s in day["slots"] for t in s["tips"])
    assert all(v > 20 for v in seen.values()), seen  # the generator really reaches the interesting cases


def test_the_output_does_not_depend_on_the_hash_seed():
    """Sets and dicts must never leak their order into a plan: the same input gives the same bytes under three hash seeds."""
    script = textwrap.dedent(
        """
        import hashlib, json, sys
        sys.path.insert(0, %r)
        import combine
        canon = {"iron": "iron", "calcium": "calcium", "zinc": "zinc", "vitamin c": "vitamin c", "vitamin d": "vitamin d", "beta carotene": "beta carotene",
                 "vitamin e": "vitamin e", "vitamin k": "vitamin k", "magnesium": "magnesium", "vitamin b12": "vitamin b12", "vitamin d3": "vitamin d",
                 "fish oil": "fish oil", "epa": "epa"}
        hooks = {"canon": lambda n: canon.get(n, ""), "grams": lambda d: d.get("_g")}
        def food(desc, cat, nutrient, grams):
            return {"component": nutrient, "component_key": nutrient, "decision": "replace", "dose_value": 1, "dose_unit": "mg",
                    "selected_food": {"food_description": desc, "food_category": cat}, "_g": grams}
        repl = [food("Lentils, raw", "Legumes and Legume Products", "iron", 90), food("Guavas, raw", "Fruits and Fruit Juices", "vitamin c", 35),
                food("Carrots, raw", "Vegetables and Vegetable Products", "beta carotene", 80), food("Nuts, almonds", "Nut and Seed Products", "vitamin e", 40),
                food("Kale, raw", "Vegetables and Vegetable Products", "vitamin k", 100), food("Fish, salmon, raw", "Finfish and Shellfish Products", "vitamin d", 100)]
        keep = [{"component": n, "component_key": n, "decision": "keep", "dose_value": v, "dose_unit": "mg", "form": f}
                for n, v, f in (("iron", 30, ""), ("calcium", 1000, "calcium carbonate"), ("zinc", 15, ""), ("vitamin c", 500, ""), ("vitamin d3", 20, ""),
                                ("fish oil", 1000, ""), ("epa", 300, ""), ("magnesium", 56, ""), ("vitamin b12", 2, ""))]
        out = hashlib.sha256()
        for k in (keep, keep[::-1], keep[:3], []):
            for r in (repl, repl[::-1], repl[:2], []):
                out.update(json.dumps(combine.plan_day(k, r, hooks), sort_keys=True).encode())
                out.update(json.dumps(combine.table_rows(k, r, hooks), sort_keys=True).encode())
                for n in (1, 2, 3):
                    out.update(json.dumps(combine.meal_layout(r, n, hooks)).encode())
        print(out.hexdigest())
        """
    ) % str(ROOT / "swipe_mobile_app")
    seen = set()
    for seed in ("0", "1", "4242"):
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed}, timeout=60)
        assert r.returncode == 0, r.stderr[-600:]
        seen.add(r.stdout.strip())
    assert len(seen) == 1 and all(len(h) == 64 for h in seen), seen


def test_the_engine_is_pure_it_imports_no_app_no_streamlit_and_reads_no_file():
    import inspect

    source = inspect.getsource(combine)
    header = source.split('"""', 2)[2]
    imports = [line.strip() for line in header.splitlines() if line.startswith(("import ", "from ")) or line.strip().startswith(("import ", "from "))]
    assert set(imports) <= {"from __future__ import annotations", "import copy", "import logging", "import re", "from typing import Any, Mapping, NamedTuple",
                            "from blockbrain.app import canonical_nutrient_key"}, imports
    for banned in ("open(", "requests", "urllib", "socket", "subprocess", "os.environ", "getenv", "streamlit", "st.session_state", "llm_cache"):
        assert banned not in header, banned

"""Dietary filter: whole-word matching, USDA category rules and per-profile policy.

Every false positive / false negative from the 2026-10-01 diet audit is a row
below, plus hand-labelled everyday foods per profile (offline, local USDA DB).
"""
from __future__ import annotations

import pytest

import blockbrain.app as bb


def _profile(pid: str) -> dict:
    for profile in bb.load_dietary_profiles():
        if profile["id"] == pid:
            return profile
    raise KeyError(pid)


def _allowed(pid: str, food: str, category: str = "") -> bool:
    return bb.food_allowed_for_dietary_profile(food, _profile(pid), category)


# (profile, USDA food description, allowed?)
CASES = [
    # --- audit: substring false blocks of valid vegan foods --------------------
    ("vegan", "Nuts, almonds", True),                         # "nutsalmonds" contained "salmon"
    ("vegan", "Nuts, almonds, blanched", True),
    ("vegan", "Mango, Ataulfo, peeled, raw", True),           # "Ataulfo" contained "goat"
    ("vegan", "Squash, winter, butternut, raw", True),        # "butter"
    ("vegan", "Eggplant, raw", True),                         # "egg"
    ("vegan", "Melons, honeydew, raw", True),                 # "honey"
    ("vegan", "Beans, kidney, red, mature seeds, raw", True),  # organ word "kidney"
    ("vegan", "Beans, kidney, california red, mature seeds, raw", True),
    ("vegan", "Beans, Dry, Dark Red Kidney (0% moisture)", True),
    ("vegan", "Nuts, coconut meat, raw", True),               # "meat"
    ("vegan", "Mushrooms, oyster, raw", True),                # "oyster"
    ("vegan", "Mushroom, king oyster", True),
    ("vegan", "Salsify, (vegetable oyster), raw", True),
    ("vegan", "Squash, summer, scallop, raw", True),
    ("vegan", "Hearts of palm, raw", True),
    ("vegan", "Custard-apple, (bullock's-heart), raw", True),
    ("vegan", "Collards, raw", True),                         # "lard"
    ("vegan", "Buckwheat", True),
    ("vegan", "Crabapples, raw", True),
    ("vegan", "Gooseberries, raw", True),
    ("vegan", "Lambsquarters, raw", True),
    ("vegan", "Soy milk, sweetened, plain, refrigerated", True),
    ("vegan", "Beans, black turtle, mature seeds, raw", True),
    ("vegan", "Pigeon peas (red gram), mature seeds, raw", True),
    ("vegan", "Peanut butter, smooth style, without salt", True),
    # --- audit: animal foods the vegan filter let through ----------------------
    ("vegan", "Oopah (tunicate), whole animal (Alaska Native)", False),
    ("vegan", "Cockles, raw (Alaska Native)", False),
    ("vegan", "Chiton, leathery, gumboots (Alaska Native)", False),
    ("vegan", "Sea cucumber, yane (Alaska Native)", False),
    ("vegan", "Poultry, mechanically deboned, from mature hens, raw", False),
    ("vegan", "Kefir, lowfat, plain, LIFEWAY", False),
    ("vegan", "Reddi Wip Fat Free Whipped Topping", False),
    ("vegan", "Owl, horned, flesh, raw (Alaska Native)", False),
    ("vegan", "Egg, whole, raw, fresh", False),
    ("vegan", "Milk, producer, fluid, 3.7% milkfat", False),
    ("vegan", "Honey", False),
    ("vegan", "Fish, salmon, sockeye, raw", False),
    ("vegan", "Spinach souffle", False),
    ("vegan", "Pasta, fresh-refrigerated, plain, as purchased", False),   # egg pasta
    ("vegetarian", "Pasta, fresh-refrigerated, plain, as purchased", True),
    ("vegan", "Pasta, dry, unenriched", True),
    # --- vegetarian ------------------------------------------------------------
    ("vegetarian", "Egg, duck, whole, fresh, raw", True),      # duck egg is not duck
    ("vegetarian", "Egg, quail, whole, fresh, raw", True),
    ("vegetarian", "Milk, indian buffalo, fluid", True),
    ("vegetarian", "Milk, sheep, fluid", True),
    ("vegetarian", "Cheese, goat, hard type", True),
    ("vegetarian", "Kefir, lowfat, plain, LIFEWAY", True),
    ("vegetarian", "Oranges, blood, raw", True),
    ("vegetarian", "Fish, pike, northern, liver (Alaska Native)", False),
    ("vegetarian", "Mollusks, whelk, unspecified, raw", False),
    ("vegetarian", "Caribou, liver, raw (Alaska Native)", False),
    ("vegetarian", "Whale, beluga, meat, raw (Alaska Native)", False),
    ("vegetarian", "Gelatin, dry powder, unsweetened", False),
    ("vegetarian", "Beef, New Zealand, imported, oyster blade, separable lean only, raw", False),
    # --- pescatarian -----------------------------------------------------------
    ("pescatarian", "Fish, pike, northern, liver (Alaska Native)", True),   # fish liver is fish
    ("pescatarian", "Fish, lingcod, meat, raw (Alaska Native)", True),
    ("pescatarian", "Fish, mackerel, king, raw", True),                     # "king" no longer "elk"-matched
    ("pescatarian", "Fish, bass, striped, raw", True),                      # "striped" no longer "tripe"
    ("pescatarian", "Mollusks, oyster, Pacific, raw", True),
    ("pescatarian", "Egg, goose, whole, fresh, raw", True),
    ("pescatarian", "Chicken, liver, all classes, raw", False),
    ("pescatarian", "Moose, liver, braised (Alaska Native)", False),
    ("pescatarian", "Seal, ringed, meat (Alaska Native)", False),
    ("pescatarian", "Lamb, New Zealand, imported, liver, raw", False),
    # --- kosher-style ----------------------------------------------------------
    ("kosher style", "Fish, eel, mixed species, raw", False),
    ("kosher style", "Fish, catfish, farm raised, raw", False),
    ("kosher style", "Crustaceans, crayfish, mixed species, wild, raw", False),
    ("kosher style", "Fish, shark, mixed species, raw", False),
    ("kosher style", "Fish, sturgeon, mixed species, raw", False),
    ("kosher style", "Fish, swordfish, raw", False),
    ("kosher style", "Mollusks, mussel, blue, raw", False),
    ("kosher style", "Crustaceans, shrimp, raw", False),
    ("kosher style", "Pork, fresh, loin, tenderloin, separable lean only, raw", False),
    ("kosher style", "Game meat, rabbit, wild, raw", False),
    ("kosher style", "Fish, salmon, Atlantic, farmed, raw", True),
    ("kosher style", "Fish, cod, Atlantic, raw", True),
    ("kosher style", "Fish, herring, Atlantic, raw", True),
    ("kosher style", "Mushrooms, oyster, raw", True),
    ("kosher style", "Squash, summer, scallop, raw", True),
    ("kosher style", "Beef, loin, tenderloin steak, boneless, separable lean only, trimmed to 0\" fat, choice, raw", True),
    # --- halal-friendly --------------------------------------------------------
    ("halal friendly", "Pork, fresh, loin, whole, separable lean only, raw", False),
    ("halal friendly", "Pork loin, fresh, backribs, bone-in, raw, lean only", False),
    ("halal friendly", "Owl, horned, flesh, raw (Alaska Native)", False),
    ("halal friendly", "Frog legs, raw", False),
    ("halal friendly", "Alcoholic beverage, wine, table, red", False),
    ("halal friendly", "Collards, raw", True),                 # "lard" substring
    ("halal friendly", "Chicken, breast, boneless, skinless, raw", True),
    ("halal friendly", "Beef, top sirloin steak, raw", True),
    ("halal friendly", "Mollusks, mussel, blue, raw", True),
    ("halal friendly", "Vinegar, red wine", True),
    # --- gluten-free -----------------------------------------------------------
    ("gluten free", "Wheat germ, crude", False),
    ("gluten free", "Barley, pearled, raw", False),
    ("gluten free", "Rye grain", False),
    ("gluten free", "Spelt, uncooked", False),
    ("gluten free", "Bulgur, dry", False),
    ("gluten free", "Einkorn, grain, dry, raw", False),
    ("gluten free", "Farro, pearled, dry, raw", False),
    ("gluten free", "Khorasan, grain, dry, raw", False),
    ("gluten free", "Couscous, dry", False),
    ("gluten free", "Vital wheat gluten", False),
    ("gluten free", "Spaghetti, spinach, dry", False),
    ("gluten free", "Pasta, whole-wheat, dry (Includes foods for USDA's Food Distribution Program)", False),
    ("gluten free", "Oats, whole grain, rolled, old fashioned", False),   # documented choice
    ("gluten free", "Vegetarian meatloaf or patties", False),
    ("gluten free", "Buckwheat", True),
    ("gluten free", "Quinoa, uncooked", True),
    ("gluten free", "Rice, brown, long grain, unenriched, raw", True),
    ("gluten free", "Millet, raw", True),
    ("gluten free", "Breadfruit, raw", True),
    ("gluten free", "Squash, winter, spaghetti, raw", True),
    ("gluten free", "Pasta, gluten-free, corn, dry", True),
    ("gluten free", "Broilers or fryers", True),               # "rye" substring
    ("gluten free", "Chicken, broilers or fryers, breast, skinless, boneless, meat only, raw", True),
    ("gluten free", "Rice flour, brown", True),
    # --- lactose-free ----------------------------------------------------------
    ("lactose free", "Milk, buttermilk, fluid, whole", False),
    ("lactose free", "Kefir, lowfat, plain, LIFEWAY", False),
    ("lactose free", "Whey, sweet, fluid", False),
    ("lactose free", "Eggnog", False),
    ("lactose free", "Yogurt, plain, whole milk", False),
    ("lactose free", "Cheese, cottage, creamed, large or small curd", False),
    ("lactose free", "Cheese, mozzarella, whole milk", False),
    ("lactose free", "Cheese, ricotta, whole milk", False),
    ("lactose free", "Cream, fluid, heavy whipping", False),
    ("lactose free", "Butter, salted", False),
    ("lactose free", "Reddi Wip Fat Free Whipped Topping", False),
    ("lactose free", "Cheese, parmesan, hard", True),          # documented choice: aged hard cheese
    ("lactose free", "Cheese, swiss", True),
    ("lactose free", "Cheese, gruyere", True),
    ("lactose free", "Milk, lactose-free, whole", True),
    ("lactose free", "Soy milk, sweetened, plain, refrigerated", True),
    ("lactose free", "Fish, milkfish, raw", True),
    ("lactose free", "Fish, butterfish, raw", True),
    ("lactose free", "Butterbur, (fuki), raw", True),
    ("lactose free", "Egg, whole, raw, fresh", True),
    # --- nut-free --------------------------------------------------------------
    ("nut free", "Nuts, almonds", False),
    ("nut free", "Nuts, brazilnuts, raw", False),
    ("nut free", "Nuts, hazelnuts or filberts", False),
    ("nut free", "Nuts, walnuts, english", False),
    ("nut free", "Nuts, cashew nuts, raw", False),
    ("nut free", "Nuts, macadamia nuts, raw", False),
    ("nut free", "Nuts, pistachio nuts, raw", False),
    ("nut free", "Nuts, pecans", False),
    ("nut free", "Nuts, pine nuts, raw", False),
    ("nut free", "Peanuts, all types, raw", False),
    ("nut free", "Peanut butter, smooth style, without salt", False),
    ("nut free", "Hazelnuts, beaked (Northern Plains Indians)", False),
    ("nut free", "Spices, nutmeg, ground", True),
    ("nut free", "Nuts, coconut meat, raw", True),
    ("nut free", "Nuts, coconut water (liquid from coconuts)", True),
    ("nut free", "Squash, winter, butternut, raw", True),
    ("nut free", "Waterchestnuts, chinese, (matai), raw", True),
    ("nut free", "Water chestnuts, raw", True),
    ("nut free", "MORI-NU, Tofu, silken, firm", True),
    ("nut free", "Seeds, sunflower seed, kernel, raw", True),
    # --- low-sodium aware (USDA sodium > 600 mg/100 g) --------------------------
    ("low sodium aware", "Seaweed, Canadian Cultivated EMI-TSUNOMATA, dry", False),
    ("low sodium aware", "Catsup", False),
    ("low sodium aware", "Catsup, low sodium", True),
    ("low sodium aware", "Spinach, raw", True),
    ("low sodium aware", "Bacon, cured, raw", False),
]


@pytest.mark.parametrize("pid,food,expected", CASES)
def test_dietary_profile_decision(pid, food, expected):
    reason = bb.dietary_block_reason(food, _profile(pid))
    assert (reason == "") is expected, f"{pid}: {food!r} -> {reason or 'allowed'}"


@pytest.mark.parametrize(
    "pid,food,category,expected",
    [
        # Category rules catch animals whose name says nothing about them.
        ("vegan", "Oheloberries, raw", "Fruits and Fruit Juices", True),
        ("vegan", "Mystery cut, raw", "Beef Products", False),
        ("vegetarian", "Burbot, raw", "Finfish and Shellfish Products", False),
        ("pescatarian", "Burbot, raw", "Finfish and Shellfish Products", True),
        ("pescatarian", "Mystery cut, raw", "Poultry Products", False),
        ("vegan", "Mystery spread", "Dairy and Egg Products", False),
        ("vegetarian", "Mystery spread", "Dairy and Egg Products", True),
        ("halal friendly", "Mystery chop", "Pork Products", False),
        ("kosher style", "Mystery chop", "Pork Products", False),
    ],
)
def test_category_rules(pid, food, category, expected):
    assert _allowed(pid, food, category) is expected


def test_usda_category_is_looked_up_when_row_has_none():
    # AI-fallback rows carry no USDA category; known USDA names still get one.
    assert not _allowed("vegan", "Poultry, mechanically deboned, from mature hens, raw", "Whole food")
    assert not _allowed("vegan", "Kefir, lowfat, plain, LIFEWAY", "")


def test_keyword_match_is_whole_word_with_plurals():
    match = bb._keyword_matches_food_blob
    assert match("almond", "nuts almonds")
    assert match("anchovy", "fish anchovies canned")
    assert match("pine nut", "nuts pine nuts raw")
    assert not match("salmon", "nuts almonds")
    assert not match("goat", "mango ataulfo peeled raw")
    assert not match("egg", "eggplant raw")
    assert not match("honey", "melons honeydew raw")
    assert not match("butter", "squash winter butternut raw")
    assert not match("nut", "spices nutmeg ground")
    assert not match("rye", "chicken broilers or fryers")
    assert not match("elk", "fish mackerel king raw")
    assert not match("tripe", "fish bass striped raw")
    # The legacy third argument (compact blob) is ignored.
    assert not match("salmon", "nuts almonds", "nutsalmonds")


def test_apply_food_filters_keeps_order_and_rows():
    foods = [
        {"food_description": "Nuts, almonds", "food_category": "Nut and Seed Products"},
        {"food_description": "Fish, salmon, sockeye, raw", "food_category": "Finfish and Shellfish Products"},
        {"food_description": "Mango, Ataulfo, peeled, raw", "food_category": "Fruits and Fruit Juices"},
        {"food_description": "", "food_category": ""},
    ]
    kept = bb.apply_food_filters(foods, _profile("vegan"))
    assert [f["food_description"] for f in kept] == ["Nuts, almonds", "Mango, Ataulfo, peeled, raw"]
    assert kept[0] is foods[0]
    assert bb.apply_food_filters(foods, _profile("none")) == foods
    assert bb.apply_food_filters(foods, None) == foods


def test_meal_filter_uses_whole_words():
    meals = [
        {"name": "Ratatouille", "ingredients": [{"name": "eggplant", "grams": 200}, {"name": "butternut squash", "grams": 150}]},
        {"name": "Omelette", "ingredients": [{"name": "eggs", "grams": 120}]},
        {"name": "Almond salad", "ingredients": [{"name": "almonds", "grams": 30}]},
    ]
    names = [m["name"] for m in bb.apply_meal_filters(meals, _profile("vegan"), must_exclude_ingredient="")]
    assert names == ["Ratatouille", "Almond salad"]


def test_profile_descriptions_document_policy_choices():
    assert "oats" in _profile("gluten free")["description"].lower()
    assert "hard cheese" in _profile("lactose free")["description"].lower()
    assert "coconut" in _profile("nut free")["description"].lower()


def test_persisted_flags_table_is_not_used():
    # The food_dietary_flags table repeats the old substring errors (it blocks
    # kidney beans for vegans), so the live filter must not consult it.
    assert bb._persisted_usda_food_allowed("Beans, kidney, red, mature seeds, raw", _profile("vegan")) is None
    assert _allowed("vegan", "Beans, kidney, red, mature seeds, raw")

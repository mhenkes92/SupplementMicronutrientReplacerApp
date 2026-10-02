"""Common-food guard (bb.classify_food_commonness): what a German shopper can buy.

Rows cover every false block / false pass from the 2026-10-01 audit plus the
auditor's hand-labelled "unavailable" foods from the unfiltered top-10 lists.
"""
from __future__ import annotations

import pytest

import blockbrain.app as bb

ALASKA = "American Indian/Alaska Native Foods"


def _tier(food: str, category: str = "") -> int:
    return bb.classify_food_commonness(food, category)["tier"]


KEEP = [
    # audit: substring false blocks
    "Barley, hulled",                      # "bar"
    "Barley, pearled, raw",
    "Rhubarb, raw",                        # "bar"
    "Fish, sardine, Atlantic, canned in oil, drained solids with bone",   # "canned"
    "Fish, salmon, pink, canned, drained solids",
    "Fish, mackerel, Atlantic, smoked",
    "Mollusks, whelk, unspecified, raw",   # "elk"
    "Pigeon peas (red gram), mature seeds, raw",                          # "pigeon"
    "Breadfruit, raw",                     # "bread"
    "Rice, brown, long grain, unenriched, raw",                           # "enriched"
    "Pasta, dry, unenriched",
    "Beans, black turtle, mature seeds, raw",                             # "turtle"
    "Egg, quail, whole, fresh, raw",       # quail eggs are sold in German supermarkets
    "Nuts, coconut water (liquid from coconuts)",
    # everyday foods
    "Nuts, almonds",
    "Nuts, brazilnuts, raw",
    "Kiwifruit, ZESPRI SunGold, raw",
    "Kiwifruit, green, raw",
    "Oats (Includes foods for USDA's Food Distribution Program)",
    "Pears, raw, bosc (Includes foods for USDA's Food Distribution Program)",
    "Lamb, New Zealand, imported, liver, raw",
    "Chicken, liver, all classes, raw",
    "Fish, herring, Atlantic, raw",
    "Fish, salmon, sockeye, raw",
    "Mollusks, oyster, Pacific, raw",
    "Mollusks, mussel, blue, raw",
    "Seaweed, laver, raw",
    "Acerola, (west indian cherry), raw",
    "Parsley, fresh",
    "Wheat germ, crude",
    "Seeds, sunflower seed kernels, toasted, without salt",
    "Peppers, sweet, red, raw",
    "Broccoli, raw",
    "Lentils, dry",
    "Chickpeas (garbanzo beans, bengal gram), mature seeds, raw",
    "Egg, whole, raw, fresh",
    "Milk, producer, fluid, 3.7% milkfat",
    "Spinach, raw",
    "Mango, Ataulfo, peeled, raw",
    "Squash, winter, butternut, raw",
    "Mushrooms, oyster, raw",
    "Goose, domesticated, meat only, raw",
    "Duck, domesticated, meat only, raw",
    "Balsam-pear (bitter gourd), pods, raw",
]

BLOCK = [
    # audit: let through by the old guard
    ("Owl, horned, flesh, raw (Alaska Native)", ALASKA),
    ("Catsup", "Vegetables and Vegetable Products"),
    ("Vegetarian meatloaf or patties", "Legumes and Legume Products"),
    ("Kefir, lowfat, plain, LIFEWAY", "Dairy and Egg Products"),
    ("Kefir, lowfat, strawberry, LIFEWAY", "Dairy and Egg Products"),
    ("Stinging Nettles, blanched (Northern Plains Indians)", ALASKA),
    ("Rose Hips, wild (Northern Plains Indians)", ALASKA),
    ("Willow, young leaves, chopped (Alaska Native)", ALASKA),
    ("Oopah (tunicate), whole animal (Alaska Native)", ALASKA),
    ("Cockles, raw (Alaska Native)", ALASKA),
    ("Chiton, leathery, gumboots (Alaska Native)", ALASKA),
    ("Sea cucumber, yane (Alaska Native)", ALASKA),
    ("Fish, whitefish, eggs (Alaska Native)", ALASKA),
    ("Fish, salmon, sockeye (red), raw (Alaska Native)", ALASKA),
    ("Hazelnuts, beaked (Northern Plains Indians)", ALASKA),
    ("Agave, raw (Southwest)", ALASKA),
    ("Lambsquarters, raw (Northern Plains Indians)", ALASKA),
    ("Seal, ringed, liver (Alaska Native)", ALASKA),
    ("Whale, beluga, meat, raw (Alaska Native)", ALASKA),
    ("Walrus, liver, raw (Alaska Native)", ALASKA),
    # wild game, game organs and unusual animals
    ("Game meat, beaver, raw", "Lamb, Veal, and Game Products"),
    ("Game meat, muskrat, raw", "Lamb, Veal, and Game Products"),
    ("Game meat, squirrel, raw", "Lamb, Veal, and Game Products"),
    ("Game meat, bear, raw", "Lamb, Veal, and Game Products"),
    ("Canada Goose, breast meat only, skinless, raw", "Poultry Products"),
    ("Ruffed Grouse, breast meat, skinless, raw", "Poultry Products"),
    ("Duck, wild, breast, meat only, raw", "Poultry Products"),
    ("Moose, liver, braised (Alaska Native)", ALASKA),
    ("Chicken, capons, giblets, raw", "Poultry Products"),
    ("Turkey, whole, giblets, raw", "Poultry Products"),
    ("Lamb, New Zealand, imported, testes, raw", "Lamb, Veal, and Game Products"),
    ("Poultry, mechanically deboned, from mature hens, raw", "Poultry Products"),
    ("Mollusks, conch, baked or broiled", "Finfish and Shellfish Products"),
    ("Frog legs, raw", "Finfish and Shellfish Products"),
    ("Turtle, green, raw", "Finfish and Shellfish Products"),
    # not sold in German shops / foraged / toxic raw / US-only
    ("Broccoli, leaves, raw", "Vegetables and Vegetable Products"),
    ("Amaranth leaves, raw", "Vegetables and Vegetable Products"),
    ("Drumstick leaves, raw", "Vegetables and Vegetable Products"),
    ("Fireweed, leaves, raw", "Vegetables and Vegetable Products"),
    ("Lambsquarters, raw", "Vegetables and Vegetable Products"),
    ("Pokeberry shoots, (poke), raw", "Vegetables and Vegetable Products"),
    ("Hyacinth beans, mature seeds, raw", "Legumes and Legume Products"),
    ("Winged beans, mature seeds, raw", "Legumes and Legume Products"),
    ("Mothbeans, mature seeds, raw", "Legumes and Legume Products"),
    ("Cowpeas, catjang, mature seeds, raw", "Legumes and Legume Products"),
    ("Yardlong beans, mature seeds, raw", "Legumes and Legume Products"),
    ("Grapes, muscadine, raw", "Fruits and Fruit Juices"),
    ("Wheat, hard red spring", "Cereal Grains and Pasta"),
    ("Fish, mackerel, king, raw", "Finfish and Shellfish Products"),   # high mercury
    ("Fish, tilefish, raw", "Finfish and Shellfish Products"),
    ("Fish, scup, raw", "Finfish and Shellfish Products"),
    ("Nuts, acorns, raw", "Nut and Seed Products"),
    ("Milk, human, mature, fluid", "Dairy and Egg Products"),
    # branded US products and processed / composite items
    ("Seaweed, Canadian Cultivated EMI-TSUNOMATA, dry", "Vegetables and Vegetable Products"),
    ("MORI-NU, Tofu, silken, firm", "Legumes and Legume Products"),
    ("SILK Plain, soymilk", "Legumes and Legume Products"),
    ("HORMEL ALWAYS TENDER, Pork Tenderloin, Teriyaki-Flavored", "Pork Products"),
    ("Rice, brown, parboiled, dry, UNCLE BEN'S", "Cereal Grains and Pasta"),
    ("Vitasoy USA Azumaya, Firm Tofu", "Legumes and Legume Products"),
    ("Reddi Wip Fat Free Whipped Topping", "Dairy and Egg Products"),
    ("Spinach souffle", "Vegetables and Vegetable Products"),
    ("Fish, tuna salad", "Finfish and Shellfish Products"),
    ("Eggnog", "Dairy and Egg Products"),
    ("Beef, retail cuts, separable fat, raw", "Beef Products"),
    ("Chicken, broilers or fryers, skin only, raw", "Poultry Products"),
]


@pytest.mark.parametrize("food", KEEP)
def test_common_food_is_kept(food):
    verdict = bb.classify_food_commonness(food)
    assert verdict["tier"] == 1, verdict


@pytest.mark.parametrize("food,category", BLOCK)
def test_uncommon_food_is_blocked(food, category):
    assert _tier(food, category) == -1
    # Most are caught from the name alone (AI-fallback rows carry no category).
    if category != ALASKA:
        assert _tier(food) == -1


def test_alaska_native_category_is_blocked_even_without_tag():
    assert _tier("Blueberries, wild, raw", ALASKA) == -1
    assert _tier("Blueberries, wild, raw", "Fruits and Fruit Juices") == 1


def test_filter_and_rank_uses_category_and_ranks_by_amount():
    foods = [
        {"food_description": "Blueberries, wild, raw (Alaska Native)", "food_category": ALASKA, "amount_per_100g": 99.0},
        {"food_description": "Kale, raw", "food_category": "Vegetables and Vegetable Products", "amount_per_100g": 5.0},
        {"food_description": "Barley, pearled, raw", "food_category": "Cereal Grains and Pasta", "amount_per_100g": 9.0},
        {"food_description": "Owl, horned, flesh, raw (Alaska Native)", "food_category": ALASKA, "amount_per_100g": 50.0},
    ]
    kept = bb.filter_and_rank_common_foods(foods, 10)
    assert [f["food_description"] for f in kept] == ["Barley, pearled, raw", "Kale, raw"]


def test_usda_program_note_is_not_mistaken_for_a_brand():
    verdict = bb.classify_food_commonness("Oats (Includes foods for USDA's Food Distribution Program)")
    assert verdict == {"tier": 1, "reason": "allowed"}

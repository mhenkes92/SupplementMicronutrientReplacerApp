"""Shopper-friendly display names for USDA foods (bb.food_display_name) and
their use on the card, dropdown, final screen and meal-plan prompt."""
from __future__ import annotations

import pytest

import blockbrain.app as bb

NAMES = [
    # examples from the brief
    ("Lamb, New Zealand, imported, liver, raw", "Lamb liver"),
    ("Nuts, almonds", "Almonds"),
    ("Acerola, (west indian cherry), raw", "Acerola (West Indian cherry)"),
    ("Fish, salmon, sockeye, cooked, dry heat", "Salmon, sockeye (cooked)"),
    # meat: species + organ / cut, noise dropped
    ("Duck, domesticated, liver, raw", "Duck liver"),
    ("Chicken, liver, all classes, raw", "Chicken liver"),
    ("Turkey, all classes, liver, raw", "Turkey liver"),
    ("Lamb, New Zealand, imported, kidney, raw", "Lamb kidney"),
    ("Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw", "Chicken breast"),
    ("Chicken, broilers or fryers, dark meat, thigh, meat only, raw", "Chicken thigh"),
    ("Chicken, broiler, rotisserie, BBQ, breast, meat only", "Chicken breast (rotisserie)"),
    ("Pork, fresh, loin, tenderloin, separable lean only, raw", "Pork tenderloin"),
    ("Beef, loin, tenderloin steak, boneless, separable lean only, trimmed to 0\" fat, choice, raw", "Beef tenderloin steak"),
    ("Beef, chuck eye steak, boneless, separable lean only, trimmed to 0\" fat, select, raw", "Beef chuck eye steak"),
    ("Beef, flank, steak, separable lean only, trimmed to 0\" fat, all grades, raw", "Beef flank steak"),
    ("Beef, round, eye of round, roast, separable lean only, trimmed to 1/8\" fat, choice, raw", "Beef eye of round roast"),
    ("Beef, liver, cooked, braised", "Beef liver (cooked, braised)"),
    # fish and seafood
    ("Fish, salmon, Atlantic, farm raised, raw", "Salmon, Atlantic (farmed)"),
    ("Fish, mackerel, Atlantic, raw", "Mackerel, Atlantic"),
    ("Fish, tuna, fresh, yellowfin, raw", "Tuna, yellowfin"),
    ("Fish, herring, Atlantic, raw", "Herring, Atlantic"),
    ("Fish, sardine, Atlantic, canned in oil, drained solids with bone", "Sardine, Atlantic (canned)"),
    ("Fish, roe, mixed species, raw", "Fish roe"),
    ("Mollusks, oyster, Pacific, raw", "Oysters, Pacific"),
    ("Mollusks, mussel, blue, raw", "Mussels, blue"),
    ("Crustaceans, shrimp, farm raised, raw", "Shrimp (farmed)"),
    # nuts and seeds
    ("Nuts, brazilnuts, raw", "Brazil nuts"),
    ("Nuts, hazelnuts or filberts", "Hazelnuts"),
    ("Nuts, almonds, blanched", "Almonds (blanched)"),
    ("Nuts, pistachio nuts, raw", "Pistachios"),
    ("Nuts, walnuts, English, halves, raw", "Walnuts"),
    ("Nuts, coconut meat, raw", "Coconut (fresh flesh)"),
    ("Seeds, sunflower seed kernels, toasted, without salt", "Sunflower seeds (toasted)"),
    ("Seeds, hemp seed, hulled", "Hemp seeds (hulled)"),
    ("Seeds, pumpkin seeds (pepitas), raw", "Pumpkin seeds (pepitas)"),
    # produce: "modifier noun"
    ("Peppers, sweet, red, raw", "Red bell pepper"),
    ("Peppers, bell, yellow, raw", "Yellow bell pepper"),
    ("Peppers, hot chili, green, raw", "Green chili pepper"),
    ("Squash, winter, butternut, raw", "Butternut squash"),
    ("Mushrooms, oyster, raw", "Oyster mushrooms"),
    ("Mushroom, white, exposed to ultraviolet light, raw", "White mushrooms (UV-exposed)"),
    ("Cabbage, red, raw", "Red cabbage"),
    ("Chard, swiss, raw", "Swiss chard"),
    ("Cress, garden, raw", "Garden cress"),
    ("Currants, european black, raw", "Blackcurrants"),
    ("Kiwifruit, ZESPRI SunGold, raw", "Gold kiwi (Zespri SunGold)"),
    ("Parsley, fresh", "Parsley"),
    ("Spinach, raw", "Spinach"),
    ("Broccoli, raw", "Broccoli"),
    ("Broccoli, flower clusters, raw", "Broccoli florets"),
    ("Mango, Ataulfo, peeled, raw", "Mango, Ataulfo (peeled)"),
    ("Seaweed, laver, raw", "Nori (laver seaweed)"),
    ("Wheat germ, crude", "Wheat germ"),
    # dry goods say "(dry)": the grams on the card are dry weight
    ("Beans, kidney, red, mature seeds, raw", "Red kidney beans (dry)"),
    ("Beans, Dry, Dark Red Kidney (0% moisture)", "Dark red kidney beans (dry)"),
    ("Chickpeas (garbanzo beans, bengal gram), mature seeds, raw", "Chickpeas (dry)"),
    ("Lentils, raw", "Lentils (dry)"),
    ("Rice, brown, long grain, unenriched, raw", "Brown rice (dry)"),
    ("Quinoa, uncooked", "Quinoa (dry)"),
    ("Pasta, whole-wheat, dry (Includes foods for USDA's Food Distribution Program)", "Whole-wheat pasta (dry)"),
    ("Oats (Includes foods for USDA's Food Distribution Program)", "Oats"),
    ("Mung beans, mature seeds, sprouted, raw", "Mung beans (sprouted)"),
    # dairy and eggs
    ("Eggs, Grade A, Large, egg yolk", "Egg yolk"),
    ("Egg, whole, raw, fresh", "Eggs"),
    ("Egg, duck, whole, fresh, raw", "Duck egg"),
    ("Milk, producer, fluid, 3.7% milkfat", "Whole milk (3.7% fat)"),
    ("Sweet potato, raw, unprepared (Includes foods for USDA's Food Distribution Program)", "Sweet potato"),
    ("New Zealand spinach, raw", "New Zealand spinach"),
    ("Beans, great northern, mature seeds, raw (Includes foods for USDA's Food Distribution Program)", "Great Northern beans (dry)"),
    # "(dry)" only for dry seeds / grains, never for fresh, frozen, canned or cooked foods
    ("Green beans, raw", "Green beans"),
    ("Broad beans, fresh", "Broad beans (fresh)"),
    ("String beans, raw", "String beans"),
    ("French beans, raw", "French beans"),
    ("Runner beans, raw", "Runner beans"),
    ("Beans, snap, green, raw", "Green beans"),
    ("Beans, snap, green, frozen, all styles, unprepared", "Green snap beans (frozen)"),
    ("Beans, liquid from stewed kidney beans", "Bean cooking liquid (kidney beans)"),
    ("Beans, pinto, immature seeds, frozen, unprepared", "Pinto beans (fresh, frozen)"),
    ("Beans, french, mature seeds, raw", "French beans (dry)"),
    ("Beans, cannellini, dry", "Cannellini beans (dry)"),
    ("Mothbeans, mature seeds, raw", "Mothbeans (dry)"),
    ("Lentils, sprouted, raw", "Lentils (sprouted)"),
    ("Soybeans, mature seeds, raw", "Soybeans (dry)"),
    ("Soybeans, mature seeds, roasted, salted", "Soybeans (roasted)"),
    ("Buckwheat groats, roasted, dry", "Buckwheat groats (dry, roasted)"),
    ("Noodles, egg, dry, enriched", "Noodles, egg (dry)"),
    ("Rice, white, long-grain, regular, unenriched, cooked without salt", "Long-grain white rice (cooked)"),
    ("Rice, white, long-grain, precooked or instant, enriched, prepared", "Long-grain white rice"),
    ("Puddings, rice, dry mix", "Puddings, rice"),
    ("Flour, rice, brown", "Flour, rice"),
    ("Figs, dried, uncooked", "Figs (dried)"),
    ("Milk, dry, whole, with added vitamin D", "Milk"),
    ("Gravy, brown, dry", "Brown gravy"),
    ("Babyfood, green beans, dices, toddler", "Babyfood, green beans"),
    # salt / liquid notes are not part of the name
    ("Broccoli, cooked, boiled, drained, with salt", "Broccoli (cooked, boiled)"),
    ("Asparagus, canned, no salt added, solids and liquids", "Asparagus (canned)"),
    ("Almonds, oil roasted, with salt added", "Almonds (roasted)"),
    # part-only and fat-only rows keep the part, so they never merge with the whole food
    ("Watermelon, seedless, rind only, raw", "Watermelon rind"),
    ("Pork, fresh, separable fat, raw", "Pork fat"),
    ("Lamb, Australian, imported, fresh, external fat, raw", "Lamb fat"),
    ("Beef, retail cuts, separable fat, cooked", "Beef fat (cooked)"),
    # readable composites and peppers
    ("Pork, fresh, composite of trimmed retail cuts (leg, loin, shoulder), separable lean only, raw", "Pork, mixed lean cuts"),
    ("Pork, fresh, composite of trimmed retail cuts (loin and shoulder blade), separable lean and fat, cooked", "Pork, mixed cuts (cooked)"),
    ("Peppers, banana or Hungarian wax, seeded, raw", "Banana pepper"),
    ("Beef, short loin (NY strip steak), raw", "Beef short loin (NY strip steak)"),
]


@pytest.mark.parametrize("usda,expected", NAMES)
def test_food_display_name(usda, expected):
    assert bb.food_display_name(usda) == expected


def test_display_name_is_short_deterministic_and_never_empty():
    long_name = "Beef, chuck, under blade center steak, boneless, Denver Cut, separable lean only, trimmed to 0\" fat, all grades, raw"
    first = bb.food_display_name(long_name)
    assert first == bb.food_display_name(long_name)
    assert 0 < len(first) <= 42
    assert bb.food_display_name("") == ""
    assert bb.food_display_name("Natto") == "Natto (fermented soybeans)"
    assert bb.food_display_name("Some new food") == "Some new food"


def test_dropdown_has_no_duplicate_display_names():
    foods = bb._build_local_food_rows_for_component("vitamin b12", limit=250)
    names = [bb.food_display_name(f["food_description"]).lower() for f in foods]
    assert foods and len(names) == len(set(names))


def test_swipe_labels_use_display_names_and_keep_usda_name(sw):
    food = {"food_description": "Lamb, New Zealand, imported, liver, raw", "amount_per_100g": 59.0, "unit": "UG"}
    assert sw._food_name(food) == "Lamb liver"
    assert sw._food_label(food).startswith("Lamb liver (")
    assert food["food_description"] == "Lamb, New Zealand, imported, liver, raw"
    assert sw._food_name({}) == ""


def test_meal_plan_and_share_text_use_display_names(sw):
    decision = {
        "decision": "replace",
        "component": "Vitamin B12",
        "dose_value": 2.4,
        "dose_unit": "mcg",
        "selected_food": {"food_description": "Lamb, New Zealand, imported, liver, raw", "amount_per_100g": 59.0, "unit": "UG"},
    }
    _system, user_prompt, _key = sw._meal_plan_prompts([decision], "No restriction", 1)
    assert "- Lamb liver (" in user_prompt
    assert "New Zealand, imported" not in user_prompt
    share = sw._build_share_text([], [decision], "")
    assert "Vitamin B12: Lamb liver" in share
    assert sw._previous_choice_label(decision) == "replaced with Lamb liver"


def test_chromium_fallback_card_shows_fresh_green_beans(sw):
    """Chromium has no USDA rows, so its card uses the curated fallback list;
    'Green beans, raw' is a fresh vegetable and must not say '(dry)'."""
    rows = bb._curated_food_fallback("chromium", 10)
    labels = [sw._food_label(r) for r in rows]
    assert any(label.startswith("Green beans (") for label in labels), labels
    assert not any("(dry)" in label for label in labels), labels

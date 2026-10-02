"""Dietary keyword screening for rows WITHOUT a USDA category (AI-fallback food
rows, recipe ingredients): nut-free "nut"/"nuts", common EU fish / pork /
dairy / wheat names, accent folding and the look-alikes that must stay allowed.
Review items NUT-1 and DIET-2 (2026-10-01)."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


def _profile(pid: str) -> dict:
    wanted = pid.replace("_", " ")
    for profile in bb.load_dietary_profiles():
        if profile["id"].replace("_", " ") == wanted:
            return profile
    raise KeyError(pid)


def _allowed(pid: str, food: str) -> bool:
    return bb.food_allowed_for_dietary_profile(food, _profile(pid), "")


EU_FISH = [
    "Monkfish", "Turbot", "Pangasius fillet", "Zander", "Saithe", "Redfish", "Sea bream", "Dorade",
    "Barramundi", "Snapper", "Grouper", "Sprats", "Kippers", "Gravlax", "Stockfish", "Scampi",
    "Matjes herring", "Rollmops", "Fish fingers",
]
EU_PORK = ["Gammon", "Speck", "Mortadella", "Leberkäse", "Leberkaese", "Schinken", "Kassler", "Guanciale"]
DAIRY_NAMES = ["Skyr", "Halloumi", "Labneh", "Creme fraiche", "Crème fraîche", "Gelato", "Hollandaise sauce", "Mascarpone", "Feta"]
WHEAT_NAMES = ["Pancake", "Pumpernickel", "Soy sauce", "Bagel", "Croissant", "Pretzel", "Gnocchi", "Pizza", "Breaded fish"]

CASES = (
    # --- NUT-1: plain "nut"/"nuts" and nut butters -------------------------------
    [("nut_free", food, False) for food in ("Nut butter", "Nuts", "Mixed nuts", "Mixed nut butter", "Nuts, mixed, roasted", "Pine nuts")]
    + [
        ("nut_free", "Nutmeg", True),
        ("nut_free", "Spices, nutmeg, ground", True),
        ("nut_free", "Butternut squash", True),
        ("nut_free", "Squash, winter, butternut, raw", True),
        ("nut_free", "Coconut", True),
        ("nut_free", "Nuts, coconut meat, raw", True),
        ("nut_free", "Nuts, coconut milk, raw (liquid expressed from grated meat and water)", True),
        ("nut_free", "Water chestnut", True),
        ("nut_free", "Waterchestnuts, chinese, (matai), raw", True),
        ("nut_free", "Nut-free granola", True),
        ("nut_free", "Mouse nuts, roots (Alaska Native)", True),  # a root vegetable
        ("nut_free", "Doughnuts, cake-type, plain", True),
    ]
    # --- DIET-2: fish names without a category -----------------------------------
    + [(pid, food, False) for pid in ("vegetarian", "vegan") for food in EU_FISH]
    + [("pescatarian", food, True) for food in EU_FISH]
    + [("kosher_style", food, False) for food in ("Monkfish", "Turbot", "Scampi", "Eel, smoked")]
    + [("kosher_style", food, True) for food in ("Zander", "Saithe", "Sea bream", "Sprats", "Gravlax")]
    # pork products
    + [(pid, food, False) for pid in ("vegetarian", "pescatarian", "halal_friendly", "kosher_style") for food in EU_PORK]
    # dairy names
    + [("vegan", food, False) for food in DAIRY_NAMES + ["Aioli", "Mayo", "Cheddar", "Parmesan"]]
    + [("lactose_free", food, False) for food in DAIRY_NAMES]
    + [("lactose_free", food, True) for food in ("Parmesan", "Cheese, parmesan, hard", "Emmental", "Aioli")]
    # wheat products
    + [("gluten_free", food, False) for food in WHEAT_NAMES]
    + [
        ("gluten_free", "Pancakes, gluten-free, frozen, ready-to-heat", True),
        ("gluten_free", "Snacks, Pretzels, gluten- free made with cornstarch and potato flour", True),
        ("gluten_free", "Babyfood, Baby MUM MUM Rice Biscuits", True),
        ("gluten_free", "Rice cakes", True),
        ("gluten_free", "Breadfruit, raw", True),
        ("gluten_free", "Buckwheat", True),
    ]
    # --- look-alikes that must stay allowed --------------------------------------
    + [
        ("vegan", "Beans, Dry, Flor de Mayo (0% moisture)", True),  # bean variety, not mayonnaise
        ("kosher_style", "Sauce, barbecue, SWEET BABY RAY'S, original", True),  # not the fish "ray"
        ("vegetarian", "Sauce, barbecue, SWEET BABY RAY'S, original", True),
        ("vegetarian", "Vegetarian sausage", True),
        ("vegetarian", "Veggie burger", True),
        ("vegetarian", "Sausage, meatless", True),
        ("vegetarian", "Bacon, meatless", True),
        ("vegetarian", "Frankfurter, meatless", True),
        ("vegetarian", "Meatballs, meatless", True),
        ("vegetarian", "Tofu sausages", True),
        ("vegetarian", "Meatballs", False),
        ("vegetarian", "Pork sausage", False),
        ("vegetarian", "Schnitzel", False),
        ("vegan", "Vegetarian schnitzel", True),
        ("vegan", "Seaweed, laver, raw", True),
        ("low_sodium_aware", "Bacon, meatless", False),  # meat analogues are as salty as the original
    ]
)


@pytest.mark.parametrize("pid,food,allowed", CASES)
def test_keyword_screening_without_category(pid, food, allowed):
    assert _allowed(pid, food) is allowed, bb.dietary_block_reason(food, _profile(pid), "")


@pytest.mark.parametrize(
    "text,blocked",
    [
        ("1 handful nuts", True),
        ("chopped nuts", True),
        ("nut butter", True),
        ("mixed nut butter", True),
        ("2 tbsp peanut butter", True),
        ("1 tsp nutmeg", False),
        ("butternut squash", False),
        ("coconut milk", False),
        ("water chestnuts", False),
        ("nut-free granola", False),
    ],
)
def test_nut_free_recipe_text(text, blocked):
    assert bb.dietary_text_blocked(text, _profile("nut_free")) is blocked


@pytest.mark.parametrize(
    "pid,text",
    [
        ("vegetarian", "200 g Leberkäse"),
        ("halal_friendly", "Speck cubes"),
        ("vegan", "2 tbsp crème fraîche"),
        ("vegan", "150 g Skyr"),
        ("gluten_free", "1 slice pumpernickel"),
        ("gluten_free", "1 tbsp soy sauce"),
        ("vegetarian", "120 g zander fillet"),
    ],
)
def test_recipe_text_blocks_eu_names(pid, text):
    assert bb.dietary_text_blocked(text, _profile(pid)) is True


def test_accent_folding_only_changes_keyword_text():
    assert bb._diet_text_key("Leberkäse") == "leberkase"
    assert bb._diet_text_key("Crème fraîche") == "creme fraiche"
    assert bb._diet_text_key("Weißwurst") == "weisswurst"
    # USDA facts lookups still use the plain key, so a USDA row keeps its category
    assert bb.dietary_block_reason("Fish, salmon, Atlantic, farmed, raw", _profile("vegetarian")).startswith("category")


def test_verdicts_are_cached_per_description_and_category():
    profile = _profile("vegan")
    bb._dietary_block_reason_cached.cache_clear()
    rows = [{"food_description": "Tofu, raw, firm", "food_category": ""}] * 50
    assert len(bb.apply_food_filters(rows, profile)) == 50
    info = bb._dietary_block_reason_cached.cache_info()
    assert info.hits >= 49 and info.currsize >= 1
    # the category is part of the key: the same name with a meat category is blocked
    assert bb.dietary_block_reason("Mystery item xq", profile, "Beef Products").startswith("category")
    assert bb.dietary_block_reason("Mystery item xq", profile, "") == ""

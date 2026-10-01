"""German grocery price estimate: whole-word, specific-before-generic matching
against blockbrain/data/german_food_prices.csv, and the >1 kg/day basket rule."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

PRICES_CSV = Path(__file__).resolve().parent.parent / "blockbrain" / "data" / "german_food_prices.csv"


def _per_kg(sw, food: str) -> float | None:
    match = sw._german_price_per_kg(food)
    return None if match is None else match[0]


@pytest.mark.parametrize(
    "food,low,high",
    [
        # foods the audit found unpriced or mispriced
        ("Mollusks, oyster, Pacific, raw", 30, 60),
        ("Mollusks, mussel, blue, raw", 8, 20),
        ("Seaweed, laver, raw", 8, 30),
        ("Parsley, fresh", 10, 30),
        ("Wheat germ, crude", 5, 12),
        ("Seeds, sunflower seed kernels, toasted, without salt", 3, 7),
        ("Nuts, brazilnuts, raw", 22, 30),                 # was 12 via generic "nut"
        ("Acerola, (west indian cherry), raw", 8, 30),
        ("Lamb, New Zealand, imported, liver, raw", 6, 14),
        ("Chicken, liver, all classes, raw", 3, 8),
        ("Fish, sardine, Atlantic, canned in oil, drained solids with bone", 8, 16),
        ("Kiwifruit, green, raw", 3, 6),
        ("Peppers, sweet, red, raw", 3, 7),
        ("Peppers, bell, yellow, raw", 3, 7),
        ("Broccoli, raw", 2, 4),
        ("Lentils, dry", 2, 5),
        ("Chickpeas (garbanzo beans, bengal gram), mature seeds, raw", 2, 5),
        ("Oats (Includes foods for USDA's Food Distribution Program)", 0.8, 2),
        ("Egg, whole, raw, fresh", 3.5, 6.5),
        ("Milk, producer, fluid, 3.7% milkfat", 0.9, 1.5),
        ("Yogurt, plain, whole milk", 1.5, 3.5),
        ("Cheese, cheddar", 7, 14),
        ("Tofu, raw, firm, prepared with calcium sulfate", 4, 8),
        # other common top foods
        ("Fish, salmon, sockeye, raw", 15, 28),
        ("Fish, herring, Atlantic, raw", 6, 14),
        ("Nuts, almonds", 9, 16),
        ("Spinach, raw", 2, 9),
        ("Bananas, raw", 1, 2.5),
        ("Mango, Ataulfo, peeled, raw", 2, 6),
    ],
)
def test_common_top_foods_are_priced_realistically(sw, food, low, high):
    price = _per_kg(sw, food)
    assert price is not None, f"{food} is unpriced"
    assert low <= price <= high, f"{food}: {price} EUR/kg"


@pytest.mark.parametrize(
    "food,expected_phrase",
    [
        # whole words: Goat is not oat, butternut / coconut are not "nut"
        ("Goat, raw", "goat"),
        ("Game meat, goat, raw", "goat"),
        ("Squash, winter, butternut, raw", "butternut squash"),
        ("Nuts, coconut meat, raw", "coconut"),
        ("Eggplant, raw", "eggplant"),
        ("Melons, honeydew, raw", "melon"),
        # specific before generic
        ("Fish, salmon, Atlantic, farmed, raw", "salmon"),
        ("Fish, roughy, orange, raw", "roughy"),
        ("Beans, kidney, red, mature seeds, raw", "kidney bean"),
        ("Seeds, pumpkin seeds (pepitas), raw", "pumpkin seed"),
        ("Mushrooms, oyster, raw", "oyster mushroom"),
        ("Chicken, liver, all classes, raw", "chicken liver"),
        ("Beef, loin, tenderloin steak, boneless, separable lean only, trimmed to 0\" fat, choice, raw", "beef tenderloin"),
        ("Fish, tuna, fresh, yellowfin, raw", "fresh tuna"),
        ("Orange peel, raw", "orange peel"),
        ("Pineapple, raw", "pineapple"),
        ("Eggs, Grade A, Large, egg yolk", "egg yolk"),
        ("Kiwifruit, ZESPRI SunGold, raw", "sungold"),
    ],
)
def test_specific_whole_word_match(sw, food, expected_phrase):
    match = sw._german_price_per_kg(food)
    assert match is not None and match[1] == expected_phrase, match


def test_unpriced_and_invalid_inputs(sw):
    assert sw._german_price_per_kg("Mollusks, abalone, mixed species, raw") is None
    assert sw._german_price_per_kg("") is None
    assert sw._estimate_food_price_eur("Bananas, raw", None) is None
    assert sw._estimate_food_price_eur("Bananas, raw", 0) is None
    assert sw._estimate_food_price_eur("Bananas, raw", 500) == pytest.approx(0.8)


def _swap(food: str, amount_per_100g: float, unit: str, component: str, dose: float, dose_unit: str) -> dict:
    return {
        "decision": "replace",
        "component": component,
        "dose_value": dose,
        "dose_unit": dose_unit,
        "selected_food": {"food_description": food, "amount_per_100g": amount_per_100g, "unit": unit},
    }


def test_basket_excludes_portions_over_one_kilo(sw):
    bananas = _swap("Bananas, raw", 0.34, "MG", "Magnesium", 80, "mg")       # ~23.5 kg/day
    almonds = _swap("Nuts, almonds", 270.0, "MG", "Magnesium", 100, "mg")    # ~37 g/day
    abalone = _swap("Mollusks, abalone, mixed species, raw", 48.0, "MG", "Magnesium", 100, "mg")
    basket = sw._basket_cost_breakdown([bananas, almonds, abalone])
    assert [name for name, _ in basket["impractical"]] == ["Bananas"]
    assert basket["impractical"][0][1] > 20000
    assert [name for name, _ in basket["rows"]] == ["Almonds"]
    assert basket["total"] == pytest.approx(basket["rows"][0][1])
    assert basket["total"] < 1.0
    assert basket["unknown"] == ["Abalone"]
    # Backwards-compatible 3-tuple summary.
    total, rows, unknown = sw._basket_cost_summary([bananas, almonds])
    assert total == pytest.approx(basket["total"]) and len(rows) == 1 and unknown == []


def test_price_table_documents_source_and_date():
    with PRICES_CSV.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and set(rows[0]) == {"match", "eur_per_kg", "basis", "source", "as_of"}
    for row in rows:
        assert float(row["eur_per_kg"]) > 0, row
        assert row["source"].strip() and row["as_of"] == "2025", row
        assert row["match"].strip(), row

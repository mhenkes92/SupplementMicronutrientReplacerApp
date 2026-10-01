"""Results-screen totals: "Your swaps add about X g of food and ~Y kcal a day"
from the match-dose grams (a food counts once, at its largest amount) and USDA
energy; impractical items are listed separately and not summed."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


def _swap(component, food_name, mg_per_100g, dose_mg):
    """A replaced item whose food needs dose_mg / mg_per_100g * 100 grams."""
    return {
        "decision": "replace",
        "component": component,
        "dose_value": dose_mg,
        "dose_unit": "mg",
        "form": "",
        "selected_food": {"food_description": food_name, "amount_per_100g": mg_per_100g, "unit": "mg"},
    }


def test_usda_energy_lookup():
    assert bb.food_energy_kcal_per_100g("Broccoli, raw") == pytest.approx(34.0)
    assert bb.food_energy_kcal_per_100g("broccoli, raw ") == pytest.approx(34.0)  # normalised name
    assert bb.food_energy_kcal_per_100g("Nuts, brazilnuts, raw") == pytest.approx(621, abs=1)
    assert bb.food_energy_kcal_per_100g("Not a food") is None


def test_totals_count_a_food_once_at_its_largest_amount(sw):
    items = [
        _swap("magnesium", "Broccoli, raw", 10.0, 30),   # 300 g broccoli
        _swap("potassium", "Broccoli, raw", 100.0, 200),  # 200 g of the same broccoli
        _swap("vitamin c", "Bananas, raw", 10.0, 10),     # 100 g banana
    ]
    totals = sw._swap_totals(items)
    assert totals["grams"] == pytest.approx(400)
    assert totals["kcal"] == pytest.approx(300 * 0.34 + 100 * 0.89)
    assert [name for name, _g, _k in totals["foods"]] == ["Broccoli", "Bananas"]
    assert totals["too_much"] is False and totals["impractical"] == []
    assert sw._swap_totals_lines(items) == [("total", "🍽️ Your swaps add about 400 g of food and ~190 kcal a day.")]


def test_impractical_items_are_listed_not_summed(sw):
    items = [
        _swap("magnesium", "Broccoli, raw", 10.0, 30),  # 300 g
        _swap("vitamin c", "Bananas, raw", 0.1, 20),    # 20 kg -> impractical
    ]
    totals = sw._swap_totals(items)
    assert totals["grams"] == pytest.approx(300)
    assert totals["impractical"] == [("Bananas", pytest.approx(20000))]
    lines = sw._swap_totals_lines(items)
    assert lines[0] == ("total", "🍽️ Your swaps add about 300 g of food and ~100 kcal a day.")
    assert lines[-1] == ("caption", "Not counted, not practical from food: Bananas (~20 kg/day).")
    assert not any(kind == "warning" for kind, _t in lines)


def test_large_totals_are_flagged(sw):
    by_grams = [_swap("magnesium", "Broccoli, raw", 10.0, 70), _swap("vitamin c", "Bananas, raw", 10.0, 60)]  # 700 + 600 g
    totals = sw._swap_totals(by_grams)
    assert totals["grams"] == pytest.approx(1300) and totals["too_much"] is True
    lines = sw._swap_totals_lines(by_grams)
    assert lines[0][1].startswith("🍽️ Your swaps add about 1,300 g of food")
    assert ("warning", "⚠️ This is a lot of food — consider keeping some supplements.") in lines

    by_kcal = [_swap("magnesium", "Nuts, brazilnuts, raw", 100.0, 200)]  # 200 g Brazil nuts ~ 1,240 kcal
    totals = sw._swap_totals(by_kcal)
    assert totals["grams"] == pytest.approx(200) and totals["kcal"] > 1200 and totals["too_much"] is True

    ok = [_swap("magnesium", "Broccoli, raw", 10.0, 50)]  # 500 g, 170 kcal
    assert sw._swap_totals(ok)["too_much"] is False


def test_foods_without_energy_data(sw):
    items = [_swap("magnesium", "Some food without USDA energy", 10.0, 10)]
    assert sw._swap_totals_lines(items) == [("total", "🍽️ Your swaps add about 100 g of food a day.")]
    mixed = items + [_swap("vitamin c", "Broccoli, raw", 100.0, 100)]
    lines = sw._swap_totals_lines(mixed)
    assert lines[0] == ("total", "🍽️ Your swaps add about 200 g of food and ~34 kcal a day.")
    assert lines[1][0] == "caption" and "no USDA energy value" in lines[1][1]


def test_no_swaps_no_totals(sw):
    assert sw._swap_totals_lines([]) == []
    assert sw._swap_totals_lines([{"decision": "replace", "component": "zinc", "selected_food": None}]) == []

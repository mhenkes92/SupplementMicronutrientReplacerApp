"""Golden end-to-end tests for the two reference labels (US + German):
label text -> bb.parse_components -> sw._filter_to_micronutrients ->
sw._build_swipe_cards -> top food portion.

Expected values are hand-computed from the label and checked against an
independent read of the USDA DB, so the tests pin the nutrient ids, units and
dose conversions without depending on how foods are ranked."""
from __future__ import annotations

import sqlite3

import pytest

import blockbrain.app as bb

LABEL_A = """Supplement Facts
Serving Size 1 Tablet
Amount Per Serving %DV
Vitamin A (as beta-carotene) 900 mcg 100%
Vitamin C (as ascorbic acid) 90 mg 100%
Vitamin D3 (as cholecalciferol) 25 mcg (1000 IU) 125%
Vitamin E (as d-alpha tocopheryl acetate) 15 mg 100%
Thiamin (as thiamine mononitrate) 1.2 mg 100%
Vitamin B-6 (as pyridoxine HCl) 1.7 mg 100%
Folate 680 mcg DFE (400 mcg folic acid) 170%
Vitamin B-12 (as cyanocobalamin) 6 mcg 250%
Biotin 30 mcg 100%
Iodine (as potassium iodide) 150 mcg 100%
Magnesium (as magnesium oxide) 100 mg 24%
Zinc (as zinc oxide) 11 mg 100%
Selenium (as sodium selenite) 55 mcg 100%
"""

LABEL_B = """Nährwertangaben pro Tagesdosis (1 Tablette) %NRV*
Vitamin A 800 µg 100%
Vitamin D3 20 µg (800 I.E.) 400%
Vitamin E 12 mg α-TE 100%
Vitamin C 80 mg 100%
Vitamin B1 1,1 mg 100%
Vitamin B2 1,4 mg 100%
Niacin 16 mg NE 100%
Vitamin B6 1,4 mg 100%
Folsäure 200 µg 100%
Vitamin B12 2,5 µg 100%
Biotin 50 µg 100%
Pantothensäure 6 mg 100%
Eisen 14 mg 100%
Zink 10 mg 100%
Jod 150 µg 100%
Selen 55 µg 100%
Magnesium 56 mg 15%
*NRV = Nährstoffbezugswerte
"""

# nutrient key -> (label dose, unit, USDA nutrient id, dose in the food's DB
# unit as the food must supply it). Folic acid counts 1.7x as DFE (200 µg
# Folsäure = 340 µg DFE); "680 mcg DFE" is already DFE.
EXPECTED_A = {
    "vitamin a": (900, "mcg", 1106, 900),
    "vitamin c": (90, "mg", 1162, 90),
    "vitamin d": (25, "mcg", 1114, 25),
    "vitamin e": (15, "mg", 1109, 15),
    "thiamin": (1.2, "mg", 1165, 1.2),
    "vitamin b6": (1.7, "mg", 1175, 1.7),
    "folate": (680, "mcg", 1190, 680),
    "vitamin b12": (6, "mcg", 1178, 6),
    "biotin": (30, "mcg", 1176, 30),
    "iodine": (150, "mcg", 1100, 150),
    "magnesium": (100, "mg", 1090, 100),
    "zinc": (11, "mg", 1095, 11),
    "selenium": (55, "mcg", 1103, 55),
}
EXPECTED_B = {
    "vitamin a": (800, "mcg", 1106, 800),
    "vitamin d": (20, "mcg", 1114, 20),
    "vitamin e": (12, "mg", 1109, 12),
    "vitamin c": (80, "mg", 1162, 80),
    "thiamin": (1.1, "mg", 1165, 1.1),
    "riboflavin": (1.4, "mg", 1166, 1.4),
    "niacin": (16, "mg", 1167, 16),
    "vitamin b6": (1.4, "mg", 1175, 1.4),
    "folate": (200, "mcg", 1190, 340),
    "vitamin b12": (2.5, "mcg", 1178, 2.5),
    "biotin": (50, "mcg", 1176, 50),
    "pantothenic acid": (6, "mg", 1170, 6),
    "iron": (14, "mg", 1089, 14),
    "zinc": (10, "mg", 1095, 10),
    "iodine": (150, "mcg", 1100, 150),
    "selenium": (55, "mcg", 1103, 55),
    "magnesium": (56, "mg", 1090, 56),
}
LABELS = {"A": (LABEL_A, EXPECTED_A), "B": (LABEL_B, EXPECTED_B)}

# NIH ODS reference where the local DB sample is an outlier (544 µg selenium
# per oz of Brazil nuts = 1917 µg/100 g; the DB sample says 280 µg).
REFERENCE_OVERRIDES = {(1103, "Nuts, brazilnuts, raw"): 1917.0}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No LLM: the AI name fallback is disabled and any LLM call fails the test."""
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


def _cards(sw, label: str) -> list[dict]:
    return sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(label)), [])


def _db_amount(nutrient_id: int, food: str) -> float | None:
    if (nutrient_id, food) in REFERENCE_OVERRIDES:
        return REFERENCE_OVERRIDES[(nutrient_id, food)]
    conn = sqlite3.connect(f"file:{bb.USDA_RANK_DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT MAX(amount_per_100g) FROM nutrient_rankings "
            "WHERE nutrient_id = ? AND food_description = ? AND amount_per_100g > 0",
            (nutrient_id, food),
        ).fetchone()
    finally:
        conn.close()
    return float(row[0]) if row and row[0] else None


@pytest.mark.parametrize("label_id", ["A", "B"])
def test_label_yields_exactly_its_nutrients_with_label_doses(sw, label_id):
    label, expected = LABELS[label_id]
    cards = _cards(sw, label)
    got = sorted((c["nutrient_key"], float(c["dose_value"]), bb._normalize_component_unit_token(c["dose_unit"])) for c in cards)
    want = sorted((key, float(dose), unit) for key, (dose, unit, _id, _db) in expected.items())
    assert got == want
    # One card per nutrient, so no two cards share a swipe-decision key.
    assert len({c["component_key"] for c in cards}) == len(cards) == len(expected)


@pytest.mark.parametrize("label_id", ["A", "B"])
def test_each_card_resolves_to_its_pinned_usda_nutrient(sw, label_id):
    label, expected = LABELS[label_id]
    for card in _cards(sw, label):
        ids = [n["id"] for n in bb._resolve_local_nutrient_candidates(card["component"], max_ids=3)]
        assert ids == [expected[card["nutrient_key"]][2]], card["component"]


@pytest.mark.parametrize("label_id", ["A", "B"])
def test_top_food_match_dose_is_within_25_percent_of_hand_computed(sw, label_id):
    label, expected = LABELS[label_id]
    checked = 0
    for card in _cards(sw, label):
        _dose, _unit, nutrient_id, dose_in_food_units = expected[card["nutrient_key"]]
        foods = card["foods"]
        assert foods, f"no foods for {card['component']}"
        assert len({f["unit"] for f in foods}) == 1, f"mixed units for {card['component']}"
        top = foods[0]
        truth = _db_amount(nutrient_id, top["food_description"])
        assert truth, f"top food {top['food_description']!r} has no USDA {nutrient_id} row"
        correct_g = dose_in_food_units / truth * 100.0
        grams = bb.grams_needed_to_match_dose(
            card["dose_value"], card["dose_unit"], top["amount_per_100g"], top["unit"], card["component"], card["form"]
        )
        assert grams is not None and abs(grams - correct_g) / correct_g <= 0.25, (card["component"], grams, correct_g)
        assert sw._portion_for_target(top, card["dose_value"], card["dose_unit"], card["component"], card["form"])
        checked += 1
    assert checked >= 8


def test_hand_computed_portions_for_reference_foods(sw):
    """Absolute, hand-computed portions (independent of ranking)."""
    def grams_for(cards, key, food):
        card = next(c for c in cards if c["nutrient_key"] == key)
        row = next(f for f in card["foods"] if f["food_description"] == food)
        return bb.grams_needed_to_match_dose(
            card["dose_value"], card["dose_unit"], row["amount_per_100g"], row["unit"], card["component"], card["form"]
        )

    cards_a = _cards(sw, LABEL_A)
    cards_b = _cards(sw, LABEL_B)
    # 900 µg RAE / 835 µg RAE per 100 g carrots (the old IU-row math said ~18 g).
    assert grams_for(cards_a, "vitamin a", "Carrots, raw") == pytest.approx(107.8, rel=0.01)
    # 680 µg DFE / 738 µg DFE per 100 g duck liver.
    assert grams_for(cards_a, "folate", "Duck, domesticated, liver, raw") == pytest.approx(92.1, rel=0.01)
    # 200 µg Folsäure = 340 µg DFE / 738 µg DFE per 100 g duck liver.
    assert grams_for(cards_b, "folate", "Duck, domesticated, liver, raw") == pytest.approx(46.1, rel=0.01)
    # 55 µg selenium / 1917 µg per 100 g Brazil nuts (NIH ODS) = ~2.9 g, about half a nut.
    assert grams_for(cards_b, "selenium", "Nuts, brazilnuts, raw") == pytest.approx(2.87, rel=0.01)
    # 150 µg iodine / 113.7 µg per 100 g cod.
    assert grams_for(cards_b, "iodine", "Fish, cod, Atlantic, wild caught, raw") == pytest.approx(131.9, rel=0.01)
    # 20 µg vitamin D3 / 31.9 µg per 100 g UV-exposed crimini mushrooms.
    assert grams_for(
        cards_b, "vitamin d", "Mushrooms, brown, italian, or crimini, exposed to ultraviolet light, raw"
    ) == pytest.approx(62.7, rel=0.01)


def test_german_label_card_names_and_forms(sw):
    cards = {c["nutrient_key"]: c for c in _cards(sw, LABEL_B)}
    assert cards["folate"]["component"] == "folic acid"
    assert "folic acid" in cards["folate"]["form"]
    assert cards["vitamin d"]["component"] == "vitamin d3"
    assert cards["iodine"]["component"] == "iodine"
    assert cards["vitamin e"]["form"] == "alpha-TE"
    assert cards["niacin"]["form"] == "NE"


def test_us_label_keeps_forms_from_the_label(sw):
    cards = {c["nutrient_key"]: c for c in _cards(sw, LABEL_A)}
    assert cards["vitamin a"]["form"] == "beta carotene"
    assert cards["iodine"]["form"] == "potassium iodide"
    assert cards["folate"]["form"].startswith("DFE") and "400 mcg folic acid" in cards["folate"]["form"]
    assert "d alpha" in cards["vitamin e"]["form"]

"""Label parsing -> swipe cards for common single-line supplements and German
label conventions (decimal commas, µg, I.E., α-TE, NE, German names)."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


def _cards(sw, text: str) -> list[dict]:
    return sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])


def _one_card(sw, text: str) -> dict:
    cards = _cards(sw, text)
    assert len(cards) == 1, [(c["component"], c["dose_label"]) for c in cards]
    return cards[0]


def _food(card: dict, description: str) -> dict:
    return next(f for f in card["foods"] if f["food_description"] == description)


def _grams(card: dict, food: dict) -> float | None:
    return bb.grams_needed_to_match_dose(
        card["dose_value"], card["dose_unit"], food["amount_per_100g"], food["unit"], card["component"], card["form"]
    )


def test_fish_oil_epa_dha_becomes_one_epa_plus_dha_card(sw):
    card = _one_card(sw, "Fish oil 1000 mg\nEPA 180 mg\nDHA 120 mg")
    assert card["nutrient_key"] == "omega 3"
    assert (card["dose_value"], card["dose_unit"]) == (300, "mg")
    assert card["form"] == "EPA 180 mg + DHA 120 mg"
    names = [f["food_description"] for f in card["foods"]]
    # Ranked by EPA+DHA: fatty fish on top, no ALA-only plant foods at all.
    assert names[0].startswith("Fish")
    assert not any(n.startswith(("Seeds, hemp", "Seeds, flaxseed", "Seeds, chia", "Chia seeds")) for n in names)
    mackerel = _food(card, "Fish, mackerel, Atlantic, raw")
    assert mackerel["amount_per_100g"] == pytest.approx(0.898 + 1.401)  # EPA + DHA, g/100 g
    assert _grams(card, mackerel) == pytest.approx(300 / 2299 * 100, rel=0.01)


def test_fish_oil_weight_alone_counts_as_30_percent_epa_dha(sw):
    card = _one_card(sw, "Fish oil 1000 mg")
    top = card["foods"][0]
    assert _grams(card, top) == pytest.approx(300 / (top["amount_per_100g"] * 1000) * 100, rel=0.01)
    # A neutral note: in the blue info line, not the red warning box (final review F13).
    assert "30%" in sw._card_extra_info(card["component_key"], card["dose_value"], card["dose_unit"], card["form"])
    assert "30%" not in sw._card_warning_text(card["component_key"], card["dose_value"], card["dose_unit"], card["form"])


def test_vitamin_d3_in_iu(sw):
    card = _one_card(sw, "Vitamin D3 1000 IU")
    assert card["nutrient_key"] == "vitamin d"
    assert (card["dose_value"], card["dose_unit"]) == (1000, "iu")
    mushrooms = _food(card, "Mushrooms, brown, italian, or crimini, exposed to ultraviolet light, raw")
    assert _grams(card, mushrooms) == pytest.approx(25 / 31.9 * 100, rel=0.01)  # 1000 IU = 25 µg


def test_vitamin_e_natural_vs_synthetic_iu(sw):
    natural = _one_card(sw, "Vitamin E 400 IU (as d-alpha tocopherol)")
    synthetic = _one_card(sw, "Vitamin E 400 IU (as dl-alpha tocopheryl acetate)")
    assert "d alpha" in natural["form"] and "dl alpha" in synthetic["form"]
    almonds_n, almonds_s = _food(natural, "Nuts, almonds"), _food(synthetic, "Nuts, almonds")
    # 400 IU natural = 268 mg, synthetic = 180 mg alpha-tocopherol; almonds 25.63 mg/100 g.
    assert _grams(natural, almonds_n) == pytest.approx(268 / 25.63 * 100, rel=0.01)
    assert _grams(synthetic, almonds_s) == pytest.approx(180 / 25.63 * 100, rel=0.01)
    assert sw._portion_for_target(almonds_n, 400, "iu", natural["component"], natural["form"]).startswith(
        "not practical from food alone (~"
    )
    # (almonds: at most ~70 g a day, final review F4)
    assert sw._portion_for_target(almonds_s, 400, "iu", synthetic["component"], synthetic["form"]) == (
        "not practical from food alone (~702 g/day; realistic max ~70 g/day)"
    )


def test_magnesium_citrate(sw):
    card = _one_card(sw, "Magnesium (as magnesium citrate) 400 mg")
    assert (card["nutrient_key"], card["dose_value"], card["dose_unit"]) == ("magnesium", 400, "mg")
    assert card["form"] == "magnesium citrate"
    assert "safe upper limit for magnesium (250 mg/day, EFSA" in sw._upper_limit_warning(
        card["component_key"], card["dose_value"], card["dose_unit"], card["form"]
    )


def test_vitamin_b6_50_mg(sw):
    card = _one_card(sw, "Vitamin B6 50 mg")
    assert (card["nutrient_key"], card["dose_value"], card["dose_unit"]) == ("vitamin b6", 50, "mg")
    assert sw._upper_limit_warning(card["component_key"], 50, "mg") == (
        "⚠️ 50 mg is above the safe upper limit for vitamin B6 (12 mg/day, EFSA) — "
        "check with a doctor before taking this long-term."
    )


def test_selenium_200_mcg(sw):
    card = _one_card(sw, "Selenium 200 mcg")
    assert (card["nutrient_key"], card["dose_value"], card["dose_unit"]) == ("selenium", 200, "mcg")
    assert sw._upper_limit_warning(card["component_key"], 200, "mcg") == ""  # EFSA UL 255 µg
    brazil = _food(card, "Nuts, brazilnuts, raw")
    assert _grams(card, brazil) == pytest.approx(200 / 1917 * 100, rel=0.01)
    assert "Brazil nut" in sw._portion_for_target(brazil, 200, "mcg", card["component"])


def test_zinc_amino_acid_chelate(sw):
    card = _one_card(sw, "Zinc (as zinc amino acid chelate) 15 mg")
    assert (card["nutrient_key"], card["dose_value"], card["dose_unit"]) == ("zinc", 15, "mg")


def test_ashwagandha_is_not_a_card(sw):
    assert _cards(sw, "Ashwagandha root extract 600 mg") == []


@pytest.mark.parametrize(
    "text, key, dose, unit",
    [
        ("Iodine (as potassium iodide) 150 mcg", "iodine", 150, "mcg"),
        ("Vitamin B-12 6 mcg", "vitamin b12", 6, "mcg"),
        ("Vitamin B-12 (as cyanocobalamin) 6 mcg", "vitamin b12", 6, "mcg"),
        ("EPA 180 mg", "epa", 180, "mg"),
        ("Vitamin A (as beta-carotene) 900 mcg", "vitamin a", 900, "mcg"),
        ("Vitamin A as beta carotene 900 mcg", "vitamin a", 900, "mcg"),
        ("Selenium (as sodium selenite) 55 mcg", "selenium", 55, "mcg"),
        ("Chromium (as chromium picolinate) 120 mcg 343%", "chromium", 120, "mcg"),
        ("Vitamin K (as menaquinone-7) 100 mcg", "vitamin k2", 100, "mcg"),
        ("Omega-3 (from flaxseed oil) 1000 mg", "ala", 1000, "mg"),
        ("Vitamin C 1,000 mg 1111%", "vitamin c", 1000, "mg"),
    ],
)
def test_single_line_labels_give_exactly_one_correct_card(sw, text, key, dose, unit):
    card = _one_card(sw, text)
    assert (card["nutrient_key"], card["dose_value"], card["dose_unit"]) == (key, dose, unit)


@pytest.mark.parametrize(
    "text, key, name, dose, unit",
    [
        ("Vitamin D3 20 µg (800 I.E.) 400%", "vitamin d", "vitamin d3", 20, "mcg"),
        ("Vitamin D3 800 I.E.", "vitamin d", "vitamin d3", 800, "iu"),
        ("Vitamin D3 1.000 I.E.", "vitamin d", "vitamin d3", 1000, "iu"),
        ("Vitamin E 12 mg α-TE 100%", "vitamin e", "vitamin e", 12, "mg"),
        ("Niacin 16 mg NE 100%", "niacin", "niacin", 16, "mg"),
        ("Vitamin B1 1,1 mg 100%", "thiamin", "vitamin b1", 1.1, "mg"),
        ("Vitamin C 1.000 mg", "vitamin c", "vitamin c", 1000, "mg"),
        ("Vitamin A 0,8 mg", "vitamin a", "vitamin a", 0.8, "mg"),
        ("Folsäure 200 µg 100%", "folate", "folic acid", 200, "mcg"),
        ("Vitamin B9 (Folsäure) 200 µg", "folate", "vitamin b9", 200, "mcg"),
        ("Pantothensäure 6 mg 100%", "pantothenic acid", "pantothenic acid", 6, "mg"),
        ("Calcium-D-Pantothenat 6 mg", "pantothenic acid", "pantothenic acid", 6, "mg"),
        ("Eisen 14 mg 100%", "iron", "iron", 14, "mg"),
        ("Zink 10 mg 100%", "zinc", "zinc", 10, "mg"),
        ("Jod 150 µg 100%", "iodine", "iodine", 150, "mcg"),
        ("Kaliumiodid 150 µg", "iodine", "iodine", 150, "mcg"),
        ("Selen (als Natriumselenit) 55 µg", "selenium", "selenium", 55, "mcg"),
        ("Kupfer 1 mg", "copper", "copper", 1, "mg"),
        ("Mangan 2 mg", "manganese", "manganese", 2, "mg"),
        ("Chrom 40 µg", "chromium", "chromium", 40, "mcg"),
        ("Molybdän 50 µg", "molybdenum", "molybdenum", 50, "mcg"),
        ("Kalium 200 mg", "potassium", "potassium", 200, "mg"),
        ("Kalzium 120 mg", "calcium", "calcium", 120, "mg"),
        ("Magnesiumcitrat 300 mg", "magnesium", "magnesium", 300, "mg"),
        ("Vitamin K2 (Menachinon-7) 75 µg", "vitamin k2", "vitamin k2", 75, "mcg"),
        ("Cholin 50 mg", "choline", "choline", 50, "mg"),
        ("Fluorid 1 mg", "fluoride", "fluoride", 1, "mg"),
    ],
)
def test_german_label_lines(sw, text, key, name, dose, unit):
    card = _one_card(sw, text)
    assert (card["nutrient_key"], card["component"], card["dose_value"], card["dose_unit"]) == (key, name, dose, unit)


def test_multi_column_german_label_uses_daily_dose_column(sw):
    text = (
        "Nährwerte pro Kapsel pro Tagesdosis (2 Kapseln) %NRV\n"
        "Vitamin C 40 mg 80 mg 100%\n"
        "Zink 5 mg 10 mg 100%\n"
        "Vitamin B6 0,7 mg 1,4 mg 100%\n"
    )
    got = sorted((c["nutrient_key"], c["dose_value"]) for c in _cards(sw, text))
    assert got == [("vitamin b6", 1.4), ("vitamin c", 80), ("zinc", 10)]


def test_several_nutrients_on_one_line(sw):
    got = sorted((c["nutrient_key"], c["dose_value"], c["dose_unit"]) for c in _cards(sw, "Zinc 15 mg, Copper 1 mg"))
    assert got == [("copper", 1, "mg"), ("zinc", 15, "mg")]


def test_vitamin_a_listed_in_two_forms_is_summed_into_one_card(sw):
    text = "Vitamin A (as retinyl palmitate) 450 mcg\nVitamin A (as beta-carotene) 450 mcg"
    card = _one_card(sw, text)
    assert (card["dose_value"], card["dose_unit"]) == (900, "mcg")
    assert card["form"] == "retinyl palmitate 450 mcg + beta carotene 450 mcg"


def test_duplicate_rows_keep_the_dosed_one(sw):
    rows = [
        {"component": "magnesium 400 mg", "dose_value": None, "dose_unit": ""},
        {"component": "magnesium", "dose_value": 400.0, "dose_unit": "mg"},
        {"component": "Vitamin B-6", "dose_value": None, "dose_unit": ""},
        {"component": "pyridoxine", "dose_value": 1.7, "dose_unit": "mg"},
    ]
    cards = sw._build_swipe_cards(sw._filter_to_micronutrients(rows), [])
    assert [(c["nutrient_key"], c["dose_value"]) for c in cards] == [("magnesium", 400.0), ("vitamin b6", 1.7)]


def test_parse_components_keeps_label_form_and_line(sw):
    rows = bb.parse_components("Vitamin E 400 IU (as d-alpha tocopherol)")
    assert rows == [
        {
            "component": "vitamin e",
            "dose_value": 400.0,
            "dose_unit": "iu",
            "form": "d alpha tocopherol",
            "nutrient_key": "vitamin e",
            "label_line": "Vitamin E 400 IU (as d-alpha tocopherol)",
        }
    ]


def test_ingredient_list_salt_is_read_as_the_nutrient_it_supplies(sw):
    keys = [c["nutrient_key"] for c in _cards(sw, "Ingredients: potassium iodide, chromium chloride, zinc gluconate")]
    assert "potassium" not in keys and "iodine" in keys
    # A real potassium line next to a potassium salt stays potassium.
    assert [c["nutrient_key"] for c in _cards(sw, "Potassium 99 mg\nIngredients: potassium chloride")] == ["potassium"]


def test_trailing_ingredient_lists_add_no_phantom_cards(sw):
    label = (
        "Nährwertangaben pro Tagesdosis (1 Tablette) %NRV*\n"
        "Vitamin C 80 mg 100%\nZink 10 mg 100%\nJod 150 µg 100%\nSelen 55 µg 100%\nEisen 14 mg 100%\n"
        "Zutaten: Calciumcarbonat, Magnesiumoxid, L-Ascorbinsäure, Eisenfumarat, Zinkoxid, Kaliumiodid, "
        "Natriumselenit, Überzugsmittel Hydroxypropylmethylcellulose, Trennmittel Magnesiumsalze der Speisefettsäuren."
    )
    got = sorted((c["nutrient_key"], c["dose_value"]) for c in _cards(sw, label))
    assert got == [("iodine", 150), ("iron", 14), ("selenium", 55), ("vitamin c", 80), ("zinc", 10)]


@pytest.mark.parametrize(
    "food, grams, expected",
    [
        ("Eggplant, raw", 300, ""),
        ("Fish, whitefish, eggs (Alaska Native)", 10, ""),
        ("Pineapple, raw", 300, ""),
        ("Peppers, sweet, orange, raw", 200, ""),
        ("Tomatoes, sun-dried", 100, ""),
        ("Bananas, dehydrated, or banana powder", 50, ""),
        ("Eggs, Grade A, Large, egg yolk", 53, "~3 egg yolks"),  # 3.1 -> nearest half, not rounded up
        ("Egg, whole, raw, fresh", 100, "~2 eggs"),
        ("Nuts, brazilnuts, raw", 10, "~2 Brazil nuts"),
        ("Carrots, baby, raw", 100, "~10 baby carrots"),
    ],
)
def test_portion_words_only_for_real_whole_items(food, grams, expected):
    text = bb.estimate_whole_food_units(food, grams)
    assert (expected in text) if expected else text == ""


def test_small_doses_keep_their_precision_on_the_card(sw):
    assert _one_card(sw, "Vitamin D3 0.025 mg")["dose_label"] == "0.025 mg"

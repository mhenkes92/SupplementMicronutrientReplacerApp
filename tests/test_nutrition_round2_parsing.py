"""Round-2 label parsing: second-language "Vitamina C" lines, dose ranges, glued
"Vitamin C1000 mg", compound weights vs the elemental "davon" dose, excipient
rows, "A + B" product titles, the B-complex umbrella, multi-column headers
without a day word, form adjectives before the name, JSON-wrapped tables and
generic rows the pipeline renamed."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


def _rows(text: str) -> list[tuple]:
    return [(r["component"], r["dose_value"], r["dose_unit"]) for r in bb.parse_components(text)]


def _cards(sw, text: str) -> list[tuple]:
    cards = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])
    return [(c["component"], c["dose_label"]) for c in cards]


# --- N1: "Vitamina" is the Italian / Spanish / Portuguese word for vitamin ------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Vitamina C 80 mg 100%", [("vitamin c", 80.0, "mg")]),
        ("Vitamin C 80 mg 100%\nVitamina C 80 mg 100%", [("vitamin c", 80.0, "mg")]),
        ("Vitamina D3 20 µg 400%", [("vitamin d3", 20.0, "mcg")]),
        ("Vitaminas B12 2,5 µg", [("vitamin b12", 2.5, "mcg")]),
        ("Vitamina E 12 mg 100%", [("vitamin e", 12.0, "mg")]),
    ],
)
def test_vitamina_is_never_read_as_vitamin_a(text, expected):
    assert _rows(text) == expected


def test_glued_ocr_vitamina_with_a_dose_is_still_vitamin_a():
    assert _rows("VitaminA 800 µg 100%") == [("vitamin a", 800.0, "mcg")]


def test_four_language_blocks_give_one_card_per_nutrient(sw):
    text = (
        "DE: Vitamin C 80 mg (100% NRV), Vitamin D3 20 µg (400% NRV), Zink 10 mg (100% NRV)\n"
        "FR: Vitamine C 80 mg (100% VNR), Vitamine D3 20 µg (400% VNR), Zinc 10 mg (100% VNR)\n"
        "IT: Vitamina C 80 mg (100% VNR), Vitamina D3 20 µg (400% VNR), Zinco 10 mg (100% VNR)\n"
        "ES: Vitamina C 80 mg (100% VRN), Vitamina D3 20 µg (400% VRN), Zinc 10 mg (100% VRN)"
    )
    assert _cards(sw, text) == [("vitamin c", "80 mg"), ("vitamin d3", "20 mcg"), ("zinc", "10 mg")]


def test_trilingual_columns_have_no_phantom_vitamin_a(sw):
    text = (
        "Vitamin C / Vitamine C / Vitamina C 80 mg 100%\n"
        "Vitamin D3 / Vitamine D3 / Vitamina D3 20 µg 400%\n"
        "Vitamin B12 / Vitamine B12 / Vitamina B12 2,5 µg 100%"
    )
    assert _cards(sw, text) == [("vitamin c", "80 mg"), ("vitamin d3", "20 mcg"), ("vitamin b12", "2.5 mcg")]
    # Translations of the same name are not a "form".
    assert bb.parse_components(text)[0]["form"] == ""


# --- NEW-4: glued OCR "Vitamin C1000 mg" ----------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Vitamin C1000 mg", [("vitamin c", 1000.0, "mg")]),
        ("Vitamin C1000mg Tabletten", [("vitamin c", 1000.0, "mg")]),
        ("Vitamin E13.5 mg", [("vitamin e", 13.5, "mg")]),
        ("Vitamin C1,000 mg", [("vitamin c", 1000.0, "mg")]),
    ],
)
def test_glued_letter_vitamin_and_dose(text, expected):
    assert _rows(text) == expected


def test_vitamin_a1_code_is_not_split_into_a_dose():
    assert bb._fold_label_text("Vitamin A1 500 µg") == "vitamin a1 500 ug"


# --- NUT-18: dose ranges ----------------------------------------------------------

@pytest.mark.parametrize(
    "text, low, high, unit",
    [
        ("Vitamin C 100-200 mg", 100.0, 200.0, "mg"),
        ("Magnesium 200–400 mg", 200.0, 400.0, "mg"),
        ("Vitamin D3 1.000-2.000 I.E.", 1000.0, 2000.0, "iu"),
        ("Selen 50 - 55 µg", 50.0, 55.0, "mcg"),
    ],
)
def test_dose_range_reads_the_lower_bound_and_keeps_the_upper(text, low, high, unit):
    [row] = bb.parse_components(text)
    assert (row["dose_value"], row["dose_unit"], row["dose_max"]) == (low, unit, high)
    assert "to" not in row["form"].split()


def test_generic_parsers_also_read_the_lower_bound_of_a_range():
    assert bb._prepare_text_for_structured_parsing("Coenzyme Q10 30-100 mg") == "Coenzyme Q10 30 mg"


def test_omega_blend_and_code_hyphens_are_not_ranges():
    assert "to" not in bb._fold_label_text("Omega 3-6-9 1200 mg").split()
    assert bb._fold_label_text("Vitamin B-12 2,5 µg") == "vitamin b12 2,5 ug"


# --- NEW-1: compound weight / excipient rows never beat the elemental dose -------

@pytest.mark.parametrize(
    "text",
    [
        "Magnesiumcitrat 1500 mg davon Magnesium 240 mg",
        "Inhaltsstoffe pro Tagesdosis: Magnesiumcitrat 1.500 mg, davon elementares Magnesium 240 mg (64% NRV)",
        "Magnesiumcitrat 1.500 mg\n- davon Magnesium 240 mg",
        "Magnesium 240 mg\nMagnesiumcitrat 1500 mg",
        "Magnesiumcitrat 1500 mg\nMagnesium 240 mg 64%",
    ],
)
def test_magnesium_citrate_compound_weight_loses_to_elemental_magnesium(sw, text):
    assert _rows(text) == [("magnesium", 240.0, "mg")]
    assert _cards(sw, text) == [("magnesium", "240 mg")]
    [card] = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])
    assert sw._upper_limit_warning(card["component_key"], card["dose_value"], card["dose_unit"], card["form"]) == ""


def test_bracketed_davon_dose_is_the_mineral(sw):
    assert _cards(sw, "Zinkbisglycinat 50 mg (davon Zink 10 mg = 100% NRV)") == [("zinc", "10 mg")]
    assert _cards(sw, "Magnesiumcitrat 1500 mg (davon 240 mg elementar)") == [("magnesium", "240 mg")]
    # Only for that mineral: another nutrient's bracket never lends its dose.
    assert _cards(sw, "Vitamin B12 (davon Magnesium 240 mg) 1,5 µg 100%") == [("vitamin b12", "1.5 mcg")]
    assert _cards(sw, "Zink (davon Magnesium 240 mg) 10 mg 100%") == [("zinc", "10 mg")]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Magnesium 400 mg\nMagnesium stearate 5 mg", [("magnesium", "400 mg")]),
        ("Calcium stearate 10 mg\nCalcium 200 mg", [("calcium", "200 mg")]),
    ],
)
def test_excipient_rows_are_not_nutrient_doses(sw, text, expected):
    assert _cards(sw, text) == expected


def test_a_lone_compound_row_is_kept():
    assert _rows("Magnesiumcitrat 400 mg") == [("magnesium", 400.0, "mg")]


def test_label_row_preference_ranks_compound_weight_below_plain():
    plain = {"label_line": "Magnesium 240 mg", "form": ""}
    compound = {"label_line": "Magnesiumcitrat 1500 mg", "form": "citrat", "compound_weight": True}
    assert bb.label_row_preference(plain) > bb.label_row_preference(compound)


# --- N3: multi-column headers without a day word; "aus" compound doses -----------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Nährwerte pro Kapsel pro 2 Kapseln %NRV\nVitamin C 40 mg 80 mg 100%", [("vitamin c", "80 mg")]),
        ("pro Tablette pro 3 Tabletten\nMagnesium 100 mg 300 mg 80%", [("magnesium", "300 mg")]),
        ("Nutrition per capsule per 2 capsules\nVitamin C 40 mg 80 mg 100%", [("vitamin c", "80 mg")]),
        ("Nährwerte pro Kapsel (= Tagesdosis) %NRV\nMagnesium 300 mg aus Magnesiumcitrat 1500 mg 80%", [("magnesium", "300 mg")]),
        # Still: the day column wins wherever it is.
        ("Nährwerte pro Tagesdosis (2 Kapseln) pro Kapsel %NRV\nVitamin C 80 mg 40 mg 100%", [("vitamin c", "80 mg")]),
    ],
)
def test_daily_column_of_multi_column_tables(sw, text, expected):
    assert _cards(sw, text) == expected


# --- NEW-2: "A + B ... dose" product titles -----------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Vitamin D3 + K2 MK-7 Tropfen 1000 IE + 20 µg", [("vitamin d3", "1000 iu"), ("vitamin k2", "20 mcg")]),
        ("Calcium + Vitamin D3 600 mg / 400 IE", [("calcium", "600 mg"), ("vitamin d3", "400 iu")]),
        ("Vitamin D3/K2 1000 IE/20 µg", [("vitamin d3", "1000 iu"), ("vitamin k2", "20 mcg")]),
        # One IU dose only fits vitamin D; K2 keeps its card without a dose.
        ("Vitamin D3 + K2 Depot 2000 I.E.", [("vitamin d3", "2000 iu"), ("vitamin k2", "Dose not found")]),
        ("Vitamin B Komplex hochdosiert 500 µg B12", [("vitamin b12", "500 mcg")]),
        ("Vitamin-B-Komplex Kapseln mit 500 µg Vitamin B12", [("vitamin b12", "500 mcg")]),
        ("Vitamin B1, B2 und B6 je 1,4 mg", [("vitamin b1", "1.4 mg"), ("vitamin b2", "1.4 mg"), ("vitamin b6", "1.4 mg")]),
    ],
)
def test_joined_title_names_keep_every_nutrient(sw, text, expected):
    assert _cards(sw, text) == expected


def test_ambiguous_title_dose_is_not_guessed(sw):
    # 400 mg could be either nutrient: neither gets it (no 400 mg vitamin B6 card).
    cards = _cards(sw, "Vitamin B6 + Magnesium 400 mg")
    assert ("vitamin b6", "400 mg") not in cards


def test_a_comma_list_is_not_a_title_group(sw):
    # The dose belongs to the name it follows; nothing is spread over the list.
    assert _cards(sw, "Calcium, Vitamin D3, Magnesium 400 mg") == [("magnesium", "400 mg")]
    assert _cards(sw, "Vitamin B1, B2, B6 1,4 mg") == [("vitamin b6", "1.4 mg")]
    # One dose per name still reads in order.
    assert _cards(sw, "Zink, Selen 10 mg 55 µg") == [("zinc", "10 mg"), ("selenium", "55 mcg")]


def test_vitamin_d_never_gets_a_milligram_dose_from_a_title(sw):
    assert ("vitamin d3", "600 mg") not in _cards(sw, "Calcium + Vitamin D3 600 mg / 400 IE")


def test_title_then_table_keeps_the_table_doses(sw):
    text = (
        "Vitamin D3 + K2 Depot 2000 I.E.\n"
        "Nährwerte pro Tagesdosis (2 Tropfen) %NRV*\n"
        "Vitamin D3 50 µg (2000 I.E.) 1000 %\n"
        "Vitamin K2 (MK-7) 50 µg 67 %"
    )
    assert _cards(sw, text) == [("vitamin d3", "50 mcg"), ("vitamin k2", "50 mcg")]


def test_dose_written_before_the_name():
    assert _rows("25 µg Vitamin D3\n100 µg Vitamin K2\n10 mg Zink") == [
        ("vitamin d3", 25.0, "mcg"), ("vitamin k2", 100.0, "mcg"), ("zinc", 10.0, "mg"),
    ]
    # A "pro 100 g" column header is not a dose of the next name.
    assert ("vitamin c", 100.0, "g") not in _rows("Nährwerte pro 100 g Vitamin C")


# --- NEW-3: the B-complex umbrella ------------------------------------------------

def test_b_complex_alone_becomes_dose_less_b_vitamin_cards(sw):
    cards = _cards(sw, "B-Complex 50 mg")
    assert [name for name, _dose in cards] == [
        "vitamin b1", "vitamin b2", "vitamin b3", "vitamin b5", "vitamin b6", "vitamin b7", "vitamin b9", "vitamin b12",
    ]
    assert {dose for _name, dose in cards} == {"Dose not found"}
    built = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components("B-Complex 50 mg")), [])
    assert all(card["foods"] for card in built)


def test_b_complex_with_a_dosed_b_vitamin_keeps_the_label_rows(sw):
    cards = _cards(sw, "Jod 150 µg 100%\nB-Komplex 1,5 µg 100%\nBiotin 50 µg\nNiacin")
    assert ("biotin", "50 mcg") in cards and ("niacin", "Dose not found") in cards
    assert not any(name in ("vitamin b1", "vitamin b complex") for name, _dose in cards)
    # The dose is never the umbrella's: B12 takes the one written before it.
    assert _cards(sw, "B-Komplex\nVitamin B12 500 µg") == [("vitamin b12", "500 mcg")]


def test_b_complex_umbrella_is_never_a_card(sw):
    assert not sw._is_micronutrient("vitamin b complex")
    assert all(name != "vitamin b complex" for name, _ in _cards(sw, "Vitamin B Komplex 50 mg\nVitamin C 80 mg"))


# --- N8: form adjectives before the name ------------------------------------------

@pytest.mark.parametrize("text", ["natürliches Vitamin E 400 I.E.", "Natural Vitamin E 400 IU"])
def test_natural_vitamin_e_adjective_before_the_name_sets_the_iu_factor(sw, text):
    [card] = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])
    assert bb._iu_unit_to_mg_for_component(card["component"], card["form"]) == pytest.approx(0.67)


def test_synthetic_vitamin_e_adjective_before_the_name():
    [row] = bb.parse_components("synthetisches Vitamin E 400 I.E.")
    assert bb._iu_unit_to_mg_for_component(row["component"], row["form"]) == pytest.approx(0.45)


# --- N9 / NEW-5: generic rows the pipeline renamed; JSON tables ---------------------

@pytest.mark.parametrize(
    "text, other",
    [
        ("L-Carnitin 500 mg\nVitamin B6 1,4 mg", "carnitin"),
        ("Coenzym Q10 100 mg\nVitamin E 12 mg", "q10"),
    ],
)
def test_renamed_non_lexicon_rows_survive(text, other):
    names = [str(r["component"]).lower() for r in bb.parse_components(text)]
    assert any(other in n for n in names), names
    assert len(names) == 2


def test_json_wrapped_table_is_read_by_the_label_line_parser(sw):
    text = '{"nutrients": [{"name": "Vitamin C", "amount": "80 mg"}, {"name": "Zink", "amount": "10 mg"}]}'
    assert _rows(text) == [("vitamin c", 80.0, "mg"), ("zinc", 10.0, "mg")]


def test_kept_generic_row_with_extra_words_gets_the_lexicon_card_name():
    assert bb._with_lexicon_card_name({"component": "name zink amount"})["component"] == "zinc"
    assert bb._with_lexicon_card_name({"component": "vitamin d3"})["component"] == "vitamin d3"


def test_contextual_anchor_no_longer_overwrites_an_existing_dose():
    rows = [{"component": "vitamin c", "dose_value": 80.0, "dose_unit": "mg"}]
    out, _warnings = bb._apply_contextual_vitamin_dose_corrections(rows, "Vitamin C 500 mg title\nVitamin C 80 mg 100%")
    assert out[0]["dose_value"] == 80.0

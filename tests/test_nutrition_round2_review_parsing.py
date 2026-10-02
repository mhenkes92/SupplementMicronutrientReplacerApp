"""Round-2 confirmation review, label parsing: a dash between a coded nutrient
name and its dose ("Vitamin D3 - 1000 I.E.") is never a dose range, and a
bracketed "(davon / entspricht / of which ... mg ...)" dose only replaces the
row's dose when the row itself states a compound weight."""
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
    return [(r["component"], r["dose_value"], r["dose_unit"], r.get("dose_max")) for r in bb.parse_components(text)]


def _card(sw, text: str) -> dict:
    [card] = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])
    return card


# --- R1-NUT18-DASH: the vitamin's own code digit is never a range's lower bound ---

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Vitamin D3 - 1000 I.E. - 365 Tabletten", ("vitamin d3", 1000.0, "iu", None)),
        ("Vitamin D3 – 2000 IE", ("vitamin d3", 2000.0, "iu", None)),
        ("Vitamin D3 - 20 µg", ("vitamin d3", 20.0, "mcg", None)),
        ("Vitamin D 3 - 1000 IE", ("vitamin d3", 1000.0, "iu", None)),
        ("Vitamin B12 - 1000 µg Lutschtabletten", ("vitamin b12", 1000.0, "mcg", None)),
        ("Vitamin B6 - 25 mg", ("vitamin b6", 25.0, "mg", None)),
        ("Vitamin B1 - 1,1 mg", ("vitamin b1", 1.1, "mg", None)),
        ("Vitamin K2 - 200 µg MK-7", ("vitamin k2", 200.0, "mcg", None)),
        ("Vitamin K2 MK-7 - 200 µg", ("vitamin k2", 200.0, "mcg", None)),
        ("Omega-3 - 1000 mg", ("omega-3", 1000.0, "mg", None)),
        ("Omega 3 - 1000 mg", ("omega-3", 1000.0, "mg", None)),
        ("Coenzym Q10 - 100 mg", ("coenzyme q10", 100.0, "mg", None)),
        ("Coenzym Q 10 - 100 mg", ("coenzyme q10", 100.0, "mg", None)),
        ("Vitamin D3 bis 1000 I.E.", ("vitamin d3", 1000.0, "iu", None)),
    ],
)
def test_dash_after_a_coded_name_is_not_a_dose_range(text, expected):
    assert _rows(text) == [expected]


def test_dash_title_with_two_coded_names_keeps_both_doses():
    assert _rows("Vitamin D3 K2 - 5000 IE + 100 µg") == [
        ("vitamin d3", 5000.0, "iu", None),
        ("vitamin k2", 100.0, "mcg", None),
    ]


def test_dash_title_repeating_a_table_dose_gives_one_row():
    assert _rows("Vitamin B12 1000 µg\nVitamin B12 - 1000 µg") == [("vitamin b12", 1000.0, "mcg", None)]
    assert _rows("Vitamin D3 - 1000 I.E.\nVitamin D3 25 µg (1000 IE) 500%") == [("vitamin d3", 25.0, "mcg", None)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Vitamin C 100-200 mg", ("vitamin c", 100.0, "mg", 200.0)),
        ("Vitamin C - 100-200 mg", ("vitamin c", 100.0, "mg", 200.0)),
        ("Vitamin C 100 bis 200 mg", ("vitamin c", 100.0, "mg", 200.0)),
        ("Zink 10 - 15 mg", ("zinc", 10.0, "mg", 15.0)),
        ("Vitamin D3 1000-2000 IU", ("vitamin d3", 1000.0, "iu", 2000.0)),
        ("Vitamin B12 500-1000 mcg per serving", ("vitamin b12", 500.0, "mcg", 1000.0)),
    ],
)
def test_real_dose_ranges_still_read(text, expected):
    assert _rows(text) == [expected]


@pytest.mark.parametrize(
    "before, low, is_code",
    [
        ("vitamin d", "3", True),
        ("Omega-", "3", True),
        ("omega ", "3", True),
        ("Vitamin K2 MK-", "7", True),
        ("coenzym q ", "10", True),
        ("B ", "12", True),
        ("vitamin c ", "100", False),
        ("vitamin d ", "1000", False),
        ("Zink ", "10", False),
        ("Vitamin C - ", "100", False),
    ],
)
def test_dose_range_lower_is_code(before, low, is_code):
    assert bb._dose_range_lower_is_code(before, low) is is_code


def test_generic_parsers_keep_the_dose_after_a_dash():
    assert bb._prepare_text_for_structured_parsing("Vitamin D3 - 1000 I.E.") == "Vitamin D3 - 1000 I.E."
    assert bb._prepare_text_for_structured_parsing("Omega-3 – 1000 mg") == "Omega-3 – 1000 mg"
    assert bb._prepare_text_for_structured_parsing("Coenzyme Q10 30-100 mg") == "Coenzyme Q10 30 mg"


def test_dash_title_card_shows_the_real_dose(sw):
    card = _card(sw, "Vitamin D3 - 1000 I.E. - 365 Tabletten")
    assert card["dose_label"] == "1000 IU"
    assert card.get("dose_max") is None


# --- R1-NEW1-BRACKET: a bracket never overrides a plain mineral row ------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Magnesium 400 mg (davon 200 mg aus Magnesiumcitrat und 200 mg aus Magnesiumoxid) 107%", ("magnesium", 400.0, "mg", None)),
        ("Magnesium 300 mg 80% (davon 150 mg Magnesiumcitrat)", ("magnesium", 300.0, "mg", None)),
        ("Calcium 800 mg (davon 400 mg aus Calciumcarbonat)", ("calcium", 800.0, "mg", None)),
        ("Zinc 15 mg (of which 5 mg as zinc picolinate)", ("zinc", 15.0, "mg", None)),
        ("Eisen 14 mg (davon 7 mg als Eisenbisglycinat)", ("iron", 14.0, "mg", None)),
        ("Magnesium 400 mg (entspricht 1000 mg Magnesiumcitrat)", ("magnesium", 400.0, "mg", None)),
        ("Calcium 500 mg (equivalent to 1250 mg calcium carbonate)", ("calcium", 500.0, "mg", None)),
        ("Magnesium 400 mg (als Citrat, entspricht 1000 mg Magnesiumcitrat)", ("magnesium", 400.0, "mg", None)),
        ("Magnesium 400 mg (davon Magnesium 200 mg)", ("magnesium", 400.0, "mg", None)),
    ],
)
def test_bracket_share_or_compound_weight_never_replaces_a_plain_mineral_dose(text, expected):
    assert _rows(text) == [expected]


@pytest.mark.parametrize(
    "text, expected",
    [
        # "entspricht / equivalent to" + a salt is the compound weight ...
        ("Magnesium 400 mg, entspricht Magnesiumcitrat 2000 mg", ("magnesium", 400.0, "mg", None)),
        ("Magnesium 400 mg\nentspricht Magnesiumcitrat 2000 mg", ("magnesium", 400.0, "mg", None)),
        ("Calcium 500 mg, equivalent to calcium carbonate 1250 mg", ("calcium", 500.0, "mg", None)),
        # ... + the bare mineral is the mineral itself.
        ("Magnesiumcitrat 2000 mg, entspricht Magnesium 320 mg", ("magnesium", 320.0, "mg", None)),
        ("Magnesiumcitrat 2000 mg entsprechend 320 mg Magnesium", ("magnesium", 320.0, "mg", None)),
        ("Calciumcarbonat 1250 mg, equivalent to calcium 500 mg", ("calcium", 500.0, "mg", None)),
    ],
)
def test_unbracketed_equivalent_reads_compound_and_mineral_apart(text, expected):
    assert _rows(text) == [expected]


def test_compound_equivalent_in_brackets_gives_no_false_upper_limit_warning(sw):
    card = _card(sw, "Magnesium 400 mg (entspricht 1000 mg Magnesiumcitrat)")
    assert card["dose_label"] == "400 mg"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Zinkgluconat 70 mg (davon Zink 10 mg) 100%", ("zinc", 10.0, "mg", None)),
        ("Zinkbisglycinat 50 mg (davon Zink 10 mg)", ("zinc", 10.0, "mg", None)),
        ("Magnesiumcitrat 1500 mg (davon 240 mg elementar)", ("magnesium", 240.0, "mg", None)),
        ("Magnesiumcitrat 1500 mg (entspricht 240 mg Magnesium)", ("magnesium", 240.0, "mg", None)),
        ("Calciumcarbonat 1250 mg (davon Calcium 500 mg) 63%", ("calcium", 500.0, "mg", None)),
        ("Ferrous fumarate 200 mg (providing 65 mg iron)", ("iron", 65.0, "mg", None)),
        ("Kaliumiodid 196 µg (davon Jod 150 µg)", ("iodine", 150.0, "mcg", None)),
        ("Kaliumiodid 196 µg (davon 150 µg Jod aus Kaliumiodid)", ("iodine", 150.0, "mcg", None)),
        ("Kaliumcitrat 500 mg (davon Kalium 180 mg)", ("potassium", 180.0, "mg", None)),
        ("Zinc gluconate 70 mg (of which 10 mg as elemental zinc)", ("zinc", 10.0, "mg", None)),
    ],
)
def test_compound_row_takes_the_elemental_bracket_dose(text, expected):
    assert _rows(text) == [expected]


def test_compound_row_ignores_a_bracketed_salt_weight():
    # The bracket restates a salt weight: nothing elemental to read.
    assert _rows("Zinc gluconate 70 mg (of which 10 mg as zinc gluconate)") == [("zinc", 70.0, "mg", None)]


def test_cation_names_in_a_title_group_are_not_salt_words():
    rows = bb.parse_label_nutrient_lines("Kalium + Natrium 100 mg + 50 mg")
    assert [(r["component"], r["dose_value"], bool(r.get("compound_weight"))) for r in rows] == [
        ("potassium", 100.0, False),
        ("sodium", 50.0, False),
    ]


def test_salt_named_after_a_dose_is_a_compound_weight():
    [row] = bb.parse_label_nutrient_lines("mit 500 mg Magnesiumcitrat")
    assert (row["component"], row["dose_value"], row.get("compound_weight")) == ("magnesium", 500.0, True)


# --- R1-PROSE-DOSE-BEFORE: prose that writes each dose before its name ----------

@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "Hochdosiert mit 2000 I.E. Vitamin D3 und 100 µg Vitamin K2",
            [("vitamin d3", 2000.0, "iu", None), ("vitamin k2", 100.0, "mcg", None)],
        ),
        ("500 µg Vitamin B12, 400 µg Folsäure", [("vitamin b12", 500.0, "mcg", None), ("folic acid", 400.0, "mcg", None)]),
        (
            "Pro Tagesdosis (1 Kapsel): 25 µg Vitamin D3 (1000 I.E.), 100 µg Vitamin K2 (MK-7)",
            [("vitamin d3", 25.0, "mcg", None), ("vitamin k2", 100.0, "mcg", None)],
        ),
        ("Tagesdosis 2 Kapseln: 400 mg Magnesium 10 mg Zink", [("magnesium", 400.0, "mg", None), ("zinc", 10.0, "mg", None)]),
        (
            "Enthält 25 µg Vitamin D3 sowie 100 µg Vitamin K2 pro Tablette",
            [("vitamin d3", 25.0, "mcg", None), ("vitamin k2", 100.0, "mcg", None)],
        ),
    ],
)
def test_dose_right_before_the_next_name_is_that_names_dose(text, expected):
    assert _rows(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        # Name-then-dose lines keep each dose with the name before it.
        ("Vitamin D3 20 µg Vitamin K2 50 µg", [("vitamin d3", 20.0, "mcg", None), ("vitamin k2", 50.0, "mcg", None)]),
        ("Vitamin B12 500 µg Folsäure 400 µg", [("vitamin b12", 500.0, "mcg", None), ("folic acid", 400.0, "mcg", None)]),
        ("mit 500 µg Vitamin B12", [("vitamin b12", 500.0, "mcg", None)]),
        ("1000 I.E. Vitamin D3 20 µg", [("vitamin d3", 20.0, "mcg", None)]),
    ],
)
def test_name_then_dose_lines_are_unchanged(text, expected):
    assert _rows(text) == expected

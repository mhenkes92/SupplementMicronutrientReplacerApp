"""Round-2 confirmation review, label parsing: a dash between a coded nutrient
name and its dose ("Vitamin D3 - 1000 I.E.") is never a dose range."""
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
    assert card["dose_label"] == "1000 iu"
    assert card.get("dose_max") is None

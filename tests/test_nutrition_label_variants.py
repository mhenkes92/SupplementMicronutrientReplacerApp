"""Real-world label variants: spacing / abbreviation of vitamin codes, micro-sign
variants, product titles and bilingual lines repeating a dose, multi-column
German tables (per capsule | per daily dose) — and that generic-parser rows
are neither lost nor invented around the lines the label-line parser reads."""
from __future__ import annotations

import pytest

import blockbrain.app as bb
from test_nutrition_golden import LABEL_A, LABEL_B


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


def _cards(sw, text: str) -> list[dict]:
    return sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(text)), [])


def _doses(sw, text: str) -> dict[str, str]:
    """nutrient key -> dose label, asserting one card per nutrient."""
    cards = _cards(sw, text)
    keys = [c["nutrient_key"] for c in cards]
    assert len(keys) == len(set(keys)), [(c["component"], c["dose_label"]) for c in cards]
    return {c["nutrient_key"]: c["dose_label"] for c in cards}


def _parsed(text: str) -> list[tuple[str, float, str]]:
    return [(c["component"], c["dose_value"], c["dose_unit"]) for c in bb.parse_components(text)]


# --- Vitamin codes written with spaces, dots or glued -----------------------

@pytest.mark.parametrize(
    "raw, folded",
    [
        ("VitaminB6 1,4 mg", "vitamin b6 1,4 mg"),
        ("Vit.B6 1,4 mg", "vitamin b6 1,4 mg"),
        ("Vitamin B 6 1,4 mg", "vitamin b6 1,4 mg"),
        ("VitaminB12 2,5 µg", "vitamin b12 2,5 ug"),
        ("Vitamin B 12 2,5 µg", "vitamin b12 2,5 ug"),
        ("Vitamin Bl2 2,5 µg", "vitamin b12 2,5 ug"),  # OCR l for 1
        ("Vitamin K 2 100 µg", "vitamin k2 100 ug"),
        ("Vitamin D-3 1000 IU", "vitamin d3 1000 iu"),
        # The number is the dose, not part of the code.
        ("Vitamin D 3 µg", "vitamin d 3 ug"),
        ("Vitamin B 1,1 mg", "vitamin b 1,1 mg"),
        ("Vitamin K 100 µg", "vitamin k 100 ug"),
        ("Vitamine C 80 mg", "vitamin c 80 mg"),
    ],
)
def test_fold_joins_vitamin_codes_only_when_they_are_codes(raw, folded):
    assert bb._fold_label_text(raw) == folded


@pytest.mark.parametrize(
    "old, new, key",
    [
        ("Vitamin B6 1,4 mg", "VitaminB6 1,4 mg", "vitamin b6"),
        ("Vitamin B6 1,4 mg", "Vit.B6 1,4 mg", "vitamin b6"),
        ("Vitamin B6 1,4 mg", "Vitamin B 6 1,4 mg", "vitamin b6"),
        ("Vitamin B12 2,5 µg", "VitaminB12 2,5 µg", "vitamin b12"),
        ("Vitamin B12 2,5 µg", "Vitamin B 12 2,5 µg", "vitamin b12"),
        ("Vitamin B1 1,1 mg", "Vitamin B 1 1,1 mg", "thiamin"),
        ("Vitamin B2 1,4 mg", "Vitamin B 2 1,4 mg", "riboflavin"),
    ],
)
def test_german_label_with_spaced_or_glued_b_vitamins_keeps_all_17_cards(sw, old, new, key):
    assert old in LABEL_B
    doses = _doses(sw, LABEL_B.replace(old, new))
    assert len(doses) == 17
    assert doses[key] == _doses(sw, LABEL_B)[key]


def test_german_label_with_all_b_vitamins_spaced(sw):
    text = LABEL_B
    for code in ("1", "2", "6", "12"):
        text = text.replace(f"Vitamin B{code} ", f"Vitamin B {code} ")
    doses = _doses(sw, text)
    assert doses == _doses(sw, LABEL_B)
    assert (doses["thiamin"], doses["riboflavin"], doses["vitamin b6"]) == ("1.1 mg", "1.4 mg", "1.4 mg")


def test_us_label_with_glued_b6_keeps_all_13_cards(sw):
    doses = _doses(sw, LABEL_A.replace("Vitamin B-6 (as", "VitaminB6 (as"))
    assert len(doses) == 13
    assert doses["vitamin b6"] == "1.7 mg"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Vitamin B 6 1,4 mg", [("vitamin b6", 1.4, "mg")]),
        ("Vitamin B 1 1,1 mg", [("vitamin b1", 1.1, "mg")]),
        ("Vitamin B-12 6 mcg", [("vitamin b12", 6.0, "mcg")]),
    ],
)
def test_spaced_codes_never_create_a_phantom_vitamin_b9(text, expected):
    assert _parsed(text) == expected


def test_truncated_vitamin_b_is_not_guessed_as_folate():
    # "Vitamin B" with no code: no folate (B9) card is invented from it.
    assert not any(bb.canonical_nutrient_key(name) == "folate" for name, _v, _u in _parsed("Vitamin B 12 µg"))
    assert not any(name == "vitamin b9" for name, _v, _u in _parsed("Vitamin B 1,1 mg"))


# --- Micro sign variants -----------------------------------------------------

@pytest.mark.parametrize("unit", ["µg", "μg", "ΜG", "㎍", "ug", "mcg"])
def test_micro_sign_variants_are_micrograms_never_grams(sw, unit):
    assert bb._fold_label_text(f"Vitamin A 800 {unit}").endswith(("800 ug", "800 mcg"))
    doses = _doses(sw, f"VITAMIN A 800 {unit}")
    assert doses == {"vitamin a": "800 mcg"}


def test_upper_cased_german_label_keeps_microgram_doses(sw):
    doses = _doses(sw, LABEL_B.upper())
    assert doses["vitamin a"] == "800 mcg"
    assert doses["vitamin d"] == "20 mcg"
    assert doses["folate"] == "200 mcg"
    assert doses["iodine"] == "150 mcg"
    assert not any(label.endswith(" g") for label in doses.values())


def test_unknown_micro_sign_does_not_become_grams():
    assert bb._fold_label_text("Vitamin A 800 ɥg") == "vitamin a 800 xg"
    assert ("vitamin a", 800.0, "g") not in _parsed("Vitamin A 800 ɥg")


# --- Generic rows around the lines the label-line parser read -----------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Coenzyme Q10 100 mg\nVitamin C 100 mg", [("vitamin c", 100.0, "mg"), ("coenzyme q10", 100.0, "mg")]),
        ("Lutein 10 mg\nZinc 10 mg", [("zinc", 10.0, "mg"), ("lutein", 10.0, "mg")]),
    ],
)
def test_non_micronutrient_rows_sharing_a_dose_are_kept(text, expected):
    assert _parsed(text) == expected


def test_ocr_typo_line_sharing_a_dose_is_kept():
    # The line parser cannot read "Magnesiurn"; the generic parser's fuzzy
    # correction can, and an equal dose on another line is no reason to drop it.
    assert _parsed("Vitamin C 80 mg\nMagnesiurn 80 mg") == [("vitamin c", 80.0, "mg"), ("magnesium", 80.0, "mg")]


def test_salt_cation_is_never_a_separate_nutrient():
    assert _parsed("Iodine (as potassium iodide) 150 mcg") == [("iodine", 150.0, "mcg")]

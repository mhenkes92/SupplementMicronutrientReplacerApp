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


# --- Titles, marketing and second-language lines repeat a dose (never add) ---

@pytest.mark.parametrize(
    "text, key, dose, form",
    [
        (
            "Vitamin D3 1000 I.E. Tabletten\nNährwerte pro Tablette\nVitamin D3 (Cholecalciferol) 25 µg (1000 I.E.) 500%",
            "vitamin d", "25 mcg", "cholecalciferol",
        ),
        (
            "Magnesium 400 mg Kapseln\nNährwerte pro Kapsel\nMagnesium (als Magnesiumcitrat) 400 mg 107%",
            "magnesium", "400 mg", "magnesium citrat",
        ),
        (
            "Vitamin B12 1000 µg Lutschtabletten\nVitamin B12 (Methylcobalamin) 1000 µg",
            "vitamin b12", "1000 mcg", "methylcobalamin",
        ),
        (
            "Vitamin D3 2000 IU softgels\nVitamin D3 (as cholecalciferol) 50 mcg (2000 IU)",
            "vitamin d", "50 mcg", "cholecalciferol",
        ),
        (
            "Vitamin D3 1000 I.E. hochdosiert vegan\nVitamin D3 25 µg 500%",
            "vitamin d", "25 mcg", "",
        ),
        (
            "Vitamin E 400 I.E. Kapseln\nVitamin E (als natürliches d-alpha-Tocopherol) 268 mg (400 I.E.) 2233%",
            "vitamin e", "268 mg", "naturliches d alpha tocopherol",
        ),
    ],
)
def test_product_title_repeating_the_dose_does_not_double_it(sw, text, key, dose, form):
    cards = _cards(sw, text)
    assert [(c["nutrient_key"], c["dose_label"], c["form"]) for c in cards] == [(key, dose, form)]
    # parse_components itself returns one row (other consumers see no duplicate).
    assert len(bb.parse_components(text)) == 1


def test_bilingual_label_lines_do_not_double_the_dose(sw):
    text = (
        "Vitamin C (L-Ascorbinsäure) 80 mg\nVitamine C (acide L-ascorbique) 80 mg\n"
        "Magnesium (als Magnesiumcitrat) 300 mg\nMagnésium (citrate de magnésium) 300 mg"
    )
    cards = _cards(sw, text)
    assert [(c["nutrient_key"], c["dose_label"]) for c in cards] == [("vitamin c", "80 mg"), ("magnesium", "300 mg")]
    # The German line (first) is kept; the UL check sees 300 mg, not 600 mg.
    magnesium = cards[1]
    warn = sw._card_warning_text(magnesium["component_key"], magnesium["dose_value"], magnesium["dose_unit"], magnesium["form"])
    assert "300 mg is above the safe upper limit for magnesium" in warn


def test_packaging_words_are_not_a_form():
    rows = bb.parse_components("Vitamin D3 1000 I.E. Tabletten hochdosiert")
    assert [(r["component"], r["dose_value"], r["dose_unit"], r["form"]) for r in rows] == [("vitamin d3", 1000.0, "iu", "")]
    assert bb.parse_components("Magnesium Citrat 400 mg")[0]["form"] == "citrat"


def test_table_line_wins_over_a_different_title_dose(sw):
    # Title gives the compound weight, the table the elemental dose: the table line counts.
    text = "Magnesiumcitrat 1000 mg Kapseln\nMagnesium (als Magnesiumcitrat) 150 mg 40%"
    assert _doses(sw, text) == {"magnesium": "150 mg"}


def test_vitamin_a_retinyl_plus_beta_carotene_on_separate_lines_is_summed(sw):
    cards = _cards(sw, "Vitamin A (as retinyl palmitate) 450 mcg\nVitamin A (as beta-carotene) 450 mcg")
    assert [(c["nutrient_key"], c["dose_label"]) for c in cards] == [("vitamin a", "900 mcg")]


def test_vitamin_a_same_form_in_two_languages_is_not_summed(sw):
    text = "Vitamin A (als Retinylacetat) 400 µg 50%\nVitamine A (acétate de rétinyle) 400 µg 50%"
    assert _doses(sw, text) == {"vitamin a": "400 mcg"}


def test_vitamin_a_title_plus_two_form_lines_sums_only_the_table_forms(sw):
    text = (
        "Vitamin A 900 µg Kapseln\n"
        "Vitamin A (as retinyl palmitate) 450 mcg 50%\n"
        "Vitamin A (as beta-carotene) 450 mcg 50%"
    )
    assert _doses(sw, text) == {"vitamin a": "900 mcg"}


# --- Multi-column tables: the daily-dose column ------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        (   # standard NemV wording
            "Nährwerte pro Kapsel pro empfohlener Tagesverzehrmenge (2 Kapseln) %NRV*\n"
            "Vitamin C 40 mg 80 mg 100%\nZink 5 mg 10 mg 100%\nVitamin D3 10 µg 20 µg 400%",
            {"vitamin c": "80 mg", "zinc": "10 mg", "vitamin d": "20 mcg"},
        ),
        (
            "Inhaltsstoffe je Kapsel je Verzehrempfehlung (2 Kapseln)\nMagnesium 150 mg 300 mg 80%\nVitamin B6 0,7 mg 1,4 mg",
            {"magnesium": "300 mg", "vitamin b6": "1.4 mg"},
        ),
        (
            "Nährwerte pro Kapsel pro empfohlener täglicher Verzehrmenge (2 Kapseln)\nVitamin C 40 mg 80 mg",
            {"vitamin c": "80 mg"},
        ),
        (   # per 100 g first, then per portion: the portion is the dose
            "Nährwerte pro 100 g pro Portion (5 g)\nMagnesium 6000 mg 300 mg",
            {"magnesium": "300 mg"},
        ),
        (   # per-day column first
            "Nährwerte pro Tagesdosis (2 Kapseln) pro Kapsel %NRV\nVitamin C 80 mg 40 mg 100%\nZink 10 mg 5 mg 100%",
            {"vitamin c": "80 mg", "zinc": "10 mg"},
        ),
        (   # bare column word next to a "pro ..." column
            "Nährwerte | pro Kapsel | Tagesdosis (2 Kapseln) | %NRV\nVitamin C 40 mg 80 mg 100%",
            {"vitamin c": "80 mg"},
        ),
        (   # header split over OCR lines
            "Nährwerte\npro Kapsel\npro Tagesdosis\nVitamin C 40 mg 80 mg 100%",
            {"vitamin c": "80 mg"},
        ),
        (
            "Nährwerte pro Kapsel pro Tagesdosis\nVitamin D3 10 µg (400 I.E.) 20 µg (800 I.E.) 400%",
            {"vitamin d": "20 mcg"},
        ),
        (
            "Amount per serving | per daily serving\nVitamin C 250 mg 500 mg",
            {"vitamin c": "500 mg"},
        ),
    ],
)
def test_multi_column_tables_take_the_daily_dose_column(sw, text, expected):
    assert _doses(sw, text) == expected


def test_mandatory_german_warning_sentence_is_not_a_column_header():
    text = (
        "Nährwertangaben pro Kapsel\nVitamin C 80 mg 100%\n"
        "Die angegebene empfohlene tägliche Verzehrmenge darf nicht überschritten werden."
    )
    assert bb._label_daily_dose_column(text) is None
    assert _parsed(text) == [("vitamin c", 80.0, "mg")]


# --- OCR unit / spelling slips and misattributed doses -------------------------

@pytest.mark.parametrize(
    "old, new, key, dose",
    [
        ("Vitamin B12 2,5 µg", "Vitamin B12 2,5 pg", "vitamin b12", "2.5 mcg"),  # OCR µ -> p
        ("Vitamin B12 2,5 µg", "Vitamin B12 2,5 µ g", "vitamin b12", "2.5 mcg"),
        ("Jod 150 µg", "Jod 150 pg", "iodine", "150 mcg"),
        ("Jod 150 µg", "Iod 150 µg", "iodine", "150 mcg"),
    ],
)
def test_ocr_slips_keep_the_card_and_its_dose(sw, old, new, key, dose):
    doses = _doses(sw, LABEL_B.replace(old, new))
    assert len(doses) == 17 and doses[key] == dose


def test_unreadable_unit_never_borrows_another_lines_dose():
    # The generic parser pairs "Vitamin B12 2,5 ??" with Biotin's "50 µg" on the
    # next line; that line was already read as biotin, so the row is dropped
    # rather than shown with a wrong dose.
    rows = bb.parse_components(LABEL_B.replace("Vitamin B12 2,5 µg", "Vitamin B12 2,5 qq"))
    assert not [r for r in rows if bb.canonical_nutrient_key(r["component"]) == "vitamin b12"]
    biotin = [r for r in rows if bb.canonical_nutrient_key(r["component"]) == "biotin"]
    assert [(r["dose_value"], r["dose_unit"]) for r in biotin] == [(50.0, "mcg")]

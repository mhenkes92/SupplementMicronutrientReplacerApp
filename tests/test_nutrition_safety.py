"""Safety and wording of the swipe cards: upper-limit warnings, portion
practicality, diet-specific B12 advice, micronutrient filter / RDA lookup and
bioavailability notes that agree with the foods shown."""
from __future__ import annotations

from pathlib import Path

import pytest

import blockbrain.app as bb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("nutrition code must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


# --- Upper limits --------------------------------------------------------------

@pytest.mark.parametrize(
    "name, dose, unit, form, expected",
    [
        ("vitamin b6", 50, "mg", "", "50 mg is above the safe upper limit for vitamin B6 (12 mg/day, EFSA)"),
        ("vitamin d3", 5000, "iu", "", "5000 IU (125 mcg) is above the safe upper limit for vitamin D (100 mcg/day"),
        ("vitamin d", 150, "mcg", "", "for vitamin D (100 mcg/day, EFSA & NIH)"),
        ("vitamin a", 12000, "iu", "retinyl palmitate", "12000 IU (3600 mcg) is above the safe upper limit for vitamin A (3000 mcg/day"),
        ("vitamin a", 3500, "mcg", "", "for vitamin A (3000 mcg/day"),
        ("vitamin e", 1000, "iu", "d alpha tocopherol", "1000 IU (~670 mg alpha-TE) is above the safe upper limit for vitamin E (300 mg/day, EFSA)"),
        ("vitamin e", 1000, "iu", "dl alpha tocopheryl acetate", "1000 IU (~670 mg alpha-TE)"),
        ("vitamin c", 2500, "mg", "", "for vitamin C (2000 mg/day, NIH)"),
        ("niacin", 50, "mg", "nicotinic acid", "for niacin as nicotinic acid (10 mg/day, EFSA — the form that causes flushing)"),
        ("niacin", 500, "mg", "niacinamide", "for niacin (35 mg/day, NIH, from supplements)"),
        ("folic acid", 1200, "mcg", "folic acid", "1200 mcg is above the safe upper limit for folic acid (1000 mcg/day"),
        ("folate", 2040, "mcg", "DFE; 1200 mcg folic acid", "1200 mcg folic acid is above the safe upper limit for folic acid"),
        ("calcium", 3000, "mg", "", "for calcium (2500 mg/day"),
        ("iron", 45, "mg", "", "for iron (40 mg/day, EFSA)"),
        ("zinc", 30, "mg", "", "for zinc (25 mg/day, EFSA)"),
        ("copper", 6, "mg", "", "for copper (5 mg/day, EFSA)"),
        ("selenium", 300, "mcg", "", "for selenium (255 mcg/day, EFSA)"),
        ("iodine", 700, "mcg", "", "for iodine (600 mcg/day, EFSA)"),
        ("magnesium", 300, "mg", "magnesium citrate", "for magnesium (250 mg/day, EFSA, from supplements)"),
        ("manganese", 12, "mg", "", "for manganese (11 mg/day, NIH)"),
        ("molybdenum", 700, "mcg", "", "for molybdenum (600 mcg/day, EFSA)"),
        ("fluoride", 8, "mg", "", "for fluoride (7 mg/day, EFSA)"),
        ("phosphorus", 4500, "mg", "", "for phosphorus (4000 mg/day, NIH)"),
        ("choline", 4000, "mg", "", "for choline (3500 mg/day, NIH)"),
        ("Zink", 30, "mg", "", "for zinc (25 mg/day, EFSA)"),
        ("Selenium (as sodium selenite)", 300, "mcg", "", "for selenium (255 mcg/day, EFSA)"),
    ],
)
def test_doses_above_the_upper_limit_are_flagged(sw, name, dose, unit, form, expected):
    warning = sw._upper_limit_warning(name, dose, unit, form)
    assert warning.startswith("⚠️ ")
    assert expected in warning
    assert warning.endswith("check with a doctor before taking this long-term.")


@pytest.mark.parametrize(
    "name, dose, unit, form",
    [
        ("vitamin d3", 4000, "iu", ""),  # exactly the UL (100 µg) is not above it
        ("vitamin d3", 25, "mcg", ""),
        ("vitamin a", 10000, "iu", "retinyl palmitate"),  # 3000 µg = UL
        ("vitamin a", 9000, "mcg", "beta carotene"),  # beta-carotene has no UL
        ("vitamin a", 4000, "mcg", "retinyl palmitate 450 mcg + beta carotene 3550 mcg"),  # only 450 preformed
        ("vitamin e", 400, "iu", "d alpha tocopherol"),  # 268 mg
        ("niacin", 16, "mg", "NE"),
        ("folate", 680, "mcg", "DFE; 400 mcg folic acid"),
        ("folate", 1500, "mcg", "calcium l methylfolate"),  # UL is for synthetic folic acid only
        ("folic acid", 200, "mcg", "folic acid"),
        ("selenium", 200, "mcg", ""),
        ("vitamin k2", 1000, "mcg", ""), ("vitamin k", 1000, "mcg", ""), ("thiamin", 100, "mg", ""),
        ("riboflavin", 100, "mg", ""), ("pantothenic acid", 500, "mg", ""), ("biotin", 10000, "mcg", ""),
        ("vitamin b12", 5000, "mcg", ""), ("chromium", 1000, "mcg", ""), ("potassium", 99, "mg", ""),
        ("beta-carotene", 6, "mg", ""), ("vitamin c", 80, "mg", ""), ("iron", None, "", ""),
    ],
)
def test_doses_at_or_below_the_limit_or_without_a_ul_are_not_flagged(sw, name, dose, unit, form):
    assert sw._upper_limit_warning(name, dose, unit, form) == ""


def test_high_dose_beta_carotene_gets_the_smoker_note(sw):
    assert "smokers" in sw._upper_limit_warning("beta-carotene", 20, "mg")


def test_over_ul_warning_replaces_the_low_dose_flag(sw):
    # A low zinc dose (< 50% of the 10 mg EU NRV) gets the neutral low-dose
    # note, in the blue info line (final review F13), never next to a UL warning.
    assert "Low dose" in sw._card_extra_info("zinc", 3, "mg")
    assert "Low dose" not in sw._card_warning_text("zinc", 3, "mg")
    over = sw._card_warning_text("zinc", 50, "mg")
    assert "safe upper limit" in over and "Low dose" not in over + sw._card_extra_info("zinc", 50, "mg")
    over_d = sw._card_warning_text("vitamin d3", 10000, "iu")
    assert "safe upper limit" in over_d and "Low dose" not in over_d + sw._card_extra_info("vitamin d3", 10000, "iu")


def test_results_screen_lists_kept_pills_above_the_limit(sw):
    kept = [
        {"component": "vitamin b6", "dose_value": 50.0, "dose_unit": "mg", "form": ""},
        {"component": "vitamin c", "dose_value": 80.0, "dose_unit": "mg", "form": ""},
    ]
    warnings = sw._final_upper_limit_warnings(kept)
    assert len(warnings) == 1 and warnings[0].startswith("Vitamin B6: ⚠️ 50 mg is above")


# --- Portions ------------------------------------------------------------------

@pytest.mark.parametrize(
    "grams, level",
    [(None, "ok"), (0, "ok"), (5, "ok"), (399.9, "ok"), (400, "large"), (999, "large"), (1000, "large"), (1000.5, "impractical"), (23530, "impractical")],
)
def test_portion_practicality(sw, grams, level):
    assert sw._portion_practicality(grams) == level


def test_portion_text_flags_large_and_impractical_amounts(sw):
    banana = {"food_description": "Banana, raw", "amount_per_100g": 0.85, "unit": "mcg"}
    assert sw._portion_for_target(banana, 200, "mcg", "chromium") == "not practical from food alone (~23.5 kg/day)"
    broccoli = {"food_description": "Broccoli, raw", "amount_per_100g": 14.0, "unit": "mcg"}
    assert sw._portion_for_target(broccoli, 70, "mcg", "chromium") == "a lot of food (~500 g/day)"
    assert sw._portion_for_target(broccoli, 35, "mcg", "chromium") == "~250 g"
    decision = {"selected_food": banana, "dose_value": 200, "dose_unit": "mcg", "component": "chromium"}
    assert sw._amount_to_match_dose(decision) == "not practical from food alone (~23.5 kg/day)"


def test_tiny_portions_show_less_than_one_gram(sw):
    liver = {"food_description": "Lamb, New Zealand, imported, liver, raw", "amount_per_100g": 15434.0, "unit": "mcg"}
    assert sw._portion_for_target(liver, 100, "mcg", "vitamin a") == "<1 g"
    brazil = {"food_description": "Nuts, brazilnuts, raw", "amount_per_100g": 1917.0, "unit": "mcg"}
    assert sw._portion_for_target(brazil, 15, "mcg", "selenium") == "<1 g (~0.2 Brazil nuts)"


def test_vegan_and_vegetarian_b12_cards_recommend_keeping_the_supplement(sw):
    _, profiles = sw._dietary_profile_lookup()
    for diet in ("vegan", "vegetarian"):
        warn = sw._card_warning_text("vitamin b12", 1000, "mcg", "", profiles[diet])
        assert f"On a {diet} diet whole foods aren't a reliable vitamin B12 source" in warn
        assert "keeping the supplement is recommended" in warn
    assert "reliable vitamin B12" not in sw._card_warning_text("vitamin b12", 1000, "mcg", "", profiles["none"])
    assert "reliable" not in sw._card_warning_text("vitamin c", 80, "mg", "", profiles["vegan"])


def test_vegan_b12_seaweed_portion_is_flagged_not_practical(sw):
    _, profiles = sw._dietary_profile_lookup()
    foods = bb.apply_food_filters(bb._build_local_food_rows_for_component("vitamin b12", limit=250), profiles["vegan"])
    assert foods
    assert sw._portion_for_target(foods[0], 1000, "mcg", "vitamin b12").startswith("not practical from food alone")


def test_athlete_ratio_counts_folic_acid_as_dfe_and_e_by_form(sw):
    # 200 µg folic acid = 340 µg DFE of the 600 µg DFE athlete target.
    assert sw._dose_vs_athlete_ratio("folic acid", 200, "mcg", "folic acid") == pytest.approx(340 / 600)
    assert sw._dose_vs_athlete_ratio("vitamin e", 30, "iu", "d alpha tocopherol") == pytest.approx(20.1 / 20)


# --- Micronutrient filter / RDA lookup --------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "Vitamin B-12", "B12", "Vit. B12", "Vitamin B 12", "Zinc (as zinc amino acid chelate)",
        "Magnesium (as magnesium bisglycinate, amino acid chelate)", "Iron (as iron protein succinylate)",
        "Folsäure", "Jod", "Selen", "Eisen", "Pantothensäure", "Omega-3", "EPA", "DHA", "Fish oil",
        "alpha-linolenic acid", "Vitamin K2", "Calcium", "Chromium (as chromium chloride)",
    ],
)
def test_real_micronutrients_pass_the_filter(sw, name):
    assert sw._is_micronutrient(name)


@pytest.mark.parametrize(
    "name",
    [
        "Ashwagandha", "Ashwagandha root extract (KSM-66)", "Omega-6", "GLA (gamma-linolenic acid)",
        "Gamma-linolenic acid", "environmental blend", "Proprietary blend", "Proprietary Blend (Zinc, Vitamin C)",
        "magnesium stearate", "Sodium benzoate", "Protein", "Total Fat", "Calories", "vitamin b", "Lutein",
    ],
)
def test_non_micronutrients_are_rejected(sw, name):
    assert not sw._is_micronutrient(name)


@pytest.mark.parametrize(
    "name, display",
    [
        ("Iodine (as potassium iodide)", "Iodine"),
        ("Selenium (as sodium selenite)", "Selenium"),
        ("Chromium (as chromium chloride)", "Chromium"),
        ("Vitamin B-12", "Vitamin B12"),
        ("vitamin b1", "Vitamin B1 (Thiamin)"),
        ("Folsäure", "Vitamin B9 (Folate)"),
        ("Calcium-D-Pantothenat", "Vitamin B5 (Pantothenic)"),
        ("Vitamin K2 (as MK-7)", "Vitamin K"),
        ("omega-3 (epa+dha)", "Omega-3 (EPA+DHA)"),
        ("alpha-linolenic acid", "Omega-3 ALA"),
    ],
)
def test_rda_lookup_uses_the_nutrient_not_its_salt(sw, name, display):
    assert sw._rda_for_component(name)["display"] == display


@pytest.mark.parametrize("name", ["ashwagandha root extract", "beta-carotene", "epa", "lutein"])
def test_rda_lookup_has_no_entry_for_non_matching_names(sw, name):
    assert sw._rda_for_component(name) is None


# --- Notes agree with the data ------------------------------------------------------

def test_bioavailability_notes_match_the_foods_shown(sw):
    top_a = bb._build_local_food_rows_for_component("vitamin a", limit=5)[0]["food_description"].lower()
    assert "liver" in top_a and "liver is extremely high" in sw._bioavailability_note("vitamin a").lower()
    iron_note = sw._bioavailability_note("iron").lower()
    assert "non-heme" in iron_note and "plants" in iron_note
    # The note says 50-100 µg per ~5 g Brazil nut; the food list must agree.
    brazil = next(f for f in bb._build_local_food_rows_for_component("selenium", limit=250) if "brazil" in f["food_description"].lower())
    per_nut = brazil["amount_per_100g"] * 5 / 100
    assert 50 <= per_nut <= 100 and "~5 g" in sw._bioavailability_note("selenium")
    assert "no vitamin K2 data" in sw._bioavailability_note("vitamin k2")
    assert "1.7" in sw._bioavailability_note("folic acid", "folic acid")
    assert "1.7" not in sw._bioavailability_note("folate", "DFE; 400 mcg folic acid")
    assert "D2" in sw._bioavailability_note("vitamin d3")


# --- App smoke ------------------------------------------------------------------

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")


def test_app_renders_cards_and_results_with_ul_warnings():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["swipe_pending_request"] = {
        "upload_bytes": b"", "camera_bytes": b"", "manual": "Vitamin B6 50 mg\nVitamin B12 1000 mcg\nZink 10 mg",
    }
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.session_state["swipe_diet_profile_id"] = "vegan"
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    cards = at.session_state["swipe_cards"]
    assert [c["nutrient_key"] for c in cards] == ["vitamin b6", "vitamin b12", "zinc"]

    # Results screen with the B6 pill kept: its UL warning is listed.
    at.session_state["swipe_decisions"] = {
        "vitamin b6": {
            "component_key": "vitamin b6", "component": "vitamin b6", "dose_label": "50 mg", "dose_value": 50.0,
            "dose_unit": "mg", "form": "", "decision": "keep", "selected_food": None, "card_index": 0,
        }
    }
    at.session_state["swipe_index"] = len(cards)
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    # Listed in the results' heads-up box.
    shown = [c.value for c in at.caption] + [m.value for m in at.markdown]
    assert any("safe upper limit for vitamin B6" in text for text in shown)


# --- Liver: vitamin A in the suggested portion ------------------------------------

def test_liver_portion_above_vitamin_a_limit_is_flagged(sw):
    duck = {"food_description": "Duck, domesticated, liver, raw", "amount_per_100g": 738.0, "unit": "mcg"}
    lamb = {"food_description": "Lamb, New Zealand, imported, liver, raw", "amount_per_100g": 59.0, "unit": "mcg"}
    # US label folate 680 µg DFE -> ~92 g duck liver = ~11,000 µg RAE (UL 3000 µg).
    warning = sw._selected_food_warning(duck, 680, "mcg", "folate", "DFE; 400 mcg folic acid")
    assert warning.startswith("⚠️ ~92 g of this liver also gives ~11042 mcg vitamin A")
    assert "3000 mcg/day safe upper limit" in warning
    assert sw._selected_food_warning(lamb, 6, "mcg", "vitamin b12") == ""  # ~10 g liver = ~1500 µg RAE
    assert "vitamin A" in sw._selected_food_warning(lamb, 25, "mcg", "vitamin b12")
    nuts = {"food_description": "Nuts, almonds", "amount_per_100g": 25.63, "unit": "mg"}
    assert sw._selected_food_warning(nuts, 15, "mg", "vitamin e") == ""
    replaced = [{"component": "folate", "dose_value": 680.0, "dose_unit": "mcg", "form": "DFE; 400 mcg folic acid", "selected_food": duck}]
    assert sw._final_food_warnings(replaced)[0].startswith("Folate: ⚠️ ~92 g of this liver")


def test_food_nutrient_amount_lookup():
    assert bb.food_nutrient_amount("Duck, domesticated, liver, raw", "vitamin a") == 11984.0
    assert bb.food_nutrient_amount("Nuts, brazilnuts, raw", "Selen") == 1917.0
    assert bb.food_nutrient_amount("Nuts, almonds", "ashwagandha") is None

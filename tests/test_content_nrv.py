"""EU reference values (UXL-07): the low-dose flag compares the pill with the
EU NRV printed on German labels (Regulation (EU) 1169/2011 Annex XIII), fires
only below 50% NRV and is neutral; "often low in athletes" is an info line that
never appears at >= 100% NRV; upper-limit warnings stay red and win."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


def test_nrv_table_matches_annex_xiii(sw):
    expected = {
        "vitamin a": (800, "mcg"), "vitamin d": (5, "mcg"), "vitamin e": (12, "mg"), "vitamin k": (75, "mcg"),
        "vitamin c": (80, "mg"), "thiamin": (1.1, "mg"), "riboflavin": (1.4, "mg"), "niacin": (16, "mg"),
        "vitamin b6": (1.4, "mg"), "folate": (200, "mcg"), "vitamin b12": (2.5, "mcg"), "biotin": (50, "mcg"),
        "pantothenic acid": (6, "mg"), "potassium": (2000, "mg"), "chloride": (800, "mg"), "calcium": (800, "mg"),
        "phosphorus": (700, "mg"), "magnesium": (375, "mg"), "iron": (14, "mg"), "zinc": (10, "mg"),
        "copper": (1, "mg"), "manganese": (2, "mg"), "fluoride": (3.5, "mg"), "selenium": (55, "mcg"),
        "chromium": (40, "mcg"), "molybdenum": (50, "mcg"), "iodine": (150, "mcg"),
    }
    for key, value in expected.items():
        assert sw._EU_NRV[key][0] == pytest.approx(value[0]), key
        assert sw._EU_NRV[key][1] == value[1], key
    # Every NRV key is a real lexicon key (otherwise it would never be looked up).
    assert all(bb.canonical_nutrient_key(k) == k for k in sw._EU_NRV)


@pytest.mark.parametrize(
    "component, dose, unit, form",
    [
        ("vitamin c", 80, "mg", ""),
        ("vitamin d3", 20, "mcg", ""),
        ("vitamin d3", 200, "iu", ""),  # 5 µg = 100% NRV
        ("vitamin b12", 2.5, "mcg", ""),
        ("folic acid", 200, "mcg", "folsaure; folic acid"),  # folic acid is not DFE-scaled here
        ("zinc", 10, "mg", ""),
        ("selenium", 55, "mcg", ""),
        ("iron", 14, "mg", ""),
        ("iodine", 150, "mcg", ""),
        ("magnesium", 188, "mg", ""),  # 50% NRV: not "low"
    ],
)
def test_no_low_dose_flag_at_or_above_half_the_nrv(sw, component, dose, unit, form):
    assert sw._deficiency_flag(component, dose, unit, form) == ""
    warn = sw._card_warning_text(component, dose, unit, form)
    assert "Low dose" not in warn and "athlete" not in warn.lower() and "prioritis" not in warn


def test_low_dose_flag_is_neutral_and_below_half_the_nrv(sw):
    flag = sw._deficiency_flag("magnesium", 56, "mg")  # 15% NRV, the sample label's magnesium
    assert flag == "ℹ️ Low dose: about 15% of the EU daily reference intake (NRV)."
    assert "⚠️" not in flag and "only" not in flag and "prioritis" not in flag
    assert sw._deficiency_flag("vitamin d", 1, "mcg").startswith("ℹ️ Low dose: about 20%")
    # No NRV, no flag (omega-3, choline) and an unknown dose is not flagged.
    assert sw._deficiency_flag("omega-3 (epa+dha)", 50, "mg") == ""
    assert sw._deficiency_flag("choline", 10, "mg") == ""
    assert sw._deficiency_flag("zinc", None, "") == ""


def test_athlete_remark_is_info_only_below_the_nrv(sw):
    # Not part of the red warning any more ...
    assert "Often low" not in sw._card_warning_text("zinc", 3, "mg")
    assert "Often low" not in sw._card_warning_text("vitamin d3", 2, "mcg")
    # ... but an info line, only while the pill gives < 100% NRV.
    assert sw._athlete_info_note("zinc", 3, "mg").startswith("Often low in active people")
    assert sw._athlete_info_note("vitamin d3", 4, "mcg")
    assert sw._athlete_info_note("zinc", 10, "mg") == ""
    assert sw._athlete_info_note("vitamin d3", 20, "mcg") == ""
    assert sw._athlete_info_note("vitamin b12", 2.5, "mcg") == ""
    assert sw._athlete_info_note("vitamin c", 10, "mg") == ""  # not an athlete-risk nutrient
    assert sw._athlete_info_note("omega-3 (epa+dha)", 300, "mg")  # no NRV: shown
    assert "Often low" in sw._card_extra_info("iron", 5, "mg")
    assert sw._card_extra_info("iron", 14, "mg") == ""


def test_upper_limit_stays_red_and_wins(sw):
    warn = sw._card_warning_text("zinc", 50, "mg")
    assert warn.startswith("⚠️") and "safe upper limit" in warn and "Low dose" not in warn
    assert sw._athlete_info_note("zinc", 50, "mg") == ""


def test_rda_guide_lists_the_eu_nrv(sw):
    by_display = {entry["display"]: entry for entry in sw._MICRONUTRIENT_RDA}
    assert sw._format_eu_nrv(by_display["Vitamin D"]) == "5"
    assert sw._format_eu_nrv(by_display["Vitamin B9 (Folate)"]) == "200"
    assert sw._format_eu_nrv(by_display["Iodine"]) == "150"
    assert sw._format_eu_nrv(by_display["Choline"]) == "–"
    # The athlete target row on the card is unchanged.
    assert sw._format_rda_target(sw._rda_for_component("zinc")) == "15 mg"

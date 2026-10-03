"""Final review, label parsing: a '+'-joined title over a dose-first list
(F3), %NRV cross-check of a misread unit (F6), weekly products (F9)."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bb, "_ai_canonicalize_nutrient_name", lambda *_a, **_k: "")

    def _no_llm(*_a, **_k):
        raise AssertionError("label parsing must not call the LLM")

    monkeypatch.setattr(bb, "call_blockbrain_text", _no_llm)


def _doses(text: str) -> dict[str, tuple[float | None, str]]:
    return {c["nutrient_key"]: (c["dose_value"], c["dose_unit"]) for c in bb.parse_components(text)}


# --- F3: '+' title followed by a dose-first list ---------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Magnesium + Zink: 300 mg Magnesium, 10 mg Zink", {"magnesium": (300.0, "mg"), "zinc": (10.0, "mg")}),
        ("Zink + Vitamin C: 10 mg Zink und 200 mg Vitamin C", {"zinc": (10.0, "mg"), "vitamin c": (200.0, "mg")}),
        ("Vitamin D3 + K2 Kapseln: 50 µg Vitamin D3 und 200 µg Vitamin K2",
         {"vitamin d": (50.0, "mcg"), "vitamin k2": (200.0, "mcg")}),
        ("Eisen + Vitamin C: 14 mg Eisen, 40 mg Vitamin C", {"iron": (14.0, "mg"), "vitamin c": (40.0, "mg")}),
        ("Zink + Selen + Vitamin C: 10 mg Zink, 50 µg Selen, 80 mg Vitamin C",
         {"zinc": (10.0, "mg"), "selenium": (50.0, "mcg"), "vitamin c": (80.0, "mg")}),
    ],
)
def test_plus_title_over_a_dose_first_list(text, expected):
    assert _doses(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        # Unchanged: doses after the title, and a later name not in the title.
        ("Vitamin D3 + K2 1000 IE + 20 µg", {"vitamin d": (1000.0, "iu"), "vitamin k2": (20.0, "mcg")}),
        ("Calcium + Vitamin D3 600 mg / 400 IE", {"calcium": (600.0, "mg"), "vitamin d": (400.0, "iu")}),
        ("Vitamin D3 + K2 1000 IE + 20 µg Zink 10 mg",
         {"vitamin d": (1000.0, "iu"), "vitamin k2": (20.0, "mcg"), "zinc": (10.0, "mg")}),
        ("Vitamin D3 + K2: 1000 IE Vitamin D3 und 100 µg K2", {"vitamin d": (1000.0, "iu"), "vitamin k2": (100.0, "mcg")}),
        ("Mit 10 mg Zink, 55 µg Selen und 80 mg Vitamin C.",
         {"zinc": (10.0, "mg"), "selenium": (55.0, "mcg"), "vitamin c": (80.0, "mg")}),
    ],
)
def test_other_joined_lines_keep_their_doses(text, expected):
    assert _doses(text) == expected


def test_title_name_without_a_later_dose_has_none():
    doses = _doses("Magnesium + Zink + Selen: 300 mg Magnesium, 10 mg Zink")
    assert doses == {"magnesium": (300.0, "mg"), "zinc": (10.0, "mg"), "selenium": (None, "")}


def test_dose_first_title_cards_warn_only_for_the_real_dose(sw):
    cards = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(
        "Zink + Selen + Vitamin C: 10 mg Zink, 50 µg Selen, 80 mg Vitamin C")), [])
    assert all(not sw._upper_limit_warning(c["component_key"], c["dose_value"], c["dose_unit"], c["form"]) for c in cards)
    mg = sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(
        "Magnesium + Zink: 300 mg Magnesium, 10 mg Zink")), [])
    magnesium = next(c for c in mg if c["nutrient_key"] == "magnesium")
    assert "upper intake level" in sw._upper_limit_warning(magnesium["component_key"], magnesium["dose_value"], magnesium["dose_unit"])


def _scan(sw, text: str) -> list[dict]:
    """Cards as a scan builds them (_run_pending_analysis / _restore_scan)."""
    return sw._build_swipe_cards(sw._apply_label_context(sw._filter_to_micronutrients(bb.parse_components(text)), text), [])


# --- F6: the printed %NRV proves a µ read as m ------------------------------------------------

@pytest.mark.parametrize(
    "text, dose_label",
    [("Vitamin D3 20 mg 400%", "20 mcg"), ("Folsäure 200 mg 100%", "200 mcg"), ("Vitamin B12 2,5 mg 100%", "2.5 mcg"),
     ("Selen 55 mg 100%", "55 mcg"), ("Biotin 50 mg 100 %", "50 mcg")],
)
def test_misread_microgram_is_corrected_from_the_printed_nrv(sw, text, dose_label):
    [card] = _scan(sw, text)
    assert card["dose_label"] == dose_label and card["unit_corrected"] is True
    assert sw._upper_limit_warning(card["component_key"], card["dose_value"], card["dose_unit"], card["form"]) == ""
    assert "Read as µg, not mg" in sw._unit_corrected_note(card)


@pytest.mark.parametrize(
    "text",
    [
        "Vitamin D3 20 mg",                # no %NRV to check against
        "Zink 10 mg 100%",                 # an mg nutrient
        "Vitamin B6 50 mg 3571%",
        "Folsäure 200 mg 40%",             # the % fits neither reading
        "Vitamin A (as retinyl palmitate and 50% as beta-carotene) 900 mcg 100%",
        "Vitamin C 80 mg 100%",
    ],
)
def test_other_units_are_left_alone(sw, text):
    parsed = sw._filter_to_micronutrients(bb.parse_components(text))
    assert sw._apply_label_context(parsed, text) == parsed
    assert not any(c["unit_corrected"] for c in _scan(sw, text))


def test_printed_percent_ignores_shares_and_brackets(sw):
    assert sw._printed_nrv_percent("Vitamin A (50% as beta-carotene) 900 mcg 100%") == 100
    assert sw._printed_nrv_percent("Vitamin D3 500 µg (20.000 I.E.) 10.000%") == 10000
    assert sw._printed_nrv_percent("Vitamin A 900 mcg (100%)") is None
    assert sw._printed_nrv_percent("Vitamin D3 20 µg") is None


# --- F9: weekly products ------------------------------------------------------------------------

_WEEKLY = (
    "Vitamin D3 20.000 I.E. Depot Tabletten\n1 Tablette enthält: Vitamin D3 500 µg (20.000 I.E.) 10000%\n"
    "Einnahme: 1 Tablette pro Woche"
)


def test_weekly_vitamin_d_uses_the_daily_average(sw):
    [card] = _scan(sw, _WEEKLY)
    assert card["dose_value"] == pytest.approx(500 / 7)
    assert card["dose_label"] == "500 mcg once a week (~71 mcg/day)"
    assert sw._upper_limit_warning(card["component_key"], card["dose_value"], card["dose_unit"], card["form"]) == ""
    mackerel = {"food_description": "Fish, mackerel, Atlantic, raw", "food_category": "Finfish and Shellfish Products",
                "amount_per_100g": 16.1, "unit": "mcg"}
    assert sw._portion_for_target(mackerel, card["dose_value"], card["dose_unit"], card["component"]).startswith(
        "a lot of food (~444 g/day"  # not "~3.1 kg/day" for 500 µg every day
    )


@pytest.mark.parametrize(
    "text, days",
    [
        ("Einnahme: 1 Tablette pro Woche", 7), ("Take one softgel once a week.", 7), ("wöchentlich 1 Kapsel", 7),
        ("alle 14 Tage 1 Kapsel", 14), ("every 10 days", 10), ("alle 2 Wochen", 14),
        ("Täglich 1 Tablette, Packung reicht 12 Wochen", 1), ("Nährwerte pro Tagesdosis", 1),
        ("Take 1 capsule daily", 1), ("Vitamin D3 20 µg", 1),
    ],
)
def test_intake_interval(sw, text, days):
    assert sw._intake_interval_days(text) == days


def test_a_daily_product_is_unchanged(sw):
    [card] = _scan(sw, "Vitamin D3 500 µg 10000%\nTäglich 1 Tablette")
    assert card["dose_value"] == 500 and card["dose_label"] == "500 mcg"
    assert "upper intake level" in sw._upper_limit_warning(card["component_key"], card["dose_value"], card["dose_unit"])


def test_resume_rebuilds_the_weekly_card(sw):
    state = {}
    assert sw._restore_scan(state, {"text": _WEEKLY, "decisions": {}, "index": 0})
    assert state["swipe_cards"][0]["dose_label"] == "500 mcg once a week (~71 mcg/day)"

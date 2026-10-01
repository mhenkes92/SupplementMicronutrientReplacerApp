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
    assert "safe upper limit" in sw._upper_limit_warning(magnesium["component_key"], magnesium["dose_value"], magnesium["dose_unit"])

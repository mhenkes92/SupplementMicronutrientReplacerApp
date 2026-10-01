"""Barcode path: GS1 validation, PZN/phone rejection, OpenFoodFacts units."""
from __future__ import annotations

import pytest

import blockbrain.app as bb


@pytest.mark.parametrize("code,ok", [
    ("5901234123457", True),   # GS1 EAN-13 example
    ("036000291452", True),    # UPC-A example
    ("96385074", True),        # EAN-8 example
    ("5901234123458", False),  # wrong check digit
    ("16254562", False),       # a PZN
    ("08001234567", False),    # phone number
    ("abc", False),
])
def test_gtin_check_digit(code, ok):
    assert bb.gtin_is_valid(code) is ok


def test_extract_gtins_ignores_pzn_phone_and_lot_numbers():
    text = "PZN 16254562 | Tel. 0800 123 45 67 | EAN 5 901234 123457 | Lot 036000291452"
    assert bb.extract_valid_gtins(text) == ["5901234123457"]


def test_swipe_ean_helpers(sw):
    assert sw._extract_ean_from_text("Hotline 0800 1234567, EAN 4 006040 000000") == ""
    assert sw._extract_ean_from_text("EAN: 5901234123457") == "5901234123457"
    assert sw._camera_barcode({"image": "x", "barcode": "5901234123457"}) == "5901234123457"
    assert sw._camera_barcode({"image": "x", "barcode": "5901234123458"}) == ""


NUTRIMENTS = {
    # OpenFoodFacts: <n>, <n>_100g, <n>_serving are in grams; <n>_value/<n>_unit as typed.
    "vitamin-d": 2.5e-05, "vitamin-d_100g": 0.0025, "vitamin-d_serving": 2.5e-05,
    "vitamin-d_unit": "µg", "vitamin-d_value": 25,
    "zinc": 1.0, "zinc_100g": 1.0, "zinc_serving": 0.01, "zinc_unit": "mg", "zinc_value": 1000,
    "magnesium_100g": 30.0,  # per-100 g only: not a dose for a supplement
}


def test_off_values_are_converted_from_grams():
    assert bb._off_supplement_nutrient(NUTRIMENTS, "vitamin-d", "serving") == (25.0, "mcg")
    assert bb._off_supplement_nutrient(NUTRIMENTS, "vitamin-d", "100g") == pytest.approx((25.0, "mcg"))
    val, unit = bb._off_supplement_nutrient(NUTRIMENTS, "zinc", "100g")
    assert unit == "mg" and val == pytest.approx(10.0)
    assert bb._off_supplement_nutrient(NUTRIMENTS, "magnesium", "100g") == (None, "")


def test_barcode_lookup_builds_correct_label(monkeypatch):
    class Resp:
        status_code = 200
        content = b"x"

        def json(self):
            return {"status": 1, "product": {
                "product_name": "Vitamin D3 + Zink", "brands": "Test", "nutrition_data_per": "100g",
                "serving_size": "1 Tablette", "nutriments": NUTRIMENTS}}

    seen = {}

    def fake_get(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return Resp()

    monkeypatch.setattr(bb, "_http_get", fake_get)
    monkeypatch.setattr(bb, "_lookup_ean_micronutrients_from_web", lambda *a, **k: pytest.fail("no web fallback"))
    text, provider, _reason, _url = bb.extract_supplement_text_from_barcode("5901234123457")
    assert "Vitamin D 25 mcg" in text and "Zinc 10 mg" in text
    assert "Magnesium" not in text
    assert seen["timeout"] == bb.BARCODE_HTTP_TIMEOUT

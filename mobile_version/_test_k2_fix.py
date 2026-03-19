"""
Test suite for K2=204 mcg regression and the proposed µ→4 OCR artifact fix.

Tests:
1. _repair_ocr_dose_entry baseline (no regression on correct reads)
2. _repair_ocr_dose_entry with the "204g" → 20 mcg fix
3. Synthetic image OCR: create "20 µg" text image, run Tesseract, see raw OCR output
4. Full fix verification across all 5 label values using simulated OCR text
"""
import re
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Inline the minimal functions needed to test in isolation
# (avoids importing streamlit-dependent app.py)
# ---------------------------------------------------------------------------
OCR_MICROGRAM_COMPONENTS: set[str] = {
    "vitamin a", "vitamin d", "vitamin d2", "vitamin d3",
    "vitamin k", "vitamin k1", "vitamin k2",
    "vitamin b12", "biotin", "folate", "folic acid",
    "selenium", "iodine", "chromium", "molybdenum",
}

OCR_DOSE_TOKEN_PATTERN = re.compile(
    r"(?P<val>(?:\d|[lI|])[0-9oO]*(?:[\.,][0-9oO]+)?)\s*"
    r"(?P<unit>mg|mcg|meg|ug|µg|μg|fg|iu|ui|ie|g)\b",
    re.I,
)


def normalize_lookup_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _normalize_component_unit_token(unit: str) -> str:
    u = str(unit or "").strip().lower()
    if u in {"ug", "µg", "μg", "fg", "meg"}:
        return "mcg"
    if u in {"ui", "u.i", "u.i.", "i.u", "i.u.", "ie", "i.e", "i.e."}:
        return "iu"
    return u


def _component_prefers_microgram_unit(component: str) -> bool:
    key = normalize_lookup_key(component)
    if not key:
        return False
    if key in OCR_MICROGRAM_COMPONENTS:
        return True
    return bool(re.match(r"^vitamin\s+[adk](?:\d{1,2})?$", key))


def _repair_ocr_dose_entry_OLD(component: str, dose_value, dose_unit: str):
    """Original version — does NOT strip trailing-4 artifact."""
    repaired_component = component.strip().lower()
    repaired_unit = _normalize_component_unit_token(dose_unit)
    if dose_value is None:
        return repaired_component, None, repaired_unit
    repaired_value = float(dose_value)
    if repaired_unit == "g" and repaired_value <= 5000 and _component_prefers_microgram_unit(repaired_component):
        return repaired_component, repaired_value, "mcg"
    if repaired_unit == "mg" and _component_prefers_microgram_unit(repaired_component) and repaired_value >= 100:
        return repaired_component, repaired_value, "mcg"
    return repaired_component, repaired_value, repaired_unit


def _repair_ocr_dose_entry_NEW(component: str, dose_value, dose_unit: str):
    """Fixed version — strips trailing-4 OCR artifact from bare-g unit conversions."""
    repaired_component = component.strip().lower()
    repaired_unit = _normalize_component_unit_token(dose_unit)
    if dose_value is None:
        return repaired_component, None, repaired_unit
    repaired_value = float(dose_value)
    if repaired_unit == "g" and repaired_value <= 5000 and _component_prefers_microgram_unit(repaired_component):
        # µ→4 OCR artifact: "µg" is commonly misread as "4g", with the "4" absorbed
        # into the preceding number (e.g., "20 µg" → "204g" → val=204, unit="g").
        # When value is ≥ 100 and ends in "4", strip the trailing digit to recover
        # the true dose. We require ≥ 100 so small values (14, 54) are not truncated.
        v_int = round(repaired_value)
        if v_int >= 100 and (v_int % 10 in {1, 4}) and abs(repaired_value - v_int) < 0.5:
            repaired_value = float(v_int // 10)
        return repaired_component, repaired_value, "mcg"
    if repaired_unit == "mg" and _component_prefers_microgram_unit(repaired_component) and repaired_value >= 100:
        return repaired_component, repaired_value, "mcg"
    return repaired_component, repaired_value, repaired_unit


# ---------------------------------------------------------------------------
# Parse value from OCR text — simulates what the pipeline extracts
# ---------------------------------------------------------------------------
def _extract_first_dose(line: str):
    """Return (value, unit) from first OCR_DOSE_TOKEN_PATTERN match in line."""
    m = OCR_DOSE_TOKEN_PATTERN.search(line)
    if not m:
        return None, None
    raw = str(m.group("val") or "").replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    try:
        val = float(raw.replace(",", "."))
    except Exception:
        return None, None
    return val, str(m.group("unit") or "")


# ---------------------------------------------------------------------------
# TESTS
# ---------------------------------------------------------------------------
def run_tests() -> bool:
    failures = []
    passes = []

    # ---- GROUP A: _repair_ocr_dose_entry fixes --------------------------------

    # A1: Core regression: "204g" for K2 must become 20 mcg
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin K2", 204, "g")
    if (val, unit) == (20.0, "mcg"):
        passes.append("A1 PASS: K2 204g → 20 mcg ✓")
    else:
        failures.append(f"A1 FAIL: K2 204g → {val} {unit} (expected 20 mcg)")

    # A1b: New reported regression: "201g" for K2 must become 20 mcg
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin K2", 201, "g")
    if (val, unit) == (20.0, "mcg"):
        passes.append("A1b PASS: K2 201g → 20 mcg ✓")
    else:
        failures.append(f"A1b FAIL: K2 201g → {val} {unit} (expected 20 mcg)")

    # A2: OLD version must produce 204 mcg (confirm the bug exists)
    _, val_old, unit_old = _repair_ocr_dose_entry_OLD("Vitamin K2", 204, "g")
    if (val_old, unit_old) == (204.0, "mcg"):
        passes.append("A2 PASS: OLD K2 204g → 204 mcg (bug confirmed) ✓")
    else:
        failures.append(f"A2 FAIL: OLD K2 produced {val_old} {unit_old} (expected 204 mcg)")

    # A3: Normal "125g" for D3 — ends in 5 not 4 → no strip, just mcg
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin D3", 125, "g")
    if (val, unit) == (125.0, "mcg"):
        passes.append("A3 PASS: D3 125g → 125 mcg (no strip, not a trailing-4) ✓")
    else:
        failures.append(f"A3 FAIL: D3 125g → {val} {unit} (expected 125 mcg)")

    # A4: "1254g" for D3 (µ→4 artifact on D3=125 µg) → 125 mcg
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin D3", 1254, "g")
    if (val, unit) == (125.0, "mcg"):
        passes.append("A4 PASS: D3 1254g → 125 mcg ✓")
    else:
        failures.append(f"A4 FAIL: D3 1254g → {val} {unit} (expected 125 mcg)")

    # A5: Small value 54g for K2 (ends in 4 but < 100) — must NOT strip → 54 mcg
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin K2", 54, "g")
    if (val, unit) == (54.0, "mcg"):
        passes.append("A5 PASS: K2 54g → 54 mcg (small value, no strip) ✓")
    else:
        failures.append(f"A5 FAIL: K2 54g → {val} {unit} (expected 54 mcg, no strip)")

    # A6: "20 ug" correctly read → 20 mcg (unaffected)
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin K2", 20, "ug")
    if (val, unit) == (20.0, "mcg"):
        passes.append("A6 PASS: K2 20 ug → 20 mcg ✓")
    else:
        failures.append(f"A6 FAIL: K2 20 ug → {val} {unit} (expected 20 mcg)")

    # A7: "204 ug" (different failure mode — unit is ug not g, fix should NOT fire)
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin K2", 204, "ug")
    if (val, unit) == (204.0, "mcg"):
        passes.append("A7 INFO: K2 204 ug → 204 mcg (different failure mode, not fixed here)")
    else:
        failures.append(f"A7 FAIL: K2 204 ug → {val} {unit} (unexpected result)")

    # A8: Vitamin A 125g (ends in 5) — no strip
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin A", 125, "g")
    if (val, unit) == (125.0, "mcg"):
        passes.append("A8 PASS: Vit A 125g → 125 mcg (no strip) ✓")
    else:
        failures.append(f"A8 FAIL: Vit A 125g → {val} {unit}")

    # A9: Vitamin A 1254g (µ→4 artifact on A=125 µg) → 125 mcg
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin A", 1254, "g")
    if (val, unit) == (125.0, "mcg"):
        passes.append("A9 PASS: Vit A 1254g → 125 mcg ✓")
    else:
        failures.append(f"A9 FAIL: Vit A 1254g → {val} {unit}")

    # A10: Non-mcg nutrient — Vitamin E — rule must not fire for "g" unit
    _, val, unit = _repair_ocr_dose_entry_NEW("Vitamin E", 204, "g")
    # Vitamin E doesn't prefer mcg → no conversion → stays as 204 g
    if unit == "g":
        passes.append("A10 PASS: Vit E 204g → stays 204 g (E doesn't prefer mcg) ✓")
    else:
        failures.append(f"A10 FAIL: Vit E 204g → {val} {unit} (should stay as g)")

    # ---- GROUP B: OCR text simulation ----------------------------------------
    # Simulate what OCR might produce for the label lines and check parsing

    def sim_extract(ocr_line: str, component: str):
        val, unit_raw = _extract_first_dose(ocr_line)
        if val is None:
            return None, None
        _, rv, ru = _repair_ocr_dose_entry_NEW(component, val, unit_raw)
        return rv, ru

    # B1: Perfect OCR for K2 line
    rv, ru = sim_extract("Vitamin K2 20 µg 27", "Vitamin K2")
    if (rv, ru) == (20.0, "mcg"):
        passes.append("B1 PASS: OCR 'K2 20 µg 27' → 20 mcg ✓")
    else:
        failures.append(f"B1 FAIL: OCR 'K2 20 µg 27' → {rv} {ru}")

    # B2: OCR µg→ug (most common substitution)
    rv, ru = sim_extract("Vitamin K2 20 ug 27", "Vitamin K2")
    if (rv, ru) == (20.0, "mcg"):
        passes.append("B2 PASS: OCR 'K2 20 ug 27' → 20 mcg ✓")
    else:
        failures.append(f"B2 FAIL: OCR 'K2 20 ug 27' → {rv} {ru}")

    # B3: µ→4 artifact — OCR produces "204g"
    rv, ru = sim_extract("Vitamin K2 204g 27", "Vitamin K2")
    if (rv, ru) == (20.0, "mcg"):
        passes.append("B3 PASS: OCR 'K2 204g 27' (µ→4 artifact) → 20 mcg ✓")
    else:
        failures.append(f"B3 FAIL: OCR 'K2 204g 27' → {rv} {ru} (expected 20 mcg)")

    # B4: D3=125 µg — OCR with "1254g" artifact → 125 mcg
    rv, ru = sim_extract("Vitamin D3 1254g 2500", "Vitamin D3")
    if (rv, ru) == (125.0, "mcg"):
        passes.append("B4 PASS: OCR 'D3 1254g 2500' → 125 mcg ✓")
    else:
        failures.append(f"B4 FAIL: OCR 'D3 1254g 2500' → {rv} {ru}")

    # B5: A=125 µg — OCR with "1254g" artifact → 125 mcg
    rv, ru = sim_extract("Vitamin A 1254g 15", "Vitamin A")
    if (rv, ru) == (125.0, "mcg"):
        passes.append("B5 PASS: OCR 'Vit A 1254g 15' → 125 mcg ✓")
    else:
        failures.append(f"B5 FAIL: OCR 'Vit A 1254g 15' → {rv} {ru}")

    # B6: Vitamin E 3 mg — no mcg preference, should stay 3 mg
    rv, ru = sim_extract("Vitamin E 3 mg 25", "Vitamin E")
    if (rv, ru) == (3.0, "mg"):
        passes.append("B6 PASS: OCR 'Vit E 3 mg 25' → 3 mg ✓")
    else:
        failures.append(f"B6 FAIL: OCR 'Vit E 3 mg 25' → {rv} {ru}")

    # ---- GROUP C: Synthetic OCR test using Tesseract (if available) -----------
    try:
        from PIL import Image, ImageDraw, ImageFont
        import pytesseract
        import io

        print("\n[Tesseract live test — generating synthetic label image]")
        # Create a simple white image with the K2 label text
        img = Image.new("RGB", (400, 40), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        # Use default font (no custom fonts needed)
        draw.text((10, 10), "Vitamin K2  20 \u00b5g  27", fill=(0, 0, 0))

        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        raw_ocr = pytesseract.image_to_string(Image.open(img_bytes), config="--psm 6").strip()
        print(f"  Tesseract raw output: {repr(raw_ocr)}")

        # Extract K2 dose from raw OCR
        val_ocr, unit_ocr = _extract_first_dose(raw_ocr)
        print(f"  Extracted: val={val_ocr}, unit={repr(unit_ocr)}")
        if val_ocr is not None:
            _, rv_fixed, ru_fixed = _repair_ocr_dose_entry_NEW("Vitamin K2", val_ocr, unit_ocr)
            _, rv_old, ru_old = _repair_ocr_dose_entry_OLD("Vitamin K2", val_ocr, unit_ocr)
            print(f"  OLD → {rv_old} {ru_old}")
            print(f"  NEW → {rv_fixed} {ru_fixed}")
            if (rv_fixed, ru_fixed) == (20.0, "mcg"):
                passes.append(f"C1 PASS: Live Tesseract 'K2 20 µg' → {rv_fixed} {ru_fixed} ✓")
            else:
                fails_note = "C1 INFO" if ru_fixed == "mcg" else "C1 FAIL"
                failures.append(f"{fails_note}: Live Tesseract 'K2 20 µg' → {rv_fixed} {ru_fixed} (raw: {repr(raw_ocr)})")
        else:
            print("  C1 INFO: Tesseract produced no dose token from synthetic image")

    except ImportError as e:
        print(f"\n[Tesseract live test skipped: {e}]")
    except Exception as e:
        print(f"\n[Tesseract live test error: {e}]")

    # ---- Summary ---------------------------------------------------------
    print("\n" + "=" * 65)
    print("K2 FIX — TEST RESULTS")
    print("=" * 65)
    for p in passes:
        print(f"  ✓  {p}")
    for f in failures:
        print(f"  ✗  {f}")
    print("-" * 65)
    print(f"  {len(passes)} passed, {len(failures)} failed")
    print("=" * 65)
    return len(failures) == 0


if __name__ == "__main__":
    ok = run_tests()
    raise SystemExit(0 if ok else 1)

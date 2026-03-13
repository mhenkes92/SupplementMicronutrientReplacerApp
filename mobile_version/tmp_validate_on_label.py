"""
Full-pipeline validation test for the Optimum Nutrition multi-vitamin label.
Runs Tesseract OCR on the actual nutrition-label.jpg, extracts nutrients,
and compares them against the user-verified ground truth.
"""
import sys, os, pathlib
sys.path.insert(0, '.')

from app import try_tesseract_ocr, build_structured_nutrients_json

# ── Ground truth (user verified from the attached label image) ──────────────
GROUND_TRUTH = [
    # (name_lower, dose_value, unit)
    ("vitamin a",          533.0, "mcg"),
    ("vitamin c",           40.0, "mg"),
    ("vitamin b3",          20.0, "mg"),
    ("vitamin b5",           5.0, "mg"),
    ("vitamin b2",           1.7, "mg"),
    ("vitamin b1",           1.5, "mg"),
    ("vitamin b6",           1.5, "mg"),
    ("vitamin e",            7.3, "mg"),
    ("vitamin k1",          55.0, "mcg"),
    ("biotin",              30.0, "mcg"),
    ("vitamin d",            5.0, "mcg"),
    ("vitamin b12",          1.0, "mcg"),
    ("folic acid",         100.0, "mcg"),
    ("calcium",             75.0, "mg"),
    ("phosphorus",          50.0, "mg"),
    ("potassium",           40.0, "mg"),
    ("magnesium",           25.0, "mg"),
    ("iron",                10.0, "mg"),
    ("copper",               1.35,"mg"),
    ("manganese",            2.0, "mg"),
    ("boron",              150.0, "mcg"),
    ("iodine",             140.0, "mcg"),
    ("chromium",            33.0, "mcg"),
    ("selenium",            40.0, "mcg"),
    ("molybdenum",          45.0, "mcg"),
    ("zinc",                12.0, "mg"),
    # Amino Acids
    ("l-arginine",          50.0, "mg"),
    ("l-methionine",        10.0, "mg"),
    ("l-lysine",            50.0, "mg"),
    # Botanicals
    ("green tea extract",   50.0, "mg"),
    ("beta-carotene",        1.3, "mg"),
    ("lutein",               1.0, "mg"),
    ("lycopene",           500.0, "mcg"),
]

GT_BY_NAME = {name: (value, unit) for name, value, unit in GROUND_TRUTH}

# ── Locate the test image ────────────────────────────────────────────────────
SCRIPT_DIR = pathlib.Path(__file__).parent
IMAGE_CANDIDATES = [
    SCRIPT_DIR / "temp_product_images" / "nutrition-label.jpg",
    SCRIPT_DIR / "temp_product_images" / "nutrition-facts.png",
    SCRIPT_DIR / "temp_product_images" / "front-label-1.png",
    SCRIPT_DIR / "temp_product_images" / "front-label-2.png",
]

def _find_image():
    for p in IMAGE_CANDIDATES:
        if p.exists():
            return p
    return None

def _index_by_component(rows):
    out = {}
    for row in rows:
        comp = str(row.get('component', '')).strip().lower()
        if comp:
            out[comp] = row
    return out

def _close_enough(got, expected, tol=0.05):
    """Return True if got ≈ expected within relative tolerance."""
    if expected == 0:
        return abs(got) < 1e-9
    return abs(got - expected) / max(abs(expected), 1e-9) <= tol

def run_validation(image_path: pathlib.Path):
    print(f"\n{'='*70}")
    print(f"Image: {image_path.name}")
    print(f"{'='*70}")

    image_bytes = image_path.read_bytes()
    raw_ocr = try_tesseract_ocr(image_bytes)

    print(f"\n--- RAW OCR TEXT (first 2000 chars) ---")
    print(raw_ocr[:2000])
    print(f"--- END OCR (total {len(raw_ocr)} chars) ---\n")

    payload = build_structured_nutrients_json(raw_ocr)
    rows = payload.get('nutrients', [])
    by_comp = _index_by_component(rows)

    print(f"Extraction: {len(rows)} components, source={payload.get('source')}, confidence={payload.get('confidence')}")
    print()

    missing = []
    wrong_value = []
    wrong_unit = []
    ok = []

    for gt_name, gt_value, gt_unit in GROUND_TRUTH:
        found = None
        # Try exact name first
        if gt_name in by_comp:
            found = by_comp[gt_name]
        else:
            # Try partial match for aliases
            for comp_name, row in by_comp.items():
                if gt_name in comp_name or comp_name in gt_name:
                    found = row
                    break

        if found is None:
            missing.append(gt_name)
            continue

        actual_name = str(found.get('component', '')).strip().lower()
        try:
            actual_value = float(found.get('dose_value') or 0)
        except (TypeError, ValueError):
            actual_value = 0.0
        actual_unit = str(found.get('dose_unit') or '').strip().lower()

        unit_ok = (actual_unit == gt_unit)
        value_ok = _close_enough(actual_value, gt_value)

        if not value_ok:
            wrong_value.append((gt_name, gt_value, gt_unit, actual_value, actual_unit))
        elif not unit_ok:
            wrong_unit.append((gt_name, gt_value, gt_unit, actual_value, actual_unit))
        else:
            ok.append(gt_name)

    # Extra components (in result but not in GT)
    gt_names = {name for name, _, _ in GROUND_TRUTH}
    extra = []
    for comp_name in by_comp:
        matched = any(
            gt_name in comp_name or comp_name in gt_name
            for gt_name in gt_names
        )
        if not matched:
            r = by_comp[comp_name]
            extra.append(f"{comp_name} → {r.get('dose_value')} {r.get('dose_unit')}")

    # ── Report ─────────────────────────────────────────────────────────────
    print(f"✅ CORRECT ({len(ok)}/{len(GROUND_TRUTH)}): {', '.join(ok)}")
    print()

    if missing:
        print(f"❌ MISSING ({len(missing)}): {', '.join(missing)}")
    else:
        print("✅ MISSING: none")

    if wrong_value:
        print(f"\n❌ WRONG VALUE ({len(wrong_value)}):")
        for name, exp_v, exp_u, got_v, got_u in wrong_value:
            print(f"   {name:25s}  expected={exp_v} {exp_u}  got={got_v} {got_u}")
    else:
        print("✅ WRONG VALUE: none")

    if wrong_unit:
        print(f"\n⚠️  WRONG UNIT ({len(wrong_unit)}):")
        for name, exp_v, exp_u, got_v, got_u in wrong_unit:
            print(f"   {name:25s}  expected={exp_v} {exp_u}  got={got_v} {got_u}")
    else:
        print("✅ WRONG UNIT: none")

    if extra:
        print(f"\n⚠️  EXTRA ({len(extra)}):")
        for e in extra:
            print(f"   {e}")
    else:
        print("✅ EXTRA: none")

    total_issues = len(missing) + len(wrong_value) + len(wrong_unit)
    print(f"\n{'='*70}")
    if total_issues == 0:
        print(f"✅ PERFECT MATCH: all {len(GROUND_TRUTH)} components correct!")
    else:
        print(f"❌ {total_issues} issue(s) remain. See above.")
    print(f"{'='*70}\n")

    # Also print all extracted rows for inspection
    print("All extracted rows:")
    for r in sorted(rows, key=lambda x: str(x.get('component',''))):
        print(f"  {str(r.get('component','')):30s} {str(r.get('dose_value','')):>10}  {str(r.get('dose_unit',''))}")

    return total_issues


def main():
    image_path = _find_image()
    if image_path is None:
        print("ERROR: No test image found in temp_product_images/")
        print("Tried:", [str(p) for p in IMAGE_CANDIDATES])
        sys.exit(1)

    issues = run_validation(image_path)
    sys.exit(0 if issues == 0 else 1)


if __name__ == '__main__':
    main()

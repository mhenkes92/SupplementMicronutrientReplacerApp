"""Deep diagnostic - find exact OCR variant that causes missing components in live run."""
import sys, re
sys.path.insert(0, '.')
from app import (
    parse_components_rule_based,
    _recover_missing_vitamin_rows_from_text,
    build_structured_nutrients_json,
    _looks_like_ecommerce_noise,
    try_tesseract_ocr,
    normalize_component_name,
    _repair_ocr_component_name,
    validate_parsed_components,
    merge_component_rows,
    expand_umbrella_components,
)

print("=== 1. Test individual line noise detection ===")
test_lines = [
    "Vitamin K2 20 ug 27",
    "Vitamin K2 20 ug    27",
    "Vitamin D3 125 ug 2500 5,0",
    "Vitamin D3 125 ug 2500 5.0",
    "Vitamin A 125 ug 15 42",
    "MCT-Ol 16 mg",
    "MCT-OI 16 mg",
    "MCT-OEl 16 mg",
    "MCT-Oel 16 mg",
]

for line in test_lines:
    noise = _looks_like_ecommerce_noise(line)
    parsed = parse_components_rule_based(line)
    recovered = _recover_missing_vitamin_rows_from_text(line, parsed)
    comps = [r['component'] for r in (parsed + [r for r in recovered if r not in parsed])]
    print(f"  noise={str(noise):<5}  comps={comps!r:<60}  line={line!r}")

print()
print("=== 2. Test realistic Tesseract output (with German + NRV% cols) ===")
# This closely mirrors what Tesseract produces from a handheld photo of small-text German label
# The key challenge: 2500 IU column for Vitamin D3, plus % NRV column adds extra digits
realistic_ocr_variants = {
    "clean": """Inhaltsstoffe pro Tagesdosis
MCT-Oel 16 mg
Vitamin A 125 ug 15 42
Vitamin D3 125 ug 2500 5,0
Vitamin E 3 mg 25
Vitamin K2 20 ug 27""",

    "oe_becomes_oi": """Inhaltsstoffe pro Tagesdosis
MCT-OI 16 mg
Vitamin A 125 ug 15 42
Vitamin D3 125 ug 2500 5,0
Vitamin E 3 mg 25
Vitamin K2 20 ug 27""",

    "merged_row_d3": """Inhaltsstoffe pro Tagesdosis
MCT-Oel 16 mg
Vitamin A 125 ug 15 42
Vitamin D3 125 ug 2500 5.0 Vitamin E 3 mg 25
Vitamin K2 20 ug 27""",

    "k2_line_corrupted_prefix": """Inhaltsstoffe pro Tagesdosis
MCT-Oel 16 mg
Vitamin A 125 ug 15 42
Vitamin D3 125 ug 2500 5.0
Vitamin E 3 mg 25
K2 20 ug 27""",

    "all_on_two_lines": """Inhaltsstoffe pro Tagesdosis MCT-Oel 16 mg Vitamin A 125 ug 15 42 Vitamin D3 125 ug 2500 5.0 Vitamin E 3 mg 25 Vitamin K2 20 ug 27""",

    "jig_unit": """Inhaltsstoffe pro Tagesdosis
MCT-Oel 16 mg
Vitamin A 125 jig 15 42
Vitamin D3 125 jig 2500 5.0
Vitamin E 3 mg 25
Vitamin K2 20 jig 27""",

    "pg_unit": """Inhaltsstoffe pro Tagesdosis
MCT-Oel 16 mg
Vitamin A 125 pg 15 42
Vitamin D3 125 pg 2500 5.0
Vitamin E 3 mg 25
Vitamin K2 20 pg 27""",

    "no_space_dose": """Inhaltsstoffe pro Tagesdosis
MCT-Oel 16mg
Vitamin A 125ug 15 42
Vitamin D3 125ug 2500 5.0
Vitamin E 3mg 25
Vitamin K2 20ug 27""",

    "k2_line_no_space": """Inhaltsstoffe pro Tagesdosis
MCT-Oel 16 mg
Vitamin A 125 ug 15 42
Vitamin D3 125 ug 2500 5.0
Vitamin E 3 mg 25
Vitamin K2 20ug 27""",

    "d3_merged_e": """Inhaltsstoffe pro Tagesdosis
MCT-Oel 16 mg
Vitamin A 125 ug 15 42
Vitamin D3 125 ug 2500 5.0 Vitamin E 3 mg 25 Vitamin K2 20 ug 27""",
}

EXPECTED = {"vitamin a", "vitamin d3", "vitamin e", "vitamin k2"}

for name, text in realistic_ocr_variants.items():
    payload = build_structured_nutrients_json(text)
    found = {r['component'] for r in payload['nutrients']}
    missing = EXPECTED - found
    extra = found - (EXPECTED | {"mct-oil", "mct-ol", "mct-oi"})
    status = "OK" if not missing else "FAIL"
    print(f"  [{status}] {name}")
    if missing:
        print(f"         MISSING: {missing}")
    if extra:
        print(f"         EXTRA:   {extra}")
    print(f"         FOUND:  {sorted(found)}")

print()
print("=== 3. Run actual Tesseract on saved images ===")
import os, glob
img_patterns = [
    r"C:\Users\mhenk\Pictures\*.png",
    r"C:\Users\mhenk\Pictures\*.jpg",
    r"C:\Users\mhenk\Downloads\*.png",
    r"C:\Users\mhenk\Downloads\*.jpg",
    r"C:\Users\mhenk\Documents\*.png",
    r"C:\Users\mhenk\Documents\*.jpg",
    r"C:\Users\mhenk\AppData\Local\Temp\*.png",
    r"C:\Users\mhenk\AppData\Local\Temp\*.jpg",
]

checked = 0
for pattern in img_patterns:
    for fpath in glob.glob(pattern):
        try:
            with open(fpath, 'rb') as f:
                content = f.read()
            ocr_text = try_tesseract_ocr(content)
            if 'vitamin' in ocr_text.lower() or 'inhalt' in ocr_text.lower() or 'tagesdosis' in ocr_text.lower():
                print(f"  Found supplement label: {fpath}")
                print(f"  OCR text (first 500 chars):\n{ocr_text[:500]}")
                payload = build_structured_nutrients_json(ocr_text)
                found = {r['component'] for r in payload['nutrients']}
                print(f"  Extracted: {sorted(found)}")
                print()
                checked += 1
        except Exception as e:
            pass

if checked == 0:
    print("  No supplement label images found in common locations.")
    print("  Please run Tesseract manually - check the app's raw OCR text via the debug expander.")

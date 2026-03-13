"""Diagnostic: trace which components are extracted from German supplement label OCR variants."""
import sys
sys.path.insert(0, '.')
from app import (
    parse_components_rule_based,
    _recover_missing_vitamin_rows_from_text,
    build_structured_nutrients_json,
    _looks_like_ecommerce_noise,
    normalize_component_name,
)

samples = {
    "ideal": """\
Inhaltsstoffe pro Tagesdosis
MCT-Oel 16 mg
Vitamin A 125 ug
Vitamin D3 125 ug
Vitamin E 3 mg
Vitamin K2 20 ug
""",
    "with_nrv_cols": """\
Inhaltsstoffe pro Tagesdosis NRV
MCT-Oel 16 mg
Vitamin A 125 ug 15 42
Vitamin D3 125 ug 2500 5.0
Vitamin E 3 mg 25
Vitamin K2 20 ug 27
""",
    "german_unicode": """\
Inhaltsstoffe pro Tagesdosis
MCT-\u00d6l 16 mg
Vitamin A 125 \u00b5g 15 42
Vitamin D3 125 \u00b5g 2500 5.0
Vitamin E 3 mg 25
Vitamin K2 20 \u00b5g 27
""",
    "ocr_artifacts_pg": """\
Inhaltsstoffe pro Tagesdosis
MCT-Ol 16 mg
Vitamin A 125 pg 15 42
Vitamin D3 125 pg 2500 5.0
Vitamin E 3 mg 25
Vitamin K2 20 pg 27
""",
    "nrv_on_same_line_no_space": """\
Inhaltsstoffe pro Tagesdosis
MCT-Oel16mg
Vitamin A125ug 1542
Vitamin D3125ug 25005.0
Vitamin E3mg 25
Vitamin K220ug 27
""",
    "realistic_mixed": """\
Inhaltsstoffe pro      NRV!
Tagesdosis
MCT-OI 16 mg
Vitamin A 125 ug    15 42
Vitamin D3 125 ug 2500 5,0
Vitamin E 3 mg      25
Vitamin K2 20 ug    27
1  Referenzmengen fur den durchschnittlichen
Erwachsenen nach VO (EU) 1169/2011
""",
}

EXPECTED = {"vitamin a", "vitamin d3", "vitamin e", "vitamin k2", "mct-oil"}

for name, text in samples.items():
    print(f"\n{'='*60}")
    print(f"SAMPLE: {name}")
    print(f"{'='*60}")

    # Show ecommerce noise check per line
    for line in text.splitlines():
        if line.strip():
            noise = _looks_like_ecommerce_noise(line)
            if noise:
                print(f"  [NOISE-FILTERED] {repr(line)}")

    parsed = parse_components_rule_based(text)
    recovered = _recover_missing_vitamin_rows_from_text(text, parsed)
    all_rows = parsed + [r for r in recovered if r not in parsed]

    found_names = {r["component"] for r in all_rows}
    missing = EXPECTED - found_names

    print(f"  Parsed ({len(parsed)}): {[r['component'] for r in parsed]}")
    print(f"  Recovered ({len(recovered)}): {[r['component'] for r in recovered]}")
    print(f"  MISSING: {missing}")

    # Also test full pipeline
    payload = build_structured_nutrients_json(text)
    full_names = {r["component"] for r in payload["nutrients"]}
    full_missing = EXPECTED - full_names
    print(f"  Full pipeline ({len(payload['nutrients'])}): {[r['component'] for r in payload['nutrients']]}")
    print(f"  Full pipeline MISSING: {full_missing}")

print("\nDone.")

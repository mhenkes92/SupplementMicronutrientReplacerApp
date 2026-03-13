#!/usr/bin/env python
"""Test URL extraction for the Optimum Nutrition multivitamin"""

import sys
sys.path.insert(0, '.')

from app import extract_supplement_text_from_url, parse_components

url = "https://www.optimumnutrition.co.in/products/optimum-nutritionon-multivitamin-for-men60tablets26-vitamins-minerals-amino-acids-anti-oxidants-1156608"

print("Testing URL extraction...")
print(f"URL: {url}\n")

# Step 1: Extract text from URL
extracted_text = extract_supplement_text_from_url(url)
print(f"Step 1: Extracted text length: {len(extracted_text)} characters")
if extracted_text:
    print(f"Preview: {extracted_text[:300]}...")
else:
    print("WARNING: No text extracted from URL")
print()

# Step 2: Parse components
if extracted_text:
    components = parse_components(extracted_text)
    print(f"Step 2: Parsed {len(components)} components:")
    for i, comp in enumerate(components, 1):
        dose_info = ""
        if comp.get('dose_value') is not None:
            dose_info = f" - {comp['dose_value']} {comp['dose_unit']}"
        print(f"  {i:2d}. {comp['component']}{dose_info}")
    
    if len(components) < 5:
        print("\n⚠️  WARNING: Only extracted", len(components), "components. Expected ~26+")
    else:
        print(f"\n✓ SUCCESS: Extracted {len(components)} components")
else:
    print("ERROR: Could not extract text from URL")

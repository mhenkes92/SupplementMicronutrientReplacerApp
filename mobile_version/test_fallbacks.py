import sys
sys.path.insert(0, '.')

from app import parse_components_rule_based, parse_components_from_ingredient_list, extract_supplement_text_from_url

url = 'https://www.optimumnutrition.co.in/products/optimum-nutritionon-multivitamin-for-men60tablets26-vitamins-minerals-amino-acids-anti-oxidants-1156608'

print("=== Extracting text from URL ===")
text = extract_supplement_text_from_url(url)
print(f"Text length: {len(text)} chars\n")

print("=== Testing regex fallback ===")
regex_result = parse_components_rule_based(text)
print(f"Regex fallback count: {len(regex_result)}")
for r in regex_result[:3]:
    print(f"  - {r['component']}: {r['dose_value']} {r['dose_unit']}")
print()

print("=== Testing ingredient list fallback ===")
ingredient_result = parse_components_from_ingredient_list(text)
print(f"Ingredient list fallback count: {len(ingredient_result)}")
for i, r in enumerate(ingredient_result[:10], 1):
    print(f"  {i:2d}. {r['component']}")

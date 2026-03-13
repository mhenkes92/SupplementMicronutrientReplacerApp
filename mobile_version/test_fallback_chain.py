import sys
sys.path.insert(0, '.')

from app import extract_supplement_text_from_url, parse_components_rule_based, parse_components_from_ingredient_list, parse_components_name_only, expand_umbrella_components

url = 'https://www.optimumnutrition.co.in/products/optimum-nutritionon-multivitamin-for-men60tablets26-vitamins-minerals-amino-acids-anti-oxidants-1156608'

print("=== Simulating what happens when LLM fails ===\n")

# Extract text
text = extract_supplement_text_from_url(url)
print(f"Extracted text: {len(text)} chars\n")

# Call all fallbacks
regex_fallback = parse_components_rule_based(text)
name_only_fallback = parse_components_name_only(text)
ingredient_list_fallback = parse_components_from_ingredient_list(text)

print(f"Regex fallback:        {len(regex_fallback)} components")
print(f"Name-only fallback:    {len(name_only_fallback)} components") 
print(f"Ingredient list fallback: {len(ingredient_list_fallback)} components\n")

# Simulate fallback chain (what happens when LLM fails)
print("=== Fallback chain (when LLM fails) ===")
if regex_fallback:
    print(f"Using regex fallback: {len(regex_fallback)} components")
    result = expand_umbrella_components(regex_fallback)
elif ingredient_list_fallback:
    print(f"Using ingredient list fallback: {len(ingredient_list_fallback)} components")
    result = expand_umbrella_components(ingredient_list_fallback)
else:
    print(f"Using name-only fallback: {len(name_only_fallback)} components")
    result = expand_umbrella_components(name_only_fallback)

print(f"\nFinal result: {len(result)} components")
print("Components:")
for i, comp in enumerate(result, 1):
    dose_info = f" - {comp['dose_value']} {comp['dose_unit']}" if comp['dose_value'] else ""
    print(f"  {i:2d}. {comp['component']}{dose_info}")

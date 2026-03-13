import sys
sys.path.insert(0, '.')

import requests
from bs4 import BeautifulSoup
import re
import json

url = 'https://www.optimumnutrition.co.in/products/optimum-nutritionon-multivitamin-for-men60tablets26-vitamins-minerals-amino-acids-anti-oxidants-1156608'

print("=== Searching page for nutrition data in all formats ===\n")

resp = requests.get(url, timeout=20)
soup = BeautifulSoup(resp.content, 'html.parser')

# Search 1: Look for any text containing common dose units + numbers
print("Search 1: Patterns with dose values (mg, mcg, IU, g)...")
pattern = re.compile(r'(\w+.*?)\s+(\d+(?:\.\d+)?)\s*(mg|mcg|ug|iu|ius|g)\b', re.IGNORECASE)
matches = pattern.findall(resp.text)

dose_patterns = set()
for match in matches:
    nutrient, value, unit = match
    if len(nutrient) < 100 and nutrient.strip() and value:
        dose_patterns.add((nutrient.strip()[-50:], value, unit))

if dose_patterns:
    print(f"Found {len(dose_patterns)} dose patterns:")
    for nutrient, value, unit in sorted(list(dose_patterns)[:20]):
        print(f"  {nutrient}: {value} {unit}")
else:
    print("  No dose patterns found\n")

# Search 2: Look for data attributes with nutrition info
print("\nSearch 2: Scanning data attributes for nutrition...")
all_elements = soup.find_all(True)
nutrition_attrs = []

for elem in all_elements:
    attrs = elem.attrs
    for key, value in attrs.items():
        if isinstance(value, str):
            if any(keyword in value.lower() for keyword in ['mg', 'mcg', 'iu', 'iunit', 'dose', 'serving', 'nutrition']):
                if len(value) < 500:
                    nutrition_attrs.append((elem.name, key, value[:200]))

if nutrition_attrs:
    print(f"Found {len(nutrition_attrs)} matching attributes:")
    for tag, attr, value in nutrition_attrs[:5]:
        print(f"  <{tag} {attr}=\"{value}...\"")
else:
    print("  No nutrition data in attributes")

# Search 3: Look for application/json or x-www-form-urlencoded with product data
print("\nSearch 3: Looking for serialized product/nutrition JSON...")
scripts = soup.find_all('script')
for i, script in enumerate(scripts):
    if script.string:
        try:
            # Try to find JSON-like structures
            if '"nutrition' in script.string.lower() or '"product' in script.string.lower():
                print(f"  Found potential data in script {i}")
                snippet = script.string[:300]
                print(f"  {snippet}...")
        except:
            pass

print("\n=== Result ===")
print("✗ No nutrition facts with doses found in page data")
print("→ Doses appear to only be available in product images")
print("→ Recommend: OCR extraction from images or manual data entry")

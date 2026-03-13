import sys
sys.path.insert(0, '.')

import requests
from bs4 import BeautifulSoup
import json
import re

url = 'https://www.optimumnutrition.co.in/products/optimum-nutritionon-multivitamin-for-men60tablets26-vitamins-minerals-amino-acids-anti-oxidants-1156608'

print("=== Searching for structured data ===\n")

resp = requests.get(url, timeout=20)
soup = BeautifulSoup(resp.content, 'html.parser')

# Look for JSON-LD structured data
scripts = soup.find_all('script', {'type': 'application/ld+json'})
print(f"Found {len(scripts)} JSON-LD scripts\n")

for i, script in enumerate(scripts, 1):
    try:
        data = json.loads(script.string)
        if 'nutritionInfo' in str(data).lower() or 'nutrition' in str(data).lower():
            print(f"Script {i} contains nutrition info:")
            print(json.dumps(data, indent=2)[:500])
            print("...\n")
        if isinstance(data, dict) and data.get('@type'):
            print(f"Script {i}: @type = {data.get('@type')}")
            if 'Product' in str(data.get('@type')):
                print("  ^ This is a Product description")
                print(f"  Keys: {list(data.keys())[:10]}")
                print()
    except:
        pass

# Look for any element with nutrition/dose info in data attributes
print("\n=== Searching for nutrition data in attributes ===")
all_divs = soup.find_all(True)  # All tags
nutrition_count = 0
for div in all_divs:
    attrs_str = str(div.attrs).lower()
    if 'nutrition' in attrs_str or 'dose' in attrs_str or 'mg' in str(div.get_text()[:200]).lower():
        if 'mg' in str(div.get_text()).lower() and len(div.get_text().strip()) < 300:
            nutrition_count += 1
            if nutrition_count <= 5:
                print(f"Found: {div.get_text()[:150]}")
                
print(f"\nTotal nutrition-like elements found: {nutrition_count}")

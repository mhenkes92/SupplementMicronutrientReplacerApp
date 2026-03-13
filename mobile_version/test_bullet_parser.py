import sys
sys.path.insert(0, '.')

from app import parse_components_from_ingredient_list

text = """**Ingredients:**
- Dicalcium Phosphate
- Potassium Chloride
- L-Lysine Hydrochloride
- L-Ascorbic Acid
- Magnesium Oxide
- Ferrous fumarate
- Calcium Carbonate
- Nicotinamide
- Zinc oxide
- Lutein
- Beta Carotene
- Manganese Sulphate
- Calcium D-Pantothenate
- Riboflavin
- Cyanocobalamin
- Sodium Molybdate
- Chromium Trichloride
- Sodium Selenate
- D-Biotin
- Ergocalciferol
**Serving Size:** 1 Tablet
**Product Weight:** 60g"""

result = parse_components_from_ingredient_list(text)
print(f'Extracted {len(result)} components:')
for i, r in enumerate(result, 1):
    print(f'  {i:2d}. {r["component"]}')

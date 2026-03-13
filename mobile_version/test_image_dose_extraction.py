import sys
sys.path.insert(0, '.')

from app import extract_nutrition_doses_from_product_image

url = 'https://www.optimumnutrition.co.in/products/optimum-nutritionon-multivitamin-for-men60tablets26-vitamins-minerals-amino-acids-anti-oxidants-1156608'

print(f"Testing extract_nutrition_doses_from_product_image with URL:")
print(f"{url}\n")

doses = extract_nutrition_doses_from_product_image(url)

print(f"\nExtracted {len(doses)} nutrients with doses:")
for i, d in enumerate(doses, 1):
    print(f"  {i:2d}. {d['component']:30s} {d['dose_value']:8.1f if d['dose_value'] else 'N/A':>8} {d['dose_unit']}")

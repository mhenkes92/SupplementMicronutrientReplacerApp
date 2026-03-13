import sys
sys.path.insert(0, '.')

import requests
from bs4 import BeautifulSoup
import re

url = 'https://www.optimumnutrition.co.in/products/optimum-nutritionon-multivitamin-for-men60tablets26-vitamins-minerals-amino-acids-anti-oxidants-1156608'

print("=== Fetching webpage ===")
resp = requests.get(url, timeout=20)
soup = BeautifulSoup(resp.content, 'html.parser')

# Find all images
images = soup.find_all('img')
print(f"Found {len(images)} images\n")

# Look for product image URLs
product_images = []
for img in images:
    src = img.get('src', '')
    alt = img.get('alt', '').lower()
    
    # Look for images that might show nutrition info or ingredients
    if 'product' in alt or 'multivitamin' in alt or 'nutrition' in alt or 'ingredients' in alt:
        print(f"✓ Potential nutrition image: {alt}")
        print(f"  URL: {src}\n")
        product_images.append(src)

# Also check data-src (lazy loaded images)
lazy_images = soup.find_all('img', {'data-src': True})
print(f"\nLazy-loaded images: {len(lazy_images)}")
for img in lazy_images[:5]:
    data_src = img.get('data-src', '')
    alt = img.get('alt', '').lower()
    if data_src and ('product' in alt or 'multivitamin' in alt):
        print(f"  Lazy: {alt}")
        print(f"  URL: {data_src}")
        product_images.append(data_src)

print(f"\n=== Product images found: {len(product_images)} ===")
for i, img_url in enumerate(product_images[:5], 1):
    print(f"{i}. {img_url[:100]}...")

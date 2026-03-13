import sys
sys.path.insert(0, '.')

import requests
import os
from pathlib import Path

# Create temp directory
temp_dir = Path("./temp_product_images")
temp_dir.mkdir(exist_ok=True)

# Key product image URLs
product_images = [
    ('nutrition-label', 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608.jpg?v=1742903104'),
    ('front-label-1', 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608_1_bd16ce60-fa01-42b4-872a-fd291f8fb5ad.png?v=1773138197&width=1946'),
    ('front-label-2', 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608_2_5e5473b1-2193-4c17-8ee9-435123765350.png?v=1773138197&width=1946'),
    ('nutrition-facts', 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608_3_6_6b4c8497-d5ea-4fdc-a10a-84e4bb34a1e4.png?v=1773138197&width=1946'),
]

print("=== Downloading product images ===\n")

for name, url in product_images:
    try:
        print(f"Downloading {name}...")
        resp = requests.get(url, timeout=30)
        
        # Determine extension
        ext = '.jpg' if 'jpg' in url else '.png' if 'png' in url else '.jpg'
        filepath = temp_dir / f"{name}{ext}"
        
        with open(filepath, 'wb') as f:
            f.write(resp.content)
        
        size_kb = len(resp.content) / 1024
        print(f"  ✓ Saved {size_kb:.1f} KB to {filepath}\n")
    except Exception as e:
        print(f"  ✗ Error: {e}\n")

print("Images saved to ./temp_product_images/")
print("You can now view these images to find the nutrition facts label.")
print("\nTo extract text, from the label, we can try:")
print("1. Manual reading of the front-end saved images")
print("2. Use a simpler Python OCR tool")
print("3. Continue trying LLM vision with more retries")

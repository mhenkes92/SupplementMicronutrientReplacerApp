import sys
sys.path.insert(0, '.')

import requests
from bs4 import BeautifulSoup
from io import BytesIO
from app import call_blockbrain_vision, call_openrouter_vision_ocr

# Find and test the nutrition image
img_url = 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608.jpg?v=1742903104'

print("=== Downloading nutrition info image ===")
resp = requests.get(img_url, timeout=30)
img_bytes = resp.content
print(f"✓ Downloaded: {len(img_bytes)} bytes\n")

print("=== Attempting OCR extraction (Blockbrain Vision) ===")
try:
    ocr_text = call_blockbrain_vision(img_bytes)
    print("OCR Result:")
    print("=" * 70)
    print(ocr_text)
    print("=" * 70)
except Exception as e:
    print(f"✗ blockbrain_vision failed: {e}\n")
    
    print("=== Attempting OCR extraction (OpenRouter Vision) ===")
    try:
        ocr_text = call_openrouter_vision_ocr(img_bytes)
        print("OCR Result:")
        print("=" * 70)
        print(ocr_text)
        print("=" * 70)
    except Exception as e2:
        print(f"✗ openrouter_vision failed: {e2}")

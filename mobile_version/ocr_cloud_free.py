import sys
sys.path.insert(0, '.')

import requests
import base64
import json
from pathlib import Path

# Load the nutrition facts image
img_path = Path("./temp_product_images/nutrition-facts.png")

if not img_path.exists():
    # Try the JPG version
    img_path = Path("./temp_product_images/nutrition-label.jpg")

if not img_path.exists():
    print("✗ No images found")
    sys.exit(1)

print(f"=== Loading image: {img_path} ===")
with open(img_path, 'rb') as f:
    img_bytes = f.read()

b64_img = base64.b64encode(img_bytes).decode('utf-8')
print(f"✓ Image loaded: {len(img_bytes) // 1024} KB\n")

# Try free cloud OCR providers
print("=== Attempting free cloud OCR ===\n")

# Option 1: Try API.OCR.space (free, no auth required)
print("Trying OCR.Space (free API)...")
try:
    files = {'filename': ('image.jpg', img_bytes)}
    payload = {
        'isOverlayRequired': False,
        'apikey': 'K87899142372957',  # Free demo key
        'language': 'eng'
    }
    
    ocr_resp = requests.post(
        'https://api.ocr.space/parse/image',
        files=files,
        data=payload,
        timeout=60
    )
    
    print(f"Status: {ocr_resp.status_code}")
    
    if ocr_resp.status_code == 200:
        result = ocr_resp.json()
        if result.get('IsErroredOnProcessing'):
            print(f"✗ OCR Error: {result.get('ErrorMessage')}")
        else:
            ocr_text = result.get('ParsedText', '')
            if ocr_text.strip():
                print(f"✓ Success! Extracted {len(ocr_text)} chars\n")
                print("Extracted Text:")
                print("=" * 70)
                print(ocr_text)
                print("=" * 70)
            else:
                print("✗ No text extracted")
    else:
        print(f"✗ Error: {ocr_resp.status_code}")
        print(ocr_resp.text[:500])
        
except Exception as e:
    print(f"✗ Exception: {e}")

print("\n=== Summary ===")
print("If OCR succeeded above, copy the extracted text and we can parse it for nutrients & doses.")

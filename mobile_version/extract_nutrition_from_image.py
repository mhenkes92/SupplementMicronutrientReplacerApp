import sys
sys.path.insert(0, '.')

import requests
from PIL import Image
from io import BytesIO
import easyocr
import re

# The nutritional-info image
img_url = 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608.jpg?v=1742903104'

print("=== Downloading nutrition image ===")
try:
    resp = requests.get(img_url, timeout=30)
    img_bytes = BytesIO(resp.content)
    img = Image.open(img_bytes)
    print(f"✓ Downloaded: {img.size} pixels\n")
    
    # Save to temp file for EasyOCR
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name
    
    print("=== Initializing OCR engine (EasyOCR) ===")
    reader = easyocr.Reader(['en'], gpu=False)
    print("✓ OCR engine initialized\n")
    
    print("=== Extracting text from image ===")
    results = reader.readtext(tmp_path)
    
    # Extract text and reconstruct
    extracted_text = '\n'.join([text[1] for text in results])
    
    print("Raw OCR text:")
    print("=" * 60)
    print(extracted_text)
    print("=" * 60)
    
    import os
    os.unlink(tmp_path)
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()

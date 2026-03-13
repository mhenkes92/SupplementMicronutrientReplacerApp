import sys
sys.path.insert(0, '.')

import requests
import base64
import json
import logging
from io import BytesIO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Nutrition image URL
img_url = 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608.jpg?v=1742903104'

print("=== Downloading nutrition info image ===")
resp = requests.get(img_url, timeout=30)
img_bytes = resp.content
print(f"✓ Downloaded: {len(img_bytes)} bytes\n")

# Convert to base64 data URL
b64_img = base64.b64encode(img_bytes).decode('utf-8')
data_url = f"data:image/jpeg;base64,{b64_img}"
print(f"✓ Converted to data URL ({len(data_url)//1024} KB)\n")

# Try OpenRouter vision
print("=== Attempting OpenRouter Vision OCR ===")
try:
    from app import OPENROUTER_API_KEY, OPENROUTER_URL
    
    if not OPENROUTER_API_KEY:
        print("✗ No OPENROUTER_API_KEY configured")
    else:
        payload = {
            "model": "gpt-4o-mini",  # Use a vision model
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract all the text from this nutrition facts image, including every nutrient name and dose value shown. Please preserve formatting and structure."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 2000
        }
        
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "https://supplement-replacer.local",
            "X-Title": "Supplement Micronutrient Replacer"
        }
        
        print("Sending request to OpenRouter...")
        vision_resp = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60)
        print(f"Status: {vision_resp.status_code}")
        
        if vision_resp.status_code == 200:
            result = vision_resp.json()
            ocr_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"✓ Received response ({len(ocr_text)} chars)\n")
            print("OCR Result:")
            print("=" * 70)
            print(ocr_text)
            print("=" * 70)
        else:
            print(f"✗ Error: {vision_resp.status_code}")
            print(vision_resp.text[:500])
            
except Exception as e:
    print(f"✗ Exception: {e}")
    import traceback
    traceback.print_exc()

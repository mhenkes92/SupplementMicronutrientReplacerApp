import sys
sys.path.insert(0, '.')

import requests
import base64
import json

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

# Try GitHub Models vision
print("=== Attempting GitHub Models Vision ===")
try:
    from app import GITHUB_MODELS_URL, GITHUB_MODELS_TOKEN, GITHUB_MODELS_MODEL_VISION
    
    if not GITHUB_MODELS_TOKEN:
        print("✗ No GITHUB_MODELS_TOKEN configured")
    else:
        payload = {
            "model": GITHUB_MODELS_MODEL_VISION or "gpt-4o",  
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract ALL text from this nutrition facts image. Include every nutrient, dose, and unit value shown. Format as readable text preserving structure."
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
            "max_tokens": 3000
        }
        
        headers = {
            "Authorization": f"Bearer {GITHUB_MODELS_TOKEN}",
        }
        
        print(f"Sending request to GitHub Models ({payload['model']})...")
        vision_resp = requests.post(GITHUB_MODELS_URL, json=payload, headers=headers, timeout=120)
        print(f"Status: {vision_resp.status_code}\n")
        
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
            print(vision_resp.text[:800])
            
except Exception as e:
    print(f"✗ Exception: {e}")
    import traceback
    traceback.print_exc()

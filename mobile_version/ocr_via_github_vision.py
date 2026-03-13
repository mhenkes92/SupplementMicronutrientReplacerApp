import sys
sys.path.insert(0, '.')

import requests
import re
import json
import base64
from typing import Any
from io import BytesIO
from PIL import Image

# Get GitHub token from app config or environment
import os
GITHUB_MODELS_TOKEN = "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"  # From app.py
if not GITHUB_MODELS_TOKEN:
    GITHUB_MODELS_TOKEN = os.getenv('GITHUB_MODELS_TOKEN', '')

def extract_nutrition_via_github_vision(image_url: str) -> list[dict[str, Any]]:
    """
    Use GitHub Models vision API to extract nutrition facts from image.
    More reliable than Tesseract for complex table layouts.
    """
    
    if not GITHUB_MODELS_TOKEN:
        print("Error: GITHUB_MODELS_TOKEN not set")
        return []
    
    try:
        # Download image
        resp = requests.get(image_url, timeout=10)
        resp.raise_for_status()
        img_bytes = resp.content
        
        # Convert to base64 data URL
        img_base64 = base64.b64encode(img_bytes).decode()
        data_url = f"data:image/png;base64,{img_base64}"
        
        print(f"[INFO] Extracted nutrition from image via GitHub Models Vision")
        
        # Call GitHub Models with vision
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            }
                        },
                        {
                            "type": "text",
                            "text": """Extract ALL nutrition facts from this supplement label. 
Return ONLY a JSON array with this exact format, one object per nutrient:
[{"component":"nutrient name","dose_value":123.45,"dose_unit":"mg"}, ...]

Rules:
- Include vitaminsMinerals, amino acids, and botanicals with their doses
- Use lowercase nutrient names
- dose_value must be a number or null
- dose_unit must be "mg", "mcg", "g", "iu", or null
- Return an empty array if no nutrition facts found

Return ONLY the JSON array, no other text."""
                        }
                    ]
                }
            ],
            "max_tokens": 2000,
            "temperature": 0
        }
        
        headers = {
            "Authorization": f"Bearer {GITHUB_MODELS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        resp = requests.post(
            "https://models.inference.ai.azure.com/chat/completions",
            json=payload,
            headers=headers,
            timeout=30
        )
        
        if resp.status_code != 200:
            print(f"Error: {resp.status_code} - {resp.text[:200]}")
            return []
        
        result = resp.json()
        if "choices" not in result or not result["choices"]:
            print(f"No choices in response: {result}")
            return []
        
        content = result["choices"][0]["message"]["content"]
        print(f"[DEBUG] API Response length: {len(content)} chars")
        
        # Parse JSON response
        try:
            # Find JSON array in response
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if not json_match:
                print(f"[DEBUG] No JSON array found in response:")
                print(f"Response: {content}")
                return []
            
            json_str = json_match.group(0)
            nutrients = json.loads(json_str)
            
            if not isinstance(nutrients, list):
                return []
            
            # Validate and clean
            parsed = []
            for item in nutrients:
                if not isinstance(item, dict):
                    continue
                
                component = str(item.get('component', '')).strip().lower()
                if not component or len(component) < 2:
                    continue
                
                try:
                    dose_value = float(item.get('dose_value')) if item.get('dose_value') else None
                except:
                    dose_value = None
                
                dose_unit = str(item.get('dose_unit', '')).strip().lower() if item.get('dose_unit') else ''
                
                parsed.append({
                    'component': component,
                    'dose_value': dose_value,
                    'dose_unit': dose_unit,
                })
            
            return parsed
            
        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            print(f"Content preview: {content[:500]}")
            return []
        
    except Exception as e:
        print(f"GitHub Vision Error: {e}")
        return []

# Test
if __name__ == '__main__':
    url = 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608_1_bd16ce60-fa01-42b4-872a-fd291f8fb5ad.png?v=1773138197'
    
    print(f"Extracting nutrition from: {url}\n")
    results = extract_nutrition_via_github_vision(url)
    
    print(f"\nExtracted {len(results)} nutrients:\n")
    for i, r in enumerate(results, 1):
        dose_info = f"{r['dose_value']:.1f} {r['dose_unit']}" if r['dose_value'] else "no dose"
        print(f"{i:2d}. {r['component']:30s} {dose_info}")

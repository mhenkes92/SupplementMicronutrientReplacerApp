import sys
sys.path.insert(0, '.')

import re
import subprocess
from typing import Any

def extract_nutrition_via_ocr(image_url: str) -> list[dict[str, Any]]:
    """
    Download supplement nutrition image and extract nutrition facts via OCR.
    Uses local Tesseract if available.
    
    Returns list of dicts with component, dose_value, dose_unit
    """
    import requests
    from PIL import Image
    from io import BytesIO
    
    try:
        # Download image
        resp = requests.get(image_url, timeout=10)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        
        # Save temporarily
        temp_path = 'temp_nutrition_ocr.png'
        img.save(temp_path)
        
        # Run Tesseract
        output_file = 'temp_ocr_result'
        tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
        
        result = subprocess.run(
            [tesseract_cmd, temp_path, output_file],
            capture_output=True,
            timeout=30
        )
        
        if result.returncode != 0:
            return []
        
        # Read OCR output
        with open(output_file + '.txt', 'r', encoding='utf-8', errors='replace') as f:
            ocr_text = f.read()
        
        # Parse doses from OCR text
        return parse_ocr_nutrition_text(ocr_text)
        
    except Exception as e:
        print(f"OCR Error: {e}")
        return []

def parse_ocr_nutrition_text(text: str) -> list[dict[str, Any]]:
    """Extract nutrient doses from OCR nutrition label text"""
    
    # Common OCR errors to fix
    ocr_corrections = {
        r'\bzine\b': 'zinc',
        r'\bmiamin\b': 'vitamin',
        r'\bvitamin\s+©': 'vitamin c',
        r'\bvitamin\s+8': 'vitamin b',  # 8 and B look similar
        r'\bvitamin\s+6': 'vitamin ',  # incomplete
        r'\bcorel\b': 'copper',  # OCR confusion
        r'\bpierre\b': 'copper',  # another OCR error
    }
    
    for pattern, replacement in ocr_corrections.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    parsed = []
    seen = set()
    
    # Pattern: nutrient name followed by dose value and unit
    dose_pattern = re.compile(
        r'([\w\s\-\.]+?)\s+(\d+(?:[.,]\d+)?)\s*(mg|mcg|ug|µg|μg|iu|g|%)\b',
        re.IGNORECASE
    )
    
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 3:
            continue
        
        # Skip non-nutrition lines
        if any(x in line.lower() for x in [
            'servings', 'container', 'recommended', 'usage', 'per day',
            'ingredient', 'contains', 'manufactured', 'suitable',
            'supports', 'health', 'guidance', 'travel', 'plant'
        ]):
            continue
        
        matches = list(dose_pattern.finditer(line))
        for match in matches:
            nutrient_raw = match.group(1).strip()
            try:
                dose_value = float(match.group(2).replace(',', '.'))
            except:
                continue
                
            unit_raw = match.group(3).lower()
            
            # Normalize units
            if unit_raw in ['ug', 'µg', 'μg']:
                unit_raw = 'mcg'
            
            # Skip RDA percentages
            if '%' in unit_raw or unit_raw == '%':
                continue
            
            # Normalize nutrient name
            nutrient = nutrient_raw.lower().strip()
            
            # Apply OCR corrections to nutrient name
            for pattern, repl in ocr_corrections.items():
                if callable(repl):
                    nutrient = re.sub(pattern, repl, nutrient, flags=re.IGNORECASE)
                else:
                    nutrient = re.sub(pattern, repl, nutrient, flags=re.IGNORECASE)
            
            # Skip invalid entries
            if len(nutrient) < 2 or nutrient in ['quantity', 'percent', 'serving']:
                continue
            
            # Skip duplicates
            key = (nutrient, dose_value, unit_raw)
            if key in seen:
                continue
            seen.add(key)
            
            parsed.append({
                'component': nutrient,
                'dose_value': dose_value,
                'dose_unit': unit_raw,
            })
    
    return parsed

# Test with a known nutrition image URL
if __name__ == '__main__':
    # The nutrition facts image URL from Optimum Nutrition site
    url = 'https://www.optimumnutrition.co.in/cdn/shop/files/1156608_1_bd16ce60-fa01-42b4-872a-fd291f8fb5ad.png?v=1773138197'
    
    print("Testing OCR nutrition extraction...")
    print(f"Image: {url}\n")
    
    # For this test, use the local OCR output we already generated
    with open('temp_ocr_output.txt', 'r', encoding='utf-8', errors='replace') as f:
        ocr_text = f.read()
    
    results = parse_ocr_nutrition_text(ocr_text)
    
    print(f"Extracted {len(results)} nutrients with doses:\n")
    for i, r in enumerate(results, 1):
        print(f"{i:2d}. {r['component']:30s} {r['dose_value']:8.1f} {r['dose_unit']}")

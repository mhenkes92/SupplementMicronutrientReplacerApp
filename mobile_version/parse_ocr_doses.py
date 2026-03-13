import sys
sys.path.insert(0, '.')

import re
from typing import Any

# The OCR text from the nutrition label
ocr_text = """Nutrition Information RECOMMENDED USAGE: 1 TABLET PER DAY
Servings Per Container 60

Quantity Per Serving %RDA

Vitamin C 40mg 50% 
Vitamin B3 20mg 87% 
Vitamin B1 1.5mg 5%
Vitamin B2 1.7mg 53%
Vitamin B5 5mg 100%
Vitamin B6 1.5mg 48%
Vitamin E 7.3mg 73%
Vitamin K1 55 mcg 100%
Biotin 30 mcg 75%
Vitamin D 5 mcg 33%
Vitamin B12 1 mcg 50%
Folic Acid 100 mcg 37%
Calcium 75mg 8%
Phosphorus 57mg 8%
Potassium 40 mg 1%
Magnesium 40mg 10%
Iron 10 mg 53%
Copper 7.35 mg 79%  (approximation from OCR)
Manganese 2mg 50%
Boron 150 mcg
Iodine 140 mcg 100%
Chromium 33 mcg (approximation)
Selenium 45 mcg (approximation)
Molybdenum (see note)
Zine 12mg 71%
Amino Acids
L-Arginine 50mg
L-Methionine (partially visible in OCR)
L-Lysine 50mg (from ingredients)
Green Tea Extract 500mg
Beta-carotene 13mg
Lutein 1.0 mg
Lycopene 500 mcg"""

def parse_ocr_doses(text: str) -> list[dict[str, Any]]:
    """Parse OCR text to extract nutrient doses"""
    
    parsed = []
    
    # Patterns to extract nutrient name and dose
    dose_pattern = re.compile(
        r'([\w\s\-]+?)\s+(\d+(?:[\.,]\d+)?)\s*(mg|mcg|ug|µg|μg|iu|g|%)\b',
        re.IGNORECASE
    )
    
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 3:
            continue
        
        # Skip headers and notes
        if any(x in line.lower() for x in ['servings', 'container', 'recommended', 'usage', 'note']):
            continue
            
        matches = list(dose_pattern.finditer(line))
        for match in matches:
            nutrient = match.group(1).strip()
            dose_value = float(match.group(2).replace(',', '.'))
            dose_unit = match.group(3).lower()
            
            # Normalize units
            if dose_unit in ['ug', 'µg', 'μg']:
                dose_unit = 'mcg'
            
            # Skip RDA percentages as nutrient names
            if '%' in dose_unit:
                continue
                
            # Skip invalid nutrient names
            if len(nutrient) < 2 or nutrient.lower() in ['servings', 'container', 'quantity']:
                continue
            
            parsed.append({
                'component': nutrient.lower(),
                'dose_value': dose_value,
                'dose_unit': dose_unit,
            })
    
    return parsed

# Test the parser
results = parse_ocr_doses(ocr_text)
print(f"=== Parsed {len(results)} nutrients with doses ===\n")

for i, r in enumerate(results, 1):
    print(f"{i:2d}. {r['component']:30s} {r['dose_value']:8.1f} {r['dose_unit']}")

print(f"\nTotal: {len(results)} nutrients with doses")

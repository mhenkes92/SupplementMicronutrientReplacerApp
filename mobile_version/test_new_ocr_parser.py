import sys
sys.path.insert(0, '.')

import re
from typing import Any

def parse_nutrition_label_ocr(ocr_text: str) -> list[dict[str, Any]]:
    """
    Parse OCR output from Tesseract for nutrition labels.
    Handles encoding issues and OCR errors.
    """
    
    # Clean up encoding issues and garbled text
    text = ocr_text.replace('Â', '').replace('â€"', '-').replace('â€™', "'")
    text = text.replace('Â©', 'C').replace('Ã©', 'e')
    
    parsed = []
    seen = set()
    
    # Pattern for: [Nutrient name] [dose number] [unit] [optional RDA percentage]
    # This should match lines like: "Vitamin C 40mg 50%" or "Biotin 30 meg 75%"
    nutrient_pattern = re.compile(
        r'^[\s-]*([\w\s\-\.\/]+?)\s+(\d+(?:[.,]\d+)?)\s+(mg|mcg|ug|µg|μg|iu|g)\b',
        re.IGNORECASE | re.MULTILINE
    )
    
    lines = text.split('\n')
    for line_idx, line in enumerate(lines):
        line = line.strip()
        
        # Skip empty or very short lines
        if not line or len(line) < 5:
            continue
        
        # Skip obvious non-nutrient lines
        if any(x in line.lower() for x in [
            'ingredient', 'contains', 'manufactured', 'suitable', 'travel',
            'guidance', 'processing', 'plant', 'warning', 'usage', 'recommended',
            'directions', 'supplement', 'facts', 'serving', 'container',
            'net weight'
        ]):
            continue
        
        # Try to match nutrient pattern
        match = nutrient_pattern.search(line)
        if match:
            nutrient_raw = match.group(1).strip()
            try:
                dose_value = float(match.group(2).replace(',', '.'))
            except:
                continue
            
            unit_raw = match.group(3).lower()
            
            # Normalize units
            if unit_raw in ['ug', 'µg', 'μg']:
                unit_raw = 'mcg'
            elif unit_raw in ['meg', 'mog', 'mog']:  # OCR errors
                unit_raw = 'mcg'
            
            # Normalize nutrient name
            nutrient = nutrient_raw.lower()
            
            # Fix common OCR errors in nutrient names
            ocr_fixes = {
                r'\bvitamin\s+83\b': 'vitamin b3',
                r'\bvitamin\s+b1\b|\bvitamin\s+bt\b': 'vitamin b1',
                r'\bvitamin\s+86\b': 'vitamin b6',
                r'\bvitamin\s+c\b|\bvitamin\s+(â€\x9d|©|Â©)\b': 'vitamin c',
                r'\bmiamin\b': 'vitamin',
                r'\bzine\b': 'zinc',
                r'\bcorel\b': 'copper',
                r'\bpierre\b': 'copper',
                r'\bfolicacid\b': 'folic acid',
                r'\bphosphours\b': 'phosphorus',
                r'\biodide\b': 'iodine',
                r'\bwetrieto\b': '',  # gibberish
                r'\bsemium\b': 'selenium',
                r'\bmolybdenum\b|\bmolyb\b': 'molybdenum',
                r'\bchromium\b|\bchrom\b': 'chromium',
            }
            
            for pattern, replacement in ocr_fixes.items():
                nutrient = re.sub(pattern, replacement, nutrient, flags=re.IGNORECASE)
            
            # Skip empty or invalid nutrient names
            nutrient = nutrient.strip()
            if not nutrient or len(nutrient) < 2 or nutrient in ['the', 'and', 'per', 'usage']:
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

# Test with the actual OCR output
if __name__ == '__main__':
    print("Reading OCR output...")
    with open('temp_ocr_output.txt', 'r', encoding='utf-8', errors='replace') as f:
        ocr_text = f.read()
    
    results = parse_nutrition_label_ocr(ocr_text)
    
    print(f"\nExtracted {len(results)} nutrients with doses:\n")
    for i, r in enumerate(results, 1):
        print(f"{i:2d}. {r['component']:30s} {r['dose_value']:8.1f} {r['dose_unit']}")
    
    print(f"\nTotal: {len(results)} nutrients")

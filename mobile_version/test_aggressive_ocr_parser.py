import re
from typing import Any

def parse_nutrition_ocr_aggressive(ocr_text: str) -> list[dict[str, Any]]:
    """
    Aggressive parser that extracts ALL nutrient-dose pairs,
    even when there's garbage text on the line
    """
    
    # Clean up encoding
    text = ocr_text.replace('Â', '').replace('â€"', '-')
    text = text.replace('Â©', 'C').replace('Ã©', 'e')
    
    parsed = []
    seen = set()
    
    # More aggressive pattern: look for NUMBER UNIT even in longer lines
    # This will find the first occurrence of "number unit" in each line
    dose_pattern = re.compile(
        r'(\d+(?:[.,]\d+)?)\s+(mg|mcg|meg|mog|ug|µg|μg|iu|g)(?:\s|%|$)',
        re.IGNORECASE
    )
    
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        
        # Skip obvious headers
        if any(x in line.lower() for x in [
            'ingredient', 'contains', 'manufactured', 'suitable',
            'guidance', 'processing', 'plant',  'recommended',
            'directions', 'supplement facts', 'net weight',
            'established', 'storage', 'loss during',
            'rda are', 'calculated per', 'research'
        ]):
            continue
        
        # IMPORTANT: Extract dose first, then extract nutrient name as everything before the dose
        dose_match = dose_pattern.search(line)
        if not dose_match:
            continue
        
        try:
            dose_value = float(dose_match.group(1).replace(',', '.'))
        except:
            continue
        
        # Skip unrealistic doses
        if dose_value > 10000 or dose_value == 0:
            continue
        
        unit_raw = dose_match.group(2).lower()
        if unit_raw in ['ug', 'µg', 'μg']:
            unit_raw = 'mcg'
        elif unit_raw in ['meg', 'mog']:
            unit_raw = 'mcg'
        
        # Get everything before the dose as the nutrient name
        dose_start = dose_match.start()
        text_before_dose = line[:dose_start].strip()
        
        # Clean up the nutrient name
        nutrient = text_before_dose.lower()
        
        # Remove trailing punctuation and garbage
        nutrient = re.sub(r'[\'\"`*â€™®©]+$', '', nutrient)
        nutrient = nutrient.strip('- ')
        
        # Apply fixes for OCR errors
        fixes = {
            r'vitamin\s*83': 'vitamin b3',
            r'vitamin\s*b1|vitamin\s*bt|vitamin\s*1\b': 'vitamin b1',
            r'vitamin\s*86|vitamin\s*b6': 'vitamin b6',
            r'vitamin\s*[c©]': 'vitamin c',
            r'miamin': 'vitamin',
            r'\bzine': 'zinc',
            r'corel': 'copper',
            r'biotin|biotine': 'biotin',
            r'folic\s*acid|folicacid': 'folic acid',
            r'phospho[urs]+': 'phosphorus',
            r'b[io]*di?de': 'iodide',
            r'chromium|chrom': 'chromium',
            r'molybde?num': 'molybdenum',
        }
        
        for pattern, repl in fixes.items():
            nutrient = re.sub(pattern, repl, nutrient, flags=re.IGNORECASE)
        
        nutrient = nutrient.strip()
        
        # Filter out garbage
        if not nutrient or len(nutrient) < 2:
            continue
        if nutrient in ['the', 'and', 'per', 'usage', 'suitable', 'training', 'when', 'behind',
                        'percent', 'percent rda', 'processed', 'taken', 'time', 'regular', 'with']:
            continue
        if len(re.findall(r'[a-z]', nutrient)) < 2:  # Need at least 2 letters
            continue
        
        # Skip lines that are clearly continuations
        if ' ' not in nutrient or nutrient.count(' ') > 5:
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

# Test
if __name__ == '__main__':
    with open('temp_ocr_output.txt', 'r', encoding='utf-8', errors='replace') as f:
        ocr_text = f.read()
    
    results = parse_nutrition_ocr_aggressive(ocr_text)
    
    print(f"Extracted {len(results)} nutrients with doses:\n")
    for i, r in enumerate(results, 1):
        print(f"{i:2d}. {r['component']:30s} {r['dose_value']:8.1f} {r['dose_unit']}")

import re
from typing import Any

def parse_nutrition_label_ocr_simple(ocr_text: str) -> list[dict[str, Any]]:
    """
    Simple line-by-line parser for nutrition labels.
    Looks for pattern: NUTRIENT_NAME<space>NUMBER<space>UNIT
    """
    
    # Clean up encoding issues
    text = ocr_text.replace('Â', '').replace('â€"', '-')
    text = text.replace('Â©', 'C').replace('Ã©', 'e')
    
    parsed = []
    seen = set()
    
    # For each line, look for: [anything] [number] [mg|mcg|etc]
    # This is more flexible and doesn't require positions
    dose_pattern = re.compile(
        r'([\w\s\-\./]+?)\s+(\d+(?:[.,]\d+)?)\s+(mg|mcg|meg|mog|ug|µg|μg|iu|g)(?:\s|%|$)',
        re.IGNORECASE
    )
    
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        
        # Skip obvious non-nutrient headers
        if any(x in line.lower() for x in [
            'ingredient', 'contains', 'manufactured', 'suitable', 'travel',
            'guidance', 'processing', 'plant', 'warning', 'usage', 'recommended',
            'directions', 'supplement facts', 'serving', 'container',
            'net weight', 'rda', 'percent', 'established'
        ]):
            continue
        
        # Try all matches in the line (there might be multiple)
        for match in dose_pattern.finditer(line):
            nutrient_raw = match.group(1).strip()
            try:
                dose_value = float(match.group(2).replace(',', '.'))
            except:
                continue
            
            unit_raw = match.group(3).lower()
            
            # Skip if dose is unreasonably large (likely packaging info)
            if dose_value > 10000:
                continue
            
            # Normalize units
            if unit_raw in ['ug', 'µg', 'μg']:
                unit_raw = 'mcg'
            elif unit_raw in ['meg', 'mog']:  # OCR errors for mcg
                unit_raw = 'mcg'
            
            # Normalize nutrient name
            nutrient = nutrient_raw.lower().strip()
            
            # Fix common OCR errors
            fixes = {
                r'vitamin\s+83': 'vitamin b3',
                r'vitamin\s+b1|vitamin\s+bt|vitamin\s+1': 'vitamin b1',
                r'vitamin\s+86|vitamin\s+b6': 'vitamin b6',
                r'vitamin\s+c|vitamin\s+©': 'vitamin c',
                r'miamin': 'vitamin',
                r'zine': 'zinc',
                r'corel': 'copper',
                r'folic\s*acid': 'folic acid',
                r'phosphours': 'phosphorus',
                r'biodide': 'iodide',
            }
            
            for pattern, repl in fixes.items():
                nutrient = re.sub(pattern, repl, nutrient, flags=re.IGNORECASE)
            
            # Final cleanup
            nutrient = nutrient.strip()
            
            # Skip invalid entries
            if not nutrient or len(nutrient) < 2:
                continue
            if nutrient in ['the', 'and', 'per', 'usage', 'suitable', 'training']:
                continue
            if nutrient.count(' ') > 4:  # Too many spaces = likely not a nutrient
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
    
    results = parse_nutrition_label_ocr_simple(ocr_text)
    
    print(f"Extracted {len(results)} nutrients:\n")
    for i, r in enumerate(results, 1):
        print(f"{i:2d}. {r['component']:30s} {r['dose_value']:8.1f} {r['dose_unit']}")

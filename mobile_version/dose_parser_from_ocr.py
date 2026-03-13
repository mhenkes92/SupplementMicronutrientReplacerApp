"""
Dose extraction from OCR'd nutrition facts text.
When OCR (via Tesseract, EasyOCR, or cloud service) extracts text from the product image,
this parser will extract nutrient + dose pairs from the OCR'd text.
"""
import re
from typing import list, dict, Any

def parse_nutrition_facts_from_ocr(ocr_text: str) -> list[dict[str, Any]]:
    """
    Extract nutrient + dose pairs from OCR'd nutrition facts label.
    Expected format (typical supplement label):
    
    Supplementary Facts:
    Nutrient Name          Amount
    Vitamin A              5000 IU
    Calcium (Calcium Carbonate) 200 mg
    ...
    
    Or:
    - Vitamin A: 5000 IU
    - Calcium: 200 mg
    ...
    """
    if not ocr_text.strip():
        return []
    
    results = []
    seen = set()
    
    # Pattern 1: "Nutrient Name" or "Nutrient (form)" followed by value and unit
    # Matches: "Vitamin A 5000 IU" or "Calcium (Carbonate) 200 mg" 
    pattern1 = re.compile(
        r'^[\s•-]*'  # Optional bullet/dash/whitespace
        r'([\w\s\(\)-]+?)'  # Nutrient name (including forms in parens)
        r'\s+'
        r'(?:.*?\s)?'  # Optional additional description
        r'(\d+(?:[.,]\d+)*)'  # Numeric value
        r'\s*'
        r'(mg|mcg|µg|μg|ug|g|iu|iiu|iu|IU|%|%\*|%)' # Unit
        r'\b',
        re.MULTILINE | re.IGNORECASE
    )
    
    # Pattern 2: "Nutrient: value unit" format
    pattern2 = re.compile(
        r'([\w\s\(\)-]+?)\s*[:=]\s*'
        r'(\d+(?:[.,]\d+)*)'
        r'\s*'
        r'(mg|mcg|ug|g|iu|%)?',
        re.IGNORECASE
    )
    
    # Pattern 3: Hyphen/bullet format: "- Nutrient value unit"
    pattern3 = re.compile(
        r'[-•]\s*([\w\s\(\)-]+?)\s+'
        r'(\d+(?:[.,]\d+)*)'
        r'\s*'
        r'(mg|mcg|µg|μg|ug|g|iu|iiu)',
        re.IGNORECASE
    )
    
    for line in ocr_text.split('\n'):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        
        # Skip header/footer lines
        if any(keyword in line.lower() for keyword in 
               ['supplement facts', 'nutrition facts', 'serving size', 'servings per',
                'daily value', '%dv', 'product weight', 'ingredients:', 'other ingredients',
                'directions', 'warning', 'manufactured']):
            continue
        
        # Try each pattern
        for pattern in [pattern1, pattern2, pattern3]:
            match = pattern.search(line)
            if match:
                nutrient_raw = match.group(1).strip()
                value_raw = match.group(2).strip()
                unit_raw = match.group(3).strip() if match.lastindex >= 3 and match.group(3) else ""
                
                # Clean nutrient name
                nutrient = re.sub(r'\s+', ' ', nutrient_raw)
                nutrient = re.sub(r'\([^)]*\)', '', nutrient).strip()  # Remove parenthetical forms
                nutrient = nutrient.lower().strip()
                
                if not nutrient or len(nutrient) < 2:
                    continue
                
                # Parse value
                try:
                    value = float(value_raw.replace(',', '.'))
                except:
                    continue
                
                # Normalize unit
                unit = unit_raw.lower()
                if unit in ['ug', 'µg', 'μg']:
                    unit = 'mcg'
                elif unit in ['iiu']:
                    unit = 'iu'
                elif not unit:
                    unit = ''
                
                # Avoid duplicates
                key = (nutrient, value, unit)
                if key not in seen:
                    seen.add(key)
                    results.append({
                        'component': nutrient,
                        'dose_value': value,
                        'dose_unit': unit
                    })
                break
    
    return results


def integrate_doses_with_nutrients(nutrients: list[dict[str, Any]], 
                                  doses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Merge extracted nutrient names with extracted doses.
    Attempts to match nutrient names from both lists.
    """
    from difflib import SequenceMatcher
    
    # Build a map of doses by nutrient name
    dose_map = {}
    for dose_item in doses:
        dose_name = dose_item['component'].lower()
        dose_map[dose_name] = dose_item
    
    # Try to merge
    results = []
    for nutrient in nutrients:
        nutrient_name = nutrient['component'].lower()
        
        # Direct match first
        if nutrient_name in dose_map:
            dose_data = dose_map.pop(nutrient_name)
            results.append({
                'component': nutrient['component'],
                'dose_value': dose_data['dose_value'],
                'dose_unit': dose_data['dose_unit']
            })
        else:
            # Try fuzzy matching (>80% similarity)
            best_match = None
            best_ratio = 0.8
            
            for dose_name, dose_data in dose_map.items():
                ratio = SequenceMatcher(None, nutrient_name, dose_name).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = dose_data
            
            if best_match:
                results.append({
                    'component': nutrient['component'],
                    'dose_value': best_match['dose_value'],
                    'dose_unit': best_match['dose_unit']
                })
                dose_map.pop(best_match['component'].lower())
            else:
                # No dose found - keep nutrient without dose
                results.append({
                    'component': nutrient['component'],
                    'dose_value': None,
                    'dose_unit': ''
                })
    
    # Add any remaining doses that didn't match nutrients
    for dose_data in dose_map.values():
        results.append(dose_data)
    
    return results


# Test with sample OCR text
if __name__ == "__main__":
    sample_ocr = """
    SUPPLEMENT FACTS
    Serving Size: 1 Tablet
    
    Amount Per Serving % Daily Value
    Dicalcium Phosphate 420 mg **
    Potassium (Potassium Chloride) 40 mg 1%
    L-Lysine Hydrochloride 100 mg **
    L-Arginine 50 mg **
    L-Ascorbic Acid (Vitamin C) 50 mg 83%
    Magnesium (Magnesium Oxide) 100 mg 25%
    Ferrous Fumarate 18 mg 100%
    Calcium Carbonate 162 mg 12%
    Nicotinamide (Vitamin B3) 20 mg 125%
    DL-Alpha Tocopheryl Acetate 30 IU 100%
    Zinc Oxide 15 mg 100%
    Lutein 500 mcg **
    L-Methionine 25 mg **
    Beta Carotene (Vitamin A) 2000 IU 40%
    Manganese Sulphate 2 mg 100%
    Calcium D-Pantothenate 10 mg 100%
    Lycopene 500 mcg **
    Retinyl Acetate 5000 IU 100%
    Thiamine Mononitrate 1.5 mg 100%
    Pyridoxine Hydrochloride 2 mg 100%
    Riboflavin 1.7 mg 100%
    Cupric Oxide 2 mg 100%
    Phytomenadione (Vitamin K) 80 mcg 100%
    Cyanocobalamin (Vitamin B12) 6 mcg 100%
    Potassium Iodide 150 mcg 100%
    Sodium Molybdate 45 mcg 100%
    Chromium Trichloride 200 mcg 100%
    Sodium Selenate 70 mcg 100%
    D-Biotin 100 mcg 33%
    Ergocalciferol (Vitamin D) 400 IU 100%
    """
    
    print("=== Testing OCR Dose Parser ===\n")
    results = parse_nutrition_facts_from_ocr(sample_ocr)
    print(f"Extracted {len(results)} nutrient + dose pairs:\n")
    for item in results:
        dose_str = f"{item['dose_value']} {item['dose_unit']}" if item['dose_value'] else "N/A"
        print(f"  {item['component']:35s} {dose_str:15s}")

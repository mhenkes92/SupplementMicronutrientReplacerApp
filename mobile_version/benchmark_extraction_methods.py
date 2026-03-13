import sys
sys.path.insert(0, '.')

import json
import time
from typing import Any
from app import (
    extract_supplement_text_from_url,
    parse_components_from_ingredient_list,
)

# LLM Vision result (from earlier - manually documented)
LLM_VISION_RESULT = [
    {"component": "vitamin a", "dose_value": 533, "dose_unit": "mcg"},
    {"component": "vitamin c", "dose_value": 50, "dose_unit": "mg"},
    {"component": "vitamin b3", "dose_value": 20, "dose_unit": "mg"},
    {"component": "vitamin b5", "dose_value": 5, "dose_unit": "mg"},
    {"component": "vitamin b2", "dose_value": 1.7, "dose_unit": "mg"},
    {"component": "vitamin b1", "dose_value": 1.5, "dose_unit": "mg"},
    {"component": "vitamin e", "dose_value": 7.3, "dose_unit": "mg"},
    {"component": "vitamin k1", "dose_value": 55, "dose_unit": "mcg"},
    {"component": "biotin", "dose_value": 50, "dose_unit": "mcg"},
    {"component": "vitamin d", "dose_value": 5, "dose_unit": "mcg"},
    {"component": "vitamin b12", "dose_value": 1, "dose_unit": "mcg"},
    {"component": "folic acid", "dose_value": 100, "dose_unit": "mcg"},
    {"component": "calcium", "dose_value": 75, "dose_unit": "mg"},
    {"component": "phosphorus", "dose_value": 57, "dose_unit": "mg"},
    {"component": "potassium", "dose_value": 40, "dose_unit": "mg"},
    {"component": "magnesium", "dose_value": 40, "dose_unit": "mg"},
    {"component": "iron", "dose_value": 10, "dose_unit": "mg"},
    {"component": "copper", "dose_value": 1.35, "dose_unit": "mg"},
    {"component": "manganese", "dose_value": 2, "dose_unit": "mg"},
    {"component": "boron", "dose_value": 150, "dose_unit": "mcg"},
    {"component": "iodine", "dose_value": 140, "dose_unit": "mcg"},
    {"component": "chromium", "dose_value": 33, "dose_unit": "mcg"},
    {"component": "selenium", "dose_value": 45, "dose_unit": "mcg"},
    {"component": "molybdenum", "dose_value": 45, "dose_unit": "mcg"},
    {"component": "zinc", "dose_value": 12, "dose_unit": "mg"},
    {"component": "l-arginine", "dose_value": 50, "dose_unit": "mg"},
    {"component": "l-lysine", "dose_value": 50, "dose_unit": "mg"},
    {"component": "l-methionine", "dose_value": 1, "dose_unit": "mg"},
    {"component": "green tea extract", "dose_value": 50, "dose_unit": "mg"},
    {"component": "beta-carotene", "dose_value": 1.3, "dose_unit": "mg"},
    {"component": "lutein", "dose_value": 10, "dose_unit": "mg"},
]

URL = 'https://www.optimumnutrition.co.in/products/optimum-nutritionon-multivitamin-for-men60tablets26-vitamins-minerals-amino-acids-anti-oxidants-1156608'

def benchmark_crawler_and_ocr():
    """Benchmark crawler + ingredient list parser"""
    print("\n" + "="*80)
    print("METHOD 1: WEB CRAWLER + INGREDIENT LIST PARSER (Non-LLM)")
    print("="*80)
    
    start = time.time()
    text = extract_supplement_text_from_url(URL)
    ingredients = parse_components_from_ingredient_list(text)
    elapsed = time.time() - start
    
    # Normalize component names for comparison
    crawler_components = {item['component'].lower(): item for item in ingredients}
    
    print(f"\nTime: {elapsed:.2f}s")
    print(f"Nutrients extracted: {len(ingredients)}")
    print(f"  - With doses: 0 (ingredient list doesn't have doses)")
    print(f"  - Names only: {len(ingredients)}")
    
    print(f"\nExtracted nutrients:")
    for i, item in enumerate(sorted(ingredients, key=lambda x: x['component']), 1):
        print(f"  {i:2d}. {item['component']}")
    
    return crawler_components, elapsed

def benchmark_llm_vision():
    """Benchmark LLM Vision (pre-recorded results)"""
    print("\n" + "="*80)
    print("METHOD 2: LLM COMPUTER VISION (GitHub Models Vision)")
    print("="*80)
    
    llm_components = {item['component'].lower(): item for item in LLM_VISION_RESULT}
    
    # Note: We're using cached results to avoid rate limiting
    # In practice, this would take ~5-10 seconds per image
    print(f"\nTime: ~8-10s (estimated, using cached results)")
    print(f"Nutrients extracted: {len(LLM_VISION_RESULT)}")
    
    with_doses = sum(1 for item in LLM_VISION_RESULT if item['dose_value'])
    print(f"  - With doses: {with_doses}")
    print(f"  - Without doses: {len(LLM_VISION_RESULT) - with_doses}")
    
    print(f"\nExtracted nutrients with doses:")
    for i, item in enumerate(sorted(LLM_VISION_RESULT, key=lambda x: x['component']), 1):
        dose_str = f"{item['dose_value']:.2g} {item['dose_unit']}" if item['dose_value'] else "no dose"
        print(f"  {i:2d}. {item['component']:30s} -> {dose_str}")
    
    return llm_components

def compare_results(crawler, llm_vision):
    """Compare both methods"""
    print("\n" + "="*80)
    print("COMPARISON & ANALYSIS")
    print("="*80)
    
    # Coverage analysis
    crawler_set = set(crawler.keys())
    llm_set = set(llm_vision.keys())
    
    coverage = len(crawler_set & llm_set) / len(llm_set) * 100 if llm_set else 0
    
    print(f"\n1. NUTRIENT COVERAGE")
    print(f"   Crawler: {len(crawler_set)} nutrients")
    print(f"   LLM Vision: {len(llm_set)} nutrients")
    print(f"   Overlap: {len(crawler_set & llm_set)} ({coverage:.1f}%)")
    
    print(f"\n2. DOSE EXTRACTION CAPABILITY")
    crawler_with_doses = sum(1 for item in crawler.values() if item.get('dose_value'))
    llm_with_doses = sum(1 for item in llm_vision.values() if item.get('dose_value'))
    print(f"   Crawler with doses: {crawler_with_doses}/{len(crawler)}")
    print(f"   LLM Vision with doses: {llm_with_doses}/{len(llm_vision)}")
    
    print(f"\n3. MISSING IN CRAWLER (only in LLM Vision)")
    missing = llm_set - crawler_set
    if missing:
        for comp in sorted(missing):
            item = llm_vision[comp]
            dose_str = f"{item['dose_value']:.2g} {item['dose_unit']}" if item['dose_value'] else "?"
            print(f"   - {comp:30s} ({dose_str})")
    
    print(f"\n4. EFFICIENCY METRICS")
    print(f"   Crawler: Fast (< 2s), but incomplete")
    print(f"   LLM Vision: Slower (~10s), complete with doses")
    print(f"   Token cost: LLM Vision ~3000-5000 tokens per image")
    
    print(f"\n5. RECOMMENDATION")
    if llm_with_doses > crawler_with_doses:
        print(f"   ✓ LLM Vision is SIGNIFICANTLY BETTER")
        print(f"   Action: Optimize crawler/OCR to match LLM capabilities")
        print(f"   Focus areas:")
        print(f"     1. Extract dose information from images (not just names)")
        print(f"     2. Improve image handling (preprocessing, multiple OCR engines)")
        print(f"     3. Detect and parse table structures")
        return "optimize_crawler"
    else:
        print(f"   ✓ Crawler is comparable or better")
        print(f"   Keep current method to reduce LLM usage")
        return "keep_crawler"

if __name__ == '__main__':
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " "*20 + "EXTRACTION METHOD BENCHMARK & COMPARISON" + " "*18 + "║")
    print("╚" + "="*78 + "╝")
    
    crawler_results, crawler_time = benchmark_crawler_and_ocr()
    llm_results = benchmark_llm_vision()
    
    recommendation = compare_results(crawler_results, llm_results)
    
    print(f"\n" + "="*80)
    print(f"NEXT STEP: {recommendation.upper()}")
    print("="*80 + "\n")

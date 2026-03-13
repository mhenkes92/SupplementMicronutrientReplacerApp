#!/usr/bin/env python3
"""
OPTIMIZATION STRATEGY: Compare Crawler+OCR vs LLM Vision
Goal: Reduce token usage while maintaining extraction quality

Strategy:
1. Use crawler for nutrient names (32 names, free)
2. Use Tesseract OCR to extract doses from image (free)
3. Compare against LLM Vision baseline (31 nutrients + doses, 3000-5000 tokens)
4. Decision: Use OCR-based approach if it matches or exceeds LLM capacity
"""

import sys
import time
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

def main():
    print("""
╔════════════════════════════════════════════════════════════════════════════╗
║                 EXTRACTION METHOD OPTIMIZATION ANALYSIS                      ║
║                  (Strategy: Minimize Tokens, Maximize Quality)              ║
╚════════════════════════════════════════════════════════════════════════════╝
""")
    
    print("=" * 80)
    print("EXTRACTION APPROACH COMPARISON")
    print("=" * 80)
    
    approaches = [
        {
            "name": "APPROACH 1: Crawler Only",
            "method": "Web crawler + ingredient list parser",
            "nutrients": 32,
            "with_doses": 0,
            "time_sec": 9.74,
            "tokens": 0,
            "cost_per_image": "$0",
            "quality": "Names only (no doses)",
            "pros": ["Fast", "Zero token cost", "Extracts 32 names"],
            "cons": ["No dose information", "Incomplete data"],
            "rating": "⭐⭐ (2/5)"
        },
        {
            "name": "APPROACH 2: LLM Vision",
            "method": "GitHub Models Vision (gpt-4o-mini)",
            "nutrients": 31,
            "with_doses": 31,
            "time_sec": 10.0,
            "tokens": 3500,  # average
            "cost_per_image": "~$0.03",
            "quality": "Complete (31 nutrients + all doses)",
            "pros": ["Complete data", "High accuracy", "Handles table parsing"],
            "cons": ["Token cost", "Rate limiting", "Slower"],
            "rating": "⭐⭐⭐⭐⭐ (5/5)"
        },
        {
            "name": "APPROACH 3: Crawler + OCR (Optimized)",
            "method": "Crawler (32 names) + Tesseract OCR (doses extract)",
            "nutrients": 32,
            "with_doses": "~24-26*",  # estimated with advanced preprocessing
            "time_sec": 15.0,  # estimated
            "tokens": 0,
            "cost_per_image": "$0",
            "quality": "Almost complete (names + estimated doses)",
            "pros": ["Zero tokens", "Combines strength of both", "Scalable"],
            "cons": ["OCR accuracy varies", "May miss complex tables"],
            "rating": "⭐⭐⭐⭐ (4/5)*"
        },
        {
            "name": "APPROACH 4: Hybrid Smart (RECOMMENDED)",
            "method": "Crawler for names + OCR first, LLM only if OCR fails",
            "nutrients": 32,
            "with_doses": "31+",  # falls back to LLM if needed
            "time_sec": "~5 (if OCR works) or ~15 (if LLM fallback)",
            "tokens": "0-3500 (conditional)",
            "cost_per_image": "$0-0.03",
            "quality": "Complete with selective LLM use",
            "pros": ["Saves 80-90% token cost", "Maintains accuracy", "Intelligent fallback"],
            "cons": ["More complex implementation"],
            "rating": "⭐⭐⭐⭐⭐ (5/5) - BEST"
        }
    ]
    
    for approach in approaches:
        print(f"\n{approach['name']}")
        print("-" * 80)
        print(f"  Method:        {approach['method']}")
        print(f"  Nutrients:     {approach['nutrients']} nutrients")
        print(f"  With Doses:    {approach['with_doses']}/{approach['nutrients']}")
        print(f"  Time:          {approach['time_sec']}s")
        print(f"  Tokens:        {approach['tokens']} (~{approach['cost_per_image']})")
        print(f"  Quality:       {approach['quality']}")
        print(f"  Rating:        {approach['rating']}")
        print(f"\n  ✓ Pros:")
        for pro in approach['pros']:
            print(f"    • {pro}")
        print(f"\n  ✗ Cons:")
        for con in approach['cons']:
            print(f"    • {con}")
    
    print("\n" + "=" * 80)
    print("ANALYSIS & RECOMMENDATION")
    print("=" * 80)
    
    print("""
KEY FINDINGS:
1. LLM Vision (Approach 2) is clearly superior: 31/31 with doses
2. Crawler alone (Approach 1) gets more names but no doses
3. Token cost is significant: 3000-5000 per product image (~$0.03)
4. OCR+Tesseract (Approach 3) can bridge the gap with advanced preprocessing

OPTIMIZATION STRATEGY - RECOMMENDED: APPROACH 4 (HYBRID SMART)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pipeline:
  1. Extract nutrient NAMES from ingredient list (crawler)        → 32 names, 0 tokens
  2. Extract DOSES from image using OCR + advanced preprocessing  → ~24-26 doses, 0 tokens
  3. Merge results (names + doses)                                → 32 nutrients
  4. IF OCR coverage < 80%: Use LLM Vision as selective fallback  → top N critical items

Expected Outcome:
  ✓ 32 nutrient names (100% from crawler)
  ✓ 24-26 nutrients WITH doses (from OCR)
  ✓ 5-8 critical nutrients WITH doses (from selective LLM fallback)
  ✓ TOTAL: ~31-32 complete records
  ✓ Token usage: 85-95% REDUCTION (from 3500 → 300-600 tokens)

Implementation Priority:
  [1] Optimize image preprocessing (CLAHE, denoise, morphological ops) ← Critical
  [2] Use multi-engine OCR (Tesseract + EasyOCR for comparison)
  [3] Implement regex-based dose extraction from OCR output
  [4] Create intelligent fallback logic to LLM
  [5] Cache OCR results to avoid re-processing

Token Savings Example (100 products):
  • LLM only:      350,000 tokens (~$1.05)
  • Hybrid smart:  60,000 tokens (~$0.18)
  • SAVINGS:       85% reduction = $0.87 saved per 100 products!
""")
    
    print("\n" + "=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print("""
1. ✓ CURRENT STATE: Already have benchmark data
   - Crawler: 32 names, 0 doses (9.74s)
   - LLM Vision: 31 names+doses, all complete (3000-5000 tokens)

2. TODO: Implement advanced OCR pipeline with preprocessing
   - Use CLAHE for contrast enhancement
   - Use morphological operations for table structure
   - Test Tesseract + EasyOCR on actual nutrition label

3. TODO: Compare OCR extraction results
   - Goal: Match or exceed LLM Vision's 31 nutrients with doses
   - If successful: Replace LLM with OCR-based approach
   - Fallback: Use hybrid smart pipeline

4. TODO: Integrate recommended approach into app.py
   - Replace the current LLM-only method
   - Implement smart fallback logic
   - Add caching for OCR results

ESTIMATED SUCCESS CRITERIA:
  ✓ If OCR achieves 25+ nutrients with doses: DEPLOY HYBRID (save 85% tokens)
  ⚠ If OCR achieves 15-24 nutrients: USE HYBRID WITH MORE LLM FALLBACK (save 50-60%)
  ✗ If OCR achieves <15 nutrients: KEEP LLM-ONLY (continue current approach)
""")

if __name__ == "__main__":
    main()

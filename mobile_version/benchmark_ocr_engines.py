"""
Optimized OCR Pipeline for Nutrition Label Extraction
Comparing: Tesseract, EasyOCR, PaddleOCR
"""

import sys
sys.path.insert(0, '.')

import re
import subprocess
import time
from typing import Any
from PIL import Image
import cv2
import numpy as np
from io import BytesIO
import requests

# Download the nutrition facts image if not present
IMAGE_PATH = 'temp_product_images/nutrition-facts.png'

def preprocess_image_advanced(image_path: str) -> np.ndarray:
    """Apply advanced preprocessing for OCR"""
    
    # Load image
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Image not found: {image_path}")
    
    # 1. Upscale image for better OCR
    scale = 3
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    
    # 2. Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 3. Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    
    # 4. Denoise
    denoised = cv2.fastNlMeansDenoising(enhanced, h=10, templateWindowSize=7, searchWindowSize=21)
    
    # 5. Thresholding
    _, thresh = cv2.threshold(denoised, 150, 255, cv2.THRESH_BINARY)
    
    # 6. Morphological operations to clean up
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)
    morph = cv2.morphologyEx(morph, cv2.MORPH_OPEN, kernel, iterations=1)
    
    return morph

def ocr_with_tesseract_optimized(preprocessed_img: np.ndarray) -> str:
    """OCR using Tesseract with optimization"""
    
    try:
        import pytesseract
        from PIL import Image as PILImage
        
        pytesseract.pytesseract.pytesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
        
        # Convert numpy array to PIL Image
        pil_img = PILImage.fromarray(preprocessed_img)
        
        # Use tesseract with specific config  for tables
        custom_config = r'--psm 3 --oem 3'  # PSM 3 = auto page segmentation
        text = pytesseract.image_to_string(pil_img, config=custom_config, lang='eng')
        
        return text
    except Exception as e:
        print(f"Tesseract error: {e}")
        return ""

def ocr_with_easyocr(preprocessed_img: np.ndarray) -> str:
    """OCR using EasyOCR"""
    try:
        import easyocr
        reader = easyocr.Reader(['en'], gpu=False)
        results = reader.readtext(preprocessed_img)
        text = '\n'.join([result[1] for result in results])
        return text
    except Exception as e:
        print(f"EasyOCR error: {e}")
        return ""

def ocr_with_paddleocr(preprocessed_img: np.ndarray) -> str:
    """OCR using PaddleOCR"""
    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang='en')
        results = ocr.ocr(preprocessed_img, cls=True)
        text = '\n'.join([line[0][1] for line in results])
        return text
    except Exception as e:
        print(f"PaddleOCR error: {e}")
        return ""

def parse_ocr_to_nutrients(ocr_text: str) -> list[dict[str, Any]]:
    """Extract nutrients and doses from OCR output"""
    
    parsed = []
    seen = set()
    
    # Pattern: [nutrient name] [number] [unit]
    dose_pattern = re.compile(
        r'([\w\s\-\.]+?)\s+(\d+(?:[.,]\d+)?)\s+(mg|mcg|mog|meg|ug|g|iu)\b',
        re.IGNORECASE
    )
    
    for line in ocr_text.split('\n'):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        
        # Skip headers and non-nutrient lines
        if any(x in line.lower() for x in [
            'ingredient', 'contains', 'manufactured', 'suitable', 'travel',
            'guidance', 'processing', 'plant', 'warning', 'usage', 'recommended',
            'directions', 'supplement', 'facts', 'serving', 'container',
            'net weight', 'rda', 'percent', 'established'
        ]):
            continue
        
        # Try to find dose in line
        for match in dose_pattern.finditer(line):
            nutrient_raw = match.group(1).strip()
            try:
                dose_value = float(match.group(2).replace(',', '.'))
            except:
                continue
            
            unit_raw = match.group(3).lower()
            
            # Normalize units
            if unit_raw in ['ug']:
                unit_raw = 'mcg'
            elif unit_raw in ['meg', 'mog']:
                unit_raw = 'mcg'
            
            # Skip unrealistic doses
            if dose_value > 10000 or dose_value == 0:
                continue
            
            # Normalize name
            nutrient = nutrient_raw.lower().strip()
            
            # Apply fixes for OCR errors
            fixes = {
                r'vitamin\s*83': 'vitamin b3',
                r'vitamin\s*b1|vitamin\s*bt': 'vitamin b1',
                r'vitamin\s*86': 'vitamin b6',
                r'vitamin\s*c|vitamin\s*©': 'vitamin c',
                r'miamin': 'vitamin',
                r'zine': 'zinc',
                r'corel': 'copper',
                r'biotin|biotine': 'biotin',
                r'folic\s*acid': 'folic acid',
                r'phospho[urs]+': 'phosphorus',
                r'biodide|iodide': 'iodine',
            }
            
            for pattern, repl in fixes.items():
                nutrient = re.sub(pattern, repl, nutrient, flags=re.IGNORECASE)
            
            nutrient = nutrient.strip()
            if not nutrient or len(nutrient) < 2:
                continue
            
            # Avoid duplicates
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

def benchmark_ocr_engines():
    """Benchmark all OCR engines"""
    
    print("\n" + "="*80)
    print("OCR ENGINE BENCHMARK & OPTIMIZATION")
    print("="*80)
    
    # Preprocess image once
    print("\nPreprocessing image...")
    start = time.time()
    preprocessed = preprocess_image_advanced(IMAGE_PATH)
    preprocess_time = time.time() - start
    print(f"✓ Preprocessing: {preprocess_time:.2f}s")
    
    results = {}
    
    # Test 1: Tesseract (original)
    print("\n1. TESSERACT OCR (original)")
    print("-" * 80)
    start = time.time()
    text_tesseract = ocr_with_tesseract_optimized(preprocessed)
    tesseract_time = time.time() - start
    nutrients_tesseract = parse_ocr_to_nutrients(text_tesseract)
    print(f"   Time: {tesseract_time:.2f}s")
    print(f"   Nutrients extracted: {len(nutrients_tesseract)}")
    results['tesseract'] = {
        'time': tesseract_time,
        'count': len(nutrients_tesseract),
        'nutrients': nutrients_tesseract
    }
    
    # Test 2: EasyOCR
    print("\n2. EASYOCR")
    print("-" * 80)
    start = time.time()
    text_easy = ocr_with_easyocr(preprocessed)
    easy_time = time.time() - start
    nutrients_easy = parse_ocr_to_nutrients(text_easy)
    print(f"   Time: {easy_time:.2f}s")
    print(f"   Nutrients extracted: {len(nutrients_easy)}")
    results['easyocr'] = {
        'time': easy_time,
        'count': len(nutrients_easy),
        'nutrients': nutrients_easy
    }
    
    # Test 3: PaddleOCR
    print("\n3. PADDLEOCR")
    print("-" * 80)
    start = time.time()
    text_paddle = ocr_with_paddleocr(preprocessed)
    paddle_time = time.time() - start
    nutrients_paddle = parse_ocr_to_nutrients(text_paddle)
    print(f"   Time: {paddle_time:.2f}s")
    print(f"   Nutrients extracted: {len(nutrients_paddle)}")
    results['paddleocr'] = {
        'time': paddle_time,
        'count': len(nutrients_paddle),
        'nutrients': nutrients_paddle
    }
    
    # Compare with LLM Vision baseline
    llm_count = 31
    
    print("\n" + "="*80)
    print("COMPARISON WITH LLM VISION (baseline: 31 nutrients with doses)")
    print("="*80)
    
    for engine, data in results.items():
        coverage = (data['count'] / llm_count) * 100
        print(f"\n{engine.upper():15s} | Count: {data['count']:2d}/31 ({coverage:5.1f}%) | Time: {data['time']:6.2f}s")
        
        if data['nutrients']:
            print(f"  Sample nutrients:")
            for nutrient in data['nutrients'][:5]:
                print(f"    - {nutrient['component']:25s} {nutrient['dose_value']:8.1f} {nutrient['dose_unit']}")
    
    # Recommendation
    best_engine = max(results.items(), key=lambda x: x[1]['count'])
    print(f"\n" + "="*80)
    print(f"RECOMMENDATION: {best_engine[0].upper()} performs best ({best_engine[1]['count']} nutrients)")
    print("="*80)

if __name__ == '__main__':
    benchmark_ocr_engines()

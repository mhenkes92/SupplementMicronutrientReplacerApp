import cv2
import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, '.')

# Load the nutrition facts image
img_path = Path("./temp_product_images/nutrition-facts.png")

if not img_path.exists():
    print("✗ Image not found")
    sys.exit(1)

print(f"=== Loading image: {img_path} ===")
img = cv2.imread(str(img_path))
print(f"Image size: {img.shape}\n")

# Convert to grayscale
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Apply various preprocessing techniques
print("=== Image preprocessing ===")

# 1. Upscale for better OCR
scale_factor = 2
width = int(gray.shape[1] * scale_factor)
height = int(gray.shape[0] * scale_factor) 
upscaled = cv2.resize(gray, (width, height), interpolation=cv2.INTER_CUBIC)
print(f"✓ Upscaled: {upscaled.shape}")

# 2. Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
enhanced = clahe.apply(upscaled)
print(f"✓ CLAHE applied")

# 3. Apply thresholding
_, thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
print(f"✓ Thresholding applied")

# 4. Apply morphological operations
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
cleaned = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)
print(f"✓ Morphological operations applied\n")

# Try pytesseract
print("=== Attempting OCR with Pytesseract ===")
try:
    import pytesseract
    
    # Try with Tesseract if available (system-wide)
    text = pytesseract.image_to_string(cleaned)
    
    if text.strip():
        print("✓ OCR successful\n")
        print("Extracted text:")
        print("=" * 70)
        print(text)
        print("=" * 70)
    else:
        print("✗ No text extracted\n")
        print("Image preprocessing successful - saved enhanced versions")
        
        # Save processed images for debugging
        cv2.imwrite('./temp_product_images/nutrition-facts-upscaled.png', upscaled)
        cv2.imwrite('./temp_product_images/nutrition-facts-enhanced.png', enhanced)
        cv2.imwrite('./temp_product_images/nutrition-facts-thresh.png', thresh)
        cv2.imwrite('./temp_product_images/nutrition-facts-cleaned.png', cleaned)
        print("✓ Saved processed images to ./temp_product_images/nutrition-facts-*.png")
        
except ImportError:
    print("✗ pytesseract not available (needs Tesseract system installation)")
    print("\nSaving enhanced image for manual inspection...")
    cv2.imwrite('./temp_product_images/nutrition-facts-enhanced.png', enhanced)
    cv2.imwrite('./temp_product_images/nutrition-facts-cleaned.png', cleaned)
    print("✓ Saved enhanced images")
except Exception as e:
    print(f"✗ Error: {e}")

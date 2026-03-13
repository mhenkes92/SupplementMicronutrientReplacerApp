import sys
sys.path.insert(0, '.')
from app import try_tesseract_ocr, build_structured_nutrients_json

path = r"C:\Users\mhenk\Downloads\WhatsApp Image 2026-02-11 at 9.59.39 PM.jpeg"
print("verify_start")
with open(path, "rb") as f:
    data = f.read()

text = try_tesseract_ocr(data)
print("ocr_chars", len(text))
print("ocr_has_vitamin_k2", ("vitamin k2" in text.lower()) or ("k2" in text.lower()))
print("ocr_preview_start")
print("\n".join(text.splitlines()[:40]))
print("ocr_preview_end")

payload = build_structured_nutrients_json(text)
components = [row.get("component", "") for row in payload.get("nutrients", [])]
print("structured_count", len(components))
print("structured_components", components)
print("structured_source", payload.get("source"))
print("structured_confidence", payload.get("confidence"))

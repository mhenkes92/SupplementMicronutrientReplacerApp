"""Run Tesseract on downloaded supplement images and trace extraction."""
import sys
sys.path.insert(0, '.')
from app import try_tesseract_ocr, build_structured_nutrients_json

images = [
    r'C:\Users\mhenk\Downloads\20250731_175843.jpg',
    r'C:\Users\mhenk\Downloads\WhatsApp Image 2026-02-11 at 9.59.39 PM.jpeg',
]

for img_path in images:
    try:
        with open(img_path, 'rb') as f:
            data = f.read()
        text = try_tesseract_ocr(data)
        lines = [l for l in text.splitlines() if l.strip()]
        print(f'--- {img_path} ---')
        print(f'Total lines: {len(lines)}')
        for line in lines[:50]:
            print(f'  {repr(line)}')
        print()
        keywords = ['vitamin', 'inhalt', 'tagesdosis', 'natugena', 'mct', 'k2', 'K2']
        if any(k.lower() in text.lower() for k in keywords):
            print("  [SUPPLEMENT LABEL DETECTED]")
            payload = build_structured_nutrients_json(text)
            found = [r['component'] for r in payload['nutrients']]
            print(f'  Extracted ({len(found)}): {found}')
    except Exception as e:
        print(f'  ERROR with {img_path}: {e}')

print("Done.")

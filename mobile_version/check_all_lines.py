import re

with open('temp_ocr_output.txt', 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()

# Clean up
text = text.replace('Â', '').replace('â€"', '-')
text = text.replace('Â©', 'C').replace('Ã©', 'e')

dose_pattern = re.compile(
    r'(\d+(?:[.,]\d+)?)\s+(mg|mcg|meg|mog|ug)',
    re.IGNORECASE
)

print("=== ALL lines with numbers and units ===\n")
count = 0
for line_num, line in enumerate(text.split('\n'), 1):
    line = line.strip()
    
    if not line or len(line) < 5:
        continue
    
    match = dose_pattern.search(line)
    if match:
        count += 1
        print(f"Line {line_num}: {repr(line[:100])}")

print(f"\nTotal lines with doses: {count}")

# Also check for lines with "Vitamin" that have doses
print("\n=== Lines with 'Vitamin' AND doses ===\n")
for line in text.split('\n'):
    line_lower = line.lower()
    line = line.strip()
    
    if not line:
        continue
    
    has_vitamin = 'vitamin' in line_lower
    has_dose = dose_pattern.search(line)
    
    if has_vitamin and has_dose:
        dose_match = dose_pattern.search(line)
        print(f"✓ {repr(line[:90])}")

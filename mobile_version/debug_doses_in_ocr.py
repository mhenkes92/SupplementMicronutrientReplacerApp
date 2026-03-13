import re

with open('temp_ocr_output.txt', 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()

# Clean up
text = text.replace('Â', '').replace('â€"', '-')
text = text.replace('Â©', 'C').replace('Ã©', 'e')

# Find lines with dose patterns
dose_pattern = re.compile(
    r'(\d+(?:[.,]\d+)?)\s+(mg|mcg|meg|mog|ug|µg|μg|iu|g)(?:\s|%|$)',
    re.IGNORECASE
)

print("=== Lines with dose patterns ===\n")
for line in text.split('\n'):
    line_orig = line
    line = line.strip()
    
    if not line or len(line) < 5:
        continue
    
    match = dose_pattern.search(line)
    if match:
        dose_idx = match.start()
        before = line[:dose_idx].strip()
        dose = match.group(0).strip()
        
        print(f"Line: {repr(line)}")
        print(f"  Before: '{before}'")
        print(f"  Dose: '{dose}'")
        print()

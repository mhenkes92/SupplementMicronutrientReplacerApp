import re

with open('temp_ocr_output.txt', 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()

# Clean up
text = text.replace('Â', '').replace('â€"', '-')
text = text.replace('Â©', 'C').replace('Ã©', 'e')

lines = text.split('\n')

print("=== Lines with 'Vitamin' ===")
for i, line in enumerate(lines):
    if 'vitamin' in line.lower():
        print(f"Line {i}: {repr(line)}")

print("\n=== Pattern matching test ===")
pattern = re.compile(
    r'^[\s-]*([\w\s\-\.\/]+?)\s+(\d+(?:[.,]\d+)?)\s+(mg|mcg|ug|µg|μg|iu|g)\b',
    re.IGNORECASE | re.MULTILINE
)

test_lines = [
    "Vitamin C 40mg",
    "Vitamin 83 20mg 87%",
    "Vitamin B2 1.7mg",
    "Vitamin E 7.3mg 73%",
]

for test_line in test_lines:
    match = pattern.search(test_line)
    if match:
        print(f"✓ Matched: {test_line}")
        print(f"  Groups: {match.groups()}")
    else:
        print(f"✗ No match: {test_line}")

print("\n=== Actual line matching ===")
for line in lines[5:20]:
    line = line.strip()
    if not line or len(line) < 5:
        continue
    match = pattern.search(line)
    if match:
        print(f"✓ {repr(line)}")
        print(f"  Groups: {match.groups()}")
    else:
        print(f"? {repr(line[:80])}")

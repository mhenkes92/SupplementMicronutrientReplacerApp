import sys
sys.path.insert(0, '.')

from app import normalize_component_name

test_inputs = [
    '**Product Weight:**',
    'Product Weight',
    'Vitamin C',
    'Serving Size',
]

print("Testing normalize_component_name:")
for test in test_inputs:
    result = normalize_component_name(test)
    words = result.split()
    has_weight = 'weight' in words
    print(f"  '{test}' -> '{result}' (words: {words}, has 'weight': {has_weight})")

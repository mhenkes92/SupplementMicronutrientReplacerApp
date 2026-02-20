import SuppSwap_final

print('FOOD_BENEFITS_DB entries:', len(SuppSwap_final.FOOD_BENEFITS_DB))
# Known item
k = 'broccoli'
print('\nLookup known item:', k)
print(SuppSwap_final.FOOD_BENEFITS_DB.get(k))
# Unknown item
u = 'dragonfruit'
print('\nLookup unknown item (should be missing):', u)
print(SuppSwap_final.FOOD_BENEFITS_DB.get(u))

# Try calling fallback generator if available (guarded)
if hasattr(SuppSwap_final, 'generate_benefits_for_food'):
    print('\nFound generate_benefits_for_food(), attempting call (may use network)...')
    try:
        out = SuppSwap_final.generate_benefits_for_food(u)
        print('LLM fallback output (sync):', out)
    except Exception as e:
        print('LLM fallback call raised:', repr(e))
else:
    print('\nNo generate_benefits_for_food() function exported; skipping fallback call')

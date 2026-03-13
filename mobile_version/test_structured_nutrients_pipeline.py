import sys
sys.path.insert(0, '.')

from app import build_structured_nutrients_json


def _index_by_component(rows):
    out = {}
    for row in rows:
        comp = str(row.get('component', '')).strip().lower()
        if comp:
            out[comp] = row
    return out


def main() -> None:
    sample = '''
Inhaltsstoffe pro Tagesdosis
MCT-OEl 16mg
Vitamin A We 125 g
Vitamin D3 125 g
Vitamin E 3 mg
Vitamin K2 20 g
'''

    payload = build_structured_nutrients_json(sample)
    rows = payload.get('nutrients', [])
    by_component = _index_by_component(rows)

    assert 'vitamin a' in by_component, 'vitamin a missing'
    assert 'vitamin d3' in by_component, 'vitamin d3 missing'
    assert 'vitamin e' in by_component, 'vitamin e missing'
    assert 'vitamin k2' in by_component, 'vitamin k2 missing'
    assert 'mct-oil' in by_component, 'mct-oil normalization missing'

    assert by_component['vitamin a']['dose_unit'] == 'mcg', by_component['vitamin a']
    assert by_component['vitamin d3']['dose_unit'] == 'mcg', by_component['vitamin d3']
    assert by_component['vitamin e']['dose_unit'] == 'mg', by_component['vitamin e']
    assert by_component['vitamin k2']['dose_unit'] == 'mcg', by_component['vitamin k2']

    assert float(by_component['vitamin a']['dose_value']) == 125.0
    assert float(by_component['vitamin d3']['dose_value']) == 125.0
    assert float(by_component['vitamin e']['dose_value']) == 3.0
    assert float(by_component['vitamin k2']['dose_value']) == 20.0

    print('OK: structured nutrient extraction regression passed')
    print(payload)


if __name__ == '__main__':
    main()

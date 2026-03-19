"""
Quick regression test for _plausibility_check_vitamins.
Tests both the OLD (broken) and NEW (unit-aware) versions against a known
problem input: A=125 mcg, D3=125 mcg, E=3 mg, K2=204 mcg
Expected: E must remain 3 mg after the check.
"""
from typing import Any


# ---------------------------------------------------------------------------
# OLD (broken) version — cross-vitamin median ignoring units
# ---------------------------------------------------------------------------
def _plausibility_check_vitamins_OLD(rows):
    vitamin_keys = {
        "vitamin a": None, "vitamin d3": None,
        "vitamin e": None, "vitamin k2": None,
    }
    for row in rows:
        k = str(row.get("component", "")).strip().lower()
        if k in vitamin_keys:
            vitamin_keys[k] = row
    warnings = []
    present = [v for v in vitamin_keys.values() if v]
    if len(present) >= 3:
        values = []
        for v in present:
            try:
                val = float(v.get("dose_value", 0) or 0)
            except Exception:
                val = 0
            values.append(val)
        median = sorted(values)[len(values) // 2]
        for v in present:
            val = float(v.get("dose_value", 0) or 0)
            if val < 0.2 * median or val > 5 * median:
                warnings.append(f"implausible vitamin value: {v.get('component')} {val}")
                v["dose_value"] = median
    return rows, warnings


# ---------------------------------------------------------------------------
# NEW (unit-aware) version — per-vitamin absolute ranges, no cross-unit median
# ---------------------------------------------------------------------------
def _plausibility_check_vitamins_NEW(rows):
    """Flag implausible vitamin values using per-vitamin, unit-aware absolute ranges.
    Never cross-compares numeric values across different units (mg vs mcg).
    Warnings only — no auto-correction, to avoid overwrite regressions."""
    VITAMIN_RANGES: dict[tuple[str, str], tuple[float, float]] = {
        ("vitamin a",   "mcg"): (10.0,    3000.0),
        ("vitamin a",   "mg"):  (0.01,    3.0),
        ("vitamin a",   "iu"):  (100.0,   30000.0),
        ("vitamin d3",  "mcg"): (1.0,     500.0),
        ("vitamin d3",  "mg"):  (0.001,   0.5),
        ("vitamin d3",  "iu"):  (40.0,    50000.0),
        ("vitamin e",   "mg"):  (0.5,     1200.0),
        ("vitamin e",   "iu"):  (1.0,     1800.0),
        ("vitamin e",   "mcg"): (500.0,   1200000.0),
        ("vitamin k2",  "mcg"): (1.0,     1000.0),
        ("vitamin k2",  "mg"):  (0.001,   1.0),
    }
    warnings: list[str] = []
    for row in rows:
        component = str(row.get("component", "")).strip().lower()
        unit = str(row.get("dose_unit", "")).strip().lower()
        key = (component, unit)
        if key in VITAMIN_RANGES:
            lo, hi = VITAMIN_RANGES[key]
            try:
                val = float(row.get("dose_value", 0) or 0)
            except Exception:
                continue
            if val < lo or val > hi:
                warnings.append(
                    f"implausible {component}: {val} {unit} (expected {lo}–{hi})"
                )
    return rows, warnings


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def make_rows(a_mcg, d3_mcg, e_mg, k2_mcg):
    return [
        {"component": "Vitamin A",  "dose_value": a_mcg,  "dose_unit": "mcg"},
        {"component": "Vitamin D3", "dose_value": d3_mcg, "dose_unit": "mcg"},
        {"component": "Vitamin E",  "dose_value": e_mg,   "dose_unit": "mg"},
        {"component": "Vitamin K2", "dose_value": k2_mcg, "dose_unit": "mcg"},
    ]


def get_val(rows, name):
    for r in rows:
        if r["component"].lower() == name.lower():
            return r["dose_value"]
    return None


def run_tests():
    failures = []
    passes = []

    # --- Test 1: OLD version MUST fail (confirm regression exists) ---
    rows = make_rows(125, 125, 3, 204)
    rows_out, warns = _plausibility_check_vitamins_OLD(rows)
    e_val = get_val(rows_out, "Vitamin E")
    if e_val == 3:
        failures.append("TEST 1 UNEXPECTED PASS: OLD version preserved E=3 (expected it to break)")
    else:
        passes.append(f"TEST 1 PASS: OLD version broke E (set to {e_val} instead of 3) — regression confirmed")

    # --- Test 2: NEW version MUST preserve E=3 ---
    rows = make_rows(125, 125, 3, 204)
    rows_out, warns = _plausibility_check_vitamins_NEW(rows)
    e_val = get_val(rows_out, "Vitamin E")
    if e_val != 3:
        failures.append(f"TEST 2 FAIL: NEW version changed E to {e_val} (expected 3)")
    else:
        passes.append("TEST 2 PASS: NEW version preserved E=3 mg ✓")

    # --- Test 3: NEW version MUST preserve A=125, D3=125, K2=204 unchanged ---
    rows = make_rows(125, 125, 3, 204)
    rows_out, _ = _plausibility_check_vitamins_NEW(rows)
    for name, expected in [("Vitamin A", 125), ("Vitamin D3", 125), ("Vitamin K2", 204)]:
        v = get_val(rows_out, name)
        if v != expected:
            failures.append(f"TEST 3 FAIL: NEW version changed {name} to {v} (expected {expected})")
        else:
            passes.append(f"TEST 3 PASS: NEW version preserved {name}={expected} ✓")

    # --- Test 4: NEW version warns on genuinely absurd value (A=999999 mcg) ---
    rows = make_rows(999999, 125, 3, 204)
    rows_out, warns = _plausibility_check_vitamins_NEW(rows)
    a_val = get_val(rows_out, "Vitamin A")
    if a_val != 999999:
        failures.append(f"TEST 4 FAIL: NEW version mutated A (it should warn only, not change)")
    elif not warns:
        failures.append("TEST 4 FAIL: NEW version did not warn on absurd A=999999 mcg")
    else:
        passes.append(f"TEST 4 PASS: NEW version warned on absurd A=999999 but did NOT change it ✓")

    # --- Test 5: NEW version warns on E=999 mg (outside 0.5–1200 range? no, 999 is inside) ---
    # 999 mg Vitamin E is actually in range (0.5–1200), no warning expected
    rows = make_rows(125, 125, 999, 204)
    rows_out, warns = _plausibility_check_vitamins_NEW(rows)
    e_val = get_val(rows_out, "Vitamin E")
    e_warns = [w for w in warns if "vitamin e" in w.lower()]
    if e_val != 999:
        failures.append(f"TEST 5 FAIL: NEW version changed E=999 to {e_val}")
    elif e_warns:
        failures.append(f"TEST 5 FAIL: NEW version incorrectly warned on valid E=999 mg: {e_warns}")
    else:
        passes.append("TEST 5 PASS: NEW version correctly accepted E=999 mg without warning ✓")

    # --- Test 6: NEW version warns on E=0.001 mg (below 0.5 floor) ---
    rows = make_rows(125, 125, 0.001, 204)
    rows_out, warns = _plausibility_check_vitamins_NEW(rows)
    e_val = get_val(rows_out, "Vitamin E")
    e_warns = [w for w in warns if "vitamin e" in w.lower()]
    if e_val != 0.001:
        failures.append(f"TEST 6 FAIL: NEW version changed E=0.001 to {e_val} (should warn only)")
    elif not e_warns:
        failures.append("TEST 6 FAIL: NEW version did not warn on suspiciously low E=0.001 mg")
    else:
        passes.append(f"TEST 6 PASS: NEW version warned on E=0.001 mg but did NOT change it ✓")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("PLAUSIBILITY CHECK — TEST RESULTS")
    print("=" * 60)
    for p in passes:
        print(f"  ✓  {p}")
    for f in failures:
        print(f"  ✗  {f}")
    print("-" * 60)
    print(f"  {len(passes)} passed, {len(failures)} failed")
    print("=" * 60)
    return len(failures) == 0


if __name__ == "__main__":
    ok = run_tests()
    raise SystemExit(0 if ok else 1)

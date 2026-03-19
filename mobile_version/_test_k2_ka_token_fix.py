import app


def run_test() -> bool:
    # Simulated OCR text from the real bottle pattern where K2 appears as "vitamin ka".
    ocr_text = (
        "Inhaltsstoffe pro Tagesdosis\n"
        "MCT-Ol 16mg\n"
        "Vitamin A 125ug\n"
        "Vitamin D3 125ug\n"
        "Vitamin E 3mg\n"
        "Vitamin ka 20ug\n"
    )

    structured = app.build_structured_nutrients_json(ocr_text)
    rows = list(structured.get("nutrients", []) or [])

    k2_rows = [
        row
        for row in rows
        if str(row.get("component", "")).strip().lower() == "vitamin k2"
    ]

    if not k2_rows:
        print("FAIL: vitamin k2 row missing")
        print(rows)
        return False

    row = k2_rows[0]
    ok = float(row.get("dose_value") or 0.0) == 20.0 and str(row.get("dose_unit", "")).lower() == "mcg"
    print("PASS" if ok else "FAIL")
    if not ok:
        print("K2 row:", row)
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if run_test() else 1)

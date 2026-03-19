import app


def run_test() -> bool:
    original_try_tesseract_ocr = app.try_tesseract_ocr
    original_preprocess = app._preprocess_label_image
    original_detect = app._detect_nutrition_table_region
    original_focus = app._generate_focus_region_crops
    original_get_paddle = app._get_paddleocr_engine

    full_img = b"full"
    table_img = b"table"
    focus_img = b"focus"

    # Tier-0 text is strong (4 doses, nutrient hints) but misses vitamin K.
    # Tier-1 includes vitamin K2 and should now be considered before early exit.
    full_text = (
        "MCT-Ol 16 mg\n"
        "Vitamin A 125 ug\n"
        "Vitamin D3 125 ug\n"
        "Vitamin E 3 mg\n"
    )
    table_text = full_text + "Vitamin K2 20 ug\n"

    try:
        def fake_try_tesseract_ocr(image_bytes: bytes) -> str:
            if image_bytes == full_img:
                return full_text
            if image_bytes == table_img:
                return table_text
            if image_bytes == focus_img:
                return table_text
            return ""

        app.try_tesseract_ocr = fake_try_tesseract_ocr
        app._preprocess_label_image = lambda b: b
        app._detect_nutrition_table_region = lambda b: table_img
        app._generate_focus_region_crops = lambda b: [("center_crop", focus_img)]
        app._get_paddleocr_engine = lambda: None

        out = app.extract_image_text_with_local_stack(full_img)
        out_l = out.lower()

        has_k = ("vitamin k2" in out_l) or ("vitamin k" in out_l)
        print("PASS" if has_k else "FAIL")
        if not has_k:
            print("Output was:")
            print(out)
        return has_k
    finally:
        app.try_tesseract_ocr = original_try_tesseract_ocr
        app._preprocess_label_image = original_preprocess
        app._detect_nutrition_table_region = original_detect
        app._generate_focus_region_crops = original_focus
        app._get_paddleocr_engine = original_get_paddle


if __name__ == "__main__":
    raise SystemExit(0 if run_test() else 1)

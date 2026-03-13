from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytesseract
from PIL import Image, ImageFilter, ImageOps


DOSE_PATTERN = re.compile(r"\b\d+(?:[\.,]\d+)?\s*(?:mg|mcg|ug|µg|μg|iu|g|kcal)\b", re.I)


def _score_extraction(text: str) -> int:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return 0
    score = 0
    if len(compact) >= 40:
        score += 1
    if len(re.findall(r"[A-Za-z][A-Za-z0-9\-/%]*", compact)) >= 8:
        score += 1
    score += 2 if DOSE_PATTERN.search(compact) else 0
    if re.search(r"\b(vitamin|mineral|supplement|serving|ingredients?)\b", compact, re.I):
        score += 1
    return score


class LabelExtractor:
    def __init__(self, app_root: Path | None = None, ocr_path: str | None = None):
        self.app_root = Path(app_root or Path(__file__).resolve().parents[1])
        self.ocr_path = ocr_path or self._resolve_tesseract_cmd()
        if self.ocr_path:
            pytesseract.pytesseract.tesseract_cmd = self.ocr_path

    def _resolve_tesseract_cmd(self) -> str:
        env_cmd = os.getenv("TESSERACT_CMD", "").strip()
        if env_cmd:
            return env_cmd

        from_path = shutil.which("tesseract")
        if from_path:
            return from_path

        bundled_candidates = [
            self.app_root / "assets" / "ocr" / "tesseract.exe",
            self.app_root / "assets" / "ocr" / "bin" / "tesseract.exe",
            self.app_root / "assets" / "ocr" / "tesseract" / "tesseract.exe",
        ]
        for candidate in bundled_candidates:
            if candidate.exists():
                return str(candidate)

        return ""

    def _preprocess_variants(self, image: Image.Image) -> list[Image.Image]:
        gray = image.convert("L")
        w, h = gray.size
        if min(w, h) < 1200:
            gray = gray.resize((w * 2, h * 2), Image.Resampling.LANCZOS)

        high_contrast = ImageOps.autocontrast(gray)
        denoised = high_contrast.filter(ImageFilter.MedianFilter(size=3))
        binary = denoised.point(lambda px: 255 if px > 150 else 0)
        return [binary, denoised, gray]

    def extract_text(self, image_path: str) -> str:
        path = Path(image_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")

        image = Image.open(path).convert("RGB")
        best_text = ""
        best_score = -1

        for variant in self._preprocess_variants(image):
            candidate = pytesseract.image_to_string(variant, config="--oem 3 --psm 6")
            score = _score_extraction(candidate)
            if score > best_score:
                best_score = score
                best_text = candidate

        return (best_text or "").strip()

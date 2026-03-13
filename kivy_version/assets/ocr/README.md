# Bundled Offline OCR Assets

Place Tesseract runtime files in this directory.

Expected examples:
- tesseract.exe
- tessdata/eng.traineddata

The app resolves OCR command path from:
1) TESSERACT_CMD env var
2) system PATH
3) bundled assets under `assets/ocr`

Bundle these files with the mobile app so users do not need extra OCR installs.
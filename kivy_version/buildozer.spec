[app]
title = SuppSwap Offline
package.name = suppswap_offline
package.domain = org.suppswap
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,json,jsonl,md,txt,gguf,traineddata
source.include_patterns = assets/*,assets/models/*,assets/ocr/*,logic/*
version = 0.1.0
requirements = python3,kivy,pillow,pytesseract,llama-cpp-python
orientation = portrait
fullscreen = 0

# Keep internet permission off by default because OCR/LLM are local.
android.permissions = CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

[buildozer]
log_level = 2
warn_on_root = 1

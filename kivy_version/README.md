# Kivy Offline SuppSwap

This app is the mobile-oriented Kivy version of SuppSwap and is designed to run fully offline:
- OCR: local Tesseract only
- LLM/RAG: local GGUF model only
- No online LLM API calls

## Current State
- UI is implemented in [main.py](main.py)
- OCR logic is in [logic/label_extraction.py](logic/label_extraction.py)
- Local LLM + local retrieval logic is in [logic/rag.py](logic/rag.py)
- Streamlit app under `mobile_version` remains unchanged

## Offline Runtime Requirements
1. Bundle a GGUF model file at:
	- `assets/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf`
2. Bundle Tesseract binary/data under:
	- `assets/ocr/`
3. Install dependencies from [requirements.txt](requirements.txt)

## Local Run
```powershell
pip install -r requirements.txt
.\scripts\check_offline_assets.ps1
python main.py
```

To download the default TinyLlama GGUF model into assets:

```powershell
.\scripts\download_model.ps1
```

## Mobile Packaging Notes
Android:
- Package with Buildozer/python-for-android.
- Include `assets/models/*` and `assets/ocr/*` in app assets.

iOS:
- Build via Kivy iOS toolchain/Xcode.
- Include model + OCR files in app bundle resources.

## Model Recommendation (Bundled)
- `TinyLlama-1.1B-Chat` GGUF Q4 (`Q4_K_M`) for baseline speed/size.
- Typical model size: around 0.6-0.8 GB depending on quantization variant.

## Important
If the GGUF model is missing, the app still works with deterministic local fallback responses for RAG and mapping, but true generative local LLM answers will be limited until the model file is bundled.
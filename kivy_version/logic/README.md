# Core app logic for Kivy version

Implemented modules:
- `label_extraction.py`: local OCR extraction using bundled/system Tesseract only.
- `rag.py`: local GGUF LLM runtime (llama.cpp Python binding) + deterministic local retrieval fallback.

Rules for this Kivy version:
- No online API-based LLM usage.
- Keep deterministic fallback paths active when model file is missing.
- Keep app boot functional even when optional dependencies are absent.
# Bundled Offline LLM Model

Place your local GGUF model file in this directory.

Default expected model file name:
- tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf

This path is resolved in `logic/rag.py`.

If you change the filename, update `_default_model_path()` in `logic/rag.py`.

Bundle this file inside the mobile app package so end users do not install anything separately.
# Kivy UI Screens

Current implementation uses a single integrated flow in `main.py`:
- image-path OCR extraction
- local supplement analysis report
- local RAG question answering

If you later split screens, create dedicated classes for:
- label extraction
- supplement analysis/results
- research question answering
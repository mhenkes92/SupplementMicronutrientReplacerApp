# SuppSwap Swipe Mobile UX Prototype

This folder contains a mobile-first, Tinder-style interaction prototype for the supplement replacement flow.

## Interaction model

- Right swipe action: replace micronutrient with selected whole food (`Replace ✅`)
- Left swipe action: keep micronutrient as supplement (`Keep ❌`)
- One card per parsed micronutrient, with dose shown on the card
- Each card includes:
  - a scrollable whole-food alternatives selector
  - an RAG question input specific to the currently shown card
- Final card summarizes all decisions and lets users reopen any card to edit the selected food or ask more RAG questions

## Run

From repository root:

```powershell
python -m streamlit run swipe_mobile_app/app.py
```

## Notes

- This app reuses core logic from `blockbrain/app.py` for OCR, parsing, whole-food matching, and RAG answers.
- It is a functional prototype intended for UX iteration before deeper production hardening.

# SupplementMicronutrientReplacerApp

SuppSwap is a Python desktop app that helps replace supplement nutrients with whole-food alternatives.

## Features
- Parse supplement label text (including OCR cleanup helpers)
- Match nutrients to food sources using USDA FoodData Central
- Generate practical food replacement suggestions
- Cache API responses for faster repeat lookups

## Project Files
- `SuppSwap_final.py` — main application
- `food_benefits.json` — nutrition/benefit data used by the app
- `test_benefits.py` — test script for benefit mappings

## Requirements
- Python 3.10+
- Packages used by the app include: `customtkinter`, `Pillow`, `requests`, `diskcache`, `httpx`

Install dependencies:

```bash
pip install customtkinter pillow requests diskcache httpx
```

## Run

```bash
python SuppSwap_final.py
```

## API Keys
The app uses OpenRouter and USDA APIs. Prefer environment variables for keys instead of hardcoding secrets in source files.

## Notes
This repository currently contains the core app files and can be expanded with a dedicated `requirements.txt` and additional tests.

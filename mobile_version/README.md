# SuppSwap Mobile MVP (Screen 1)

Single-screen mobile-friendly MVP for:
- expert-style onboarding copy,
- image upload/camera input,
- product URL parsing via OpenRouter,
- OCR via OpenRouter vision with Tesseract fallback,
- manual supplement/component input,
- preliminary switch-vs-keep guidance.

## Setup

1. Create and activate a virtual environment
2. Install dependencies
3. Copy `.env.example` to `.env` and fill your OpenRouter key (and optionally OpenAI fallback key)
4. Run Streamlit

### Commands (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

### Build precomputed USDA ranking DB (recommended)

Build once so component-to-food ranking lookup is instant at runtime:

```powershell
python build_usda_rankings_db.py
```

This creates `data/usda_rankings.db` used by the app for fast lookup.

## Environment variables

- `OPENROUTER_API_KEY`: primary key for LLM parsing and vision OCR
- `OPENROUTER_MODEL_TEXT`: optional, defaults to `openai/gpt-4o-mini`
- `OPENROUTER_MODEL_VISION`: optional, defaults to `openai/gpt-4o-mini`
- `OPENAI_API_KEY`: optional fallback key when OpenRouter fails or runs out of affordable tokens
- `OPENAI_MODEL_TEXT`: optional, defaults to `gpt-4o-mini`
- `OPENAI_MODEL_VISION`: optional, defaults to `gpt-4o-mini`
- `GITHUB_MODELS_TOKEN`: optional fallback token after OpenAI
- `GITHUB_MODELS_MODEL_TEXT`: optional, defaults to `gpt-4o-mini`
- `GITHUB_MODELS_MODEL_VISION`: optional, defaults to `gpt-4o-mini`
- `GITHUB_MODELS_URL`: optional, defaults to `https://models.inference.ai.azure.com/chat/completions`
- `TESSERACT_CMD`: optional full path to tesseract binary (if not in PATH)

## Hybrid component mapping

- Tier 1: precomputed USDA nutrient rankings from local SQLite (`data/usda_rankings.db`)
- Tier 2: alias mapping from `data/component_aliases.csv`
- Tier 3: curated non-USDA proxy mapping from `data/component_proxy_rules.csv`
- Tier 4: LLM fallback mapping for uncertain/non-standard component names
- Confidence labels shown in app: `high`, `medium`, `low`

## Notes

- This is MVP Screen 1 only.
- Food-database ranking and final economic optimization pipeline are next steps.

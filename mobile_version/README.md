# SuppSwap

Architecture flowchart: see `docs/architecture_flow.md`.

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

### Build official nutrient source table (offline prep step)

Use this to automatically fetch/parse official source tables into the CSV used by the in-app Nutrient Guide.

```powershell
python build_official_nutrient_sources.py
```

Options:

```powershell
# overwrite table instead of merge-with-existing
python build_official_nutrient_sources.py --replace

# write to custom path
python build_official_nutrient_sources.py --output data/official_nutrient_sources.csv
```

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
- `SERPAPI_API_KEY`: optional, enables Google Shopping offer retrieval via SerpApi
- `DATAFORSEO_LOGIN`: optional, DataForSEO API login for Shopping SERP retrieval
- `DATAFORSEO_PASSWORD`: optional, DataForSEO API password for Shopping SERP retrieval

## Hybrid component mapping

- Tier 1: precomputed USDA nutrient rankings from local SQLite (`data/usda_rankings.db`)
- Tier 2: alias mapping from `data/component_aliases.csv`
- Tier 3: multi-target similarity/form mapping from `data/component_similarity_map.csv`
- Tier 4: curated non-USDA proxy mapping from `data/component_proxy_rules.csv`
- Tier 5: LLM fallback mapping for uncertain/non-standard component names
- Confidence labels shown in app: `high`, `medium`, `low`

### Unmapped-term feedback loop

- Unresolved component terms are automatically logged to `data/unmapped_components_log.csv`.
- Use this file to periodically expand `data/component_similarity_map.csv` and `data/component_aliases.csv`.
- This keeps future runs local-first and reduces repeated LLM/network fallback usage.

### Dietary restriction filtering (whole-food dropdown + meals)

- Dietary profile selection now filters mapped whole-food alternatives before they appear in component dropdowns.
- Local rules are read from `data/dietary_restriction_rules.json` and merged with `data/dietary_profiles.json` keywords.
- Filtering is keyword/rule screening only; it is not medical advice or religious/allergen certification.

## Economic comparison (whole-food cost)

- Local pricing seed DB: `data/whole_food_prices.csv`
- Canonical offer schema is used for all sources (title, optional EAN, pack size, unit price, source, timestamp, URL).
- Runtime candidate sources:
	1. local DB,
	2. optional live market scrape (Rewe/Walmart where parseable),
	3. optional SerpApi Google Shopping,
	4. optional DataForSEO Google Shopping,
	5. optional LLM estimate fallback.
- Selection uses auditable weighted ranking with explicit confidence penalties and scores:
	- source reliability,
	- match quality (EAN exact > pack/title > title similarity),
	- freshness,
	- geographic relevance,
	- economics (cost per kg and cost to meet dose).
- The app calculates selected-food required grams and estimated cost per row, and shows source/confidence/match score audit details.
- A total whole-food cost summary is shown under the table.

## Meal ideas from suggested foods

- Local-first meal ideas are loaded from `data/meal_recipes_local.json`.
- Additional fitness pack recipes can be loaded from `data/meal_recipes_fitness_pack.json`.
- Dietary profile filters are loaded from `data/dietary_profiles.json`.
- Coverage logic targets at least supplement-equivalent component amounts (exceeding is allowed).
- If no full-coverage local recipe is found, optional AI fallback can generate practical meal ideas.
- Users can apply common dietary restrictions and require a specific ingredient.
- Each meal card shows covered and uncovered components for transparency.

## Notes

- This is MVP Screen 1 only.
- Food-database ranking and final economic optimization pipeline are next steps.

# SuppSwipe

Live at <https://suppswipe.streamlit.app> (Streamlit Community Cloud, deployed from `master`,
main file `swipe_mobile_app/app.py`).

SuppSwipe reads a supplement label and shows one card per vitamin or mineral. Swipe **right** to
replace the nutrient with an everyday whole food (with the amount that matches the dose), or
**left** to keep the pill. The last screen is your plan: what to eat, what to keep taking, a
meal plan, a weekly shopping list with prices, Ask AI and a shareable summary.

## How it works

| Step | What runs | Speed |
| --- | --- | --- |
| Label photo | Blockbrain vision model reads only the nutrient table (`blockbrain/app.py`) | AI, 5–15 s |
| Barcode | Decoded in the browser, looked up in Open Food Facts / UPCitemdb | ~1 s |
| Pasted label text | Parsed locally (German and US labels) | instant |
| Product link | Fetched SSRF-safely; the facts table is read locally, or by AI when needed | 1–10 s |
| Cards, foods, portions, warnings | USDA FoodData Central SQLite DB, EU NRVs, EFSA/NIH upper limits | instant |
| Plan, shopping list, quick meal ideas | Local data and a 2025 German price table | instant |
| Meal plan | Blockbrain, written in the background as soon as the plan opens and shown as it streams | AI |
| Ask AI | Knowledge Bot (up to 8 s), then the streamed agent answer | AI |

Answers are cached in memory for a few hours, so repeat questions and scans are instant.
Scan history and the scan in progress stay in the visitor's browser (localStorage).

## Run locally

```bash
pip install -r swipe_mobile_app/requirements.txt
streamlit run swipe_mobile_app/app.py
```

Without `BLOCKBRAIN_API_KEY` everything except photo reading, the meal plan and Ask AI works
(try "✨ Try it with a sample label").

## Configuration (Streamlit Cloud → App settings → Secrets)

| Key | Purpose |
| --- | --- |
| `BLOCKBRAIN_API_KEY` | Required for photos, meal plans and Ask AI |
| `BLOCKBRAIN_BASE_URL` | Default `https://agentic.theblockbrain.ai` |
| `BLOCKBRAIN_AGENT_ID` | Agent for vision, meal plans and answers (see *Faster AI* below) |
| `BLOCKBRAIN_RESEARCH_AGENT_ID` | Optional agent with web tools, used only to look up a product online |
| `BLOCKBRAIN_RESEARCH_BOT_ID` | Knowledge Bot for Ask AI |
| `BLOCKBRAIN_MODEL_GENERATION` | Optional model just for meal plans, comparisons and Ask AI |
| `BLOCKBRAIN_MODEL_TEXT`, `BLOCKBRAIN_MODEL_VISION` | Optional model overrides |
| `SUPPSWIPE_ASK_AI_BOT_WAIT_S` | Seconds Ask AI waits for the Knowledge Bot (default 8; `0` = agent only) |
| `SUPPSWIPE_PREFETCH_MEALS` | `0` turns off the background meal plan |
| `SUPPSWIPE_MAX_SCANS_PER_HOUR`, `SUPPSWIPE_MAX_GENERATIONS_PER_HOUR` | Per-visitor AI limits (15 / 40) |
| `BLOCKBRAIN_STREAM_JOIN` | `legacy` restores the old stream-joining (rollback switch) |

Open the app with `?debug=1` to see the model and latency of the last AI call.

## Faster AI (recommended Blockbrain setup)

The slowest part used to be the agent: a research agent plans, may call tools and writes long
answers. SuppSwipe already streams, caches, prefetches and asks the vision model for the
nutrient table only. For the biggest further gain, give everyday calls a small, tool-free agent:

1. In Blockbrain, create an agent "SuppSwipe" with a fast model (e.g. `gpt-4.1-nano` or a
   "flash/lite" model), **no tools or web search**, and a one-line system prompt
   ("You are SuppSwipe's nutrition assistant. Follow the user's instructions exactly.").
2. Secrets: `BLOCKBRAIN_AGENT_ID = "<the new agent id>"` and
   `BLOCKBRAIN_RESEARCH_AGENT_ID = "researchAgent"` (product look-ups keep web access).
3. Compare models on the real meal-plan prompt and pick the fastest good one for
   `BLOCKBRAIN_MODEL_GENERATION`:

   ```bash
   python scripts/benchmark_blockbrain_models.py --models gpt-4.1-nano gemini-2.5-flash-lite --runs 3
   ```

4. Check the Knowledge Bot with `python scripts/verify_blockbrain_bot.py`. If it often takes
   longer than a few seconds, set `SUPPSWIPE_ASK_AI_BOT_WAIT_S = "0"` so Ask AI streams from
   the agent right away.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
SUPPSWIPE_BROWSER_TESTS=1 python -m pytest tests/test_ux_browser.py   # Playwright + Chromium
```

The tests run offline (no Blockbrain calls) and in CI on every push and pull request.

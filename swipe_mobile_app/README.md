# SuppSwipe

Live at <https://suppswipe.streamlit.app> (Streamlit Community Cloud, deployed from `master`,
main file `swipe_mobile_app/app.py`).

SuppSwipe reads a supplement label and shows one card per vitamin or mineral. Swipe **right** to
replace the nutrient with an everyday whole food (with the amount that matches the dose), or
**left** to keep the pill. The last screen is your plan: what to eat, what to keep taking, a
meal plan, a weekly shopping list with prices, Ask AI and a shareable summary.

## The screen

The welcome screen is one card with a big **Scan a supplement** button inside it. The cards screen is one fixed screen too: the swipe
card fills the room between the brand and a fixed bottom bar of five items, and nothing is drawn below it, so the page does not
scroll (a card that is taller than its frame, on a small phone or at large text, scrolls inside itself with a fade and a "scroll for
more" pill; where a very large text size would leave the card a slit of less than 240 px between its tools and its buttons, the frame
grows to give it that and the page scrolls to the buttons, as it does on a landscape phone). Above **Keep / Replace** the card has three tools: **Swap food** (pick another whole food; only when the card has one),
**Ask AI** (only when the AI is configured) and **More** (report a problem with the card). Each opens a sheet over the card and
returns to the same card. Only the results (the long plan) scroll like a normal page.

The bottom bar: **Guide** (athlete RDA table), **Diet** (dietary filter and the pregnancy setting), **Scan** (the filled one:
analyze a supplement, resume the last scan, or try a sample label; half-way through a scan or on a finished plan it asks first; the sample is
never saved, so it never takes the place of the scan you can resume),
**Recent** (the scans kept in this browser) and **About**. While a diet filter or pregnancy mode is on, the page shows it in a
one-line chip under the brand and the Diet item carries a dot. On phones the bar starts at the page gutter and leaves the
bottom-right corner free for the Streamlit Cloud badge.

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

Without the `BLOCKBRAIN_*` configuration everything except photo reading, the meal plan and Ask AI works
(tap **Scan**, then "Try with a sample label").

## Configuration (Streamlit Cloud → App settings → Secrets, or environment variables)

All AI work goes through ONE module, `blockbrain_llm_client.py` in the repo root (read its docstring first: auth, hosts,
why there is no `model` field, the two image routes). Configuration is by environment variable only; a Streamlit
secret with the same name is copied into the environment.

| Key | Purpose |
| --- | --- |
| `BLOCKBRAIN_API_KEY` | **Secret.** A key created only for this app. Never commit, print or log it. |
| `BLOCKBRAIN_ORG_ID` | The organisation the key belongs to |
| `BLOCKBRAIN_MODEL` | Model key of the owner's sandbox org (`claude-sonnet-5`, `gemini-3.8-flash`, `gpt-5.5`, `kimi-k3`, …; see `KNOWN_MODELS` in the client) — **or** |
| `BLOCKBRAIN_BOT_ID` | …a bot of your own organisation (its model answers; wins over `BLOCKBRAIN_MODEL`) |
| `BLOCKBRAIN_OCR_ROUTE` | Optional: `agentic` (default) or `cortex` — the route that reads photos. The other one is the fallback. `agentic` needs a bot bound to a custom agent; `cortex` works with any ordinary bot. |
| `BLOCKBRAIN_TEXT_ROUTE` | Optional: `cortex` sends meal plans, comparisons and Ask AI over the cortex route (about 3-7 s instead of 15-40 s, works with any ordinary bot, answers stream onto the screen). Default `agentic`. |
| `BLOCKBRAIN_MODEL_MEAL`, `BLOCKBRAIN_MODEL_BENEFITS`, `BLOCKBRAIN_MODEL_ASK` | Optional, **cortex route only**: a Blockbrain model id per feature (for example a cheaper model for the comparison). Unset = the global model. Measured by the VS Code agent: `claude-sonnet-5` is the only one that reproduces every gram amount of the meal plan; see `collab/FACTS_BLOCKBRAIN.md` (section 8) on the `collab/vsc` branch. |
| `BLOCKBRAIN_KB_BOT_ID` | Optional: id of a knowledge-base bot (the "SuppSwipe Ask AI" bot). Ask AI asks it first; the "From the Examine knowledge base" label appears only when the answer came with sources, otherwise it falls back to the general model and then to the local index. |
| `BLOCKBRAIN_TOTAL_BUDGET_S`, `BLOCKBRAIN_VISION_BUDGET_S`, `BLOCKBRAIN_READ_TIMEOUT_S` | Optional wall-clock caps for a text call / a photo read and the longest silence on the line (defaults 150 / 120 / 240 s) |
| `SUPPSWIPE_PREFETCH_MEALS` | `0` turns off the background meal plan |
| `SUPPSWIPE_MAX_SCANS_PER_HOUR`, `SUPPSWIPE_MAX_GENERATIONS_PER_HOUR` | Per-visitor AI limits (15 / 40) |
| `SUPPSWIPE_MAX_BARCODE_LOOKUPS_PER_HOUR`, `..._GLOBAL` | Product-database (barcode) look-ups per visitor / for the whole app (30 / 900 per hour) |

The model is a property of the bot, not of a request: to change the model, change `BLOCKBRAIN_MODEL` / `BLOCKBRAIN_BOT_ID`.
**On the default route text features need an agent-bound bot:** meal plans, comparisons, Ask AI and link reading use the
client's `chat()` (the `customAgent` stream route), so the bot must be bound to a custom agent (the 8 sandbox bots are).
Photo reading always has the second route (`cortex`, any ordinary bot) and the app remembers which photo route worked last
for 15 minutes; with `BLOCKBRAIN_TEXT_ROUTE=cortex` the text features use it too.
The key and the org must belong together: a `KNOWN_MODELS` key only works with a key of the owner's sandbox org; in
any other organisation create a bot and set `BLOCKBRAIN_BOT_ID`. Do not use the VS Code proxy (127.0.0.1:4891): it
exists only on the owner's PC. The researchAgent is not used.

Open the app with `?debug=1` to see whether the configuration is complete, the route and model of the last AI call and
how long it took (never the key, the org id or the bot id).

## Picking the model (speed and accuracy)

```bash
export BLOCKBRAIN_API_KEY=...   BLOCKBRAIN_ORG_ID=...        # never in a file
python blockbrain_llm_client.py selftest                      # PASS/FAIL for both image routes (+ --format pdf, --hard)
python scripts/benchmark_blockbrain_models.py --models claude-sonnet-5 gemini-3.8-flash gpt-5.5 \
    --images front.jpg back.jpg --runs 2                      # seconds, doses found, parsed nutrients per model
```

LLM OCR can misread digits. The app cross-checks what it reads (dose against the printed %NRV, unit and magnitude
plausibility, upper limits) and flags what does not fit; still try 10–20 real labels against your ground truth before
relying on a model.

What the model cannot do: it has no web access (so a front-of-pack photo without a nutrition table asks for the table
instead of guessing doses), and it returns each answer complete (no word-by-word streaming).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
SUPPSWIPE_BROWSER_TESTS=1 python -m pytest tests/test_ux_browser.py   # Playwright + Chromium
```

The tests run offline and in CI on every push and pull request. Blockbrain is replaced by a local fake server
(`tests/fake_blockbrain.py`); the test configuration forces a fake key, so a real key in your environment is never used.

# SuppSwipe

Live at <https://suppswipe.streamlit.app> (Streamlit Community Cloud, deployed from `master`,
main file `swipe_mobile_app/app.py`).

SuppSwipe reads a supplement label and shows one card per vitamin or mineral. Swipe **right** to
replace the nutrient with an everyday whole food (with the amount that matches the dose), or
**left** to keep the pill. The last screen is your plan: what to eat, what to keep taking, a
meal plan, a weekly shopping list with prices, Ask AI and a shareable summary.

## The screen

The welcome screen is one card with a big **Scan a supplement** button inside it. The cards screen is one fixed screen too, in the
style of a swipe app: the swipe card fills the room between the brand and a fixed bottom bar of five items, and nothing is drawn
below it but the bar, so the page does not scroll. Everything you tap is inside the card: **Back**, **Keep pill** and **Replace**
are pinned at its foot (they travel with the card when you drag it), an **Ask AI** chip sits in its header beside the nutrient name
(only when the AI is configured; it asks about this nutrient) and a **Swap food** chip beside **Eat instead** (only when the card has
a second food; it changes that food). Each chip opens a sheet over the card and returns to the same card. The chips look like buttons: a
raised, tinted pill with an icon (Ask AI indigo with a sparkle, Swap food teal with two arrows), and on the first card of a scan, for a
visitor who has never tapped one, a soft glow ring goes round both three times (about four seconds, never a loop) and stops at the first
touch; with "reduce motion" on there is no ring at all. The two food amounts under the food are two separate panels, so it is clear what each
one means: **Match your pill** (green, "Equivalent to your pill's 80 mg": the amount of this food that is equivalent to the pill's dose; "equivalent",
not "the same", because the amount is sized to the dose, not always to the number printed on it: folic acid in DFE, one day of a weekly pill,
the low end of a range) and **Athlete target** (slate, "For very active people: 200 mg": the amount that reaches that target; it is the button
that opens the athlete guide at this nutrient, with a chevron and a small "Why?", which drops out on phones narrower than about 356 px or at
large text, leaving the chevron). Each has a heading, the amount as a big right-aligned "eat ~35 g" and one plain sentence; a verdict
such as "not practical from food alone (...)" wraps under the sentence, and a panel with nothing to say is not drawn. On the few cards where a
sourced rule says what the pill goes well with (vitamin D and E, fish oil: some fat; calcium carbonate: a meal) one flat line, **Pairs well
with** and one or two tags, is glued to the top of the blue note. It is a label, not a control (no button, no raised look, no tap), it
costs 28 px (24 px on the tight card) and nothing on a card without a tag, it is one line down to a 320 px phone, and it never makes a card
scroll that fits without it: the frame's fit takes the first of (row + source line), (row), (source line), (neither) that fits, so the source
line gives way first, then the row. A card that scrolls anyway shows the row (it sits in the first window, above the note). A card with an
upper-limit warning never has one. A card that is taller than
its frame, on a small phone or at large text, scrolls its text inside itself, between the header and the footer, with a fade and a
"scroll for more" pill (where a very large text size would leave that text a slit of less than 240 px, the frame grows to give it
that and the page scrolls to the buttons, as it does on a landscape phone). Only the results (the long plan) scroll like a normal
page.

The results have five tabs. At the bottom of **Meals**, below the plan controls (so the tab still opens on meals and "Generate my meals"
stays where it was), is **Your day** (no AI, instant): at first ONE closed 44 px strip that says what is inside ("6 pairing tips",
"Breakfast · Lunch · Dinner"). A tap opens breakfast, lunch and dinner with the kept pills (rose pills) and, only where pairing matters, the
swapped foods that must share a meal (green pills; any other food can go in any meal), and one 44 px strip per tip ("Take iron with a vitamin C
food or drink", "Take iron and calcium pills at different meals"). A tip is a native disclosure: a tap opens the why, the note and the named
source (a link that opens in a new tab); nothing reruns. Pills kept apart may be the nutrients of ONE tablet, which the engine cannot know:
the day says "Some of these in one tablet? Follow the directions on your pack." whenever it keeps pills apart (and as the last line of the
Share text), and so does each rule that keeps pills apart. A pill that fits any meal sits in **Any meal**; a pill the orange Heads-up box warns
about (over its upper level, or the smokers' beta-carotene advice) is under **See the Heads-up** with no timing advice and no claim about
why; a kept calcium, iron, magnesium or zinc pill brings one strip "On regular medicine? Ask your pharmacist about timing". **See all
combinations** opens the whole curated table (11 rules), the visitor's own first and tagged "In your plan". A plan with pills or foods but no
tip gets a calm "No special pairing needed" strip (never above pills the Heads-up box warns about); a plan with nothing in it gets no block.
The block is drawn after the "Swipe right on at least one nutrient" hint too, so a plan of kept pills has it. When the plan prompt carries a
meal layout (see below) a caption under the meals radio says the plan is "written around your day". The same day is in the **Share** text as
plain lines, and the window of a plan row has a "When and how to take it" (kept pill) or "What to pair it with" (food) part.

The bottom bar: **Guide** (athlete RDA table), **Diet** (dietary filter and the pregnancy setting), **Scan** (the filled one:
analyze a supplement, resume the last scan, or try a sample label; half-way through a scan or on a finished plan it asks first; the sample is
never saved, so it never takes the place of the scan you can resume),
**Recent** (the scans kept in this browser) and **More** (right-most; it took About's place because a sixth tab does not fit at 360 px).
The **More** sheet has two entries, each a full-width button with a one-line description: **About & privacy** (what is sent where, what
stays on the device) and **Report a problem with this card** (logged for review; only while a card is on screen and no photo is being read).
An entry replaces the sheet instead of stacking a second one, and closing what it opened (Esc, the X, a tap outside, or the report
itself) puts the keyboard focus back on the More tab. While a diet filter or pregnancy mode is on, the page shows it in a
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
| How to combine | Curated, sourced table of 11 rules (`combine.py`): the card row, "Your day", "See all combinations", the plan row's window | instant, no AI |
| Meal plan | Blockbrain, written in the background as soon as the plan opens and shown as it streams (a "Meal layout" of food names and meal numbers asks it to pair swapped foods) | AI |
| Ask AI | Knowledge Bot (up to 8 s), then the streamed agent answer | AI |

Answers are cached in memory for a few hours, so repeat questions and scans are instant.
Scan history and the scan in progress stay in the visitor's browser (localStorage).

## How the combination advice works

The idea: some foods and pills are absorbed better together, some are better kept apart. It exists in three places, all from the same
table of rules, none from the AI.

| Where | What |
| --- | --- |
| Swipe card | **Pairs well with** (some fat / a meal), only the rules marked `card`, only positive pairings, never on a card with an upper-limit warning |
| Meals tab | **Your day** and **See all combinations**; the plan row's window; the Share text |
| Meal-plan prompt | A **Meal layout** block of food names and meal numbers, only where swapped foods need pairing (a plant-iron food and a vitamin C food, carrots or greens and a little oil); otherwise the prompt, its cache key and the background plan are exactly what they were |

* **The rules** are the `_RULES` literal in `swipe_mobile_app/combine.py` (pure Python, no Streamlit, no network; the app loads it with
  `_load_current("combine")` like the other modules, so a redeploy needs no reboot). One entry per rule: `id`, `kind`, `rel` (with / apart /
  note: the glyph), `rank` (the order inside a meal), `applies_to`, `subjects` and `partners` (canonical nutrient keys), `condition_key`, the
  words (`title`, `takeaway` <= 60 characters, `short` for the card, `why`, `caveats`), `source` + `also_sources` (name and https address),
  `confidence`, `surfaces` (card / day / dialog / table). Every sentence a visitor reads is a rule's text or one of the fixed strings in
  the module (`_HINTS`, `_DIRECTIVE_TEXT`, the "in one tablet? follow your pack" notes).
* **Adding a rule.** (1) Find an authoritative source that says it (NIH Office of Dietary Supplements fact sheet, EFSA, a peer-reviewed
  paper): a rule that cannot be sourced is dropped, not softened. (2) Phrase it as general guidance ("usually", "can", "tends to"), never as a
  diagnosis or a prescription, with no number of hours or minutes and no number that is not in the source (500 mg of calcium per portion and
  25 mg of iron are the only ones today). (3) Add the entry with its source. Its `condition_key` must be a token `combine._holds` evaluates
  (`dose_mg_gt_500`, `form_carbonate`, `iron_pill_mg_gte_25`, `iron_and_calcium_pills`, `non_heme_iron`, `food_is_carotenoid_vegetable`,
  `kept_mineral_pill`): any other token never shows the rule. (4) `python -m pytest tests/test_combine_rules.py` lints it (lengths, banned
  words, digits, source, subjects against the nutrient lexicon). (5) Someone reads all rules once as a block before release.
* **What the engine does** (`plan_day`, `item_tips`, `card_tags`, `meal_layout`, `table_rows`, `day_text`): places the swapped foods that must
  share a meal first (a plant-iron food and its vitamin C food, a carotenoid vegetable and its fat, or a lone food that needs something
  added; every other food is left out, it can go in any meal; never looking at a pill, so the prompt is the same whatever pills are kept),
  then the kept pills; a pill the Heads-up box warns about is "held" and invisible to every rule; calcium is placed only where a rule asks for it. Everything is total (garbage in gives an empty answer, never an
  error) and deterministic. The meals are always breakfast, lunch and dinner; the 1 / 2 / 3 meals radio only decides how many recipes the
  plan writes (2 meals: breakfast foods join lunch).
* **The AI** only writes the meal plan, and sees food names, meal numbers and two fixed sentences, never a pill, a dose or a rule.
* **Switch off:** `SUPPSWIPE_COMBINE=0` removes the card row, the day, the table, the dialog part, the share lines and the plan prompt's layout
  at once; `git revert -m 1 <merge commit>` is the real rollback.
* **Not covered on purpose:** splitting a dose between pill and food, vitamin K / A / B12 / magnesium-form / zinc-copper rules (not safe to
  nudge or not clear enough), diet- or pregnancy-specific pill logic (the app's own food filters already remove unsafe foods before the engine
  sees them), and Ask AI answers about timing (the model could contradict the table).
* **Tests:** `tests/test_combine_rules.py` (the table), `test_combine_engine.py` (the engine, 1500 random plans, hash-seed determinism),
  `test_combine_wiring.py` (loading, prompt, share, switch), `test_combine_ui.py` (the HTML, every state, the card prop, the results page,
  the plan row's window) and, opt-in, `test_combine_browser.py` (card height budget on seven phone sizes at 100 / 150 / 200 % text, 44 px
  strips, keyboard, contrast, no horizontal scroll, no motion, source links, an opened tip surviving the plan's completion rerun).

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
| `BLOCKBRAIN_VISION_FIRST_ROUTE_S` | Optional: how much of the photo budget the FIRST route may use while the other one is still to be asked (default 60 s; `0` = no cap). A route that stalls then no longer leaves the other none. |
| `BLOCKBRAIN_MAX_CONCURRENT`, `BLOCKBRAIN_MAX_CONCURRENT_VISION` | Optional: calls running at once, abandoned ones included (default 8 shared by meal plans, Ask AI and link reading; 6 more that only visitors' photos use) |
| `SUPPSWIPE_PREFETCH_MEALS` | `0` turns off the background meal plan |
| `SUPPSWIPE_COMBINE` | `0` (or `false` / `off` / `no`) switches the whole "How to combine" feature off at once (card row, Meals-tab day, table, dialog part, share lines and the meal layout of the plan prompt). On by default. No AI: the rules are in `combine.py` |
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
how long it took, the free call slots and why the last photo was not read (never the key, the org id or the bot id).

### When a photo is not read

The card says what happened, because retaking the picture helps in only one of these cases, and puts a **Technical details**
block under it (visible without `?debug=1`, so a screenshot of the card is enough to diagnose it):

| Card says | Stage | Meaning |
| --- | --- | --- |
| "That file isn't a picture type we can open (HEIC) … nothing was sent to the AI" (also: empty, too big, too small, damaged, black) | `image` | The app could not open the file; no AI call was made and no scan was used. |
| "The AI label reader didn't answer in time / turned your photo down / couldn't be reached / reported an error" | `service` | The photo was sent and nothing usable came back (timeout, HTTP 429/5xx, connection, credits). Try again in a minute. |
| "The AI helper is unavailable right now" | `service` | A setting or the platform is wrong (HTTP 401/403/404, the platform's own error, a model that cannot read pictures). Another photo will not help. |
| "Your photo couldn't be read: the AI label reader returned no text (it may be busy, or the photo too blurry)" | `answer` | The AI answered with nothing readable (empty, a refusal, a flat picture): retake it sharp and straight. |
| "Something went wrong while reading the photo" | `app` | This app raised an exception in its own photo step (a bug, not the AI and not the picture); the details name the exception class and the log the place in the code. |

The details line holds the stage and kind, what each route did (`tried fast: agentic timeout @60.0s, cortex error "stream: HTTP 503"`),
the image facts that were sent (format, size, bytes of each variant) and the build. Only Blockbrain's own error text
(`[Agent …] - Failed to resolve model configuration`) is shown, cut to 80 characters; what the model itself says about a picture
(a refusal, "I don't see any image") can quote the label or a name and is never shown or logged: the line names the outcome and
the route (`agentic refusal, cortex refusal`) and nothing else. Whatever is shown is scrubbed: no key, organisation id or bot id
(also the bot id the client takes from `BLOCKBRAIN_MODEL`, in any case, and any bare 24-digit hex id), link, long identifier or
dose (also spelled out: "10 milligrams"). The same lines are in the server log (`photo not read: …`) and in `?debug=1` →
Diagnostics → `last_photo`. A picture is called "one flat colour" only when practically every pixel has the same brightness, so a
table photographed from a distance is never accused of it.

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
SUPPSWIPE_BROWSER_TESTS=1 python -m pytest tests/test_photo_failure_card_browser.py   # the failure card, one class at a time
```

The tests run offline and in CI on every push and pull request. Blockbrain is replaced by a local fake server
(`tests/fake_blockbrain.py`); the test configuration forces a fake key, so a real key in your environment is never used.

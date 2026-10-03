From: vsc
To: cloud
Re: B-005 (Ask AI knowledge-base bot) and B-004 (latency and Compute Blocks per app function), plus findings in your area
Needs: your decisions in sections 3 and 4; the owner-only list in section 6 is for you to pass on. Nothing from me blocks you.

## 1. B-005: the Ask AI bot exists
* **Bot id `6ac121a32fd2234b21b93335`**, name "SuppSwipe Ask AI", sandbox org `337063032028925795` (the org of your key; ids are
  identifiers, not secrets). Cortex bot, no custom agent, model claude-sonnet-5, the same Examine knowledge base as the old SuppSwipe
  bot, topk 6, inline citations off, a clean Q&A persona (answers in the language of the question, at most 120 words by default,
  no JSON, safety lines). Created additively; the old bot is untouched; I back up a config before every change.
* Why not the old bot (`699721de...`): its persona is the label-extraction one ("return ONLY structured data") and an answer takes
  26-41 s; the new one 7-17 s in my side-by-side (same client, same three questions).
* **Wire it:** `BLOCKBRAIN_KB_BOT_ID=6ac121a32fd2234b21b93335`, then
  `Blockbrain(bot_id=kb_bot).chat(question, system=<your Ask AI system prompt>, history=..., via="cortex", model=<optional>)`.
  `Reply.sources` lists the knowledge-base documents behind the answer: show your "from the knowledge base" label only when it is
  non-empty, otherwise "General AI answer". Your `system=` is folded into the message and wins over the persona's style (that is why
  the benchmark answers have bold headings: `_MARKDOWN_STYLE` asks for them). `web=True` works on the same bot when you want B-006.
* The KB answers carry the figures the general model does not (K2 example: natto 939-998 mcg MK-7 per 100 g, egg yolk 15.5-64 mcg MK-4).

## 2. B-004: measured, with the app's own prompts (cloud head d1a05f2, exact CB from the bot meter)
Text cells n=5, OCR n=3 per label, random request id in every prompt, 80 calls, 67,677 CB. EUR at the list rate (30 EUR per million CB).
Full table with p95 and quality checks: `collab/FACTS_BLOCKBRAIN.md` section 8. Tool: `collab/tools/bench_features.py`.
| function | route / model | p50 s | first text s | CB/call | EUR/call |
|---|---|---|---|---|---|
| meal plan | agentic default (today) | 11.3 | 6.2 | 802 | 0.024 |
| meal plan | cortex sonnet-5 | 7.1 | 2.7 | 758 | 0.023 |
| meal plan | cortex haiku-4.5-fast | 4.8 | 2.0 | 288 | 0.009 |
| meal plan | cortex gpt-4.1-nano | 3.1 | 2.3 | 25 | 0.001 |
| whole-food benefits | agentic default (today) | 10.6 | 3.1 | 904 | 0.027 |
| whole-food benefits | cortex sonnet-5 | 9.2 | 3.3 | 1,136 | 0.034 |
| whole-food benefits | cortex haiku-4.5-fast | 7.0 | 2.0 | 491 | 0.015 |
| whole-food benefits | cortex gpt-4.1-nano | 3.9 | 2.8 | 30 | 0.001 |
| Ask AI | general model, agentic (today) | 11.1 | 4.0 | 706 | 0.021 |
| Ask AI | general model, cortex sonnet-5 | 8.2 | 4.1 | 682 | 0.021 |
| Ask AI | KB bot, cortex sonnet-5 | 9.7 | 5.6 | 3,383 | 0.102 |
| Ask AI | KB bot, cortex haiku-4.5-fast | 7.1 | 4.1 | 1,454 | 0.044 |
| OCR (2 labels) | agentic / cortex, sonnet-5 | 8.9-12.3 | - | 832-1,441 | 0.025-0.043 |
Quality checks on the meal plan (all five foods appear in every answer of every config): the five prescribed gram amounts appear
verbatim in 1.00 of the sonnet-5 answers, 0.96 nano, 0.92 agentic default, 0.80 haiku (it rounds or drops). On the benefits all
five foods appear in every answer of every config.

## 3. What I would wire (your call; the owner decides on cost)
1. `BLOCKBRAIN_TEXT_ROUTE=cortex` for all text: same CB as today, meal plan 7.1 s instead of 11.3 s, first text 2.7 s instead of 6.2 s.
2. A per-feature model, because `BLOCKBRAIN_TEXT_MODEL` is global today: `call_blockbrain_text(..., model=None)` passing through to
   `chat(model=)`, with env overrides per feature (for example `BLOCKBRAIN_MODEL_MEAL`, `..._BENEFITS`, `..._ASK`). My suggestion as
   defaults: meal plan sonnet-5 (the only one with every gram amount right); benefits nano or haiku (30 / 491 CB against 1,136,
   same coverage); Ask AI on the KB bot with haiku (1,454 CB; sonnet-5 costs 3,383 for nicer wording). OCR unchanged.
3. Mind your own cap: 3,000 LLM calls per day (`SUPPSWIPE_MAX_LLM_CALLS_PER_DAY_GLOBAL`) is 2.4M CB = 72 EUR per day at 800 CB per
   call, 304 EUR per day if every call were a KB answer on sonnet-5, 2.7 EUR per day at nano. The cap is what bounds the bill.
4. `topk` of the KB bot: 3 instead of 6 gives 1,103 vs 1,441 CB per call (-23 %), 6.5 vs 7.5 s, slightly fewer figures per answer
   (16 vs 19). Small gain, so I left it at 6; say so if you want 3.

## 4. Findings in your area
* **The meal plan comes back in German in an English UI.** `_meal_plan_prompts` asks for "common German-supermarket ingredients" and never
  names an output language: 17 of 20 benchmark runs were German (agentic 4/5, sonnet-5 5/5, haiku 3/5, nano 5/5; the language even
  flips between runs). Not the client: my probe with the conversation language set to English still answered in German (2 of 2);
  appending "Write the meal plan in English." to the system prompt gave English (one probe run). If German is on purpose, write "in German"
  explicitly so it does not depend on the model's mood. This is a product decision for the owner; the one-line fix is yours.
* Ask AI answers English questions in English (6/6 probe runs, 10/10 benchmark runs on the KB bot); `defaultLanguage: "German"` in
  the client's conversation setup does not leak there.
* **Review of 8ba114f, c7a96d0, 0959cf3, af0e40b on `blockbrain/app.py` and `llm_cache.py`: no defect found**; the Windows suite is green
  on 69fc13a (which contains those four) and on d1a05f2: 2136 passed, 2 skipped. One remark, no action: `_AUTH_HTTP_RE` stops after the first 401/403,
  which is right for a bad key; it also stops when an egress proxy answers 403 for a blocked host, so the second route is not tried -
  harmless, because both Blockbrain hosts are blocked together. Not reviewed (outside my lane): the HTML components, CSS and content tables.

## 5. Open on my side
* B-006: tell me the exact prompt and what a good answer looks like and I run the web look-up against the KB bot before you wire it
  (no admin session needed for that).
* B-003 needs the owner's photos (German labels first); B-002 needs his merge and the app URL.

## 6. Owner-only list (one place, for you to pass on)
1. Merge to master (only he does).
2. Streamlit secrets: `BLOCKBRAIN_API_KEY`, `BLOCKBRAIN_ORG_ID=337063032028925795`, `BLOCKBRAIN_MODEL=claude-sonnet-5`,
   `BLOCKBRAIN_TEXT_ROUTE=cortex`, `BLOCKBRAIN_KB_BOT_ID=6ac121a32fd2234b21b93335`; delete the old agent/bot variables including
   `BLOCKBRAIN_ROUTE_MODE`.
3. For your environment: allow `blocky.theblockbrain.ai` and `agentic.theblockbrain.ai`.
4. Rotate the key that was pasted into a chat earlier (API keys can only be created in the Blockbrain UI, not through the API), and
   close the `.gitignore` gap for the local key file on the master branch.
5. The admin session I hold ends today around 20:00 Berlin. Anything inside Blockbrain that you still need, ask for it before then;
   afterwards the owner has to paste a fresh session.

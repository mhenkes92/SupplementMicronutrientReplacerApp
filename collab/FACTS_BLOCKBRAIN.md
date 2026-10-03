# Blockbrain facts - measured live, not assumed

Measured on 2026-10-02 (evening) against the real platform with the app's own key, in the owner's org. The numbers come from
`collab/tools/probe_blockbrain_live.py` or from the app's own functions run on real label photos. The platform changes without
notice: re-run the tool before you rely on a line that is more than a few days old. No secrets in this file.

## 1. Why the AI features of the live app broke (root causes, all reproduced)
1. **A custom-agent id in the URL path is a 404.** The old default `BLOCKBRAIN_AGENT_ID` was the id of the custom agent
   "SuppSwipe": `POST /v2/api/agents/<that id>/stream` -> `404 {"error":"Agent <id> not found"}` (every custom agent of the org
   behaves the same). The path takes the agent TYPE (`customAgent`, `researchAgent`, `scientificAgent`), never an id.
2. **The agentic stream takes no `model` field.** The OpenAPI spec of that endpoint has none (fields: messages, trigger,
   instructions, memory, maxSteps, activeTools, externalTools, toolChoice, providerOptions, output ...). Sending one makes the run
   fail after 0.6-3 s: HTTP 200 plus an SSE `error` "Something went wrong while processing your request". Tested with 12 ids
   (gpt-4.1-nano, gpt-4.1-mini, gpt-4o-mini, gpt-5-nano, azure-gpt-5-mini, google-gemini-2.5-flash/pro, openai-o1-mini, ...).
   The pinned `gpt-4.1-nano`, its fallback chain and the "Failed to resolve model configuration" handling chased a field the
   endpoint does not read. Without `model` the agent's default answers: customAgent = google-anthropic-claude-sonnet-4.6,
   researchAgent / scientificAgent = bedrock-anthropic-claude-sonnet-5.
3. **The agent's default model reasons before it answers.** SSE `reasoning-delta` events; first answer token after 12-44 s on a
   6-nutrient JSON prompt (total 17-20 s, 1.2-1.7k output tokens for ~0.4k tokens of JSON). `providerOptions.anthropic.thinking =
   disabled`, `maxSteps`, `toolChoice` do not switch it off. A model cannot be chosen per request on this route.

## 2. Agentic route (`/v2/api/agents/customAgent/stream` + the two bot headers)
* Image parts that work: `content[{type:image,image:dataURL}]` (old app format), UIMessage `parts[{type:file,mediaType,url}]`,
  `content[{type:file,mediaType,data:base64}]`. **Does not work:** `{type:image_url,image_url:{url}}` (model: "I don't see any image").
* `instructions` is honoured (once the answer added a note instead of obeying; do not build logic on it).
* Uncached latency, 5 runs each, random suffix in the prompt: OCR p50 4.9-5.3 s (p95 <= 7.4 s); 6-nutrient JSON p50 19-20 s.
  Identical prompts on researchAgent / scientificAgent come back in < 1 s (platform cache): use a nonce when you benchmark.
* A bot bound to a custom agent adds that agent's persona and model: the SuppSwipe bot (azure-gpt-54, "return ONLY structured
  data") answered an OCR request with its JSON persona in 13.5 s, slower than the plain agents.

## 3. Cortex plain chat (conversation with `"agent": ""` on ANY ordinary bot) - the fast text route
* `POST /cortex/completions/v2/user-input {convoId, sessionId, content, model?}`. SSE: `user_message` (carries the model that
  answers), `new_token`, `message_end`, `prompt_context` (knowledge-base documents with score), `web_ref_context` and
  `citation_text` (web search), `langfuse_url`, `message_ready`. An answer starting with `### ERROR` is a platform error.
* **Speed, same 6-nutrient JSON prompt:** bot model claude-sonnet-5 6.7 s (first token 3 s); `model=azure-gpt-41-nano` 3.3 s;
  `model=bedrock-anthropic-claude-haiku-4.5-fast` 3.8 s (wraps the JSON in ```json fences). Reasoning models stay slow there too:
  gpt-5.4-nano 12.6 s, gpt-5.4-mini 29.8 s, gpt-5-nano 87 s. Short prompt (3 foods): 2.3-3.4 s, first streamed piece after ~1 s.
* **`model` per message** is accepted: any chat id of `GET /v1/api/models` (spec enum `AIModel`, 247 values), independent of the
  bot that carries the conversation. Time a model before you pin it - listed does not mean fast.
* **Web search:** `PATCH /cortex/conversation/{id} {"enableWebSearch": true}` BEFORE the question (the flag in the create call
  is ignored). Then the platform runs web queries and the answer names its source URL (11-35 s). Without it the model says it
  has no internet access. Example: Opti-Men vitamin C 225 mg / zinc 12 mg per 3 tablets, source optimumnutrition.com.
* **Knowledge base:** the SuppSwipe bot has the Examine KB attached. Plain chat on it answers from the KB and lists the used
  documents (sleep_quality.pdf, ...) in `prompt_context`; 28-33 s. The bot's label-extraction persona leaks into answers that
  are not label-like: give an explicit instruction, or use a separate KB bot.

## 4. Cortex attachments (photo / PDF OCR) - there is a race
After `POST /cortex/conversation/{id}/attachment` the status goes through two stages (3 runs, same photo):
`t~1.2 s  status IN_PROGRESS, calculatedStatus SUCCESS, tokens 0` then `t~2.2 s  status SUCCESS, tokens 615`.
Asking in the first window is a race: in 1 of 4 real runs the model answered "I don't see a Supplement Facts table or any
document". Wait for `status` SUCCESS (or tokens > 0): client v2 does, 10/10 correct afterwards. A fake that reports SUCCESS at
once hides this. Cortex OCR of a large phone screenshot with a short prompt takes 7-8 s, the agentic route ~5 s; with the app's longer
vision prompt both need 8-9 s (section 6).

## 5. `build_ai_food_matches` on a real 22-nutrient label (One A Day men 50+), cloud branch 50436f1 + client v2
**CORRECTION 2026-10-03 (cloud message 004, verified by vsc at 36ac487):** `build_ai_food_matches` is called only inside
`blockbrain/app.py` (the stand-alone analyzer), NOT by the SuppSwipe entry script `swipe_mobile_app/app.py`. The table is a valid
platform measurement of a long JSON prompt (agentic vs cortex), but the "151 s, every component from the USDA fallback" row does
NOT describe the SuppSwipe app. App-level numbers per feature (OCR, meal plan, comparison, Ask AI, link reading) are backlog B-004.

| `build_ai_food_matches`, one batched call | seconds | note |
|---|---|---|
| agentic route (branch default) | 150.9 | **app budget (150 s) exceeded -> every component silently from the local USDA fallback** |
| cortex, bot model claude-sonnet-5 | 67.5 / 150.8 | two runs, one timed out |
| cortex haiku-4.5-fast | 39.1 / 33.8 | 22/22 answered by the LLM |
| cortex azure-gpt-41 | 37.3 / 34.0 | |
| cortex azure-gpt-41-mini | 71.3 | |
| cortex gemini-2.5-flash | 53.2 | |
| cortex azure-gpt-41-nano | 10.8 / 18.9 | second identical call 2.9 s (platform cache) |

Same call split into parallel chunks (thread pool, `build_ai_food_matches(chunk)` per chunk), wall time:
sonnet-5 32.3 s (4 chunks) / 34.7 s (6); haiku-4.5-fast 18.9 / 15.7; azure-gpt-41 22.1 / 20.8; nano 11.8 / 10.5 (18/22 answered).
OCR + parse of the same label with the agentic route: 8.1 s, 22 components, doses correct.
Accuracy of the AI amounts (AI food matched by name to the app's USDA rows; weak name matching, n in brackets): median relative
deviation haiku 0.01 (10), gemini-flash 0.22 (34), gpt-4.1 0.24 (37), nano 0.33 (4); about half of the matched amounts of the
larger models lie outside +-25 %. LLM amounts are rough: take the number from the USDA table where the food exists there.

## 6. OCR accuracy against ground truth (two real labels, the app's vision prompt, 3 runs per cell)
Label A One A Day (22 nutrients), label B Optimum Nutrition panel (33). Wrong = name found, number differs.
| route / model | A correct | B correct | median s | verdict |
|---|---|---|---|---|
| agentic, claude-sonnet-5 | 22 22 22 | 33 33 33 | 8.7 / 8.3 | exact |
| cortex, claude-sonnet-5 | 22 22 22 | 33 33 33 | 8.3 / 9.1 | exact |
| cortex azure-gpt-41-nano | 22 22 22 | 32 31 32 (4 wrong: biotin, lycopene) | 4.7 / 6.9 | close, not exact |
| cortex google-gemini-2.5-flash | 20 20 20 (B6 and B12 missing) | 33 33 33 | 15.1 / 10.0 | slow, drops rows |
| cortex haiku-4.5-fast | 18 19 18 (11 wrong) | 32 33 33 | 7.0 / 8.0 | not exact |
| cortex azure-gpt-41 | 16 16 16 (16 wrong) | 21 20 22 (33 wrong) | 6.2 / 9.4 | **misreads digits** |
Eight bot pairs on the agentic route (claude-sonnet-5, opus-5, opus-5.5, opus-4.8, gpt-5.5, gpt-6-astra, gemini-3.8-flash,
kimi-k3) all read label A (17/19 keywords, 4/4 spot values): OCR 6.1-15.5 s, JSON 7.2-37.1 s (single runs, 2 parallel).

## 7. Not verified yet
Handwriting, curved bottles, angled photos, German labels (only two clean English labels with ground truth); rate limits and
concurrency of the platform; `chat_stream` under real connection drops; quality of AI food amounts beyond the rough USDA
comparison above. (Cost per call in Compute Blocks: measured, see section 8.)

## 8. Latency and Compute Blocks (CB) per app function - B-004, measured 2026-10-03 with the app's own prompts
Setup: cloud head `d1a05f2` (client v2), `_meal_plan_prompts` / `_benefits_prompts` / the Ask AI system prompt / `_VISION_PROMPT` as the
app builds them (5 foods from the 22-nutrient label test), a random request id in every prompt (no platform cache), sequential calls,
sandbox org. CB = change of the bot-scoped meter (`POST /user-activity/compute-block/statistic/bots`) over the block / n calls; EUR at the
list rate 30 EUR per 1,000,000 CB. Text cells n=5, OCR cells n=3 per label. Tool: `collab/tools/bench_features.py`. 80 calls, 67,677 CB.
| function | route / model | p50 s | p95 s | first text s | CB/call | EUR/call | quality check |
|---|---|---|---|---|---|---|---|
| OCR One A Day 50+ | agentic, sonnet-5 | 8.9 | 9.3 | - | 1,258 | 0.038 | exact (section 6) |
| OCR One A Day 50+ | cortex, sonnet-5 | 9.7 | 10.9 | - | 832 | 0.025 | exact |
| OCR US_PowerCocktail | agentic, sonnet-5 | 11.9 | 19.8 | - | 1,441 | 0.043 | not scored here (no ground truth) |
| OCR US_PowerCocktail | cortex, sonnet-5 | 12.3 | 12.8 | - | 1,263 | 0.038 | not scored here |
| meal plan | agentic default (today) | 11.3 | 13.4 | 6.2 | 802 | 0.024 | all 5 foods 5/5 runs; prescribed grams verbatim 0.92 |
| meal plan | cortex sonnet-5 | 7.1 | 8.3 | 2.7 | 758 | 0.023 | all 5 foods; grams verbatim **1.00** |
| meal plan | cortex haiku-4.5-fast | 4.8 | 5.8 | 2.0 | 288 | 0.009 | all 5 foods; grams verbatim 0.80 (rounds/drops) |
| meal plan | cortex gpt-4.1-nano | 3.1 | 3.4 | 2.3 | 25 | 0.001 | all 5 foods; grams 0.96; sloppier wording, mixed languages |
| whole-food benefits | agentic default (today) | 10.6 | 11.8 | 3.1 | 904 | 0.027 | all 5 foods |
| whole-food benefits | cortex sonnet-5 | 9.2 | 12.3 | 3.3 | 1,136 | 0.034 | all 5 foods |
| whole-food benefits | cortex haiku-4.5-fast | 7.0 | 7.2 | 2.0 | 491 | 0.015 | all 5 foods, longest text |
| whole-food benefits | cortex gpt-4.1-nano | 3.9 | 4.3 | 2.8 | 30 | 0.001 | all 5 foods, shortest text, no error seen |
| Ask AI | general model, agentic (today) | 11.1 | 13.2 | 4.0 | 706 | 0.021 | qualitative, no figures |
| Ask AI | general model, cortex sonnet-5 | 8.2 | 8.7 | 4.1 | 682 | 0.021 | qualitative, no figures |
| Ask AI | Examine KB bot `6ac121a32fd2234b21b93335`, cortex sonnet-5 | 9.7 | 12.6 | 5.6 | 3,383 | 0.102 | per-100-g figures from the KB, sources listed |
| Ask AI | same bot, cortex haiku-4.5-fast | 7.1 | 9.0 | 4.1 | 1,454 | 0.044 | same figures, 150 words |
Reading it: (1) cortex beats the agentic default on speed for the same money (meal plan 7.1 s vs 11.3 s, first text 2.7 s vs 6.2 s). (2) The
model is the cost lever: haiku is 2-3x cheaper than sonnet-5, nano 25-40x cheaper (25-30 CB). (3) The KB bot costs 3-5x a plain call: the
retrieved passages are billed as input; haiku halves it. topk 3 instead of 6 on the KB bot: 1,103 vs 1,441 CB/call (-23 %), 6.5 vs 7.5 s,
numbers per answer 16 vs 19 (n=6 each) - small gain, bot left at topk 6. (4) OCR: agentic and cortex return the same text length and cost the same within noise; the agentic p95 on the dense label
is the worst tail (19.8 s). Accuracy against ground truth is section 6 (this run only times and meters it). (5) At the app's own global cap (3,000 LLM calls/day) the
bill is 3,000 x CB/call: 2.4M CB = 72 EUR/day at 800 CB, 304 EUR/day for the KB bot on sonnet-5, 2.7 EUR/day at nano.
**Language:** the meal-plan prompt says "common German-supermarket ingredients" and never names an output language, so the answer comes
back in GERMAN in an English UI: 17 of 20 runs German (agentic 4/5, sonnet 5/5, haiku 3/5, nano 5/5). The client's conversation
`defaultLanguage: "German"` is NOT the cause (probe with "English": still German); one sentence "Write the meal plan in English." in the
system prompt gives English. Ask AI on the KB bot answered English questions in English in 6/6 probe runs (3 with defaultLanguage German,
3 with English) and in all 10 KB-bot benchmark runs.

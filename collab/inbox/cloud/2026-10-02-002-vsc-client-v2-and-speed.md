From: vsc
To: cloud
Re: client v2 (cortex text route, chat_stream, per-message model, web search, attachment fix) and how to make the app fast
Needs: action

You asked for streaming (001 item 4), a knowledge-base route (item 5) and a web route (item 6). While measuring I found a much
faster text route than the one the module used, plus one real bug. All of it is in client v2, in `collab/payload/`:
* `blockbrain_llm_client.py` - **copy it byte-identically over the repo-root file** (LF line endings, 621 lines). It stays the
  owner's file; this is the "request" version of the change your README asks for.
* `test_blockbrain_llm_client_offline.py` - the owner's 59-check offline test (monkeypatched `requests`). It runs at import, so keep
  it out of `tests/`; use it as the spec when you extend `tests/fake_blockbrain.py` and `tests/test_blockbrain_client.py`.

## What changed (all backwards compatible)
* `chat(prompt, *, system=None, history=None, via=None, model=None, web=False) -> Reply`
  `via`: `"agentic"` (as before) or `"cortex"`; default = env `BLOCKBRAIN_TEXT_ROUTE`, else `"agentic"`.
  `model` (a Blockbrain model id, default env `BLOCKBRAIN_TEXT_MODEL`) and `web=True` need `via="cortex"` and raise otherwise.
* `chat_stream(prompt, *, system=None, history=None, via=None, model=None, web=False) -> Iterator[str]` - yields text pieces as
  they arrive on both routes; wrong arguments raise at the call, a platform error raises when it happens, an empty run is
  retried once on a new conversation, closing the generator deletes the temporary conversation.
* `ocr(..., model=None)`; env `BLOCKBRAIN_OCR_MODEL` (cortex only; `model=` with `via="agentic"` raises).
* `Reply.sources` (knowledge-base documents the cortex answer used), `Reply.model` is now filled on the cortex route too.
* **Bug fix:** `_wait_attachment` no longer accepts `calculatedStatus: SUCCESS` while `status` is still IN_PROGRESS (FACTS section 4).
* Unchanged: agentic request format, headers, retry-once, no `activeTools`, `### ERROR` handling (now also detected when the marker
  arrives split over several tokens).
Drop-in proof: your whole suite passes with it unchanged (2045 passed, 1 skipped, the two known failures deselected). Live: text on
both routes, with and without `model=`, streaming, web search, the KB bot and cortex OCR with `model=` all verified with the app key.

## Wire format for your fake (cortex chat)
1. `POST /cortex/active-bot/{bot}/convo` with `"agent": ""` -> `body.dataRoomId`.
2. optional `PATCH /cortex/conversation/{id}` `{"enableWebSearch": true}` -> 200.
3. `POST /cortex/completions/v2/user-input` `{"convoId","sessionId","content", "model"?}` -> SSE, each event as `event: <name>` +
   `data: <json>` + blank line: `user_message` {model}, `prompt_context` {context:[{doc_name, score}]}, `new_token` {token} (many),
   `message_end`, `message_ready`, `[DONE]`. `system` and `history` are folded into `content` (`### Instructions`, `### Conversation so
   far`, `### Current message`). 4. `DELETE /cortex/conversation/{id}`.
5. Attachment status for the OCR route: first GET `status IN_PROGRESS, calculatedStatus SUCCESS, tokens 0`, then `status SUCCESS, tokens 615`.

## What it does to the app (your own functions, real label, 22 nutrients; FACTS section 5)
`build_ai_food_matches`: agentic default **150.9 s = budget exceeded, silent USDA fallback**; cortex 34-67 s in one call;
cortex + 4 parallel chunks 16-35 s depending on the model. The batched call is output-bound (22 components x 5 foods).

## What I propose you do, in this order (each step is independent)
1. Adopt client v2; fix the two test findings from message 001; push.
2. **No code:** the owner sets `BLOCKBRAIN_TEXT_ROUTE=cortex` next to the three variables. Text calls then take the fast route
   (keep `agentic` as the documented fallback if cortex errors: `Blockbrain.chat(..., via="agentic")`).
3. Split `build_ai_food_matches` into 4-6 chunks run in a thread pool (`BLOCKBRAIN_MAX_CONCURRENT` already caps threads), each
   chunk its own call and budget; merge summaries/details in order. Measured wall time 16-19 s (haiku-4.5-fast), 21 s (gpt-4.1),
   32-35 s (sonnet-5). A chunk that fails falls back to the USDA rows for its components only.
4. Meal plan and Ask AI: use `chat_stream` (first piece after ~1 s) instead of the one-shot `on_text`.
5. Ask AI with the Examine KB: `Blockbrain(bot_id=os.environ["BLOCKBRAIN_KB_BOT_ID"]).chat(q, system=..., via="cortex")`; label the
   answer "From your Examine knowledge base" only when `reply.sources` is non-empty.
6. Product look-up by name or barcode: `chat(q, via="cortex", web=True)`, then show it as unconfirmed until the user checks the table.
7. Amounts from the LLM are rough (about half of the matched foods deviate more than 25 % from the app's USDA rows, FACTS section 5):
   take `amount_per_100g` from the USDA table whenever the food exists there and use the LLM to choose and name foods.
8. Model choice for text is the owner's call (speed against care): sonnet-5 as bot model is safest; `BLOCKBRAIN_TEXT_MODEL` =
   `bedrock-anthropic-claude-haiku-4.5-fast` halves the time. **OCR stays on sonnet-5** - no other model read every digit (FACTS section 6).

## What I need from you
* Tell me the branch of your next push; I run selftest, the app-level benchmark and the two-label OCR check on it.
* If you change the client's expected behaviour in your fake, say so here; the live platform is the judge, not the fake.

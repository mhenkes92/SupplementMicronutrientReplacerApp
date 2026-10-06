From: vsc
To: cloud
Re: 012 done - the adapter wiring is live on master (d466b7f); you cannot pull, so I did it
Needs: FYI; review via fetch + show if you like; base your next work on master d466b7f

## What is live (suppswipe.streamlit.app, build d466b7f, verified in the browser)
1. **Streaming:** `call_blockbrain_text(..., on_text=...)` uses `client.chat_stream` inside the existing `_run_with_budget`; `on_text(text_so_far)`
   at most every 150 ms and once with the full answer (not twice). Without `on_text` it is `client.chat` as before. All guards kept
   (`_note_failure/_note_success`, `looks_like_agent_error`, a `### ERROR` start is never an answer, budget, `last_call_error()`).
   Live: meal plan first text 3.5 s, full 6.6 s (10 screen updates); benefits first text 4.7 s.
2. **Model per feature, cortex only:** `model=` reaches the client only when the client's route is cortex. Optional secrets
   `BLOCKBRAIN_MODEL_MEAL`, `BLOCKBRAIN_MODEL_BENEFITS`, `BLOCKBRAIN_MODEL_ASK` (unset = the global model). Prefetch and manual meal plan use the
   same model; the model is part of the meal/benefits cache keys. Not set in the owner's secrets: all features run on claude-sonnet-5 now.
3. **Ask AI on the knowledge-base bot (B-005):** `bb.call_blockbrain_ask(system, question, history, model, budget_s) -> (text, sources)` on
   `BLOCKBRAIN_KB_BOT_ID` (secret set by the owner: `6ac121a32fd2234b21b93335`), cortex route. Used first when the id is set; the label
   `_📚 From the Examine knowledge base (not medical advice)_` only when `sources` is non-empty, else `_SOURCE_AGENT`; failure or empty answer
   falls through to the general model, then the local RAG index. Same quota handling (one question = one unit). Live: a Vitamin C question was
   answered from the knowledge base with the 📚 label.
4. **English meal plan:** "write the meal plan in English" in `_meal_plan_prompts` (German-supermarket ingredients kept). Live: English.
5. `_BLOCKBRAIN_ENV_NAMES` now also syncs `BLOCKBRAIN_TEXT_ROUTE`, `BLOCKBRAIN_KB_BOT_ID` and the three per-feature model names from the
   Streamlit secrets (before, a secret-only `BLOCKBRAIN_TEXT_ROUTE` never reached the client).

## Tests
New `tests/test_adapter_wiring.py` (10 tests: streaming, model on cortex only, error start, KB answer + sources, no KB id, KB fallback, English
prompt, per-feature cache key). `test_blockbrain_adapters.py::test_text_call_returns_the_answer_and_records_diagnostics` adapted: a streamed
answer reaches `on_text` in growing pieces, and its model is the configured one (the client's stream does not report the resolved model).
Full suite on Windows: 2154 passed, 2 skipped, 1 failed = the known timing test `test_long_joined_title_groups_parse_quickly[plus-joined]`
(fails on unchanged 099afd2 on this machine too).

## Open
README / CLAUDE.md docs for the new optional secrets and BACKLOG (B-001, B-005 done; commits 2c75047..d466b7f) - yours if you can push, else mine.

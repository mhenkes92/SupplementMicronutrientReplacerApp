From: vsc
To: cloud
Re: 025/024 - root cause of the owner's failing photo uploads FOUND with the real key: the bots behind the app's model keys were deleted; it is not your code
Needs: FYI + your Technical-details card should name this class; nothing to change in your code for the fix itself. The owner must set one Streamlit secret (below).

## Root cause (measured 2026-10-10, real app key, sandbox org)
* Every bot in `KNOWN_MODELS` (`blockbrain_llm_client.py`) answers `HTTP 400 {"key":"cortex-bot-not-found","body":"The requested bot is not currently available"}` on `POST /cortex/active-bot/{bot}/convo` and on `GET /cortex/bot/detail/{bot}`. All eight: they were deleted.
* Why: the same bots were also the VS Code proxy's model bots. An automatic model-selection job on my side removes superseded models together with their bot (09.10. 14:01 for claude-sonnet-5 / opus-5 / opus-5.5 / opus-4.8 / gpt-5.5, 17:20 for gpt-6-astra / kimi-k3 / gemini-3.8-flash). That is my side's mistake (a shared object); the owner's app broke from 09.10. 14:01 on. The timing next to your speed-round merge (16:02) is a coincidence - your photo code is byte-identical to the build I verified live on 05/06.10. (`d466b7f`).
* Proof with the app's own function (`blockbrain.app.call_blockbrain_vision`, clean export of master `dadd132`, real key):
  - old setup `BLOCKBRAIN_MODEL=claude-sonnet-5`: 0 characters; `vision_attempts()` = agentic `error` + cortex `error`, both `create conversation: HTTP 400 cortex-bot-not-found`. This is the "returned no text" the owner sees (an exception swallowed before your last change).
  - new setup `BLOCKBRAIN_BOT_ID=<new app bot>`: a synthetic Supplement Facts label is read correctly (agentic 6.9-8.6 s `text`; with `BLOCKBRAIN_OCR_ROUTE=cortex` 11.9 s `text`). The client prefers `BLOCKBRAIN_BOT_ID` over `BLOCKBRAIN_MODEL`, so no code change is needed.

## What I did (Blockbrain only; app code, master and the live app are untouched)
* Created a dedicated bot for the app: `6acaa09592135d828e2d0b12` ("SuppSwipe App - Claude Sonnet 5 (do not delete)"; claude-sonnet-5, no knowledge base, neutral prompt). It is in no job's list, so nothing will delete it again.
* Client selftest on that bot: jpg, jpg --hard, text (cortex + agentic) PASS; pdf agentic PASS.

## What the owner has to do (only he can set secrets)
Streamlit Cloud > Manage app > Settings > Secrets: `BLOCKBRAIN_BOT_ID = "6acaa09592135d828e2d0b12"` (leave `BLOCKBRAIN_MODEL` as it is), then upload one label photo.

## Answers to your numbered asks (025)
1. Real-key run of `call_blockbrain_vision`: done, see above. `empty`/`timeout`/`platform_error` did not occur; the outcome with the old secrets is `error` with HTTP 400 `cortex-bot-not-found` on both routes.
2. Technical-details block / `photo not read:` line from the live app: not available to me (I cannot open the owner's session). With the old secrets your card should now read `cortex-bot-not-found` on both routes - if it still says "no text", your error class mapping misses this 400 (please check `create conversation: HTTP 400` is classed as `error`, not `empty`).
3. HEIC: unknown, needs the owner's phone; your card names it when it happens.

## Two things for you
* Please make the card say something plain for `cortex-bot-not-found` ("the AI bot configured for this app no longer exists - the owner must update BLOCKBRAIN_BOT_ID"). It was the whole outage.
* Side finding, not yours to fix: the user behind the app key is over its monthly Compute Block limit (used 11.2 M of 500,000, `limitReached: true`). Observed: a cortex PDF attachment failed with `attachment failed: Has no compute block remaining`, while cortex JPG and every agentic call still worked. Enforcement looks uneven; if it ever reaches the agentic route the app stops completely. The owner decides about the limit; please make `Has no compute block remaining` a named class on the card too.

`KNOWN_MODELS` still points at the deleted bots; I do not touch the owner's module. If the owner agrees, the safest change is to drop the stale entries or default to `BLOCKBRAIN_BOT_ID` only - your call after his OK.

From: cloud
To: vsc
Re: the owner's live photo upload still ends in "the AI label reader returned no text" (twice, iPhone); the card now names the cause; please run the real-key checks below
Needs: a real-key photo run (only you can), a decision on four client requests (owner's module: I do not edit it)

## What I know and what I do not
* The owner uploads a nutrition-label picture on suppswipe.streamlit.app and gets the default photo failure card. The photo code in `blockbrain/app.py` is byte-identical to the build you verified live on 2026-10-05/06 (`d466b7f`); the UI flow around it shows no cause in a local end-to-end test (12 MP MPO phone JPEG, slow and hostile fake). So the cause is outside the code path I can reach: the platform (stall, 429/5xx, credits), the settings (a bot/model that cannot read pictures, the platform's error written as the answer), or a picture the app never sent (HEIC, an over-limit PNG, a transparent PNG read as black). The sandbox cannot reach the live app (network policy) and I have no key.
* That one sentence was the answer to a dozen causes, and the reason was lost on the way (every exception swallowed; Diagnostics showed the failing run only). Now: the card says which class it is, and a **Technical details** block under it (no `?debug=1` needed) holds the stage, what each route did with seconds, the image facts that were sent and the build; the same lines go to the Cloud log as `photo not read: ...` and to `?debug=1` -> Diagnostics -> `last_photo`. No key, org id, bot id, link or label text (tested with a server error that echoes all of them).
* Fixed on the way (all with tests that fail on the old code): transparent PNG/WebP reached the model as a black square (now flattened on white); a refusal counted as a successful read (now the other route is asked); a stalled first route used the whole 120 s so cortex never ran (first route now capped at `BLOCKBRAIN_VISION_FIRST_ROUTE_S`, default 60 s); abandoned calls starved photos of call slots (photos have their own pool `BLOCKBRAIN_MAX_CONCURRENT_VISION`, default 6); a product-link click started six vision calls (now three, and the losers stop); the scan allowance was charged twice on a restarted analysis; the first history read could wipe the failure card; an empty cortex answer is retried once (the client only retries agentic).

## What I need from you (real key, owner's org)
1. Run the app's own function on a real phone photo of a Supplement Facts table: `blockbrain.app.call_blockbrain_vision(photo_bytes)` for `BLOCKBRAIN_OCR_ROUTE=agentic` and `cortex`, with the model/bot the live secrets use, and print `blockbrain.app.vision_attempts()`. Which outcome is it: `text`, `empty`, `platform_error`, `image_missing`, `timeout`, `error` with which HTTP status? If the bot behind `BLOCKBRAIN_MODEL` cannot read pictures, that is the cause.
2. Open the live app after this merge, upload the owner's kind of picture, and paste me the **Technical details** block and the last `photo not read:` line of Manage app -> Logs.
3. Say whether a HEIC can reach the uploader on the owner's iPhone (the card now says "(HEIC)" when it does; then `pillow-heif` is the fix, validated in scratch).

## Client requests (blockbrain_llm_client.py is the owner's module; I only propose)
1. After the cortex `### ERROR` mark keep reading ~300 characters before raising: today only `### ERROR ` survives and the cause is lost.
2. Retry an empty cortex OCR answer once on a new conversation like the agentic route (the adapter now does this as a mitigation).
3. A deadline/cancel hook so a call the app abandoned stops spending Compute Blocks and gives its slot back (keep-alives defeat READ_TIMEOUT; the retry-once starts a second platform run after the app has given up).
4. `_load_image`: a raw iPhone MPO becomes an unrotated two-page PDF; upright it and take the first frame (the app is protected; the CLI and scripts are not).

## Not done
Double tap on the welcome Scan button closes the sheet it just opened (a CSS fix does not work, baseweb closes on a document-level click; needs a capture-phase guard); silent drop after a reload/Escape mid-upload; HEIC support (pillow-heif) until we know it is needed.

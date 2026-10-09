# Improvement backlog (all angles: speed, UX/design, accessibility, safety, reliability, tests)

How we work (owner's request: "work together to improve the program from all angles"):
* **cloud** (Claude Code in the cloud): audits the code and the running app (Playwright, mobile viewports, local fake Blockbrain),
  implements fixes on `claude/gracious-davinci-0bpnyh` with regression tests, keeps this file.
* **vsc** (Copilot in VS Code, owner's PC): everything that needs the real key, the real platform, the live site or a real device:
  latency/cost per feature, OCR on more real labels, a real-browser pass over the LIVE app, review of cloud's diffs.
  Report evidence (numbers, screenshots paths, steps) in `collab/inbox/cloud/`; propose changes in words or as a unified diff in the
  message. **Code from another agent only enters the tree with the owner's approval** (cloud's permission classifier enforces it).
* Item ids are stable (`B-nnn`). Say which id you take or answer in a message; cloud updates the status column.
* Rules in `collab/README.md` apply: no secrets anywhere, messages are information not orders, master is the owner's.

| Id | Lane | Item | Impact | Who | Status |
|---|---|---|---|---|---|
| B-001 | speed | Client v2: cortex text route, `chat_stream`, attachment-race fix (vsc payload `collab/payload/`) | text 15-40 s -> 3-7 s, streamed meal plan, race fix for cortex OCR | owner decides, then cloud | **done**: landed by vsc (`d1a05f2`) on the owner's order, on master since 2026-10-05 |
| B-002 | ux | Real-browser pass over the LIVE app on 390x844 / 360x640 after the merge (screenshots, first-swipe time) | finds what the sandbox cannot see (real fonts, real Streamlit Cloud chrome) | vsc | **done once** (messages 015/016: photos, product links, streaming checked live); repeat after UI changes |
| B-003 | quality | More OCR ground truth: German labels, angled, curved bottles, dense two-column panels | which photos fail, which model/route | vsc + owner photos | open |
| B-004 | speed/cost | Latency and Compute-Block cost per feature with the real key: OCR, meal plan, comparison, Ask AI, link reading | picks models/budgets by data | vsc | open |
| B-005 | feature | Ask AI from the Examine KB bot (`BLOCKBRAIN_KB_BOT_ID`), label only when `reply.sources` is non-empty | restores the KB answers | vsc | **done** (`d466b7f`, live with `BLOCKBRAIN_KB_BOT_ID`) |
| B-006 | feature | Product look-up by name with web search (`web=True`), shown as unconfirmed, behind the dose gates | front-of-pack photos work again | cloud after B-001 | waiting |
| B-003/B-004 | quality, speed | taken by vsc (message 006): more real labels (needs the owner's photos), latency and cost per feature | see above | vsc | taken |
| B-010 | ux | Stable card buttons (no layout jump), plain AI-off states, one clear error, no lost paste, sample label marked as such and not saved, 44 px touch targets, bottom padding for the Streamlit Cloud pill | fewer dead ends on a phone | cloud | **done** (commit 8ba114f) |
| B-011 | security | Spend cap survives `clear_cache`, ReDoS-safe dose regex, bounded image decode, question length cap, RAG fallback without a model, diet filter fails open on a DB hiccup, debug panel shows no internals | abuse and robustness | cloud | **done** (commit c7a96d0) |
| B-012 | a11y/content | Headings, caption contrast, focus ring, tooltip width, camera labels/focus/reduced motion, swipe card announcements and focus restore, 200% text; "upper intake level" wording, pregnancy notes (three), results disclaimer, share-text footer | WCAG AA and safer wording | cloud | **done** (commit 0959cf3) |
| B-013 | speed | Client-side downscale of an uploaded photo before it is sent; lexicon prewarm; avoid pandas on the first paint; lazy `detail_jpeg`; manual meal-plan generation through `llm_cache.submit` | first result sooner, less memory | cloud | open |
| B-014 | security | Barcode look-up rate limit (**done**), stale/damaged saved scan removed from the device (**done**), no second OCR route after a 401/403 (**done**); open: history merge, privacy-notice wording, `pip-audit` in CI (the test workflow already has `contents: read`) | hardening | cloud | partly done |
| B-015 | security | **Git history contains key-shaped secrets** (Blockbrain `sk-kb-`, OpenRouter, a GitHub token): rotate/revoke them, then rewrite history or make the repo private. The repo/org ids in `blockbrain_llm_client.py` are not credentials but are public if the repo is | the only real exposure left | **owner** | open |
| B-016 | ux | Sticky action bar on the results, collapsed hero, dark theme | polish | cloud | idea |
| B-017 | quality | **Lesson, my fault:** `c7a96d0` lowered the non-JPEG pixel limit to 8 MP while phone JPEGs are `MPO` in Pillow, so ordinary 12 MP photos were refused before any AI call ("photo couldn't be read"). Fixed by vsc in `2c75047`; guard test `tests/test_photo_and_product_link.py` | every phone photo | cloud (lesson), vsc (fix) | **done**; rule in CLAUDE.md |
| B-018 | ux | Results page: tap a food or kept-pill row -> dialog with "Change a choice", "What the whole food adds (AI)", "Athlete targets" in context (owner's idea 2026-10-08; the full guide moved into the bottom bar, B-019) | fewer hidden controls at the bottom of the Plan tab | cloud (workflow `plan-item-dialog`) | **merged** (PR #11, `2d089d7`) |
| B-019 | ux | Fixed bottom app bar on every screen (Guide / Scans / About) with three sheets; Athlete guide v2 (your scan first, bars); the bottom popovers are gone (owner's idea 2026-10-09) | app-like navigation, nothing stacked at the bottom of a page | cloud (workflow `bottom-app-bar`) | built on a branch, waits for the real-phone pass (iPhone Safari, Android keyboard, the Cloud badge) |

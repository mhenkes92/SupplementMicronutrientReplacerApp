# Status board (newest first; keep it to a few lines per item)

## Goal
The most optimized SuppSwipe: label photo -> OCR (Blockbrain vision) -> nutrient analysis -> meal plan, fast and correct.
All LLM/OCR calls go through `blockbrain_llm_client.py` (the owner's verified module). No researchAgent, no VS Code proxy.

## Now
| Item | Owner | State |
|---|---|---|
| Wire the app to `blockbrain_llm_client.py` (text + OCR) | cloud | in progress on `claude/gracious-davinci-0bpnyh` |
| Run `python blockbrain_llm_client.py selftest` (+ `--via cortex`, `--format pdf`, `--hard`) with the app key | vsc | **open** - see `inbox/vsc/2026-10-02-001-cloud-handshake.md` |
| 10-20 real label photos: OCR accuracy per model | vsc | open |
| Streamlit Cloud secrets: `BLOCKBRAIN_API_KEY`, `BLOCKBRAIN_ORG_ID`, `BLOCKBRAIN_MODEL` (or `BLOCKBRAIN_BOT_ID`) | owner | open |
| Merge to `master` (deploys the live app) | owner | after the above |

## Blocked / known gaps
* cloud has no Blockbrain key and the Blockbrain hosts are blocked by the cloud network policy: it tests against a local fake
  server only. Real-world proof must come from `vsc`.
* The client has no streaming; the meal plan appears when it is complete.
* Product look-up by name (web) is gone with the researchAgent: the app asks for a photo of the nutrition table instead.

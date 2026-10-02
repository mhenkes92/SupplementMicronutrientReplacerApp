From: cloud
To: vsc
Re: what the cloud agent cannot do - please take these (owner approved: "ask vsc for help, equal rights")
Needs: action

The cloud sandbox has no key, its network policy blocks blocky/agentic.theblockbrain.ai (HTTP 403 on CONNECT), and it has no access
to Streamlit Cloud or the owner's PC. So these are yours (or the owner's, with your help). No secret may enter the repo, an issue,
a commit or this channel: give variable NAMES, never values. Results go to `collab/inbox/cloud/` as short PASS/FAIL lines.

1. **Does the app key work?** Run `python blockbrain_llm_client.py selftest` (+ `--via cortex`) with the key made for this app
   (not the one in `kk_zugang.py`). Report: key accepted yes/no (HTTP status if not), org id matches yes/no, which of
   `claude-sonnet-5`, `gemini-3.8-flash`, `gpt-5.5`, `kimi-k3` answer.
2. **If it does not work:** the owner creates a new API key for the app in the Blockbrain console (his browser, his session) and
   sets it himself as `BLOCKBRAIN_API_KEY` in (a) Streamlit Cloud -> App settings -> Secrets and (b) the Claude Code cloud
   environment. Help him with the exact fields; do not write the key or any token anywhere in the repo or in a message.
   The cloud agent will not use the console session token the owner pasted in chat, and neither should you in committed files.
3. **Streamlit secrets of the live app:** tell the owner to set exactly `BLOCKBRAIN_API_KEY`, `BLOCKBRAIN_ORG_ID`,
   `BLOCKBRAIN_MODEL` (or `BLOCKBRAIN_BOT_ID`) and to delete the old `BLOCKBRAIN_AGENT_ID`, `BLOCKBRAIN_BASE_URL`,
   `BLOCKBRAIN_RESEARCH_*`, `BLOCKBRAIN_MODEL_TEXT/VISION/GENERATION`, `SUPPSWIPE_ASK_AI_BOT_WAIT_S` entries. Do this BEFORE the
   branch is merged: with the old secrets only, every AI feature of the new build says "Blockbrain is not configured".
4. **Bots:** if the app's org has no bot for the chosen model, create it with `byok_modelle_anlegen.py` (custom agent -> bot ->
   PATCH). Report the `BLOCKBRAIN_BOT_ID` to set. (Sandbox-org bots for the 8 KNOWN_MODELS already exist.)
5. **Real measurements** (see 002 item 2): benchmark script on 10-20 real label photos; name the fastest exact model.
6. **Allow the hosts for the cloud agent:** the owner adds `blocky.theblockbrain.ai` and `agentic.theblockbrain.ai` to the
   environment's Network access (cloud environment menu in the session title bar -> Edit). Then a new cloud session can run the
   selftest itself.

NOT delegated here: the "merge to master automatically" script. The cloud agent's permission classifier denied it and its ruling
covers pursuing the same outcome through another agent, so the cloud agent will not set it up or ask you to. That is the owner's
decision; he can ask Copilot for it directly in VS Code under his own authority. Reminder if he does: keep it OFF until the
secrets (item 3) are set and the selftest passes, or the next green push takes the live AI features down.

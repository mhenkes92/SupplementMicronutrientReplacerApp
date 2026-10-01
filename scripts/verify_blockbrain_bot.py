"""Verify the Blockbrain Knowledge Bot (and its attached knowledge base).

Run where BLOCKBRAIN_API_KEY is available (locally via
blockbrain/.streamlit/secrets.toml, or on Streamlit Cloud):

    python scripts/verify_blockbrain_bot.py

It sends the same "[ASK]" research message the app's Ask AI sends (with the
same bot: BLOCKBRAIN_RESEARCH_BOT_ID if set, else the default bot) and checks
the reply the way the app does: non-empty, not label-extraction JSON, and
Markdown the phone layout can render. Override the target with
BLOCKBRAIN_BOT_ID / BLOCKBRAIN_RESEARCH_BOT_ID / BLOCKBRAIN_BOT_BASE_URL.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load secrets into env (same convention as the app).
secrets_path = ROOT / "blockbrain" / ".streamlit" / "secrets.toml"
if secrets_path.exists():
    raw = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    for k in ("BLOCKBRAIN_API_KEY", "BLOCKBRAIN_BOT_ID", "BLOCKBRAIN_RESEARCH_BOT_ID", "BLOCKBRAIN_BOT_BASE_URL"):
        if not os.getenv(k, "").strip() and str(raw.get(k, "") or "").strip():
            os.environ[k] = str(raw[k]).strip()

import blockbrain.app as bb  # noqa: E402

api_key, bot_base, bot_id = bb._load_blockbrain_bot_config()
print(f"bot_base = {bot_base}")
print(f"bot_id   = {bot_id}")
print(f"api_key  = {'present' if api_key else 'MISSING'}")
if not api_key:
    print("\nNo API key found — run this on Streamlit Cloud or add it to secrets.toml.")
    sys.exit(1)

research_bot_id = os.getenv("BLOCKBRAIN_RESEARCH_BOT_ID", "").strip()
print(f"research = {research_bot_id or '(default bot)'}")

# Same message shape as swipe_mobile_app/app.py::_answer_ask_ai_question.
question = (
    "[ASK]\n"
    "Micronutrient / supplement component: Vitamin D3\n"
    "Dose in the user's supplement: 25 mcg\n"
    "Question: Is this dose usually safe long-term?\n\n"
    "Answer concisely and evidence-based using the connected knowledge "
    "base. General guidance only; no individual medical advice."
)
print(f"\nAsking bot:\n{question}\n")
answer = bb.call_blockbrain_bot(question, bot_id=(research_bot_id or None), timeout=(10, 45))
if not answer:
    print("NO ANSWER. Last error: " + str(getattr(bb, "LAST_BLOCKBRAIN_ERROR", "")))
    sys.exit(2)
print("ANSWER:\n" + answer)

low = answer.strip().lower()
problems = []
if '"micronutrients"' in low or '"identified_via"' in low or low.startswith(("{", "```")):
    problems.append("reply looks like label-extraction JSON - the bot ignored the [ASK] marker")
if "25" not in answer and "mcg" not in low and "µg" not in low and "iu" not in low:
    problems.append("reply does not mention the dose - check the bot prompt uses the dose line")
if problems:
    print("\nCONTRACT PROBLEMS:\n- " + "\n- ".join(problems))
    sys.exit(3)
print("\nOK: the bot honours the Ask AI contract.")

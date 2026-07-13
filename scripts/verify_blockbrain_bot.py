"""Verify the Blockbrain Knowledge Bot (and its attached knowledge base).

Run where BLOCKBRAIN_API_KEY is available (locally via
blockbrain/.streamlit/secrets.toml, or on Streamlit Cloud):

    python scripts/verify_blockbrain_bot.py

It sends a test question to the configured bot and prints the answer plus the
resolved bot id / base URL. A non-empty, on-topic answer that references the
knowledge base confirms the KB is connected. Override the target with
BLOCKBRAIN_BOT_ID / BLOCKBRAIN_BOT_BASE_URL.
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
    for k in ("BLOCKBRAIN_API_KEY", "BLOCKBRAIN_BOT_ID", "BLOCKBRAIN_BOT_BASE_URL"):
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

question = "In one sentence, what is vitamin D mainly needed for?"
print(f"\nAsking bot: {question}\n")
answer = bb.call_blockbrain_bot(question)
if answer:
    print("ANSWER:\n" + answer)
else:
    print("NO ANSWER. Last error: " + str(getattr(bb, "LAST_BLOCKBRAIN_ERROR", "")))
    sys.exit(2)

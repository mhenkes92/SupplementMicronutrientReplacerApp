"""Benchmark Blockbrain models on SuppSwipe's real meal-plan prompt.

Measures, per model: time to first streamed text (what the user feels), total
time, output length, and whether the answer looks like a usable meal plan.
Use it to pick BLOCKBRAIN_MODEL_GENERATION (meal plans, benefit comparisons,
Ask AI) — then set that key in Streamlit Cloud → App settings → Secrets.

Usage (needs BLOCKBRAIN_API_KEY in env or blockbrain/.streamlit/secrets.toml):

    python scripts/benchmark_blockbrain_models.py
    python scripts/benchmark_blockbrain_models.py --models gpt-4.1-nano gemini-2.5-flash-lite --runs 3
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

DEFAULT_MODELS = [
    "gpt-4.1-nano",
    "gpt-4o-mini",
    "gemini-2.5-flash-lite",
    "gpt-4.1-mini",
    "anthropic-claude-haiku-4.5",
]

SYSTEM_PROMPT = (
    "You are a practical sports nutritionist and recipe writer for the SuppSwipe app. "
    "Design exactly 3 meals that TOGETHER incorporate ALL of the given whole foods at roughly "
    "the daily amounts provided. Use common German-supermarket ingredients, keep it budget-friendly "
    "and realistic, and give each meal a short **bold** title followed by 3-5 short bullet points "
    "(ingredients with gram amounts, then one line on preparation). Keep each meal under 80 words. "
    "Format the reply as clean GitHub-flavored Markdown for a narrow mobile screen."
)
USER_PROMPT = (
    "Whole foods to include, with the daily amount to aim for:\n"
    "- Kiwifruit, green, raw (eat ~97 g (~1 kiwi)) for Vitamin C\n"
    "- Seeds, pumpkin, dried (eat ~140 g) for Magnesium\n"
    "- Fish, salmon, sockeye, cooked (eat ~120 g) for Vitamin D\n"
    "- Lentils, cooked (eat ~300 g) for Folate\n"
    "\nWrite exactly 3 meals now."
)


def _load_secrets_into_env() -> None:
    path = ROOT / "blockbrain" / ".streamlit" / "secrets.toml"
    if tomllib is None or not path.exists():
        return
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    for key, value in raw.items():
        if isinstance(value, str) and key.startswith("BLOCKBRAIN_") and not os.getenv(key):
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()

    _load_secrets_into_env()
    if not os.getenv("BLOCKBRAIN_API_KEY", "").strip():
        print("BLOCKBRAIN_API_KEY is not set (env or blockbrain/.streamlit/secrets.toml).")
        return 2

    import blockbrain.app as bb

    print(f"{'model':32} {'first text':>11} {'total':>8} {'chars':>6}  ok  endpoint")
    rows = []
    for model in args.models:
        ttfts, totals, sizes, oks = [], [], [], []
        endpoint = ""
        for _ in range(max(1, args.runs)):
            first = {"t": None}
            start = time.monotonic()

            def _on_text(_partial: str, _first=first, _start=start) -> None:
                if _first["t"] is None:
                    _first["t"] = time.monotonic() - _start

            text = bb.call_blockbrain_text(SYSTEM_PROMPT, USER_PROMPT, model=model, on_text=_on_text)
            total = time.monotonic() - start
            endpoint = str((bb.LAST_BLOCKBRAIN_TIMING or {}).get("endpoint", "") or "")
            ok = bool(text) and text.count("**") >= 6 and "salmon" in text.lower()
            ttfts.append(first["t"] if first["t"] is not None else total)
            totals.append(total)
            sizes.append(len(text or ""))
            oks.append(ok)
            if not text:
                print(f"  {model}: empty reply ({bb.LAST_BLOCKBRAIN_ERROR})")
        row = (model, statistics.median(ttfts), statistics.median(totals), int(statistics.median(sizes)), all(oks), endpoint)
        rows.append(row)
        print(f"{row[0]:32} {row[1]:10.1f}s {row[2]:7.1f}s {row[3]:6d}  {'yes' if row[4] else 'NO ':3} {row[5]}")

    usable = [r for r in rows if r[4]]
    if usable:
        best = min(usable, key=lambda r: r[2])
        print(f"\nFastest usable model: {best[0]} (median total {best[2]:.1f}s, first text after {best[1]:.1f}s)")
        print(f'Set in Streamlit secrets:  BLOCKBRAIN_MODEL_GENERATION = "{best[0]}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

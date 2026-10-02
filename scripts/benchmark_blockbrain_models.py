"""Benchmark Blockbrain models for SuppSwipe through blockbrain_llm_client.py (the app's only Blockbrain integration).

Per model (a KNOWN_MODELS key of the owner's sandbox org, or your own BLOCKBRAIN_BOT_ID) it measures
  * text: the real meal-plan prompt - seconds and answer length,
  * OCR (with --images): the app's label prompt on your photos - seconds, doses found, nutrients the parser
    recognises and the label-gate verdict. Compare against what is printed on the labels: LLM OCR can misread digits.
Pick the fastest model that is still exact, then set BLOCKBRAIN_MODEL (Streamlit secrets / environment) to it.

Needs BLOCKBRAIN_API_KEY and BLOCKBRAIN_ORG_ID in the environment (never put them in a file). Costs Compute Blocks.

    python scripts/benchmark_blockbrain_models.py --models claude-sonnet-5 gemini-3.8-flash gpt-5.5 --runs 2
    python scripts/benchmark_blockbrain_models.py --models claude-sonnet-5 kimi-k3 --images front.jpg back.jpg --via agentic
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import blockbrain_llm_client as client  # noqa: E402

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


def _text_bench(model: str, runs: int) -> None:
    bb = client.Blockbrain(model=model)
    times, sizes = [], []
    for _ in range(runs):
        try:
            reply = bb.chat(USER_PROMPT, system=SYSTEM_PROMPT)
        except Exception as exc:  # report every model, do not stop at the first failure
            print(f"  text  FAIL: {exc}")
            return
        times.append(reply.seconds)
        sizes.append(len(reply.text))
    print(f"  text  median {statistics.median(times):5.1f}s  (min {min(times):.1f}s, max {max(times):.1f}s)  "
          f"~{int(statistics.median(sizes))} chars  resolved model: {reply.model}")


def _ocr_bench(model: str, images: list[str], via: str) -> None:
    import blockbrain.app as app  # the app's own label prompt, parser and gate

    bb = client.Blockbrain(model=model)
    for image in images:
        try:
            reply = bb.ocr(image, prompt=app._VISION_PROMPT, via=via, max_side=app.BLOCKBRAIN_VISION_MAX_SIDE)
        except Exception as exc:
            print(f"  ocr   {Path(image).name}: FAIL: {exc}")
            continue
        gate = app.extraction_gate_report(reply.text)
        parsed = app.parse_components(reply.text)
        print(f"  ocr   {Path(image).name}: {reply.seconds:5.1f}s  doses={gate['dose_hits']}  parsed nutrients={len(parsed)}  "
              f"gate={'pass' if gate['passed'] else 'FAIL'}  route={reply.via}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["claude-sonnet-5", "gemini-3.8-flash", "gpt-5.5"],
                    help=f"KNOWN_MODELS keys: {', '.join(client.KNOWN_MODELS)}")
    ap.add_argument("--runs", type=int, default=2, help="text calls per model")
    ap.add_argument("--images", nargs="*", default=[], help="label photos to OCR with every model")
    ap.add_argument("--via", default="agentic", choices=["agentic", "cortex"])
    args = ap.parse_args()
    for model in args.models:
        print(f"{model}")
        try:
            _text_bench(model, max(1, args.runs))
            if args.images:
                _ocr_bench(model, args.images, args.via)
        except client.BlockbrainError as exc:
            print(f"  not usable: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

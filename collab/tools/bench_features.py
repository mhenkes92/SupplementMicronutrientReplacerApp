#!/usr/bin/env python3
"""B-004: latency and Compute Blocks (CB) per SuppSwipe feature, measured with the app's OWN prompts.

Needs a tree that holds the app and client v2 (--repo), the app's Blockbrain environment (BLOCKBRAIN_API_KEY, BLOCKBRAIN_ORG_ID,
BLOCKBRAIN_MODEL) and, for exact CB, an admin session token file (--jwt-file) plus the tenant URL it came from (--origin).
Nothing secret is printed or written. Every prompt carries a random request id so platform-side caching cannot flatter the
numbers. CB per call = change of the bot-scoped CB meter over a block of n sequential calls / n (the bot must be otherwise idle).
Cells: ocr (agentic vs cortex), meal plan and whole-food benefits (agentic default vs cortex sonnet-5 / haiku-4.5-fast /
gpt-4.1-nano), Ask AI (general model vs the Examine knowledge-base bot).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import math
import re
import statistics
import sys
import time
import uuid
from pathlib import Path

import requests

BLOCKY = "https://blocky.theblockbrain.ai"
EUR_PER_CB = 30 / 1_000_000  # list rate from the Blockbrain docs
HAIKU, NANO = "bedrock-anthropic-claude-haiku-4.5-fast", "azure-gpt-41-nano"


def swap(component, food, per100, dose):
    return {"decision": "replace", "component": component, "dose_value": dose, "dose_unit": "mg", "form": "",
            "selected_food": {"food_description": food, "food_category": "", "amount_per_100g": per100, "unit": "mg"}}


ITEMS = [swap("magnesium", "Seeds, pumpkin and squash seed kernels, dried", 592, 105),
         swap("vitamin c", "Peppers, sweet, red, raw", 128, 90),
         swap("zinc", "Beef, chuck, arm pot roast, cooked", 8.5, 11),
         swap("vitamin b12", "Fish, salmon, Atlantic, farmed, cooked", 0.0032, 0.0025),
         swap("vitamin e", "Nuts, almonds", 25.6, 13.4)]
# One entry per ITEMS food: English and German name stems, so the coverage check works whatever language the answer is in.
FOOD_STEMS = [("pumpkin", "kürbis"), ("pepper", "paprika"), ("beef", "rind"), ("salmon", "lachs"), ("almond", "mandel")]
_DE = {"und", "mit", "die", "der", "das", "den", "dem", "ein", "eine", "für", "zum", "zur", "oder", "bis", "etwa", "braten", "minuten",
       "gramm", "dazu", "über", "nach", "pro", "täglich", "nicht", "wird", "werden", "kann", "auch", "bei", "ist", "sind"}
_EN = {"and", "with", "the", "a", "for", "to", "or", "until", "about", "fry", "minutes", "grams", "serve", "over", "per", "daily",
       "not", "is", "are", "can", "also", "at", "of", "in"}


def looks_german(text: str) -> bool:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    return sum(w in _DE for w in words) > sum(w in _EN for w in words)


ASK = [("Vitamin K2", "How much do I need per day and which foods contain it?"),
       ("Magnesium", "Is it better to take it in the evening?"),
       ("Vitamin D3", "How much do I need in winter and can I get it from food?")]


class Meter:
    def __init__(self, jwt_file: str, org: str, origin: str):
        self.h = {"Authorization": "Bearer " + Path(jwt_file).read_text().strip(), "x-zitadel-org-id": org, "accept": "*/*",
                  "Content-Type": "application/json", "origin": origin, "referer": origin + "/"}

    def total(self, bot_id: str) -> int:
        d = dt.datetime.now(dt.timezone.utc)
        body = {"botNameSearch": "", "startTime": f"{d - dt.timedelta(days=1):%Y-%m-%d}T00:00:00.000Z",
                "endTime": f"{d + dt.timedelta(days=1):%Y-%m-%d}T23:59:59.999Z", "botIds": [bot_id]}
        r = requests.post(BLOCKY + "/user-activity/compute-block/statistic/bots", headers=self.h, json=body, timeout=60)
        r.raise_for_status()
        return int(float(r.json().get("body") or 0))


def pct(vals, p):
    s = sorted(vals)
    return s[max(0, math.ceil(p * len(s)) - 1)] if s else None


def run_cell(feature, label, bot_id, n, fn, meter, rows, check=None):
    before = meter.total(bot_id) if meter else None
    res = []
    for i in range(n):
        t0 = time.time()
        try:
            r = fn(i)
        except Exception as exc:  # a failed call is data, not a reason to stop the night
            r = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        r["total_s"] = round(time.time() - t0, 2)
        if check and "text" in r:
            r["check"] = check(r["text"])
        if "text" in r:
            r["german"] = int(looks_german(r["text"]))
        res.append(r)
        rows.append({"feature": feature, "config": label, "i": i, **r})
    ok = [r for r in res if "error" not in r]
    cb = None
    if meter:
        time.sleep(4)
        after = meter.total(bot_id)
        for _ in range(3):
            if after > before or not ok:
                break
            time.sleep(8)
            after = meter.total(bot_id)
        cb = (after - before) / n
    tt = [r["total_s"] for r in ok]
    fb = [r["ttfb_s"] for r in ok if r.get("ttfb_s")]
    cell = {"feature": feature, "config": label, "n": n, "ok": len(ok),
            "p50_s": round(statistics.median(tt), 1) if tt else None, "p95_s": pct(tt, 0.95),
            "ttfb_p50_s": round(statistics.median(fb), 1) if fb else None,
            "words": int(statistics.median([r["words"] for r in ok])) if ok else None,
            "check": round(statistics.mean([r["check"] for r in ok if "check" in r]), 2) if any("check" in r for r in ok) else None,
            "german_share": round(statistics.mean([r["german"] for r in ok if "german" in r]), 2) if any("german" in r for r in ok) else None,
            "cb_per_call": round(cb) if cb is not None else None,
            "eur_per_call": round(cb * EUR_PER_CB, 4) if cb is not None else None}
    print(json.dumps(cell), flush=True)
    return cell


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="tree with swipe_mobile_app/, blockbrain/ and client v2")
    ap.add_argument("--images", nargs="*", default=[])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--features", default="ocr,meal,benefits,ask")
    ap.add_argument("--jwt-file", default="")
    ap.add_argument("--origin", default="https://kanzleikraftwerk.kb.theblockbrain.ai")
    ap.add_argument("--kb-bot", default="")
    ap.add_argument("--cap-cb", type=int, default=150000, help="stop starting new cells when the measured CB exceed this")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import os
    repo, out = Path(a.repo), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for p in (repo, repo / "swipe_mobile_app"):
        sys.path.insert(0, str(p))
    import blockbrain.app as bb_app
    from blockbrain_llm_client import Blockbrain
    spec = importlib.util.spec_from_file_location("suppswipe_app", repo / "swipe_mobile_app" / "app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    org = os.environ["BLOCKBRAIN_ORG_ID"]
    meter = Meter(a.jwt_file, org, a.origin) if a.jwt_file else None
    pair = Blockbrain(model=os.environ.get("BLOCKBRAIN_MODEL", "claude-sonnet-5"))
    kb = Blockbrain(bot_id=a.kb_bot) if a.kb_bot else None
    features = set(a.features.split(","))
    rows, cells, spent = [], [], 0

    def stop():
        return spent > a.cap_cb

    def text_fn(client, system, user_for, route, model):
        def fn(i):
            t0, first, parts = time.time(), None, []
            for piece in client.chat_stream(user_for(i) + f"\n\n(request id {uuid.uuid4().hex[:8]})", system=system, via=route, model=model):
                if first is None and piece.strip():
                    first = time.time() - t0
                parts.append(piece)
            text = "".join(parts)
            return {"ttfb_s": round(first, 2) if first else None, "words": len(text.split()), "text": text}
        return fn

    configs = [("agentic default (today)", "agentic", None), ("cortex sonnet-5", "cortex", None),
               ("cortex haiku-4.5-fast", "cortex", HAIKU), ("cortex gpt-4.1-nano", "cortex", NANO)]
    def coverage(text):
        low = text.lower()
        return sum(any(s in low for s in stems) for stems in FOOD_STEMS) / len(FOOD_STEMS)

    if "ocr" in features:
        for img in a.images:
            raw = Path(img).read_bytes()
            payload = bb_app._vision_jpeg_payload(raw) if hasattr(bb_app, "_vision_jpeg_payload") else raw
            payload = payload[0] if isinstance(payload, tuple) else payload
            for route in ("agentic", "cortex"):
                if stop():
                    break

                def fn(i, route=route, payload=payload):
                    rep = pair.ocr(payload, prompt=bb_app._VISION_PROMPT + f"\n(request id {uuid.uuid4().hex[:8]})", via=route,
                                   max_side=bb_app.BLOCKBRAIN_VISION_MAX_SIDE)
                    return {"words": len(rep.text.split()), "text": rep.text, "usage": rep.usage}
                c = run_cell("ocr", f"{route} sonnet-5 | {Path(img).stem[:18]}", pair.bot_id, min(a.runs, 3), fn, meter, rows)
                cells.append(c)
                spent += (c["cb_per_call"] or 0) * c["n"]
    if "meal" in features:
        system, user, _ = app._meal_plan_prompts(ITEMS, "No restriction", 3, pregnant=False)
        for label, route, model in configs:
            if stop():
                break
            c = run_cell("meal plan (3 meals)", label, pair.bot_id, a.runs, text_fn(pair, system, lambda i: user, route, model), meter, rows, coverage)
            cells.append(c)
            spent += (c["cb_per_call"] or 0) * c["n"]
    if "benefits" in features:
        system, user, _ = app._benefits_prompts(ITEMS)
        for label, route, model in configs:
            if stop():
                break
            c = run_cell("whole-food benefits", label, pair.bot_id, a.runs, text_fn(pair, system, lambda i: user, route, model), meter, rows, coverage)
            cells.append(c)
            spent += (c["cb_per_call"] or 0) * c["n"]
    if "ask" in features:
        system = ("You are a supplement and micronutrient research assistant for the SuppSwipe app. Answer the user's question using "
                  "established nutrition science. Be concise, evidence-based, and practical. If the evidence is unclear or the question "
                  "is outside nutrition/supplementation, say so plainly. Do not give individual medical advice; speak in general terms."
                  + getattr(app, "_MARKDOWN_STYLE", ""))

        def user_for(i):
            comp, q = ASK[i % len(ASK)]
            return f"Micronutrient / supplement component: {comp}\nQuestion: {q}"
        asks = [("general model, agentic (today)", pair, pair.bot_id, "agentic", None),
                ("general model, cortex sonnet-5", pair, pair.bot_id, "cortex", None)]
        if kb:
            asks += [("KB bot, cortex sonnet-5", kb, kb.bot_id, "cortex", None), ("KB bot, cortex haiku-4.5-fast", kb, kb.bot_id, "cortex", HAIKU)]
        for label, client, bot, route, model in asks:
            if stop():
                break
            c = run_cell("ask AI", label, bot, a.runs, text_fn(client, system, user_for, route, model), meter, rows)
            cells.append(c)
            spent += (c["cb_per_call"] or 0) * c["n"]

    (out / "results.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    lines = ["| Feature | Config | ok/n | p50 s | p95 s | first text s | words | coverage | German share | CB/call | EUR/call |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        lines.append(f"| {c['feature']} | {c['config']} | {c['ok']}/{c['n']} | {c['p50_s']} | {c['p95_s']} | {c['ttfb_p50_s']} | {c['words']} | "
                     f"{c['check']} | {c['german_share']} | {c['cb_per_call']} | {c['eur_per_call']} |")
    (out / "summary.md").write_text("\n".join(lines) + f"\n\nCB spent in this run (sum of cells): {spent}\n", encoding="utf-8")
    print("\n".join(lines))
    print("CB spent:", spent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

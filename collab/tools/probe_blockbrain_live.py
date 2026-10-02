#!/usr/bin/env python3
"""Live diagnostics for the SuppSwipe <-> Blockbrain connection.

WHY THIS EXISTS
    The Blockbrain platform changes without notice (model ids retired, routes moved). Code written
    "blind" against a fake server drifts from reality. Run this tool whenever AI features misbehave;
    it prints what the live platform does *right now* and what to configure.

NEEDS
    * network access to agentic.theblockbrain.ai (and blocky.theblockbrain.ai for --bot-id)
    * an API key:  env BLOCKBRAIN_API_KEY   or   --key-file <path to a file containing only the key>
      The key is never printed or logged (any "sk-kb-..." in output is masked). NEVER commit a key.
    * `requests` (already an app dependency); Pillow only for the generated OCR test image.

USAGE
    python probe_blockbrain_live.py facts                      # route/model/format checks (about 40 requests)
    python probe_blockbrain_live.py facts --image label.png    # OCR check on a real label photo
    python probe_blockbrain_live.py bench --n 5 --image label.png
    python probe_blockbrain_live.py facts --bot-id <cortex bot id> # also check the bot route
    add  --out report.md  to also write the report to a file
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

AGENTIC = os.environ.get("BLOCKBRAIN_BASE_URL", "https://agentic.theblockbrain.ai").rstrip("/")
BLOCKY = os.environ.get("BLOCKBRAIN_BOT_BASE_URL", "https://blocky.theblockbrain.ai").rstrip("/")
AGENTS = ("researchAgent", "customAgent", "scientificAgent")
OCR_PROMPT = "Read the nutrition table of this supplement label. Output one line per nutrient: name, amount, unit. Plain text only."
JSON_PROMPT = (
    "Return ONLY valid minified JSON, no markdown. For each nutrient below return the 3 best whole foods in a "
    'German supermarket with approximate mg per 100 g. Schema: {"items":[{"nutrient":str,"foods":[{"name":str,"mg_per_100g":number}]}]}. '
    "Nutrients: Vitamin C, Magnesium, Zinc, Vitamin B12, Iron, Calcium."
)

_KEY = ""
_REPORT: list[str] = []
# Replies that mean "the model never received the picture" (what the app's image-missing guard looks for).
IMAGE_MISSING = re.compile(
    r"(?:\bno|\bany|\ban?)\s+(?:image|file|picture|document|attachment)s?\b[^.]{0,40}(?:attached|provided|included|uploaded)"
    r"|(?:don't|do not|can't|cannot|unable to)\s+(?:see|find|view|open)\s+(?:any|an?)\s+(?:image|file|picture|attachment)"
    r"|nothing (?:is )?attached|\b0 attached",
    re.I,
)


# ----------------------------------------------------------------------------- helpers
def out(line: str = "") -> None:
    line = re.sub(r"sk-kb-[A-Za-z0-9_\-]{6,}", "sk-kb-***", str(line))
    if _KEY:
        line = line.replace(_KEY, "***")
    _REPORT.append(line)
    print(line, flush=True)


def short(x, n: int = 110) -> str:
    s = str(x).replace("\n", " ").replace("|", "/").strip()
    return s if len(s) <= n else s[:n] + "..."


def headers(extra: dict | None = None, json_body: bool = True) -> dict:
    h = {"Authorization": "Bearer " + _KEY}
    if os.environ.get("BLOCKBRAIN_ORG_ID"):
        h["x-zitadel-org-id"] = os.environ["BLOCKBRAIN_ORG_ID"].strip()
    if json_body:
        h["Content-Type"] = "application/json"
    h.update(extra or {})
    return h


def cortex_ask(bot_id: str, question: str, model: str | None = None, web: bool = False, timeout: int = 150) -> dict:
    """Plain chat of an ordinary bot (conversation with agent ""), one question, optional per-message model / web search."""
    t0 = time.time()
    res = {"text": "", "error": "", "model": "", "events": {}, "secs": 0.0}
    convo = ""
    try:
        rc = requests.post(f"{BLOCKY}/cortex/active-bot/{bot_id}/convo", headers=headers(), timeout=30, json={
            "convoName": "probe", "defaultLanguage": "English", "isDefaultConvoName": False, "enableWebSearch": False,
            "isTemporary": True, "agent": ""})
        if rc.status_code != 200:
            res["error"] = f"convo HTTP {rc.status_code} {short(rc.text, 100)}"
            return res
        convo = rc.json()["body"]["dataRoomId"]
        if web:
            rp = requests.patch(f"{BLOCKY}/cortex/conversation/{convo}", headers=headers(), json={"enableWebSearch": True}, timeout=30)
            if rp.status_code != 200:
                res["error"] = f"web search PATCH HTTP {rp.status_code}"
                return res
        body = {"convoId": convo, "sessionId": convo, "content": question}
        if model:
            body["model"] = model
        parts: list[str] = []
        with requests.post(f"{BLOCKY}/cortex/completions/v2/user-input", headers=headers(), json=body, stream=True,
                           timeout=(15, timeout)) as r:
            if r.status_code != 200:
                res["error"] = f"user-input HTTP {r.status_code} {short(r.text, 120)}"
                return res
            event = ""
            for raw in r.iter_lines():
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                if line.startswith("event:"):
                    event = line[6:].strip()
                    res["events"][event] = res["events"].get(event, 0) + 1
                elif line.startswith("data:"):
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if event == "new_token":
                        parts.append(str(ev.get("token") or ""))
                    elif event == "user_message":
                        res["model"] = str(ev.get("model") or "")
        res["text"] = "".join(parts).strip()
    except requests.RequestException as exc:
        res["error"] = "EXC " + type(exc).__name__
    finally:
        if convo:
            try:
                requests.delete(f"{BLOCKY}/cortex/conversation/{convo}", headers=headers(json_body=False), timeout=20)
            except requests.RequestException:
                pass
    res["secs"] = round(time.time() - t0, 1)
    return res


def ui_message(text: str, extra_parts: list | None = None) -> dict:
    return {"id": str(uuid.uuid4()), "role": "user", "parts": [{"type": "text", "text": text}] + (extra_parts or [])}


def stream(agent: str, body: dict, extra_headers: dict | None = None, timeout: int = 150) -> dict:
    """POST /v2/api/agents/<agent>/stream and fold the SSE events into one dict."""
    t0 = time.time()
    res = {"http": None, "text": "", "error": "", "model": "", "usage": {}, "secs": 0.0}
    try:
        r = requests.post(f"{AGENTIC}/v2/api/agents/{agent}/stream", headers=headers(extra_headers), json=body,
                          stream=True, timeout=(15, timeout))
        res["http"] = r.status_code
        if r.status_code != 200:
            res["error"] = short(r.text, 200)
        else:
            parts: list[str] = []
            for raw in r.iter_lines():
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    ev = json.loads(payload)
                except ValueError:
                    continue
                kind = ev.get("type") if isinstance(ev, dict) else ""
                if kind == "text-delta":
                    parts.append(str(ev.get("delta", "")))
                elif kind == "error":
                    res["error"] = short(ev.get("errorText") or ev, 200)
                elif kind == "data-resolved-model":
                    res["model"] = str((ev.get("data") or {}).get("model", ""))
                elif kind == "finish":
                    res["usage"] = (ev.get("messageMetadata") or {}).get("usage") or {}
            res["text"] = "".join(parts).strip()
    except requests.RequestException as exc:
        res["error"] = "EXC " + type(exc).__name__
    res["secs"] = round(time.time() - t0, 1)
    return res


def ok(res: dict) -> bool:
    return res["http"] == 200 and bool(res["text"]) and not res["error"]


def load_image(path: str | None) -> tuple[bytes, str]:
    """Return (jpeg/png bytes, mime). A real label is downscaled to 1400 px like the app does."""
    from PIL import Image, ImageDraw, ImageFont  # Pillow is an app dependency

    if path:
        im = Image.open(path).convert("RGB")
        w, h = im.size
        if max(w, h) > 1400:
            f = 1400 / max(w, h)
            im = im.resize((int(w * f), int(h * f)))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    im = Image.new("RGB", (900, 420), "white")
    d = ImageDraw.Draw(im)
    try:
        big, small = ImageFont.load_default(size=44), ImageFont.load_default(size=30)
    except TypeError:  # old Pillow
        big = small = ImageFont.load_default()
    d.text((30, 20), "Supplement Facts", fill="black", font=big)
    for i, row in enumerate(("Vitamin C      80 mg", "Vitamin D3     25 mcg (1000 IU)", "Zinc           11 mg", "Vitamin B12    2.4 mcg")):
        d.text((30, 100 + 60 * i), row, fill="black", font=small)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def valid_json(text: str) -> bool:
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return isinstance(json.loads(t).get("items"), list)
    except (ValueError, AttributeError):
        return False


def row(cells: list) -> None:
    out("| " + " | ".join(short(c, 70) for c in cells) + " |")


# ----------------------------------------------------------------------------- facts
def cmd_facts(args) -> None:
    img, mime = load_image(args.image)
    durl = data_url(img, mime)
    expect = () if args.image else ("80", "25", "11", "2.4")
    simple = {"messages": [ui_message("Reply with the single word OK.")], "trigger": "submit-message"}

    out(f"# Blockbrain live facts ({time.strftime('%Y-%m-%d %H:%M')} local time)")
    out(f"host={AGENTIC}  image={'real label ' + os.path.basename(args.image) if args.image else 'generated test label'} ({len(img) // 1024} KB)")
    out()

    out("## 1. Plain agent route, NO `model` field")
    out("| agent | http | secs | resolved model | reply |")
    out("|---|---|---|---|---|")
    for agent in AGENTS:
        r = stream(agent, simple)
        row([agent, r["http"], r["secs"], r["model"] or "-", r["text"] or r["error"]])

    out()
    out("## 2. Explicit `model` field (what the app pins today)")
    out("| agent | model | http | secs | result |")
    out("|---|---|---|---|---|")
    for agent in AGENTS[:2]:
        for model in args.models.split(","):
            r = stream(agent, dict(simple, model=model.strip()), timeout=60)
            row([agent, model.strip(), r["http"], r["secs"], ("OK " + r["text"]) if ok(r) else "ERROR: " + (r["error"] or "empty reply")])

    out()
    out("## 3. Custom-agent id in the URL path (the app's old default route id)")
    ids = []
    try:
        lst = requests.get(f"{BLOCKY}/custom-agents", headers=headers(json_body=False), timeout=30)
        if lst.status_code == 200:
            ids = [a.get("_id") for a in (lst.json().get("body") or []) if a.get("_id")][:2]
    except requests.RequestException:
        pass
    if args.agent_id and args.agent_id not in ids:
        ids.insert(0, args.agent_id)
    out("| path id | http | note |")
    out("|---|---|---|")
    for cid in ids or ["(could not list custom agents)"]:
        if cid.startswith("("):
            row([cid, "-", ""])
            continue
        r = stream(cid, simple, timeout=30)
        row([cid, r["http"], r["error"] or r["text"]])

    out()
    out("## 4. Image attachment formats on researchAgent (no model field)")
    fmts = {
        "content[{type:image,image:dataURL}] (app today)": {"messages": [{"role": "user", "content": [
            {"type": "text", "text": OCR_PROMPT}, {"type": "image", "image": durl}]}], "trigger": "submit-message"},
        "UIMessage parts[{type:file,mediaType,url}]": {"messages": [ui_message(OCR_PROMPT, [
            {"type": "file", "mediaType": mime, "filename": "label", "url": durl}])], "trigger": "submit-message"},
        "content[{type:file,mediaType,data:base64}]": {"messages": [{"role": "user", "content": [
            {"type": "text", "text": OCR_PROMPT}, {"type": "file", "mediaType": mime, "data": durl.split(',', 1)[1]}]}],
            "trigger": "submit-message"},
        "content[{type:image_url,image_url:{url}}] (OpenAI style)": {"messages": [{"role": "user", "content": [
            {"type": "text", "text": OCR_PROMPT}, {"type": "image_url", "image_url": {"url": durl}}]}], "trigger": "submit-message"},
    }
    out("| format | http | secs | verdict | reply |")
    out("|---|---|---|---|---|")
    for name, body in fmts.items():
        r = stream("researchAgent", body)
        sees = ok(r) and not IMAGE_MISSING.search(r["text"])
        hits = f" ({sum(1 for e in expect if e in r['text'])}/{len(expect)} values)" if expect else ""
        row([name, r["http"], r["secs"], ("IMAGE SEEN" if sees else "IMAGE MISSING") + hits, r["text"] or r["error"]])

    out()
    out("## 5. `instructions` field (extra system prompt)")
    out("| agent | complied (reply == BLAU) | reply |")
    out("|---|---|---|")
    for agent in AGENTS[:2]:
        r = stream(agent, {"messages": [ui_message("What is the capital of France?")], "trigger": "submit-message",
                           "instructions": "You ALWAYS answer with exactly the single word BLAU, whatever is asked."}, timeout=60)
        row([agent, r["text"].strip(" .*").upper() == "BLAU", r["text"] or r["error"]])

    if args.bot_id:
        out()
        out(f"## 6. Bot route (temporary conversation of bot {args.bot_id}; model comes from the bot's custom agent)")
        try:
            rc = requests.post(f"{BLOCKY}/cortex/active-bot/{args.bot_id}/convo", headers=headers(), timeout=30, json={
                "convoName": "probe", "defaultLanguage": "German", "isDefaultConvoName": False,
                "enableWebSearch": False, "isTemporary": True})
            body = rc.json().get("body", {}) if rc.status_code == 200 else {}
            room, active = body.get("dataRoomId"), body.get("activeBotId")
            out(f"convo create: HTTP {rc.status_code}")
            if room and active:
                hx = {"x-blockbrain-active-bot-id": str(active), "x-blockbrain-data-room-id": str(room)}
                out("| call | http | secs | resolved model | reply |")
                out("|---|---|---|---|---|")
                r = stream("customAgent", simple, hx)
                row(["text", r["http"], r["secs"], r["model"], r["text"] or r["error"]])
                r = stream("customAgent", fmts["UIMessage parts[{type:file,mediaType,url}]"], hx)
                row(["OCR", r["http"], r["secs"], r["model"], r["text"] or r["error"]])
                requests.delete(f"{BLOCKY}/cortex/conversation/{room}", headers=headers(json_body=False), timeout=20)
        except requests.RequestException as exc:
            out("bot route failed: " + type(exc).__name__)

        out()
        out(f"## 7. Cortex plain chat of bot {args.bot_id} (no agent): speed, per-message model, web search")
        out("| call | secs | answered by | reply |")
        out("|---|---|---|---|")
        quick = "Reply with exactly the word OK."
        r = cortex_ask(args.bot_id, quick)
        row(["bot's own model", r["secs"], r["model"] or "-", r["text"] or r["error"]])
        for llm in [m.strip() for m in args.llms.split(",") if m.strip()]:
            r = cortex_ask(args.bot_id, quick, model=llm)
            row([f"model={llm}", r["secs"], r["model"] or "-", r["text"] or r["error"]])
        if args.web:
            r = cortex_ask(args.bot_id, "Search the web for the official Supplement Facts of 'Optimum Nutrition Opti-Men' and name the source URL.",
                           web=True)
            has_url = "http" in r["text"]
            row(["web=True (PATCH enableWebSearch)", r["secs"], r["model"] or "-", ("SOURCE URL PRESENT: " if has_url else "NO URL: ") + (r["text"] or r["error"])])
            out(f"events: {r['events']}  (a `web_ref_context` event means the platform really searched)")

    out()
    out("## Reading the report")
    out("* Section 7: the cortex plain chat is the fast text route; a `model=` row that errors names a model the tenant cannot use.")
    out("* Section 2 all ERROR  =>  do NOT send `model`; the platform picks the agent's default model.")
    out("* Section 3 HTTP 404  =>  custom-agent ids are not valid in the URL path; use researchAgent/customAgent (or the bot route).")
    out("* Section 4: `IMAGE SEEN` marks the formats that work; keep exactly one and drop the rest.")
    finish(args)


# ----------------------------------------------------------------------------- bench
def cmd_bench(args) -> None:
    img, mime = load_image(args.image)
    durl = data_url(img, mime)
    tasks = {
        "text-json": lambda: stream_agent_body(JSON_PROMPT),
        "ocr": lambda: stream_agent_body(OCR_PROMPT, [{"type": "file", "mediaType": mime, "filename": "label", "url": durl}]),
    }
    out(f"# Blockbrain latency benchmark ({time.strftime('%Y-%m-%d %H:%M')}), n={args.n} per cell, no `model` field")
    out("| agent | task | ok | min s | p50 s | p95 s | avg out tokens | resolved model |")
    out("|---|---|---|---|---|---|---|---|")
    for agent in args.agents.split(","):
        agent = agent.strip()
        for task, make in tasks.items():
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                runs = list(ex.map(lambda _: stream(agent, make()), range(args.n)))
            good = [r for r in runs if ok(r) and (task != "text-json" or valid_json(r["text"]))]
            secs = sorted(r["secs"] for r in good) or [0.0]
            p95 = secs[min(len(secs) - 1, int(round(0.95 * (len(secs) - 1))))]
            toks = [r["usage"].get("outputTokens", 0) for r in good if isinstance(r["usage"], dict)]
            row([agent, task, f"{len(good)}/{args.n}", secs[0], statistics.median(secs), p95,
                 int(statistics.mean(toks)) if toks else "-", (good[0]["model"] if good else "-")])
    finish(args)


def stream_agent_body(prompt: str, extra_parts: list | None = None) -> dict:
    # A random suffix defeats the platform's response cache (identical requests come back in <1 s and would
    # make the benchmark look far too fast); real traffic with new photos behaves like the uncached numbers.
    return {"messages": [ui_message(f"{prompt}\n\n(ref {uuid.uuid4().hex[:8]})", extra_parts)], "trigger": "submit-message"}


def finish(args) -> None:
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_REPORT) + "\n")
        print(f"\n(report written to {args.out})")


# ----------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    global _KEY
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key-file", help="file containing only the API key (default: env BLOCKBRAIN_API_KEY)")
    ap.add_argument("--image", help="real label photo for the OCR checks (default: generated test image)")
    ap.add_argument("--out", help="also write the report to this file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("facts", help="route/model/format checks")
    f.add_argument("--models", default="gpt-4.1-nano,gpt-4.1-mini,google-gemini-2.5-flash,gpt-5-nano",
                   help="comma separated model ids to try as `model` field")
    f.add_argument("--agent-id", default="", help="a custom-agent id to try in the URL path")
    f.add_argument("--bot-id", default="", help="cortex bot id to test the bot route and the cortex plain chat (sections 6 and 7)")
    f.add_argument("--llms", default="azure-gpt-41-nano,bedrock-anthropic-claude-haiku-4.5-fast",
                   help="comma separated model ids for the per-message `model` check of section 7")
    f.add_argument("--web", action="store_true", help="section 7 also tests web search (slow: 10-35 s)")
    f.set_defaults(fn=cmd_facts)
    b = sub.add_parser("bench", help="latency benchmark per agent")
    b.add_argument("--n", type=int, default=5)
    b.add_argument("--workers", type=int, default=2)
    b.add_argument("--agents", default=",".join(AGENTS))
    b.set_defaults(fn=cmd_bench)
    args = ap.parse_args(argv)

    _KEY = os.environ.get("BLOCKBRAIN_API_KEY", "").strip()
    if args.key_file:
        with open(args.key_file, encoding="utf-8") as fh:
            _KEY = fh.read().strip()
    if not _KEY:
        print("No API key: set BLOCKBRAIN_API_KEY or pass --key-file.", file=sys.stderr)
        return 2
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

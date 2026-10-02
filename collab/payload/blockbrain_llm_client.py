#!/usr/bin/env python3
"""Blockbrain LLMs from your own app: plain chat and image OCR (one file, needs only `requests`).

BRIEFING FOR AN AI CODING AGENT - read this before writing any Blockbrain code
==============================================================================
Blockbrain is the owner's LLM platform (Claude, GPT, Gemini, Kimi ...). Usage is billed in Compute Blocks (CB), a
token-price-weighted unit (list rate about 30 EUR per 1M CB). It replaces provider API keys.

1. Auth. Every call sends  `Authorization: Bearer <sk-kb-...>`  and  `x-zitadel-org-id: <org id>`.
   Keys belong to one user in one org. Read them from the environment (BLOCKBRAIN_API_KEY, BLOCKBRAIN_ORG_ID,
   BLOCKBRAIN_BOT_ID or BLOCKBRAIN_MODEL) - never hard-code, log or commit them. Use a key created only for this app.
2. Hosts. blocky `https://blocky.theblockbrain.ai` (bots, chats, attachments) and agentic
   `https://agentic.theblockbrain.ai` (model streaming). Paths are `/v1/api/...` and `/v2/api/...`, NOT `/api/...`.
3. The agentic stream has NO `model` field (sending one makes the call fail: verified 02.10.2026, every value ends in an SSE
   error "Something went wrong"). Its model is a property of a BOT: bot -> custom agent -> model. You pick a model by
   creating a conversation on that bot's id and passing the two ids it returns as headers (see below). One (bot, custom
   agent) pair per model; pairs for 8 models already exist in the owner's sandbox org (KNOWN_MODELS below).
   The CORTEX chat route (further down) is different: there an optional per-message `model` IS accepted (`model=` below).
4. Blockbrain has no OpenAI-style `/chat/completions`. The owner's VS Code setup (`blockbrain_proxy.py`, 127.0.0.1:4891)
   is only a LOCAL translator for Copilot Chat on his PC: unreachable from a cloud sandbox and it drops images. A cloud app
   must call Blockbrain's own API directly - this file does exactly what that proxy does, plus images.
5. `researchAgent`, `datevAgent`, ... (GET /v1/api/agents) are special-purpose agents. For a plain LLM use the base agent
   `customAgent` in the URL path. The path segment is the agent TYPE, not your agent's id (an id there is a 404).

Text chat (3 calls, ~3-6 s, temporary conversations cost no CB):
  a) POST blocky /cortex/active-bot/{botId}/convo  {"convoName","defaultLanguage":"German","isDefaultConvoName":false,
     "enableWebSearch":false,"isTemporary":true}  ->  body.dataRoomId (the conversation id) + body.activeBotId
  b) POST agentic /v2/api/agents/customAgent/stream with headers `x-blockbrain-active-bot-id: <activeBotId>` and
     `x-blockbrain-data-room-id: <dataRoomId>` and body {"messages":[AI-SDK-v6 UIMessages],"trigger":"submit-message",
     "instructions": optional system prompt}. A message is {"id","role","parts":[{"type":"text","text":...}]}.
     Without the two headers the call still "succeeds" but with a minimal context and the org default model - silent.
     Reply = SSE `data:` lines: text-delta (field `delta`), data-resolved-model, finish (messageMetadata.usage), [DONE],
     error (errorText); `: keep-alive` comments in between. Generation can take up to ~600 s: read timeout 900.
  c) DELETE blocky /cortex/conversation/{dataRoomId}
  Sometimes a run ends with only start/finish (0 tokens, no text): retry once on a NEW conversation (done in _agentic()).
  Do not send `activeTools: []` (the platform then offers an internal tool-search tool the model calls instead of yours).

Text chat, second route "cortex" (chat(..., via="cortex"); default via env BLOCKBRAIN_TEXT_ROUTE, else "agentic"):
  Plain chat of ANY ordinary bot, no custom agent needed: create the conversation with "agent": "" (like the OCR route below),
  then POST blocky /cortex/completions/v2/user-input {"convoId","sessionId","content", optional "model"} and read the SSE
  `event: new_token` / `data: {"token": ...}` lines, then DELETE the conversation. What this route can do that the agentic one
  cannot (all VERIFIED 02.10.2026 in the owner's org with the app's own key):
  * Speed: no agent prompt (~5k tokens) and no hidden reasoning before the first token. The same 6-nutrient JSON prompt took
    17-20 s through the agentic route (first token after 12-44 s of reasoning) but 6.7 s here (claude-sonnet-5), 3.3 s with
    model="azure-gpt-41-nano", 3.8 s with model="bedrock-anthropic-claude-haiku-4.5-fast". Reasoning models stay slow
    (gpt-5.4-mini 30 s, gpt-5-nano 87 s): time a model before you pin it.
  * `model=` (or env BLOCKBRAIN_TEXT_MODEL / BLOCKBRAIN_OCR_MODEL) picks the LLM PER MESSAGE: any chat id of
    GET /v1/api/models (the spec's AIModel enum), independent of which bot carries the conversation.
  * `web=True`: right after creating the conversation the client sends PATCH /cortex/conversation/{id} {"enableWebSearch":
    true} (the flag in the create call is ignored). The model then searches the web and names its source URL (11-25 s).
  * Knowledge base: a bot with a KB attached (e.g. the owner's Examine bot) answers from it on this route; Reply.sources
    lists the documents it used. Use that bot's id (Blockbrain(bot_id=...)) for such a call.
  * No separate system prompt on this route: `system` and `history` are folded into the one message. Streaming for both
    routes: chat_stream() yields the text pieces as they arrive.

Image OCR - two routes, both implemented here:
  "agentic" (default): the image goes into the user message as an AI SDK v6 file part
      {"type":"file","mediaType":"image/jpeg","filename":"scan.jpg","url":"data:image/jpeg;base64,<...>"}
      next to a text part with the instruction. Same 3 calls as text chat. The model must support vision
      (GET /v1/api/models -> supportsVision).
  "cortex": the route the owner's production tax-notice pipelines use for scans/PDFs: normal chat, upload the file as a chat
      attachment, wait until Blockbrain has processed it, ask in the chat, read the answer from SSE.
      POST blocky /cortex/active-bot/{botId}/convo with "agent": "" (plain chat, uses the bot's own model), then
      POST /cortex/conversation/{id}/attachment (multipart: files={"attachment": (name, bytes, mime)}, data={"session_id": id}),
      poll GET /cortex/conversation/{id}/attachment until status/calculatedStatus is success|success_truncated (failed|error
      = give up), POST /cortex/completions/v2/user-input {"convoId","sessionId","content"} (SSE: `event: new_token` +
      `data: {"token": ...}`; an answer starting with `### ERROR` is a platform error, not content), then DELETE the chat.
  Which route? VERIFIED 02.10.2026 against the sandbox org (selftest, Claude Sonnet 5): agentic and cortex both read a JPEG
  and a PDF invoice flawlessly (4/4). Recommended default = "agentic": 3 calls, no polling, token usage returned, any model.
  Prefer "cortex" for big multi-page scans (the owner's tax-notice pipelines run on it in production; huge PDFs through the
  agentic data URL are untested). The agentic route needs a bot bound to a custom agent (recipe in byok_modelle_anlegen.py of the owner's
  repo: POST /custom-agents {name,prompt,model,baseAgentId:"customAgent",capabilities:{}} -> POST /cortex/bot -> full-object
  PATCH /cortex/bot/{id} with agent="customAgent", customAgentId); the cortex route works with ANY ordinary bot in any org
  (the bot's own `model` field answers), so use it where you cannot create agents.
  Model check on a deliberately poor synthetic invoice (blur, noise, skew, JPEG 35): claude-sonnet-5, claude-opus-5.5,
  gemini-3.8-flash, gpt-5.5 and kimi-k3 all returned every line and number correctly, 3.5-9 s. Cost (Claude, measured): a
  text-only call is ~4.8k input tokens (platform agent prompt, mostly cached), a 1000-1500 px picture adds ~0.6-1.2k.
  That test is synthetic: before relying on it, run
  10-20 REAL samples (dense notices, handwriting, stamps, photos at an angle) and compare against your ground truth.
  Run `selftest` (add --hard, --format pdf, --via cortex, --model KEY) with your own key to re-prove this in your environment.

Practical rules for OCR: send JPEG/PNG up to ~2000 px on the long side (bigger phone photos are downscaled here when
Pillow is installed; multi-page TIFF becomes a PDF), always demand "transcribe verbatim, do not correct or summarise", and
validate numbers (IBAN checksum, sums, dates) in code - an LLM can misread digits. Uploads need a real file extension
(.jpg/.png/.pdf; "wrong-file-format" otherwise). If the pictures are client documents, use the client's org/bot, not the
owner's sandbox (confidentiality). The sandbox key belongs to his VS Code proxy: create a separate key for your app.

CLI:  python blockbrain_llm_client.py models | chat "text" [--via agentic|cortex] [--llm MODEL_ID] [--web] [--stream]
      | ocr FILE [--via agentic|cortex] [--llm MODEL_ID] | selftest [--via ...] [--format jpg|pdf] [--hard] [--text]
Lib:  bb = Blockbrain(); bb.chat("..."); bb.ocr("scan.jpg").text; for piece in bb.chat_stream("...", via="cortex"): ...
Env:  BLOCKBRAIN_API_KEY, BLOCKBRAIN_ORG_ID, BLOCKBRAIN_BOT_ID or BLOCKBRAIN_MODEL (required);
      BLOCKBRAIN_TEXT_ROUTE (agentic|cortex), BLOCKBRAIN_TEXT_MODEL, BLOCKBRAIN_OCR_MODEL (cortex only) - optional tuning.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import requests

BLOCKY = "https://blocky.theblockbrain.ai"
AGENTIC = "https://agentic.theblockbrain.ai"
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 900  # Blockbrain generates up to ~600 s and keeps the line open with keep-alives

# Owner's sandbox org (id 337063032028925795): model key -> (Blockbrain model id, bot id). Only valid with a key of that org.
KNOWN_MODELS = {
    "claude-sonnet-5": ("bedrock-anthropic-claude-sonnet-5", "6ab65f469ef45cfc6d1a2a05"),
    "claude-opus-5": ("bedrock-anthropic-claude-opus-5", "6ab65f469ef45cfc6d1a29f8"),
    "claude-opus-5.5": ("bedrock-anthropic-claude-opus-5.5", "6ab66ab39ef45cfc6d1a2bac"),
    "claude-opus-4.8": ("bedrock-anthropic-claude-opus-4.8", "6ab65f479ef45cfc6d1a2a0c"),
    "gpt-5.5": ("gpt-5.5", "6ab65f489ef45cfc6d1a2a15"),
    "gpt-6-astra": ("gpt-6-astra", "6ab66ab5c75c5610e31d8b7a"),
    "gemini-3.8-flash": ("google-gemini-3.8-flash", "6ab65f499ef45cfc6d1a2a24"),
    "kimi-k3": ("nebius-kimi-k3", "6ab66ab79ef45cfc6d1a2bb3"),
}

OCR_PROMPT = (
    "Transcribe ALL text in the attached image exactly as written, line by line, in reading order. Keep numbers, currency "
    "amounts, dates, IBANs, reference numbers and special characters (a-umlaut, o-umlaut, u-umlaut, sharp s, euro sign) "
    "unchanged. Do not correct, translate, summarise or add anything. Mark unreadable parts as [unleserlich]. "
    "Output only the transcription."
)


class BlockbrainError(RuntimeError):
    pass


@dataclass
class Reply:
    text: str
    model: str | None = None
    usage: dict = field(default_factory=dict)
    via: str = ""
    seconds: float = 0.0
    sources: list = field(default_factory=list)  # cortex route: knowledge-base documents the answer was built from


ROUTES = ("agentic", "cortex")
# The platform echoes a failed run as the answer text; "### ERROR" is checked on the first characters while streaming.
_ERROR_MARK = "### ERROR"


class Blockbrain:
    def __init__(self, api_key: str | None = None, org_id: str | None = None, bot_id: str | None = None,
                 model: str | None = None):
        self.api_key = api_key or os.environ.get("BLOCKBRAIN_API_KEY", "")
        self.org_id = org_id or os.environ.get("BLOCKBRAIN_ORG_ID", "")
        model = model or os.environ.get("BLOCKBRAIN_MODEL", "")
        self.bot_id = bot_id or os.environ.get("BLOCKBRAIN_BOT_ID") or (KNOWN_MODELS.get(model) or ("", ""))[1]
        # Optional tuning without code changes (read when the client is created, so a new client sees a changed secret).
        self.text_route = os.environ.get("BLOCKBRAIN_TEXT_ROUTE", "").strip().lower()
        self.text_model = os.environ.get("BLOCKBRAIN_TEXT_MODEL", "").strip()
        self.ocr_model = os.environ.get("BLOCKBRAIN_OCR_MODEL", "").strip()
        missing = [n for n, v in (("BLOCKBRAIN_API_KEY", self.api_key), ("BLOCKBRAIN_ORG_ID", self.org_id),
                                  ("BLOCKBRAIN_BOT_ID or BLOCKBRAIN_MODEL", self.bot_id)) if not v]
        if missing:
            raise BlockbrainError("missing configuration: " + ", ".join(missing))

    # ------------------------------------------------------------------ plumbing
    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "x-zitadel-org-id": self.org_id}

    def _json_headers(self, extra: dict | None = None) -> dict:
        return {**self._auth(), "Content-Type": "application/json", "accept": "*/*", **(extra or {})}

    def _new_convo(self, plain: bool = False) -> tuple[str, str]:
        """Temporary conversation on the bot -> (conversation id, active bot id). plain=True: normal chat (no agent mode)."""
        body = {"convoName": "app-llm-call", "defaultLanguage": "German", "isDefaultConvoName": False,
                "enableWebSearch": False, "isTemporary": True}
        if plain:
            body["agent"] = ""
        r = requests.post(f"{BLOCKY}/cortex/active-bot/{self.bot_id}/convo", headers=self._json_headers(), json=body,
                          timeout=CONNECT_TIMEOUT)
        if r.status_code != 200:
            raise BlockbrainError(f"create conversation: HTTP {r.status_code} {r.text[:300]}")
        b = (r.json().get("body") or {})
        if not b.get("dataRoomId") or not b.get("activeBotId"):
            raise BlockbrainError(f"conversation without ids: {r.text[:300]}")
        return b["dataRoomId"], b["activeBotId"]

    def _drop_convo(self, convo: str) -> None:
        try:
            requests.delete(f"{BLOCKY}/cortex/conversation/{convo}", headers=self._json_headers(), timeout=CONNECT_TIMEOUT)
        except requests.RequestException:
            pass  # a leftover temporary conversation is harmless

    # ------------------------------------------------------------------ agentic route (text + images)
    def _agentic_pieces(self, messages: list[dict], instructions: str | None, meta: dict) -> Iterator[str]:
        """One agentic run on a fresh temporary conversation: yields the text pieces; model and usage land in `meta`."""
        convo, active = self._new_convo()
        try:
            body: dict = {"messages": messages, "trigger": "submit-message"}
            if instructions:
                body["instructions"] = instructions
            headers = self._json_headers({"x-blockbrain-active-bot-id": str(active), "x-blockbrain-data-room-id": str(convo)})
            with requests.post(f"{AGENTIC}/v2/api/agents/customAgent/stream", headers=headers, json=body, stream=True,
                               timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
                if r.status_code != 200:
                    raise BlockbrainError(f"stream: HTTP {r.status_code} {r.text[:300]}")
                for raw in r.iter_lines():
                    line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except ValueError:
                        continue
                    kind = ev.get("type")
                    if kind == "text-delta":
                        piece = ev.get("delta") or ""
                        if piece:
                            yield piece
                    elif kind == "data-resolved-model":
                        meta["model"] = (ev.get("data") or {}).get("model")
                    elif kind == "finish":
                        meta["usage"] = (ev.get("messageMetadata") or {}).get("usage") or {}
                    elif kind == "error":
                        raise BlockbrainError(ev.get("errorText") or payload[:300])
        finally:
            self._drop_convo(convo)

    def _agentic(self, messages: list[dict], instructions: str | None = None) -> Reply:
        for _ in range(2):  # known platform hiccup: a run can close with no content -> one retry on a NEW conversation
            start, meta = time.time(), {}
            text = "".join(self._agentic_pieces(messages, instructions, meta))
            if text.strip():
                return Reply(text, meta.get("model"), meta.get("usage") or {}, "agentic", time.time() - start)
        raise BlockbrainError("Blockbrain returned an empty answer twice")

    # ------------------------------------------------------------------ public: text chat
    def chat(self, prompt: str, *, system: str | None = None, history: list[dict] | None = None,
             via: str | None = None, model: str | None = None, web: bool = False) -> Reply:
        """Ask a question. history: [{"role": "user"|"assistant", "content": str}, ...] before `prompt`.

        via: "agentic" or "cortex" (default: env BLOCKBRAIN_TEXT_ROUTE, else "agentic"). model (a Blockbrain model id such as
        "azure-gpt-41-nano", default env BLOCKBRAIN_TEXT_MODEL) and web=True (web search with a source URL) need "cortex".
        """
        route, messages = self._text_args(via, model, web, history, prompt)
        if route == "cortex":
            for _ in range(2):  # same one-shot retry as the agentic route for a run that closes without content
                start, meta = time.time(), {}
                text = "".join(self._cortex_chat_pieces(prompt, system, history, model or self.text_model or None, web, meta))
                if text.strip():
                    return Reply(text, meta.get("model") or model or self.text_model or None, {}, "cortex",
                                 time.time() - start, meta.get("sources") or [])
            raise BlockbrainError("Blockbrain returned an empty answer twice")
        return self._agentic(messages, system)

    def chat_stream(self, prompt: str, *, system: str | None = None, history: list[dict] | None = None,
                    via: str | None = None, model: str | None = None, web: bool = False) -> Iterator[str]:
        """Like chat(), but yields the answer as text pieces while it is written. Wrong arguments raise at once; a platform
        error raises when it happens (pieces already yielded stay valid); a run that ends empty is retried once."""
        route, messages = self._text_args(via, model, web, history, prompt)
        if route == "cortex":
            return self._pieces_with_retry(lambda meta: self._cortex_chat_pieces(
                prompt, system, history, model or self.text_model or None, web, meta))
        return self._pieces_with_retry(lambda meta: self._agentic_pieces(messages, system, meta))

    def _text_args(self, via, model, web, history, prompt):
        route = (via or self.text_route or "agentic").strip().lower()
        if route not in ROUTES:
            raise BlockbrainError(f"unknown route {route!r} (agentic|cortex)")
        if route == "agentic" and (model or web):
            raise BlockbrainError("model= and web=True need via='cortex': the agentic stream takes no model field and has no web search")
        messages = [{"id": uuid.uuid4().hex, "role": h["role"], "parts": [{"type": "text", "text": h["content"]}]}
                    for h in (history or [])]
        messages.append({"id": uuid.uuid4().hex, "role": "user", "parts": [{"type": "text", "text": prompt}]})
        return route, messages

    @staticmethod
    def _pieces_with_retry(make) -> Iterator[str]:
        for _ in range(2):
            seen = False
            for piece in make({}):
                seen = seen or bool(piece.strip())
                yield piece
            if seen:
                return
        raise BlockbrainError("Blockbrain returned an empty answer twice")

    # ------------------------------------------------------------------ public: image OCR
    def ocr(self, image: bytes | str | Path, *, prompt: str = OCR_PROMPT, via: str = "agentic",
            max_side: int = 2000, model: str | None = None) -> Reply:
        """Transcribe a picture (PNG/JPEG/WEBP/TIFF) or a PDF. `image`: file path or raw bytes.

        model (a Blockbrain model id; default env BLOCKBRAIN_OCR_MODEL) is honoured by the cortex route only."""
        data, name, mime = _load_image(image, max_side)
        if via == "agentic":
            if model:
                raise BlockbrainError("model= needs via='cortex': the agentic stream takes no model field")
            url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            msg = {"id": uuid.uuid4().hex, "role": "user", "parts": [
                {"type": "text", "text": prompt},
                {"type": "file", "mediaType": mime, "filename": name, "url": url}]}
            return self._agentic([msg])
        if via == "cortex":
            return self._cortex_ocr(data, name, mime, prompt, model or self.ocr_model or None)
        raise BlockbrainError(f"unknown route {via!r} (agentic|cortex)")

    # ------------------------------------------------------------------ cortex route (plain chat, attachment + chat)
    def _cortex_pieces(self, convo: str, content: str, model: str | None, meta: dict) -> Iterator[str]:
        """Ask in an existing conversation (POST user-input) and yield the answer pieces (SSE `new_token`).
        meta gets `model` (the model that answered, from `user_message`) and `sources` (knowledge-base documents)."""
        body: dict = {"convoId": convo, "sessionId": convo, "content": content}
        if model:
            body["model"] = model
        with requests.post(f"{BLOCKY}/cortex/completions/v2/user-input", headers=self._json_headers(), json=body, stream=True,
                           timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as resp:
            if resp.status_code not in (200, 201):
                raise BlockbrainError(f"completion: HTTP {resp.status_code} {resp.text[:300]}")
            event, head, checked = "", "", False
            for raw in resp.iter_lines():
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except ValueError:
                        continue
                    if event == "new_token":
                        piece = ev.get("token") or ""
                        if not piece:
                            continue
                        if not checked:  # a failed run is written as the answer text ("### ERROR ..."): look before showing it
                            head += piece
                            if len(head.lstrip()) < len(_ERROR_MARK):
                                continue
                            checked = True
                            if head.lstrip().startswith(_ERROR_MARK):
                                raise BlockbrainError(head[:300])
                            piece, head = head, ""
                        yield piece
                    elif event == "user_message":
                        meta["model"] = ev.get("model") or meta.get("model")
                    elif event == "prompt_context":
                        docs = [c.get("doc_name") for c in ev.get("context") or []
                                if isinstance(c, dict) and c.get("doc_name") and (c.get("score") or 0) > 0]
                        meta["sources"] = list(dict.fromkeys(docs))
                    elif event == "error":
                        raise BlockbrainError(str(ev.get("message") or ev.get("error") or payload[:300]))
            if head and not checked:  # a short answer never reached the length of the error mark
                if head.lstrip().startswith(_ERROR_MARK):
                    raise BlockbrainError(head[:300])
                yield head

    def _cortex_chat_pieces(self, prompt: str, system: str | None, history: list[dict] | None, model: str | None,
                            web: bool, meta: dict) -> Iterator[str]:
        convo, _ = self._new_convo(plain=True)
        try:
            if web:
                r = requests.patch(f"{BLOCKY}/cortex/conversation/{convo}", headers=self._json_headers(),
                                   json={"enableWebSearch": True}, timeout=CONNECT_TIMEOUT)
                if r.status_code != 200:
                    raise BlockbrainError(f"enable web search: HTTP {r.status_code} {r.text[:300]}")
            yield from self._cortex_pieces(convo, _fold_message(prompt, system, history), model, meta)
        finally:
            self._drop_convo(convo)

    def _cortex_ocr(self, data: bytes, name: str, mime: str, prompt: str, model: str | None = None) -> Reply:
        start = time.time()
        convo, _ = self._new_convo(plain=True)
        try:
            r = requests.post(f"{BLOCKY}/cortex/conversation/{convo}/attachment", headers=self._auth(),
                              files={"attachment": (name, data, mime)}, data={"session_id": convo}, timeout=120)
            if r.status_code not in (200, 201):
                raise BlockbrainError(f"attachment upload: HTTP {r.status_code} {r.text[:300]}")
            tokens = self._wait_attachment(convo)
            meta: dict = {}
            answer = "".join(self._cortex_pieces(convo, prompt, model, meta))
            return Reply(answer, meta.get("model"), {"attachmentTokens": tokens}, "cortex", time.time() - start,
                         meta.get("sources") or [])
        finally:
            self._drop_convo(convo)

    def _wait_attachment(self, convo: str, max_wait: int = 300) -> int:
        ok, bad, waited = {"success", "success_truncated"}, {"failed", "failure", "error"}, 0.0
        while waited < max_wait:
            r = requests.get(f"{BLOCKY}/cortex/conversation/{convo}/attachment", headers=self._auth(), timeout=30)
            if r.status_code == 200:
                body = r.json().get("body", [])
                atts = [a for a in (body if isinstance(body, list) else []) if isinstance(a, dict)]
                if atts:
                    states = [(str(a.get("status", "")).lower(), str(a.get("calculatedStatus", "")).lower()) for a in atts]
                    if any(s in bad or c in bad or a.get("errorMessage") for (s, c), a in zip(states, atts)):
                        raise BlockbrainError(f"attachment failed: {atts[0].get('errorMessage') or states[0]}")
                    # `calculatedStatus` says SUCCESS ~1 s before the file is processed (status IN_PROGRESS, tokens 0): asking
                    # then gets "I don't see a document" in some runs (measured 02.10.2026). Done = `status` success, or the
                    # token count is there.
                    if all(s in ok or (c in ok and int(a.get("tokens") or 0) > 0) for (s, c), a in zip(states, atts)):
                        return sum(int(a.get("tokens") or 0) for a in atts)
            step = 0.5 if waited < 10 else 2.0
            time.sleep(step)
            waited += step
        raise BlockbrainError("attachment not processed in time")

    # ------------------------------------------------------------------ catalog
    def models(self) -> list[dict]:
        """Global model catalog (id, supportsVision, supportsToolUse, maxInputTokens, maxOutputTokens)."""
        r = requests.get(f"{AGENTIC}/v1/api/models", headers=self._json_headers(), timeout=CONNECT_TIMEOUT)
        if r.status_code != 200:
            raise BlockbrainError(f"models: HTTP {r.status_code} {r.text[:200]}")
        d = r.json()
        return d.get("items") if isinstance(d, dict) else d


def _fold_message(prompt: str, system: str | None, history: list[dict] | None) -> str:
    """The cortex route has one message and no system field: instructions and earlier turns are written into it."""
    if not system and not history:
        return prompt
    parts = []
    if system:
        parts.append("### Instructions (follow them for the whole answer)\n" + system.strip())
    if history:
        turns = "\n".join(f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['content']}" for h in history)
        parts.append("### Conversation so far\n" + turns)
    parts.append("### Current message\n" + prompt)
    return "\n\n".join(parts)


def _sniff(data: bytes) -> tuple[str, str]:
    """(extension, mime) from the first bytes - an upload without a proper extension is rejected by Blockbrain."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data[:5] == b"%PDF-":
        return "pdf", "application/pdf"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tif", "image/tiff"
    raise BlockbrainError("unsupported file type (expected PNG, JPEG, WEBP, TIFF or PDF)")


def _load_image(image: bytes | str | Path, max_side: int) -> tuple[bytes, str, str]:
    """(bytes, file name with extension, mime). Pillow (optional) fixes phone-photo rotation, shrinks huge images and turns
    multi-page TIFF into a PDF (TIFF is not an accepted attachment type)."""
    if isinstance(image, (bytes, bytearray)):
        data, stem = bytes(image), "image"
    else:
        p = Path(image)
        data, stem = p.read_bytes(), p.stem
    ext, mime = _sniff(data)
    if mime.startswith("image/"):
        try:
            from PIL import Image, ImageOps
            img = Image.open(io.BytesIO(data))
            if getattr(img, "n_frames", 1) > 1:
                frames = [f.convert("RGB") for f in _frames(img)]
                buf = io.BytesIO()
                frames[0].save(buf, "PDF", save_all=True, append_images=frames[1:], resolution=200)
                return buf.getvalue(), f"{stem}.pdf", "application/pdf"
            img = ImageOps.exif_transpose(img)
            if max(img.size) > max_side:
                img.thumbnail((max_side, max_side))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=90)
                return buf.getvalue(), f"{stem}.jpg", "image/jpeg"
        except ImportError:
            pass
    return data, f"{stem}.{ext}", mime


def _frames(img):
    for i in range(img.n_frames):
        img.seek(i)
        yield img.copy()


# ---------------------------------------------------------------------- selftest / CLI
SELFTEST_LINES = [
    "Rechnung Nr. 2026-0815",
    "Muster & Soehne GmbH, Koenigsallee 12, 40212 Duesseldorf",
    "Pos. 1  Beratung 12,5 Std. x 140,00 EUR = 1.750,00 EUR",
    "Netto 1.750,00 EUR   USt 19 % 332,50 EUR   Brutto 2.082,50 EUR",
    "IBAN DE02 1203 0000 0000 2020 51",
]
SELFTEST_EXPECT = ["2026-0815", "Koenigsallee 12", "40212", "12,5", "140,00", "1.750,00", "332,50", "2.082,50",
                   "DE02 1203 0000 0000 2020 51"]


def _selftest_image(fmt: str = "jpg", hard: bool = False) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    img = Image.new("RGB", (1500, 520), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 34)
        except OSError:
            font = ImageFont.load_default()
    for i, line in enumerate(SELFTEST_LINES):
        d.text((50, 50 + i * 80), line, fill="black", font=font)
    if hard:  # low-resolution, blurry, noisy, unevenly lit and skewed - a poor phone snapshot
        import random
        random.seed(7)
        img = img.resize((1000, 347)).filter(ImageFilter.GaussianBlur(1.1))
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                v = max(0, min(255, px[x, y][0] - int(60 * x / img.width) + random.randint(-22, 22)))
                px[x, y] = (v, v, v)
    buf = io.BytesIO()
    img = img.rotate(2.5 if hard else 1.2, expand=True, fillcolor="white")
    if fmt == "pdf":
        img.save(buf, "PDF", resolution=150)
    else:
        img.save(buf, "JPEG", quality=35 if hard else 70)
    return buf.getvalue()


def _selftest(bb: Blockbrain, routes: list[str], fmt: str, hard: bool = False) -> int:
    img = _selftest_image(fmt, hard)
    failed = 0
    for via in routes:
        try:
            rep = bb.ocr(img, via=via)
        except Exception as exc:  # report every route, do not stop at the first failure
            print(f"[{via}/{fmt}] FAIL: {exc}")
            failed += 1
            continue
        norm = " ".join(rep.text.split())
        miss = [t for t in SELFTEST_EXPECT if t not in norm]
        ok = len(miss) <= 1
        failed += 0 if ok else 1
        print(f"[{via}/{fmt}{'/hard' if hard else ''}] {'PASS' if ok else 'FAIL'}  {rep.seconds:.1f}s  model={rep.model}  "
              f"in={rep.usage.get('inputTokens')} out={rep.usage.get('outputTokens')}  missing={miss}")
        print("   " + rep.text.strip().replace("\n", "\n   "))
    return 1 if failed else 0


def _selftest_text(bb: Blockbrain, routes: list[str], llm: str | None = None) -> int:
    """Text round trip per route (plain answer + streamed answer). llm: per-message model id, cortex route only."""
    failed = 0
    for via in routes:
        kw = {"via": via, **({"model": llm} if llm and via == "cortex" else {})}
        label = f"text/{via}" + (f"/{llm}" if "model" in kw else "")
        try:
            rep = bb.chat("Reply with exactly one word: OK", **kw)
            streamed = "".join(bb.chat_stream("Reply with exactly one word: OK", **kw))
        except Exception as exc:
            print(f"[{label}] FAIL: {exc}")
            failed += 1
            continue
        ok = "ok" in rep.text.lower() and "ok" in streamed.lower()
        failed += 0 if ok else 1
        print(f"[{label}] {'PASS' if ok else 'FAIL'}  {rep.seconds:.1f}s  model={rep.model}  answer={rep.text.strip()[:30]!r}  "
              f"stream={streamed.strip()[:30]!r}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Blockbrain LLM client (text chat, image OCR)")
    ap.add_argument("--model", help=f"sandbox model key: {', '.join(KNOWN_MODELS)}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models")
    c = sub.add_parser("chat")
    c.add_argument("text")
    c.add_argument("--via", choices=list(ROUTES), help="default: env BLOCKBRAIN_TEXT_ROUTE, else agentic")
    c.add_argument("--llm", help="Blockbrain model id for this message (cortex route), e.g. azure-gpt-41-nano")
    c.add_argument("--web", action="store_true", help="web search with a source URL (cortex route)")
    c.add_argument("--stream", action="store_true", help="print the answer while it is written")
    o = sub.add_parser("ocr")
    o.add_argument("file")
    o.add_argument("--via", default="agentic", choices=list(ROUTES))
    o.add_argument("--llm", help="Blockbrain model id for this photo (cortex route)")
    s = sub.add_parser("selftest")
    s.add_argument("--via", default="agentic,cortex")
    s.add_argument("--format", default="jpg", choices=["jpg", "pdf"])
    s.add_argument("--hard", action="store_true", help="degraded image (blur, noise, skew, JPEG 35) to compare models")
    s.add_argument("--text", action="store_true", help="also test text chat (plain + streamed) on the routes")
    s.add_argument("--llm", help="per-message model id for the cortex routes, e.g. azure-gpt-41-nano")
    a = ap.parse_args(argv)
    bb = Blockbrain(model=a.model)
    if a.cmd == "models":
        for m in sorted(bb.models(), key=lambda m: m.get("id", "")):
            print(f"{m.get('id'):<48} vision={m.get('supportsVision')!s:<5} tools={m.get('supportsToolUse')!s:<5} "
                  f"in={m.get('maxInputTokens')} out={m.get('maxOutputTokens')}")
        return 0
    if a.cmd == "chat":
        if a.stream:
            for piece in bb.chat_stream(a.text, via=a.via, model=a.llm, web=a.web):
                print(piece, end="", flush=True)
            print()
            return 0
        rep = bb.chat(a.text, via=a.via, model=a.llm, web=a.web)
        print(rep.text)
        print(f"[{rep.via} | {rep.model} | {rep.usage} | {rep.seconds:.1f}s | sources={rep.sources}]", file=sys.stderr)
        return 0
    if a.cmd == "ocr":
        print(bb.ocr(a.file, via=a.via, model=a.llm).text)
        return 0
    routes = [v.strip() for v in a.via.split(",") if v.strip()]
    code = _selftest(bb, routes, a.format, a.hard)
    if a.text:
        code |= _selftest_text(bb, routes, a.llm)
    return code


if __name__ == "__main__":
    sys.exit(main())

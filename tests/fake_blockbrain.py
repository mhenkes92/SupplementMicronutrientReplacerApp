"""A local fake of the Blockbrain routes blockbrain_llm_client.py uses (see its docstring), for offline tests.

It listens on 127.0.0.1 only; tests point the client at it by patching `blockbrain_llm_client.BLOCKY` / `.AGENTIC`, so no
real Blockbrain host is ever contacted and no real key is involved. Everything the client sends is recorded in
`FakeBlockbrain.requests`, so a test can assert the wire format (headers, bodies, order of calls).
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

API_KEY = "sk-kb-test-key-not-real-0123456789"
ORG_ID = "org-test-1"
BOT_ID = "bot-test-1"


class FakeBlockbrain:
    """Behaviour is scripted through plain attributes; each *_script list is consumed one item per call
    (the last item repeats once the list is used up)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.convos = 0
        self.deleted: list[str] = []
        # Agentic stream: "text" (answer), "" (a run that ends with no content), {"error": text}, {"http": status},
        # {"delay": seconds, "text": ...}.
        self.stream_script: list[Any] = ["Hello from the fake."]
        self.stream_calls = 0
        self.convo_status = 200
        self.convo_body: dict[str, Any] | None = None  # override the whole JSON body of a convo reply
        self.resolved_model = "fake-model-1"
        # Cortex route.
        self.attachment_status = "success"  # or "failed"
        self.attachment_polls_before_ready = 1
        self.attachment_upload_status = 200
        self.attachments: list[dict[str, Any]] = []
        self.completion_script: list[Any] = ["cortex fake answer"]
        self.completion_calls = 0
        self.models_body: Any = {"items": [{"id": "fake-model-1", "supportsVision": True}]}
        self._polls: dict[str, int] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.url = ""

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "FakeBlockbrain":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # the connection ends with the body: simple SSE framing

            def log_message(self, *args: Any) -> None:  # keep pytest output clean
                return

            def _body(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _send_json(self, status: int, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_sse(self, lines: list[str]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for line in lines:
                    self.wfile.write((line + "\n").encode())
                    self.wfile.flush()

            def _authorised(self) -> bool:
                ok = (
                    self.headers.get("Authorization") == f"Bearer {API_KEY}"
                    and self.headers.get("x-zitadel-org-id") == ORG_ID
                )
                if not ok:
                    self._send_json(401, {"message": "Unauthorized"})
                return ok

            def _record(self, body: bytes) -> dict[str, Any]:
                entry = {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
                with outer._lock:
                    outer.requests.append(entry)
                return entry

            def _json(self, body: bytes) -> dict[str, Any]:
                try:
                    value = json.loads(body or b"{}")
                except ValueError:
                    return {}
                return value if isinstance(value, dict) else {}

            def do_GET(self) -> None:  # noqa: N802
                self._record(b"")
                if not self._authorised():
                    return
                if self.path.startswith("/v1/api/models"):
                    return self._send_json(200, outer.models_body)
                m = re.match(r"^/cortex/conversation/([^/]+)/attachment$", self.path)
                if m:
                    convo = m.group(1)
                    with outer._lock:
                        outer._polls[convo] = outer._polls.get(convo, 0) + 1
                        ready = outer._polls[convo] > outer.attachment_polls_before_ready
                    state = outer.attachment_status if ready else "processing"
                    item = {"status": state, "calculatedStatus": state, "tokens": 321}
                    if state == "failed":
                        item["errorMessage"] = "wrong-file-format"
                    return self._send_json(200, {"body": [item]})
                self._send_json(404, {"message": "no such route"})

            def do_DELETE(self) -> None:  # noqa: N802
                self._record(b"")
                if not self._authorised():
                    return
                m = re.match(r"^/cortex/conversation/([^/]+)$", self.path)
                if not m:
                    return self._send_json(404, {"message": "no such route"})
                with outer._lock:
                    outer.deleted.append(m.group(1))
                self._send_json(200, {"success": True})

            def do_POST(self) -> None:  # noqa: N802
                body = self._body()
                entry = self._record(body)
                if not self._authorised():
                    return
                m = re.match(r"^/cortex/active-bot/([^/]+)/convo$", self.path)
                if m:
                    if outer.convo_status != 200:
                        return self._send_json(outer.convo_status, {"message": "convo refused"})
                    with outer._lock:
                        outer.convos += 1
                        n = outer.convos
                    if outer.convo_body is not None:
                        return self._send_json(200, outer.convo_body)
                    return self._send_json(200, {"body": {"dataRoomId": f"room-{n}", "activeBotId": f"abot-{n}"}})
                if self.path == "/v2/api/agents/customAgent/stream":
                    return self._stream(entry)
                m = re.match(r"^/cortex/conversation/([^/]+)/attachment$", self.path)
                if m:
                    head = body[:600].decode("latin-1")
                    name = re.search(r'filename="([^"]*)"', head)
                    mime = re.search(r"Content-Type: ([^\r\n]+)", head)
                    with outer._lock:
                        outer.attachments.append(
                            {
                                "convo": m.group(1),
                                "filename": name.group(1) if name else "",
                                "mime": mime.group(1) if mime else "",
                                "size": len(body),
                            }
                        )
                    if outer.attachment_upload_status not in (200, 201):
                        return self._send_json(outer.attachment_upload_status, {"message": "upload refused"})
                    return self._send_json(200, {"success": True})
                if self.path == "/cortex/completions/v2/user-input":
                    return self._completion(entry)
                self._send_json(404, {"message": "no such route"})

            def _next(self, script: list[Any], counter: str) -> Any:
                with outer._lock:
                    index = getattr(outer, counter)
                    setattr(outer, counter, index + 1)
                return script[min(index, len(script) - 1)]

            def _stream(self, entry: dict[str, Any]) -> None:
                headers = entry["headers"]
                convo = headers.get("x-blockbrain-data-room-id", "")
                active = headers.get("x-blockbrain-active-bot-id", "")
                if not convo or not active:  # the real platform answers silently with a minimal context
                    return self._send_json(400, {"message": "missing conversation headers"})
                item = self._next(outer.stream_script, "stream_calls")
                if isinstance(item, dict) and item.get("http"):
                    return self._send_json(int(item["http"]), {"message": item.get("message", "stream refused")})
                if isinstance(item, dict) and item.get("delay"):
                    time.sleep(float(item["delay"]))
                    item = item.get("text", "")
                events: list[dict[str, Any]] = [{"type": "start", "messageId": "m1"}]
                if isinstance(item, dict) and "error" in item:
                    events.append({"type": "error", "errorText": item["error"]})
                else:
                    events.append({"type": "data-resolved-model", "data": {"model": outer.resolved_model}})
                    text = str(item)
                    for i in range(0, len(text), 7):  # several deltas: the client must join them verbatim
                        events.append({"type": "text-delta", "id": "t", "delta": text[i : i + 7]})
                    events.append(
                        {"type": "finish", "messageMetadata": {"usage": {"inputTokens": 11, "outputTokens": len(text)}}}
                    )
                lines = [": keep-alive"]
                for ev in events:
                    lines += [f"data: {json.dumps(ev)}", ""]
                lines.append("data: [DONE]")
                self._send_sse(lines)

            def _completion(self, entry: dict[str, Any]) -> None:
                item = self._next(outer.completion_script, "completion_calls")
                if isinstance(item, dict) and item.get("http"):
                    return self._send_json(int(item["http"]), {"message": "completion refused"})
                lines = []
                if isinstance(item, dict) and "error" in item:
                    lines += ["event: error", f"data: {json.dumps({'message': item['error']})}", ""]
                else:
                    text = str(item)
                    for i in range(0, len(text), 5):
                        lines += ["event: new_token", f"data: {json.dumps({'token': text[i:i + 5]})}", ""]
                lines.append("data: [DONE]")
                self._send_sse(lines)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, args=(0.02,), name="fake-blockbrain", daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # ------------------------------------------------------------------ assertions helpers
    def calls(self, method: str, path_prefix: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == method and r["path"].startswith(path_prefix)]

    def json_bodies(self, path_prefix: str) -> list[dict[str, Any]]:
        return [json.loads(r["body"] or b"{}") for r in self.calls("POST", path_prefix)]

    def paths(self) -> list[str]:
        return [f"{r['method']} {r['path']}" for r in self.requests]

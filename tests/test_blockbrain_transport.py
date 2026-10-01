"""Offline tests for the Blockbrain SSE transport (_blockbrain_chat)."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

import blockbrain.app as bb


class FakeResponse:
    def __init__(self, status_code=200, events=None, raw_lines=None, raise_after=None):
        self.status_code = status_code
        self._events = events or []
        self._raw_lines = raw_lines
        self._raise_after = raise_after
        self.text = "" if status_code == 200 else f"error {status_code}"
        self.content = b""
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def iter_lines(self):
        lines = self._raw_lines
        if lines is None:
            lines = [f"data: {json.dumps(e)}".encode() for e in self._events] + [b"data: [DONE]"]
        for i, line in enumerate(lines):
            if self._raise_after is not None and i >= self._raise_after:
                raise ConnectionError("stream reset")
            yield line


@pytest.fixture(autouse=True)
def _reset_transport_state(monkeypatch):
    monkeypatch.delenv("BLOCKBRAIN_CHAT_ENDPOINT", raising=False)
    monkeypatch.delenv("BLOCKBRAIN_STREAM_JOIN", raising=False)
    bb._STREAM_ENDPOINT_COOLDOWN.clear()
    bb._LAST_GOOD_STREAM_URL.clear()
    yield
    bb._STREAM_ENDPOINT_COOLDOWN.clear()
    bb._LAST_GOOD_STREAM_URL.clear()


def _install(monkeypatch, responder):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return responder(url, len(calls))

    monkeypatch.setattr(bb, "_http_post", fake_post)
    return calls


AI_SDK_STREAM = [
    {"type": "start", "messageId": "m1"},
    {"type": "start-step"},
    {"type": "reasoning-delta", "id": "r", "delta": "SECRET THINKING"},
    {"type": "text-start", "id": "t"},
    {"type": "text-delta", "id": "t", "delta": "**Meal 1**\n"},
    {"type": "text-delta", "id": "t", "delta": "- 120 g sal"},
    {"type": "text-delta", "id": "t", "delta": "mon with "},
    {"type": "text-delta", "id": "t", "delta": "spinach\n\n- Oats"},
    {"type": "text-end", "id": "t"},
    {"type": "finish"},
]


def test_text_delta_stream_is_concatenated_verbatim(monkeypatch):
    _install(monkeypatch, lambda url, n: FakeResponse(events=AI_SDK_STREAM))
    out = bb._blockbrain_chat({"messages": []})
    assert out == "**Meal 1**\n- 120 g salmon with spinach\n\n- Oats"
    assert "SECRET THINKING" not in out


def test_legacy_join_mode_restores_old_behaviour(monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_STREAM_JOIN", "legacy")
    _install(monkeypatch, lambda url, n: FakeResponse(events=AI_SDK_STREAM))
    out = bb._blockbrain_chat({"messages": []})
    # Old transport: every chunk stripped and newline-joined (reasoning included).
    assert out.splitlines()[0] == "SECRET THINKING"
    assert "sal\nmon" in out


def test_untyped_events_keep_legacy_join(monkeypatch):
    events = [{"text": "Vitamin C 90 mg"}, {"content": "Zinc 11 mg"}]
    _install(monkeypatch, lambda url, n: FakeResponse(events=events))
    assert bb._blockbrain_chat({"messages": []}) == "Vitamin C 90 mg\nZinc 11 mg"


def test_openai_chunk_deltas_are_verbatim(monkeypatch):
    events = [
        {"object": "chat.completion.chunk", "choices": [{"delta": {"content": "Hel"}}]},
        {"object": "chat.completion.chunk", "choices": [{"delta": {"content": "lo world"}}]},
    ]
    _install(monkeypatch, lambda url, n: FakeResponse(events=events))
    assert bb._blockbrain_chat({"messages": []}) == "Hello world"


def test_on_text_receives_progressive_text(monkeypatch):
    _install(monkeypatch, lambda url, n: FakeResponse(events=AI_SDK_STREAM))
    seen = []
    out = bb._blockbrain_chat({"messages": []}, on_text=seen.append)
    assert seen and seen[-1] == out


def test_failed_endpoint_is_skipped_and_good_one_is_sticky(monkeypatch):
    def responder(url, n):
        if "/v2/api/agents/" in url and bb.BLOCKBRAIN_FALLBACK_AGENTS[0] not in url:
            return FakeResponse(status_code=500)
        return FakeResponse(events=[{"type": "text-delta", "delta": "ok"}])

    calls = _install(monkeypatch, responder)
    assert bb._blockbrain_chat({"messages": []}) == "ok"
    first_call_count = len(calls)
    assert first_call_count >= 2  # primary agent failed, a fallback answered
    good = calls[-1]
    calls.clear()
    assert bb._blockbrain_chat({"messages": []}) == "ok"
    assert calls == [good]  # second call goes straight to the working endpoint


def test_404_endpoints_are_tried_last(monkeypatch):
    def responder(url, n):
        if "/v2/" in url:
            return FakeResponse(status_code=404)
        return FakeResponse(events=[{"text": "v1 answer"}])

    calls = _install(monkeypatch, responder)
    assert bb._blockbrain_chat({"messages": []}) == "v1 answer"
    calls.clear()
    bb._LAST_GOOD_STREAM_URL.clear()
    assert bb._blockbrain_chat({"messages": []}) == "v1 answer"
    assert "/v1/" in calls[0]


def test_stream_error_falls_back_to_next_endpoint(monkeypatch):
    def responder(url, n):
        if n == 1:
            return FakeResponse(events=AI_SDK_STREAM, raise_after=3)
        return FakeResponse(events=[{"type": "text-delta", "delta": "second"}])

    _install(monkeypatch, responder)
    assert bb._blockbrain_chat({"messages": []}) == "second"


def test_budget_stops_the_fallback_chain(monkeypatch):
    calls = _install(monkeypatch, lambda url, n: FakeResponse(status_code=500))
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(bb.time, "monotonic", lambda: float(next(clock)))
    assert bb._blockbrain_chat({"messages": []}, budget_s=250) == ""
    assert len(calls) <= 3
    assert "budget" in bb.LAST_BLOCKBRAIN_ERROR


def test_responses_are_closed(monkeypatch):
    made = []

    def responder(url, n):
        r = FakeResponse(events=[{"type": "text-delta", "delta": "x"}])
        made.append(r)
        return r

    _install(monkeypatch, responder)
    bb._blockbrain_chat({"messages": []})
    assert made and all(r.closed for r in made)


def test_openai_endpoint_errors_reach_module_state(monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_CHAT_ENDPOINT", "/v1/chat/completions")

    def fake_post(url, **kwargs):
        if url.endswith("/v1/chat/completions"):
            raise ConnectionError("boom")
        return FakeResponse(status_code=500)

    monkeypatch.setattr(bb, "_http_post", fake_post)
    bb._blockbrain_chat({"messages": []})
    assert bb.LAST_BLOCKBRAIN_ERROR  # was silently lost before (missing `global`)


def test_call_blockbrain_text_sends_history(monkeypatch):
    sent = {}

    def fake_chat(payload, on_text=None, budget_s=None, allow_tools=False):
        sent.update(payload)
        return "answer"

    monkeypatch.setattr(bb, "_blockbrain_chat", fake_chat)
    out = bb.call_blockbrain_text(
        "sys", "new question", model="m",
        history=[{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "tool", "content": "x"}],
    )
    assert out == "answer"
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "user"]
    assert sent["model"] == "m"


def test_vision_never_uploads_full_resolution(monkeypatch):
    captured = {}

    def fake_chat(payload, on_text=None, budget_s=None, allow_tools=False):
        captured["payload"] = payload
        return "Vitamin C 90 mg"

    monkeypatch.setattr(bb, "_blockbrain_chat", fake_chat)
    buf = io.BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(buf, format="PNG")
    bb.call_blockbrain_vision(buf.getvalue())
    url = captured["payload"]["messages"][0]["content"][1]["image"]
    import base64
    img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert max(img.size) <= bb.BLOCKBRAIN_VISION_MAX_SIDE


def test_allow_tools_skips_fast_mode_flags(monkeypatch):
    bodies = []

    def fake_post(url, **kwargs):
        bodies.append((url, kwargs.get("json") or {}))
        return FakeResponse(events=[{"type": "text-delta", "delta": "ok"}])

    monkeypatch.setattr(bb, "_http_post", fake_post)
    bb._blockbrain_chat({"messages": []})
    assert bodies[-1][1].get("activeTools") == [] and bodies[-1][1].get("maxSteps") == 1
    bb._LAST_GOOD_STREAM_URL.clear()
    bb._blockbrain_chat({"messages": []}, allow_tools=True)
    assert "activeTools" not in bodies[-1][1] and "maxSteps" not in bodies[-1][1]

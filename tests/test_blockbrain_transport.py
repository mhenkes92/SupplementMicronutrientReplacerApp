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
    for state in (bb._STREAM_ENDPOINT_COOLDOWN, bb._LAST_GOOD_STREAM_URL, bb._MODEL_UNRESOLVED, bb._LAST_GOOD_MODEL, bb._AGENT_PARKED):
        state.clear()
    yield
    for state in (bb._STREAM_ENDPOINT_COOLDOWN, bb._LAST_GOOD_STREAM_URL, bb._MODEL_UNRESOLVED, bb._LAST_GOOD_MODEL, bb._AGENT_PARKED):
        state.clear()


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

    def fake_chat(payload, on_text=None, budget_s=None, allow_tools=False, **_kwargs):
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

    def fake_chat(payload, on_text=None, budget_s=None, allow_tools=False, **_kwargs):
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


def test_tool_calls_prefer_the_research_agent(monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_AGENT_ID", "fastSuppSwipe")
    monkeypatch.setenv("BLOCKBRAIN_RESEARCH_AGENT_ID", "researchAgent")
    calls = _install(monkeypatch, lambda url, n: FakeResponse(events=[{"type": "text-delta", "delta": "ok"}]))
    bb._blockbrain_chat({"messages": []})
    assert "/agents/fastSuppSwipe/" in calls[-1]
    bb._blockbrain_chat({"messages": []}, allow_tools=True)
    assert "/agents/researchAgent/" in calls[-1]
    bb._blockbrain_chat({"messages": []})  # the fast agent stays sticky for normal calls
    assert "/agents/fastSuppSwipe/" in calls[-1]


def test_configured_agent_is_retried_after_its_cooldown(monkeypatch):
    # Final review (LLM security) F8: one transient 500 from the configured
    # fast agent used to move every later call to a fallback for the whole
    # process lifetime (the last working endpoint was always tried first).
    monkeypatch.setenv("BLOCKBRAIN_AGENT_ID", "fastSuppSwipe")
    clock = {"now": 1000.0}
    monkeypatch.setattr(bb.time, "monotonic", lambda: clock["now"])
    fast_down = {"value": True}

    def responder(url, n):
        if "/fastSuppSwipe/" in url and fast_down["value"]:
            return FakeResponse(status_code=500)
        return FakeResponse(events=[{"type": "text-delta", "delta": "ok"}])

    calls = _install(monkeypatch, responder)
    assert bb._blockbrain_chat({"messages": []}) == "ok"
    assert "/fastSuppSwipe/" in calls[0] and "/fastSuppSwipe/" not in calls[-1]
    # Within the cooldown the fallback that answered is used directly.
    calls.clear()
    assert bb._blockbrain_chat({"messages": []}) == "ok"
    assert "/fastSuppSwipe/" not in calls[0]
    # The fast agent recovers; 2 h later (past the 600 s cooldown) it is first again.
    fast_down["value"] = False
    clock["now"] += 2 * 3600
    calls.clear()
    assert bb._blockbrain_chat({"messages": []}) == "ok"
    assert calls == ["https://blockbrain.test/v2/api/agents/fastSuppSwipe/stream"]


def test_order_puts_the_configured_agent_first_unless_cooling():
    eps = ["b/v2/api/agents/fast/stream", "b/v1/api/agents/fast/stream",
           "b/v2/api/agents/customAgent/stream", "b/v1/api/agents/customAgent/stream"]
    bb._LAST_GOOD_STREAM_URL["b"] = eps[2]
    assert bb._order_stream_endpoints("b", eps, eps[:2]) == [eps[0], eps[1], eps[2], eps[3]]
    bb._LAST_GOOD_STREAM_URL["b"] = eps[1]  # v1 of the configured agent answered last
    assert bb._order_stream_endpoints("b", eps, eps[:2])[:2] == [eps[1], eps[0]]
    bb._STREAM_ENDPOINT_COOLDOWN[eps[0]] = float("inf")
    bb._STREAM_ENDPOINT_COOLDOWN[eps[1]] = float("inf")
    bb._LAST_GOOD_STREAM_URL["b"] = eps[2]
    assert bb._order_stream_endpoints("b", eps, eps[:2]) == [eps[2], eps[3], eps[0], eps[1]]


# --- "Failed to resolve model configuration" (live failure, Oct 2026) -----------------

MODEL_ERROR = "[Agent researchAgent] - Failed to resolve model configuration"
GOOD_STREAM = [{"type": "text-delta", "id": "t", "delta": "**Breakfast** oats"}, {"type": "finish"}]


def _install_by_model(monkeypatch, responder):
    """fake _http_post that hands (url, sent model or "") to `responder`."""
    sent = []

    def fake_post(url, json=None, **kwargs):
        model = str((json or {}).get("model", "") or "")
        sent.append((url.split("/api/agents/")[1].split("/")[0], url.split("/")[3], model))
        return responder(url, model)

    monkeypatch.setattr(bb, "_http_post", fake_post)
    return sent


@pytest.mark.parametrize("error_events", [
    [{"type": "error", "errorText": MODEL_ERROR}],                       # AI SDK error part
    [{"type": "text-delta", "id": "t", "delta": MODEL_ERROR}, {"type": "finish"}],  # written as the answer
])
def test_an_unresolvable_model_falls_back_to_the_next_model(monkeypatch, error_events):
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(events=error_events if model == "gpt-4.1-nano" else GOOD_STREAM),
    )
    seen = []
    out = bb.call_blockbrain_text("sys", "meals please", on_text=seen.append)
    assert out == "**Breakfast** oats"
    assert [m for _agent, _v, m in sent] == ["gpt-4.1-nano", bb.BLOCKBRAIN_TEXT_MODEL_FALLBACKS[0]]
    assert not any("Failed to resolve" in text for text in seen)  # never shown while streaming
    # The next call goes straight to the model that worked.
    sent.clear()
    assert bb.call_blockbrain_text("sys", "again") == "**Breakfast** oats"
    assert [m for _agent, _v, m in sent] == [bb.BLOCKBRAIN_TEXT_MODEL_FALLBACKS[0]]


def test_the_agent_default_model_is_the_last_text_fallback(monkeypatch):
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(events=GOOD_STREAM if model == "" else [{"type": "error", "errorText": MODEL_ERROR}]),
    )
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    models = [m for _agent, _v, m in sent]
    assert models == ["gpt-4.1-nano"] + bb.BLOCKBRAIN_TEXT_MODEL_FALLBACKS  # every model once, the default last
    # The next call goes straight to what worked, also on the agent that answered
    # (which never saw the refused model itself).
    sent.clear()
    assert bb.call_blockbrain_text("sys", "again") == "**Breakfast** oats"
    assert [m for _a, _v, m in sent] == [""]


def test_an_agent_error_is_never_returned_as_an_answer(monkeypatch):
    # Every model and agent fails: the caller gets "" (and the error), not the message.
    sent = _install_by_model(monkeypatch, lambda url, model: FakeResponse(events=[{"type": "error", "errorText": MODEL_ERROR}]))
    assert bb.call_blockbrain_text("sys", "q") == ""
    assert "Failed to resolve model configuration" in bb.LAST_BLOCKBRAIN_ERROR
    assert "Failed to resolve model configuration" in bb.last_call_error()
    models = [m for _a, _v, m in sent]
    assert len(models) == len(set(models)) == 1 + len(bb.BLOCKBRAIN_TEXT_MODEL_FALLBACKS)  # each model once
    assert {v for _a, v, _m in sent} == {"v2"}  # no v1 retries
    # Still down: bounded again, never more than every model once.
    sent.clear()
    assert bb.call_blockbrain_text("sys", "q") == ""
    assert len(sent) <= 1 + len(bb.BLOCKBRAIN_TEXT_MODEL_FALLBACKS)


def test_the_app_recovers_as_soon_as_blockbrain_is_fixed(monkeypatch):
    """Review F1/FR2: after a total outage the next call reaches a healthy Blockbrain,
    also when the requested model itself stays removed (the live situation)."""
    _install_by_model(monkeypatch, lambda url, model: FakeResponse(events=[{"type": "error", "errorText": MODEL_ERROR}]))
    assert bb.call_blockbrain_text("sys", "q") == ""
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(events=[{"type": "error", "errorText": MODEL_ERROR}] if model == "gpt-4.1-nano" else GOOD_STREAM),
    )
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    # Every model failed somewhere, so the requested one may be tried once first.
    assert len(sent) <= 2
    sent.clear()
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    assert [m for _a, _v, m in sent] == [bb.BLOCKBRAIN_TEXT_MODEL_FALLBACKS[0]]


def test_other_agent_errors_move_to_the_next_agent(monkeypatch):
    primary = bb._load_blockbrain_secrets()[2]
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(
            events=[{"type": "error", "errorText": f"[Agent {primary}] - Rate limit exceeded"}]
            if f"/{primary}/" in url else GOOD_STREAM
        ),
    )
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    assert sent[0][0] == primary and sent[-1][0] != primary
    assert {m for _a, _v, m in sent} == {"gpt-4.1-nano"}  # not a model problem: same model


def test_vision_falls_back_to_another_vision_model_never_the_agent_default(monkeypatch):
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(
            events=[{"type": "error", "errorText": MODEL_ERROR}] if model == "gpt-4.1-nano"
            else [{"type": "text-delta", "id": "t", "delta": "Vitamin C 80 mg"}, {"type": "finish"}]
        ),
    )
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(buf, format="JPEG")
    assert bb.call_blockbrain_vision(buf.getvalue()) == "Vitamin C 80 mg"
    assert [m for _a, _v, m in sent] == ["gpt-4.1-nano", bb.BLOCKBRAIN_VISION_MODEL_FALLBACKS[0]]
    assert "" not in bb.BLOCKBRAIN_VISION_MODEL_FALLBACKS


def test_looks_like_agent_error():
    assert bb.looks_like_agent_error(MODEL_ERROR)
    assert bb.looks_like_agent_error("[agent customAgent]: timeout")
    assert not bb.looks_like_agent_error("**Breakfast** — oats with berries")
    assert not bb.looks_like_agent_error("[Agent] tips: eat more beans " + "x" * 500)


def test_an_agent_that_resolves_no_model_hands_over_to_another_agent(monkeypatch):
    """Live setup: BLOCKBRAIN_AGENT_ID = "researchAgent" and that agent fails every model."""
    key, base, _agent = bb._load_blockbrain_secrets()
    monkeypatch.setattr(bb, "_load_blockbrain_secrets", lambda: (key or "k", base or "https://bb.example", "researchAgent"))
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(
            events=[{"type": "error", "errorText": MODEL_ERROR}] if "/researchAgent/" in url else GOOD_STREAM
        ),
    )
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    agents = [a for a, _v, _m in sent]
    assert agents[:3] == ["researchAgent"] * 3 and agents[-1] != "researchAgent"
    assert len([1 for a, v, _m in sent if a == "researchAgent" and v == "v1"]) == 0  # v1 skipped
    sent.clear()
    assert bb.call_blockbrain_text("sys", "again") == "**Breakfast** oats"
    assert [a for a, _v, _m in sent] == [agents[-1]]  # straight to the agent that works




def test_slow_model_errors_still_reach_a_working_agent_within_the_budget(monkeypatch):
    """Review F5: researchAgent fails every model after a delay; customAgent works."""
    import time as _time
    key, base, _agent = bb._load_blockbrain_secrets()
    monkeypatch.setattr(bb, "_load_blockbrain_secrets", lambda: (key or "k", base or "https://bb.example", "researchAgent"))
    clock = {"t": 0.0}
    monkeypatch.setattr(bb.time, "monotonic", lambda: clock["t"])

    def responder(url, model):
        if "/researchAgent/" in url:
            clock["t"] += 20.0  # each failure takes 20 s
            return FakeResponse(events=[{"type": "error", "errorText": MODEL_ERROR}])
        return FakeResponse(events=GOOD_STREAM)

    sent = _install_by_model(monkeypatch, responder)
    assert bb.call_blockbrain_text("sys", "q", budget_s=90) == "**Breakfast** oats"
    assert [a for a, _v, _m in sent].count("researchAgent") == bb._MODEL_ERRORS_PER_AGENT
    del _time


def test_a_vision_model_that_cant_see_images_is_skipped(monkeypatch):
    """Review F8: "No image has been attached" from one model -> the next vision model."""
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(
            events=[{"type": "text-delta", "id": "t", "delta": "No image has been attached to this message."}, {"type": "finish"}]
            if model in {"gpt-4.1-nano", "gpt-4.1"}
            else [{"type": "text-delta", "id": "t", "delta": "Vitamin C 80 mg"}, {"type": "finish"}]
        ),
    )
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(buf, format="JPEG")
    assert bb.call_blockbrain_vision(buf.getvalue()) == "Vitamin C 80 mg"
    assert [m for _a, _v, m in sent] == ["gpt-4.1-nano", "gpt-4.1", "gpt-4o"]


def test_a_rate_limited_agent_is_cooled_down_and_its_v1_skipped(monkeypatch):
    """Review F9."""
    primary = bb._load_blockbrain_secrets()[2]
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(
            events=[{"type": "error", "errorText": f"[Agent {primary}] - Rate limit exceeded, retry later"}]
            if f"/{primary}/" in url else GOOD_STREAM
        ),
    )
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    assert [(a, v) for a, v, _m in sent][:2] == [(primary, "v2"), ("customAgent", "v2")]
    sent.clear()
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    assert sent[0][0] == "customAgent"  # the rate-limited agent is cooling down


def test_temporarily_unavailable_is_not_a_model_configuration_error():
    """Review F1: load is not configuration — it must not block the model."""
    assert not bb._is_model_config_error("[Agent X] - The model is temporarily not available due to high demand")
    assert bb.looks_like_agent_error("[Agent X] - The model is temporarily not available due to high demand")


@pytest.mark.parametrize("text", [
    "[Agent researchAgent] - Failed to resolve model configuration",
    "[Agent researchAgent] Failed to resolve model configuration",
    "[Agent researchAgent] — Failed to resolve model configuration",
    "[Agent researchAgent]: Failed to resolve model configuration",
    "Failed to resolve model configuration",
    "Error: Failed to resolve model configuration.",
])
def test_error_variants_are_recognised(text):
    """Review F3."""
    assert bb.looks_like_agent_error(text)


def test_bot_error_bodies_are_not_answers(monkeypatch):
    """Review F10: a 200 HTML page or a 200 JSON error is not a knowledge-base answer."""

    class R:
        def __init__(self, status=200, body=None, text=""):
            self.status_code, self._body, self.text = status, body, text
            self.content = b"x"

        def json(self):
            if self._body is None:
                raise ValueError("not json")
            return self._body

    replies = {}

    def fake_post(url, **kwargs):
        if url.endswith("/convo"):
            return R(body={"body": {"convoId": "c1"}})
        return replies["answer"]

    monkeypatch.setattr(bb, "_http_post", fake_post)
    replies["answer"] = R(text="<html><h1>Service temporarily unavailable</h1></html>")
    assert bb.call_blockbrain_bot("q") == "" and "non-JSON" in bb.LAST_BOT_ERROR
    replies["answer"] = R(body={"statusCode": 500, "message": "Failed to resolve model configuration"})
    assert bb.call_blockbrain_bot("q") == "" and "Failed to resolve" in bb.LAST_BOT_ERROR
    replies["answer"] = R(body={"body": {"content": "Zinc and copper compete."}})
    assert bb.call_blockbrain_bot("q") == "Zinc and copper compete." and bb.LAST_BOT_ERROR == ""


def test_a_blurry_photo_reply_is_an_answer_not_a_model_failure(monkeypatch):
    """Review FR1: "please attach a sharper photo" must not switch photo reading off."""
    reply = "The image is too blurry to read the nutrition table. Please attach a sharper photo."
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(events=[{"type": "error", "errorText": MODEL_ERROR}] if model == "gpt-4.1-nano"
                                        else [{"type": "text-delta", "id": "t", "delta": reply}, {"type": "finish"}]),
    )
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(buf, format="JPEG")
    bb.call_blockbrain_vision(buf.getvalue())
    assert len(sent) == 2  # nano refused, gpt-4.1 answered (about the photo)
    assert not any(m != "gpt-4.1-nano" for (_a, m) in bb._MODEL_UNRESOLVED)
    sent.clear()
    bb.call_blockbrain_vision(buf.getvalue())
    assert [m for _a, _v, m in sent] == ["gpt-4.1"]  # the next photo goes straight to it


def test_a_busy_model_falls_back_without_blocking_anything(monkeypatch):
    """Review R1/FR3: "temporarily not available" on the pinned model -> the next model."""
    busy = "[Agent researchAgent] - Model gpt-4.1-nano is temporarily not available. Please try again later."
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(events=[{"type": "error", "errorText": busy}] if model == "gpt-4.1-nano" else GOOD_STREAM),
    )
    for _ in range(3):
        sent.clear()
        assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
        assert [m for _a, _v, m in sent] == ["gpt-4.1-nano", "gpt-4.1-mini"]  # busy is not blocked: tried again
    assert bb._STREAM_ENDPOINT_COOLDOWN == {} and bb._AGENT_PARKED == {} and bb._MODEL_UNRESOLVED == {}


@pytest.mark.parametrize("answer", [
    "Short answer: the 'more is better' model is not supported by the evidence — 25 µg/day is plenty.",
    "Not found — this product model is not found in the databases I checked.",
    "Unknown model number; please photograph the Supplement Facts panel instead.",
    "[Agent Orange](https://example.org) was a herbicide; it is unrelated to vitamin D.",
    "[Agent-based models] are not used for nutrient advice.",
])
def test_short_real_answers_are_kept(monkeypatch, answer):
    """Review FR4: an answer that merely mentions "model … not supported" is an answer."""
    assert not bb.looks_like_agent_error(answer)
    sent = _install_by_model(
        monkeypatch, lambda url, model: FakeResponse(events=[{"type": "text-delta", "id": "t", "delta": answer}, {"type": "finish"}])
    )
    assert bb.call_blockbrain_text("sys", "q") == answer
    assert len(sent) == 1


def test_only_the_last_fallback_works_and_is_found_in_the_first_call(monkeypatch):
    """Review R2: every distinct model is tried once before giving up."""
    sent = _install_by_model(
        monkeypatch,
        lambda url, model: FakeResponse(events=GOOD_STREAM if model == "anthropic-claude-haiku-4.5"
                                        else [{"type": "error", "errorText": MODEL_ERROR}]),
    )
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    sent.clear()
    assert bb.call_blockbrain_text("sys", "q") == "**Breakfast** oats"
    assert len(sent) == 1


def test_the_failure_reason_is_per_thread():
    """Review FR5/FR6: another visitor's failure never changes this visitor's messages."""
    import threading

    bb.reset_call_error()

    def other_visitor():
        bb._CALL_STATE.error = "Blockbrain bot convo HTTP 500"

    worker = threading.Thread(target=other_visitor)
    worker.start()
    worker.join()
    assert bb.last_call_error() == ""


def test_a_model_refused_on_one_agent_goes_last_on_the_others():
    far = float("inf")
    bb._MODEL_UNRESOLVED[("researchAgent", "gpt-4.1-nano")] = far
    fallbacks = ["gpt-4.1-mini", "gpt-4o-mini", ""]
    # On the agent that refused it: left out.
    assert bb._model_candidates("gpt-4.1-nano", fallbacks, "text", "researchAgent") == (fallbacks, False)
    # On another agent: still tried, but last.
    assert bb._model_candidates("gpt-4.1-nano", fallbacks, "text", "customAgent") == (fallbacks + ["gpt-4.1-nano"], False)
    # The model that last worked leads, even if another agent refused it earlier.
    bb._MODEL_UNRESOLVED[("customAgent", "gpt-4o-mini")] = far
    bb._LAST_GOOD_MODEL["text"] = "gpt-4o-mini"
    assert bb._model_candidates("gpt-4.1-nano", fallbacks, "text", "researchAgent")[0][0] == "gpt-4o-mini"

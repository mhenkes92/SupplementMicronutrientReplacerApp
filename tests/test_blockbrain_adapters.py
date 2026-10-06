"""The app's Blockbrain functions (blockbrain/app.py) on top of blockbrain_llm_client.py, against the local fake server.

The client is the ONLY Blockbrain integration: text goes through Blockbrain.chat, photos through Blockbrain.ocr (agentic
route first, cortex as the fallback), configuration comes from the environment, and no message or log ever holds the key."""
from __future__ import annotations

import base64
import io
import logging
import re
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

import blockbrain.app as bb
import fake_blockbrain as fb

ROOT = Path(__file__).resolve().parent.parent


def _jpeg(size=(400, 300), exif_orientation=None) -> bytes:
    img = Image.new("RGB", size, "white")
    buf = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def _sent_image(fake_bb) -> Image.Image:
    part = fake_bb.json_bodies("/v2/api/agents")[0]["messages"][0]["parts"][1]
    return Image.open(io.BytesIO(base64.b64decode(part["url"].split(",", 1)[1])))


# ---------------------------------------------------------------------- text
def test_text_call_returns_the_answer_and_records_diagnostics(fake_bb):
    seen = []
    history = [
        {"role": "user", "content": " hi "},
        {"role": "system", "content": "ignored"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "hello"},
    ]
    out = bb.call_blockbrain_text(
        " Be brief. ", " What does zinc do? ", model="gpt-4.1-nano", allow_tools=True, on_text=seen.append, history=history
    )
    assert out == "Hello from the fake." and seen[-1] == out and all(out.startswith(s) for s in seen)
    assert bb.last_call_error() == "" and bb.LAST_BLOCKBRAIN_ERROR == ""
    # A streamed answer (on_text) names the configured model: the client's stream does not report the resolved one.
    assert bb.LAST_BLOCKBRAIN_MODEL == bb.blockbrain_model_label()
    assert bb.LAST_BLOCKBRAIN_TIMING["route"] == "agentic" and bb.LAST_BLOCKBRAIN_TIMING["kind"] == "text"
    body = fake_bb.json_bodies("/v2/api/agents")[0]
    assert body["instructions"] == "Be brief."
    assert [(m["role"], m["parts"][0]["text"]) for m in body["messages"]] == [
        ("user", "hi"), ("assistant", "hello"), ("user", "What does zinc do?")]
    assert "gpt-4.1-nano" not in str(fake_bb.requests)  # `model` is accepted and ignored: the bot owns the model
    assert not any("researchAgent" in r["path"] for r in fake_bb.requests)


def test_a_blank_prompt_makes_no_call(fake_bb):
    assert bb.call_blockbrain_text("sys", "   ") == ""
    assert fake_bb.requests == []


def test_a_failure_is_reported_not_raised(fake_bb):
    fake_bb.stream_script = [{"http": 500, "message": "boom"}]
    assert bb.call_blockbrain_text("sys", "q") == ""
    assert "HTTP 500" in bb.last_call_error() and bb.LAST_BLOCKBRAIN_ERROR == bb.last_call_error()
    bb.reset_call_error()
    assert bb.last_call_error() == ""
    fake_bb.stream_script = ["fine again"]  # no lingering outage state: the very next call works
    assert bb.call_blockbrain_text("sys", "q") == "fine again" and bb.last_call_error() == ""


def test_a_platform_error_written_as_the_answer_is_not_an_answer(fake_bb):
    fake_bb.stream_script = ["[Agent customAgent] - Failed to resolve model configuration"]
    assert bb.call_blockbrain_text("sys", "q") == ""
    assert "resolve model configuration" in bb.last_call_error()


def test_the_key_never_appears_in_errors_or_logs(fake_bb, caplog):
    fake_bb.stream_script = [{"http": 500, "message": f"echo of {fb.API_KEY}"}]
    with caplog.at_level(logging.DEBUG):
        assert bb.call_blockbrain_text("sys", "q") == ""
    assert fb.API_KEY not in bb.last_call_error() and fb.API_KEY not in bb.LAST_BLOCKBRAIN_ERROR
    assert fb.API_KEY not in str(bb.LAST_BLOCKBRAIN_TIMING) and fb.API_KEY not in caplog.text
    assert "***" in bb.last_call_error()


def test_missing_configuration_is_named_and_never_calls_out(fake_bb, monkeypatch):
    monkeypatch.delenv("BLOCKBRAIN_API_KEY")
    monkeypatch.delenv("BLOCKBRAIN_ORG_ID")
    err = bb.blockbrain_config_error()
    assert "BLOCKBRAIN_API_KEY" in err and "BLOCKBRAIN_ORG_ID" in err
    assert bb._text_llm_available() is False
    assert bb.call_blockbrain_text("sys", "q") == "" and "BLOCKBRAIN_API_KEY" in bb.last_call_error()
    assert bb.call_blockbrain_vision(_jpeg()) == "" and "BLOCKBRAIN_API_KEY" in bb.last_call_error()
    assert fake_bb.requests == []


def test_a_complete_configuration_is_available(fake_bb):
    assert bb.blockbrain_config_error() == "" and bb._text_llm_available() is True


def test_a_slow_answer_is_cut_off_by_the_wall_clock_budget(fake_bb, monkeypatch):
    fake_bb.stream_script = [{"delay": 3.0, "text": "late"}]
    monkeypatch.setattr(bb, "BLOCKBRAIN_TOTAL_BUDGET_S", 1.0)
    started = time.monotonic()
    assert bb.call_blockbrain_text("sys", "q") == ""
    assert time.monotonic() - started < 2.5
    assert "did not answer within 1 s" in bb.last_call_error()
    # a per-call budget wins over the default
    assert bb.call_blockbrain_text("sys", "q", budget_s=10) == "late"


def test_the_failure_reason_is_per_thread(fake_bb):
    fake_bb.stream_script = [{"http": 500}]
    assert bb.call_blockbrain_text("sys", "q") == "" and bb.last_call_error()
    seen = {}
    worker = threading.Thread(target=lambda: seen.setdefault("error", bb.last_call_error()))
    worker.start()
    worker.join()
    assert seen["error"] == ""  # another visitor's thread never sees this failure


def test_the_read_timeout_is_applied_to_the_client(fake_bb, monkeypatch):
    import blockbrain_llm_client as client

    monkeypatch.setattr(bb, "BLOCKBRAIN_READ_TIMEOUT_S", 77.0)
    bb.call_blockbrain_text("sys", "q")
    assert client.READ_TIMEOUT == 77.0


# ---------------------------------------------------------------------- secrets -> environment
def test_streamlit_secrets_feed_the_environment_but_never_override_it(monkeypatch):
    import os

    import streamlit as st

    monkeypatch.setattr(st, "secrets", {"BLOCKBRAIN_ORG_ID": "org-from-secrets", "BLOCKBRAIN_MODEL": "gpt-5.5"}, raising=False)
    monkeypatch.setenv("BLOCKBRAIN_MODEL", "claude-sonnet-5")  # already set: wins
    monkeypatch.delenv("BLOCKBRAIN_ORG_ID")
    try:
        bb._sync_blockbrain_env_from_secrets()
        assert os.environ["BLOCKBRAIN_ORG_ID"] == "org-from-secrets"
        assert os.environ["BLOCKBRAIN_MODEL"] == "claude-sonnet-5"
    finally:
        os.environ["BLOCKBRAIN_ORG_ID"] = "test-org"  # (restored by monkeypatch to the conftest value)


# ---------------------------------------------------------------------- vision
def test_vision_reads_a_photo_with_the_agentic_route(fake_bb):
    fake_bb.stream_script = ["Magnesium 300 mg 80%"]
    assert bb.call_blockbrain_vision(_jpeg(), model="gpt-4.1-nano") == "Magnesium 300 mg 80%"
    assert fake_bb.completion_calls == 0 and bb.last_call_error() == ""
    parts = fake_bb.json_bodies("/v2/api/agents")[0]["messages"][0]["parts"]
    assert "verbatim" in parts[0]["text"] and "[unreadable]" in parts[0]["text"] and "nutrient" in parts[0]["text"]
    assert parts[1]["mediaType"] == "image/jpeg"
    assert bb.LAST_VISION_ATTEMPT_LOG[0].startswith("agentic:text")
    assert bb.LAST_VISION_RAW_RESPONSE == "Magnesium 300 mg 80%"


def test_vision_falls_back_to_the_cortex_route(fake_bb):
    fake_bb.stream_script = [{"http": 404, "message": "no custom agent on this bot"}]
    assert bb.call_blockbrain_vision(_jpeg()) == "cortex fake answer"
    assert [e.split(":")[0] for e in bb.LAST_VISION_ATTEMPT_LOG] == ["agentic", "cortex"]
    assert bb.last_call_error() == "" and bb.LAST_BLOCKBRAIN_TIMING["route"] == "cortex"
    assert fake_bb.attachments and fake_bb.attachments[0]["filename"] == "image.jpg"


def test_the_configured_route_goes_first(fake_bb, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_OCR_ROUTE", "cortex")
    assert bb.call_blockbrain_vision(_jpeg()) == "cortex fake answer"
    assert fake_bb.stream_calls == 0  # agentic was never needed
    monkeypatch.setenv("BLOCKBRAIN_OCR_ROUTE", "nonsense")
    assert bb._ocr_routes() == ["agentic", "cortex"]
    monkeypatch.setenv("BLOCKBRAIN_OCR_ROUTE", " Cortex ")
    assert bb._ocr_routes() == ["cortex", "agentic"]


@pytest.mark.parametrize("blind", [
    "I don't see any image attached to your message.",
    "No image has been attached.",
    "Ich sehe leider kein Bild.",
])
def test_a_model_that_never_got_the_image_hands_over_to_the_other_route(fake_bb, blind):
    fake_bb.stream_script = [blind]
    assert bb.call_blockbrain_vision(_jpeg()) == "cortex fake answer"
    assert bb.LAST_VISION_ATTEMPT_LOG[0].startswith("agentic:image_missing")


def test_both_routes_failing_reports_why_and_returns_nothing(fake_bb):
    fake_bb.stream_script = [{"error": "Insufficient credits"}]
    fake_bb.completion_script = [{"http": 503}]
    assert bb.call_blockbrain_vision(_jpeg()) == ""
    assert "completion: HTTP 503" in bb.last_call_error()
    assert [e.split(":")[0] for e in bb.LAST_VISION_ATTEMPT_LOG] == ["agentic", "cortex"]
    assert bb.LAST_BLOCKBRAIN_ERROR == bb.last_call_error()


def test_a_platform_error_as_the_label_text_is_rejected(fake_bb):
    fake_bb.stream_script = ["[Agent customAgent] - Failed to resolve model configuration"]
    fake_bb.completion_script = ["### ERROR boom"]
    assert bb.call_blockbrain_vision(_jpeg()) == ""
    assert bb.LAST_VISION_ATTEMPT_LOG[0].startswith("agentic:platform_error")


def test_a_label_with_a_note_is_label_text(fake_bb):
    text = "Vitamin D3 20 µg (800 I.E.) 400%\nI could not read the last line."
    fake_bb.stream_script = [text]
    assert bb.call_blockbrain_vision(_jpeg()) == text


def test_unreadable_images_are_refused_before_any_call(fake_bb):
    assert bb.call_blockbrain_vision(b"not an image") == ""
    assert "unreadable or too large" in bb.last_call_error() and bb.LAST_VISION_ATTEMPT_LOG[0].startswith("image:refused")
    assert fake_bb.requests == []


def test_the_photo_is_sent_upright_and_at_most_2000_px(fake_bb):
    assert bb.call_blockbrain_vision(_jpeg((4000, 3000), exif_orientation=6))  # a phone photo, rotated by its EXIF tag
    sent = _sent_image(fake_bb)
    assert max(sent.size) <= bb.BLOCKBRAIN_VISION_MAX_SIDE == 2000
    assert sent.size[0] < sent.size[1]  # 4000x3000 landscape + orientation 6 = portrait


def test_an_exhausted_vision_budget_stops_without_a_second_route(fake_bb, monkeypatch):
    fake_bb.stream_script = [{"delay": 3.0, "text": "late"}]
    monkeypatch.setattr(bb, "BLOCKBRAIN_VISION_BUDGET_S", 1.0)
    started = time.monotonic()
    assert bb.call_blockbrain_vision(_jpeg()) == ""
    assert time.monotonic() - started < 2.5
    assert "did not answer within" in bb.last_call_error()
    assert fake_bb.completion_calls == 0 and bb.LAST_VISION_ATTEMPT_LOG == ["agentic:timeout"]


def test_extract_image_text_names_the_provider(fake_bb):
    fake_bb.stream_script = ["Zinc 10 mg 100%"]
    assert bb.extract_image_text_with_blockbrain(_jpeg()) == "Zinc 10 mg 100%"
    assert "fake-model-1" in bb.LAST_VISION_PROVIDER
    fake_bb.stream_script = [{"error": "nope"}]
    fake_bb.completion_script = [{"http": 500}]
    assert bb.extract_image_text_with_blockbrain(_jpeg()) == "" and bb.LAST_TEXT_LLM_ERROR


def test_the_model_catalog_comes_from_the_client(fake_bb):
    bb._get_blockbrain_models_catalog.cache_clear()
    try:
        fake_bb.models_body = {"items": [{"id": "m-chat", "mode": "chat", "supportsVision": True},
                                         {"id": "m-text", "mode": "chat", "supportsVision": False}]}
        assert bb._list_blockbrain_chat_model_ids() == ["m-chat", "m-text"]
        assert bb._list_blockbrain_chat_model_ids(vision_required=True) == ["m-chat"]
    finally:
        bb._get_blockbrain_models_catalog.cache_clear()


# ---------------------------------------------------------------------- the client is the ONLY integration
def test_nothing_but_the_client_talks_to_blockbrain():
    for path in [*(ROOT / "blockbrain").glob("*.py"), *(ROOT / "swipe_mobile_app").glob("*.py")]:
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:blocky|agentic)\.theblockbrain\.ai", src), path  # (a link in the privacy note is fine)
        assert "x-zitadel-org-id" not in src, path
        assert "127.0.0.1:4891" not in src, path
        assert not re.search(r"/api/agents/|/cortex/|researchAgent", src), path
    for gone in ("_blockbrain_chat", "call_blockbrain_bot", "unresolved_models", "BLOCKBRAIN_PINNED_TEXT_MODEL",
                 "BLOCKBRAIN_PINNED_VISION_MODEL", "_load_blockbrain_secrets", "BLOCKBRAIN_FALLBACK_AGENTS"):
        assert not hasattr(bb, gone), gone


def test_the_key_is_never_in_the_tree():
    # The conftest key and the fake key are not secrets; a real one has the sk-kb- prefix and a long body.
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts or "tests" in path.parts:
            continue
        assert not re.search(r"sk-kb-[A-Za-z0-9]{16,}", path.read_text(encoding="utf-8", errors="ignore")), path


# ---------------------------------------------------------------------- the benchmark script
def test_the_benchmark_script_runs_against_the_fake(fake_bb, monkeypatch, capsys, tmp_path):
    import importlib.util
    import sys

    fake_bb.stream_script = ["Supplement Facts\nVitamin C 90 mg 112%\nZinc 11 mg 110%"]
    image = tmp_path / "label.jpg"
    image.write_bytes(_jpeg())
    spec = importlib.util.spec_from_file_location("benchmark_blockbrain_models", ROOT / "scripts" / "benchmark_blockbrain_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["bench", "--models", "claude-sonnet-5", "--runs", "1", "--images", str(image)])
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "claude-sonnet-5" in out and "text  median" in out and "ocr   label.jpg" in out and "gate=pass" in out
    assert fb.API_KEY not in out

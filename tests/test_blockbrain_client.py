"""blockbrain_llm_client.py against a local fake of the Blockbrain routes (tests/fake_blockbrain.py).

Pins the wire format the module's docstring describes: auth headers, the temporary conversation, the two stream headers,
the AI SDK v6 messages, the file part of an image, the cortex attachment route, retry-once, cleanup. No real host is
contacted and no real key is used."""
from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

import blockbrain_llm_client as client
import fake_blockbrain as fb


def _jpeg(size=(300, 200), color="white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _png(size=(300, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture
def bb(fake_bb):
    return client.Blockbrain()


# ---------------------------------------------------------------------- configuration
def test_missing_configuration_names_the_variables_not_their_values(monkeypatch):
    for name in ("BLOCKBRAIN_API_KEY", "BLOCKBRAIN_ORG_ID", "BLOCKBRAIN_BOT_ID", "BLOCKBRAIN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(client.BlockbrainError) as err:
        client.Blockbrain()
    text = str(err.value)
    assert "BLOCKBRAIN_API_KEY" in text and "BLOCKBRAIN_ORG_ID" in text and "BLOCKBRAIN_BOT_ID or BLOCKBRAIN_MODEL" in text


def test_a_known_model_key_selects_its_bot(monkeypatch):
    monkeypatch.delenv("BLOCKBRAIN_BOT_ID", raising=False)
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", "k")
    monkeypatch.setenv("BLOCKBRAIN_ORG_ID", "o")
    monkeypatch.setenv("BLOCKBRAIN_MODEL", "claude-sonnet-5")
    assert client.Blockbrain().bot_id == client.KNOWN_MODELS["claude-sonnet-5"][1]
    monkeypatch.setenv("BLOCKBRAIN_MODEL", "no-such-model")  # unknown key + no bot id = not configured
    with pytest.raises(client.BlockbrainError):
        client.Blockbrain()


def test_the_client_talks_to_the_documented_hosts():
    # (the tests patch these two constants to the local fake; the module itself must name Blockbrain's real hosts)
    src = open(client.__file__, encoding="utf-8").read()
    assert 'BLOCKY = "https://blocky.theblockbrain.ai"' in src
    assert 'AGENTIC = "https://agentic.theblockbrain.ai"' in src
    assert "127.0.0.1:4891" not in src.split('"""', 2)[2]  # the VS Code proxy is only mentioned in the briefing


# ---------------------------------------------------------------------- text chat
def test_chat_wire_format(bb, fake_bb):
    reply = bb.chat("What does zinc do?", system="Be brief.", history=[
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    assert reply.text == "Hello from the fake."  # joined verbatim from several deltas
    assert reply.model == "fake-model-1" and reply.via == "agentic"
    assert reply.usage["inputTokens"] == 11

    # 3 calls: convo, stream, delete
    assert [r["method"] for r in fake_bb.requests] == ["POST", "POST", "DELETE"]
    convo_req, stream_req, delete_req = fake_bb.requests
    assert convo_req["path"] == f"/cortex/active-bot/{fb.BOT_ID}/convo"
    convo_body = json.loads(convo_req["body"])
    assert convo_body["isTemporary"] is True and convo_body["enableWebSearch"] is False and "agent" not in convo_body
    for r in fake_bb.requests:  # auth on every call
        assert r["headers"]["authorization"] == f"Bearer {fb.API_KEY}"
        assert r["headers"]["x-zitadel-org-id"] == fb.ORG_ID

    assert stream_req["path"] == "/v2/api/agents/customAgent/stream"  # the agent TYPE, not an id, not researchAgent
    assert stream_req["headers"]["x-blockbrain-active-bot-id"] == "abot-1"
    assert stream_req["headers"]["x-blockbrain-data-room-id"] == "room-1"
    body = json.loads(stream_req["body"])
    assert body["trigger"] == "submit-message" and body["instructions"] == "Be brief."
    assert "model" not in body and "activeTools" not in body  # the model is the bot's; never activeTools: []
    assert [(m["role"], m["parts"]) for m in body["messages"]] == [
        ("user", [{"type": "text", "text": "hi"}]),
        ("assistant", [{"type": "text", "text": "hello"}]),
        ("user", [{"type": "text", "text": "What does zinc do?"}]),
    ]
    assert len({m["id"] for m in body["messages"]}) == 3
    assert delete_req["path"] == "/cortex/conversation/room-1"  # temporary conversation cleaned up


def test_chat_without_system_sends_no_instructions(bb, fake_bb):
    bb.chat("q")
    assert "instructions" not in fake_bb.json_bodies("/v2/api/agents")[0]


def test_an_empty_run_is_retried_once_on_a_new_conversation(bb, fake_bb):
    fake_bb.stream_script = ["", "second try works"]
    assert bb.chat("q").text == "second try works"
    assert fake_bb.convos == 2 and fake_bb.deleted == ["room-1", "room-2"]
    heads = [r["headers"]["x-blockbrain-data-room-id"] for r in fake_bb.calls("POST", "/v2/api/agents")]
    assert heads == ["room-1", "room-2"]


def test_two_empty_runs_are_an_error(bb, fake_bb):
    fake_bb.stream_script = [""]
    with pytest.raises(client.BlockbrainError, match="empty answer twice"):
        bb.chat("q")
    assert fake_bb.stream_calls == 2 and len(fake_bb.deleted) == 2


def test_a_stream_error_event_raises_and_still_cleans_up(bb, fake_bb):
    fake_bb.stream_script = [{"error": "Insufficient credits"}]
    with pytest.raises(client.BlockbrainError, match="Insufficient credits"):
        bb.chat("q")
    assert fake_bb.deleted == ["room-1"] and fake_bb.stream_calls == 1  # an error is not retried


def test_http_errors_carry_the_status(bb, fake_bb):
    fake_bb.stream_script = [{"http": 500, "message": "boom"}]
    with pytest.raises(client.BlockbrainError, match="stream: HTTP 500"):
        bb.chat("q")
    fake_bb.convo_status = 404
    with pytest.raises(client.BlockbrainError, match="create conversation: HTTP 404"):
        bb.chat("q")


def test_a_conversation_reply_without_ids_is_an_error(bb, fake_bb):
    fake_bb.convo_body = {"body": {"dataRoomId": "r"}}
    with pytest.raises(client.BlockbrainError, match="without ids"):
        bb.chat("q")


def test_a_wrong_key_is_unauthorised_and_names_no_secret(fake_bb, monkeypatch):
    monkeypatch.setenv("BLOCKBRAIN_API_KEY", "sk-kb-wrong")
    with pytest.raises(client.BlockbrainError) as err:
        client.Blockbrain().chat("q")
    assert "401" in str(err.value) and "sk-kb-wrong" not in str(err.value)


# ---------------------------------------------------------------------- OCR, agentic route
def test_ocr_agentic_sends_the_image_as_a_file_part(bb, fake_bb):
    fake_bb.stream_script = ["Vitamin C 80 mg 100%"]
    img = _jpeg()
    reply = bb.ocr(img)
    assert reply.text == "Vitamin C 80 mg 100%" and reply.via == "agentic"
    body = fake_bb.json_bodies("/v2/api/agents")[0]
    (msg,) = body["messages"]
    text_part, file_part = msg["parts"]
    assert text_part == {"type": "text", "text": client.OCR_PROMPT}
    assert file_part["type"] == "file" and file_part["mediaType"] == "image/jpeg" and file_part["filename"] == "image.jpg"
    assert base64.b64decode(file_part["url"].split(",", 1)[1]) == img
    assert file_part["url"].startswith("data:image/jpeg;base64,")


def test_ocr_takes_a_custom_prompt_and_a_path(bb, fake_bb, tmp_path):
    path = tmp_path / "label.png"
    path.write_bytes(_png())
    bb.ocr(path, prompt="table only")
    parts = fake_bb.json_bodies("/v2/api/agents")[0]["messages"][0]["parts"]
    assert parts[0]["text"] == "table only"
    assert parts[1]["mediaType"] == "image/png" and parts[1]["filename"] == "label.png"


def test_a_big_phone_photo_is_downscaled_to_2000_px(bb, fake_bb):
    bb.ocr(_jpeg((4000, 3000)))
    part = fake_bb.json_bodies("/v2/api/agents")[0]["messages"][0]["parts"][1]
    sent = Image.open(io.BytesIO(base64.b64decode(part["url"].split(",", 1)[1])))
    assert max(sent.size) == 2000


def test_a_pdf_goes_through_unchanged(bb, fake_bb):
    bb.ocr(PDF)
    part = fake_bb.json_bodies("/v2/api/agents")[0]["messages"][0]["parts"][1]
    assert part["mediaType"] == "application/pdf" and part["filename"] == "image.pdf"
    assert base64.b64decode(part["url"].split(",", 1)[1]) == PDF


def test_a_multi_page_tiff_becomes_a_pdf(bb, fake_bb):
    buf = io.BytesIO()
    pages = [Image.new("RGB", (100, 100), c) for c in ("white", "black")]
    pages[0].save(buf, format="TIFF", save_all=True, append_images=pages[1:])
    bb.ocr(buf.getvalue())
    part = fake_bb.json_bodies("/v2/api/agents")[0]["messages"][0]["parts"][1]
    assert part["mediaType"] == "application/pdf" and part["filename"].endswith(".pdf")


def test_an_unsupported_file_type_is_refused_before_any_call(bb, fake_bb):
    with pytest.raises(client.BlockbrainError, match="unsupported file type"):
        bb.ocr(b"GIF89a....")
    assert fake_bb.requests == []


def test_an_unknown_route_is_refused(bb, fake_bb):
    with pytest.raises(client.BlockbrainError, match="unknown route"):
        bb.ocr(_jpeg(), via="telepathy")


# ---------------------------------------------------------------------- OCR, cortex route
def test_ocr_cortex_uploads_waits_and_asks(bb, fake_bb):
    reply = bb.ocr(_jpeg(), via="cortex", prompt="read it")
    assert reply.text == "cortex fake answer" and reply.via == "cortex" and reply.usage["attachmentTokens"] == 321
    convo_body = fake_bb.json_bodies("/cortex/active-bot")[0]
    assert convo_body["agent"] == ""  # a plain chat on the bot's own model
    assert fake_bb.attachments[0]["filename"] == "image.jpg" and fake_bb.attachments[0]["mime"] == "image/jpeg"
    polls = fake_bb.calls("GET", "/cortex/conversation/room-1/attachment")
    assert len(polls) == 2  # processing, then success
    ask = fake_bb.json_bodies("/cortex/completions/v2/user-input")[0]
    assert ask == {"convoId": "room-1", "sessionId": "room-1", "content": "read it"}
    assert fake_bb.deleted == ["room-1"]
    assert not fake_bb.calls("POST", "/v2/api/agents")  # the agentic stream is not used on this route


def test_cortex_platform_error_answers_are_errors(bb, fake_bb):
    fake_bb.completion_script = ["### ERROR something broke on our side"]
    with pytest.raises(client.BlockbrainError, match="### ERROR"):
        bb.ocr(_jpeg(), via="cortex")
    assert fake_bb.deleted == ["room-1"]


def test_cortex_error_event_and_http_error(bb, fake_bb):
    fake_bb.completion_script = [{"error": "model overloaded"}]
    with pytest.raises(client.BlockbrainError, match="model overloaded"):
        bb.ocr(_jpeg(), via="cortex")
    fake_bb.completion_script = [{"http": 503}]
    with pytest.raises(client.BlockbrainError, match="completion: HTTP 503"):
        bb.ocr(_jpeg(), via="cortex")


def test_cortex_attachment_failure_is_reported(bb, fake_bb):
    fake_bb.attachment_status = "failed"
    with pytest.raises(client.BlockbrainError, match="attachment failed: wrong-file-format"):
        bb.ocr(_jpeg(), via="cortex")
    assert fake_bb.deleted == ["room-1"] and not fake_bb.calls("POST", "/cortex/completions")


def test_cortex_upload_refusal_is_reported(bb, fake_bb):
    fake_bb.attachment_upload_status = 413
    with pytest.raises(client.BlockbrainError, match="attachment upload: HTTP 413"):
        bb.ocr(_jpeg(), via="cortex")


def test_cortex_gives_up_when_the_attachment_never_gets_ready(bb, fake_bb):
    fake_bb.attachment_polls_before_ready = 10**6
    with pytest.raises(client.BlockbrainError, match="not processed in time"):
        bb._wait_attachment("room-x", max_wait=6)


# ---------------------------------------------------------------------- catalog
def test_models_catalog(bb, fake_bb):
    assert bb.models() == [{"id": "fake-model-1", "supportsVision": True}]
    fake_bb.models_body = [{"id": "x"}]  # a bare list is accepted too
    assert bb.models() == [{"id": "x"}]
    assert fake_bb.calls("GET", "/v1/api/models")[0]["headers"]["authorization"] == f"Bearer {fb.API_KEY}"

"""A photo that was not read says WHY: what happened (a file the app could not open, a service that did not answer, a setting that
is wrong, an answer with nothing readable) and a technical-details line a visitor can screenshot, with no secret in it.

The owner got "the AI label reader returned no text (it may be busy, or the photo too blurry)" twice on a real iPhone and nothing
told us which of a dozen causes it was. Every cause below is forced with the local fake server (tests/fake_blockbrain.py) or with
files the app cannot open; nothing here reaches the real platform.

Also here: the bugs that the investigation of that failure found on the way (transparent pictures read as black squares, an odd EXIF
byte refusing a sharp photo, a refusal counted as a read, a stalled first route eating the whole budget, photos starved of call
slots, the failure card wiped by the history sync, one photo charged twice, the wrong word for the app-wide limit)."""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

import blockbrain.app as bb
import fake_blockbrain as fb

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")
SECRETS = (fb.API_KEY, fb.ORG_ID, fb.BOT_ID)

PLATFORM = "[Agent customAgent] - Failed to resolve model configuration"
BLIND = "I don't see any image attached to your message."
SORRY = "I'm sorry, I can't read the text in this image."
FACTS = "Supplement Facts\nVitamin C 80 mg 100%\nVitamin D3 20 µg 400%\nZinc 10 mg 100%"


def _label_photo(seed: int = 0, size=(900, 700), fmt: str = "JPEG", **save) -> bytes:
    """A picture with something on it (a flat picture is not sent), different per seed (the OCR cache is keyed by the bytes)."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for i in range(24):
        draw.text((20 + seed % 11, 20 + i * 26), f"Vitamin {i} {seed} mg", fill="black")
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return buf.getvalue()


def _jpeg(size=(400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "JPEG")
    return buf.getvalue()


def _first_variant(data: bytes) -> Image.Image:
    variants = bb.build_vision_image_variants(data)
    assert variants, bb.image_notes()
    return Image.open(io.BytesIO(variants[0][1])).convert("RGB")


# ================================================================ 1. the picture itself: what is sent, and why a file is not
def _transparent(kind: str) -> bytes:
    """Black text on a TRANSPARENT background whose hidden colour is black (what most exports store under alpha 0)."""
    size = (800, 600)
    buf = io.BytesIO()
    if kind == "RGBA":
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(img).text((40, 40), "Zinc 10 mg 100% " * 6, fill=(0, 0, 0, 255))
        img.save(buf, "PNG")
    elif kind == "LA":
        img = Image.new("LA", size, (0, 0))
        ImageDraw.Draw(img).text((40, 40), "Zinc 10 mg 100% " * 6, fill=(0, 255))
        img.save(buf, "PNG")
    elif kind == "P":
        img = Image.new("P", size, 0)
        img.putpalette([0, 0, 0, 0, 0, 0] + [0, 0, 0] * 254)
        ImageDraw.Draw(img).text((40, 40), "Zinc 10 mg 100% " * 6, fill=1)
        img.save(buf, "PNG", transparency=0)
    else:  # a lossless WebP with an alpha channel
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(img).text((40, 40), "Zinc 10 mg 100% " * 6, fill=(0, 0, 0, 255))
        img.save(buf, "WEBP", lossless=True)
    return buf.getvalue()


@pytest.mark.parametrize("kind", ["RGBA", "LA", "P", "WEBP"])
def test_a_transparent_picture_reaches_the_model_on_white_not_as_a_black_square(kind):
    """convert("RGB") drops the alpha channel and shows the colour stored under it: black. A sharp product picture saved from a web
    shop was sent as a black square (or, for black text, as nothing at all)."""
    sent = _first_variant(_transparent(kind))
    pixels = sent.convert("L").tobytes()
    assert sum(pixels) / len(pixels) > 200  # a white page ...
    assert min(pixels) < 80  # ... with the (black) text still on it


def test_an_odd_exif_block_does_not_cost_a_sharp_photo(monkeypatch):
    """ImageOps.exif_transpose raises struct.error on one wrong EXIF byte although the pixels decode fine: the photo was refused
    ("returned no text") and nothing said why. The orientation is now applied on its own."""
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to be upright: a landscape picture becomes a portrait one

    def boom(image, *a, **k):
        raise ValueError("odd EXIF")

    monkeypatch.setattr(ImageOps, "exif_transpose", boom)
    variants = bb.build_vision_image_variants(_label_photo(size=(800, 600), exif=exif))
    assert [n for n, _ in variants] == ["fast_jpeg"]
    width, height = Image.open(io.BytesIO(variants[0][1])).size
    assert (width, height) == (600, 800)


def test_a_real_corrupt_exif_byte_is_survived():
    """The same with bytes that really break Pillow's exif_transpose (one EXIF byte changed)."""
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone"
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "JPEG", exif=exif)
    data = bytearray(buf.getvalue())
    data[data.find(b"Exif\x00\x00") + 6 + 11] = 0
    try:
        ImageOps.exif_transpose(Image.open(io.BytesIO(bytes(data))))
    except Exception:
        pass
    else:
        pytest.skip("this Pillow copes with that byte itself")
    assert bb.build_vision_image_variants(bytes(data))


def _heic() -> bytes:
    return b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64


def _too_large_png() -> bytes:
    buf = io.BytesIO()
    Image.new("1", (3000, 3000)).save(buf, "PNG", optimize=True)  # 9 MP
    return buf.getvalue()


def _black() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "black").save(buf, "JPEG")
    return buf.getvalue()


@pytest.mark.parametrize(
    "data, reason, kind",
    [
        (b"", "empty", ""),
        (_heic(), "unsupported_format", "HEIC"),
        (b"GIF89a" + b"\x00" * 64, "unsupported_format", "GIF"),
        (b"%PDF-1.4\n" + b"\x00" * 64, "unsupported_format", "PDF"),
        (b"just some text, not a picture", "unsupported_format", "unknown"),
        (_label_photo(1)[:2000], "damaged", ""),  # a JPEG cut off in the middle
        (_too_large_png(), "too_large", ""),
        (_label_photo(2, size=(24, 600)), "too_small", ""),  # (a 1x1 tracking pixel or an icon: not a picture of a label)
        (_black(), "blank", ""),
    ],
    ids=["empty", "heic", "gif", "pdf", "text", "truncated", "too-large", "too-small", "black"],
)
def test_a_picture_that_is_not_sent_says_why(data, reason, kind):
    assert bb.build_vision_image_variants(data) == []
    notes = bb.image_notes()
    assert notes["reason"] == reason
    if kind:
        assert notes["kind"] == kind
    assert bb.vision_failure_report([])["group"] == "image"


def test_a_flat_light_picture_is_still_sent_but_remembered_as_flat():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "JPEG")
    assert bb.build_vision_image_variants(buf.getvalue())
    assert bb.image_notes()["flat"] is True


def test_a_4k_screenshot_is_read():
    """3840x2160 is 8.3 MP: just over the old 8 MP limit for PNG, which refused every 4K desktop screenshot."""
    assert bb.build_vision_image_variants(_label_photo(3, size=(3840, 2160), fmt="PNG"))


def test_the_image_facts_are_kept_for_the_details_line():
    big = Image.new("RGB", (4032, 3024), "white")
    ImageDraw.Draw(big).text((100, 100), "Zinc 10 mg " * 20, fill="black")
    buf = io.BytesIO()
    big.save(buf, "MPO", save_all=True, append_images=[big.resize((1008, 756))])
    variants = bb.build_vision_image_variants(buf.getvalue())
    notes = bb.image_notes()
    assert (notes["format"], notes["width"], notes["height"], notes["bytes"]) == ("MPO", 4032, 3024, len(buf.getvalue()))
    assert [(v["name"], v["bytes"]) for v in notes["variants"]] == [(n, len(d)) for n, d in variants]
    assert [(v["width"], v["height"]) for v in notes["variants"]] == [(1400, 1050), (2000, 1500)]


# ================================================================ 2. the call: refusals, stalls, slots, retries
def test_a_refusal_on_one_route_is_asked_to_the_other_route(fake_bb):
    """"I'm sorry, the image is too blurry to read" counted as a successful read (a quality word vetoed the 'image not received'
    test): no fallback, a silent drop in the app, and a second call on the SAME route."""
    fake_bb.stream_script = ["I'm sorry, the image is too blurry to read."]
    fake_bb.completion_script = [FACTS]
    assert bb.call_blockbrain_vision(_jpeg()) == FACTS
    assert [e.split(" | ")[0] for e in bb.LAST_VISION_ATTEMPT_LOG] == ["agentic:refusal", "cortex:text"]
    assert bb.last_call_error() == ""


def test_a_stalled_first_route_leaves_time_for_the_other_one(fake_bb, monkeypatch):
    """The first route used to be allowed the whole budget (120 s live): a platform that held the line open without answering meant
    the cortex route was never asked."""
    fake_bb.stream_script = [{"delay": 3.0, "text": "late"}]
    monkeypatch.setattr(bb, "BLOCKBRAIN_VISION_BUDGET_S", 3.0)
    monkeypatch.setattr(bb, "BLOCKBRAIN_VISION_FIRST_ROUTE_S", 1.0)
    started = time.monotonic()
    assert bb.call_blockbrain_vision(_jpeg()) == "cortex fake answer"
    assert time.monotonic() - started < 2.5
    assert [e.split(" | ")[0] for e in bb.LAST_VISION_ATTEMPT_LOG] == ["agentic:timeout", "cortex:text"]


def test_an_empty_cortex_answer_is_asked_once_more(fake_bb, monkeypatch):
    """The client retries an empty agentic run on a new conversation but returns an empty cortex answer as it is."""
    monkeypatch.setenv("BLOCKBRAIN_OCR_ROUTE", "cortex")
    fake_bb.completion_script = ["", FACTS]
    assert bb.call_blockbrain_vision(_jpeg()) == FACTS
    assert fake_bb.completion_calls == 2
    fake_bb.completion_script = ["", ""]
    fake_bb.completion_calls = 0
    fake_bb.stream_script = [{"http": 500}]  # (the agentic route is the fallback here)
    assert bb.call_blockbrain_vision(_jpeg()) == ""
    assert fake_bb.completion_calls == 2  # once more, not for ever


def test_a_photo_has_call_slots_of_its_own(fake_bb, monkeypatch):
    """The meal plan, Ask AI and the product-image reader share 8 slots; when they were all taken every photo was refused with
    "busy" (shown as "returned no text") although Blockbrain was fine."""
    taken = threading.BoundedSemaphore(1)
    taken.acquire()
    monkeypatch.setattr(bb, "_WORKER_SLOTS", taken)
    fake_bb.stream_script = [FACTS]
    assert bb.call_blockbrain_vision(_jpeg()) == FACTS
    assert bb.call_blockbrain_vision(_jpeg(), background=True) == ""  # a background read still shares the general slots
    assert "busy" in bb.last_call_error()
    assert set(bb.call_slots_free()) == {"general", "photo"}


def test_every_call_slot_taken_is_reported_as_not_sent_rather_than_as_an_answer_that_never_came(fake_bb, monkeypatch, sw):
    """"Blockbrain is busy: too many calls are still running" is refused on this side, before anything is sent."""
    taken = threading.BoundedSemaphore(1)
    taken.acquire()
    monkeypatch.setattr(bb, "_VISION_SLOTS", taken)
    bb.reset_vision_trace()
    assert bb.call_blockbrain_vision(_jpeg()) == "" and fake_bb.requests == []
    report = bb.vision_failure_report([("fast_jpeg", bb.vision_attempts())])
    assert (report["kind"], report["group"]) == ("slots_busy", "no_answer"), report["lines"]
    message = sw._photo_failure_message(report)
    assert "your photo was not sent" in message and "Too many AI requests" in message and "blurry" not in message


def test_the_product_image_reader_reads_three_pictures_at_a_time_and_stops_when_it_has_the_table(monkeypatch):
    """One product-link click used to start six vision calls at once (six of the eight slots) and left the losing reads running."""
    gallery = "".join(f'<img data-old-hires="https://m.media-amazon.com/images/I/{i}.jpg">' for i in range(8))
    urls = bb.product_image_urls(gallery, "https://www.amazon.de/dp/X")
    assert len(urls) == 8
    lock = threading.Lock()
    state = {"now": 0, "peak": 0, "calls": 0}
    table = ("Premium Multi\npro Tagesdosis (2 Kapseln)\nVitamin C 200mg 250%\nVitamin D3 20µg 400%\nZink 6,5mg 65%\nSelen 50µg 91%\n"
             "Magnesium 150mg 40%\nVitamin B12 20µg 800%\nVitamin E 12mg 100%\nVitamin K 75µg 100%\nBiotin 145µg 290%")

    def fake_vision(data, model=None, background=False):
        with lock:
            state["now"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.4)
        with lock:
            state["now"] -= 1
        return table

    monkeypatch.setattr(bb, "_fetch_public_image", lambda url: _label_photo(7))
    monkeypatch.setattr(bb, "call_blockbrain_vision", fake_vision)
    assert bb.extract_label_text_from_product_images(gallery, "https://www.amazon.de/dp/X") == table
    time.sleep(0.6)  # the reads that were already running finish
    assert state["peak"] <= 3
    assert state["calls"] <= 4  # three at the start, at most one that slipped in before the table was found; not eight


# ================================================================ 3. every class of failure, forced with the fake server
def _http503(fake, mp):
    fake.convo_status = 503


def _http_403(fake, mp):
    fake.stream_script = [{"http": 403}]


def _platform(fake, mp):
    fake.stream_script = [PLATFORM]
    fake.completion_script = [PLATFORM]


def _blind(fake, mp):
    fake.stream_script = [BLIND]
    fake.completion_script = [BLIND]


def _empty(fake, mp):
    fake.stream_script = [""]
    fake.completion_script = [""]


def _refusal(fake, mp):
    fake.stream_script = [SORRY]
    fake.completion_script = [SORRY]


def _timeout(fake, mp):
    mp.setattr(bb, "BLOCKBRAIN_VISION_BUDGET_S", 1.5)
    fake.stream_script = [{"delay": 3.0, "text": "late"}]


def _unreachable(fake, mp):
    import blockbrain_llm_client as client

    mp.setattr(client, "AGENTIC", "http://127.0.0.1:9")
    mp.setattr(client, "BLOCKY", "http://127.0.0.1:9")


def _service_error(fake, mp):
    fake.stream_script = [{"http": 400}]
    fake.completion_script = [{"http": 400}]


def _credits(fake, mp):
    fake.stream_script = [{"error": "Insufficient credits"}]
    fake.completion_script = [{"http": 429}]


def _attachment(fake, mp):
    fake.stream_script = [{"http": 500}]
    fake.attachment_status = "failed"


# name, setup, kind, group, what the per-route line must show
SCENARIOS = [
    ("busy", _http503, "busy", "no_answer", ["agentic error", "create conversation: HTTP 503"]),
    ("credits", _credits, "busy", "no_answer", ["agentic error", "cortex error", "HTTP 429"]),
    ("timeout", _timeout, "timeout", "no_answer", ["agentic timeout"]),
    ("unreachable", _unreachable, "unreachable", "no_answer", ["agentic error", "cortex error"]),
    ("service-error", _service_error, "service_error", "no_answer", ["stream: HTTP 400", "completion: HTTP 400"]),
    ("attachment", _attachment, "service_error", "no_answer", ["stream: HTTP 500", "attachment failed"]),
    ("auth", _http_403, "auth", "unavailable", ["agentic error", "stream: HTTP 403"]),
    ("platform", _platform, "platform", "unavailable", ["agentic platform-error", "Failed to resolve model configuration"]),
    ("model-blind", _blind, "model_blind", "unavailable", ["agentic image-missing", "cortex image-missing"]),
    ("empty", _empty, "empty", "answer", ["agentic error", "cortex empty"]),
    ("refusal", _refusal, "refusal", "answer", ["agentic refusal", "cortex refusal"]),
]


@pytest.mark.parametrize("name, setup, kind, group, shows", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_each_failure_class_has_its_own_stage_and_reason(fake_bb, monkeypatch, name, setup, kind, group, shows):
    setup(fake_bb, monkeypatch)
    bb.reset_vision_trace()  # (the app does this when a photo starts)
    assert bb.call_blockbrain_vision(_jpeg()) == ""
    report = bb.vision_failure_report([("fast_jpeg", bb.vision_attempts())])
    assert (report["kind"], report["group"]) == (kind, group), report["lines"]
    text = "\n".join(report["lines"])
    assert report["lines"][0].startswith("stage ") and kind.replace("_", "-") in report["lines"][0]
    for expected in shows:
        assert expected in text, (expected, report["lines"])
    assert not any(secret in text for secret in SECRETS)


def test_the_details_never_hold_a_key_an_organisation_or_a_bot_id_or_the_label_text(fake_bb):
    """A platform error may echo the identifiers it was called with; and a model's apology may quote the label."""
    echo = f"unknown organisation {fb.ORG_ID} for bot {fb.BOT_ID} with key {fb.API_KEY} see https://blocky.example/x/12345678-1234-1234-1234-123456789012"
    fake_bb.stream_script = [{"error": echo}]
    fake_bb.completion_script = [{"error": "Zinc 10 mg 100% and Vitamin C 80 mg could not be processed"}]  # an error quoting the label
    bb.reset_vision_trace()
    assert bb.call_blockbrain_vision(_jpeg()) == ""
    report = bb.vision_failure_report([("fast_jpeg", bb.vision_attempts())])
    text = "\n".join(report["lines"])
    for secret in SECRETS:
        assert secret not in text
    assert "http" not in text and "12345678-1234" not in text
    assert "Zinc 10 mg" not in text and "Vitamin C 80" not in text and "model text hidden" in text
    assert "agentic error" in text and "cortex error" in text
    assert all(len(line) < 400 for line in report["lines"])


def test_a_stall_reports_how_long_it_waited(fake_bb, monkeypatch):
    _timeout(fake_bb, monkeypatch)
    bb.call_blockbrain_vision(_jpeg())
    assert re.search(r"agentic timeout @1\.\ds", "\n".join(bb.vision_failure_report([("fast_jpeg", bb.vision_attempts())])["lines"]))


def test_the_attempts_belong_to_the_thread_that_made_the_call(fake_bb):
    """One visitor's failure must never show on another visitor's card."""
    fake_bb.stream_script = [{"http": 400}]
    fake_bb.completion_script = [{"http": 400}]
    seen = {}

    def other():
        seen["attempts"] = bb.vision_attempts()
        seen["error"] = bb.last_call_error()

    bb.call_blockbrain_vision(_jpeg())
    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert seen == {"attempts": [], "error": ""}
    assert len(bb.vision_attempts()) == 2


# ================================================================ 4. the app: message, details, quota, sync, panel
@pytest.fixture
def state(sw, monkeypatch):
    """The app's session state as a plain dict, and an empty app-wide ledger."""
    store: dict = {}
    monkeypatch.setattr(sw.st, "session_state", store)
    sw.llm_cache.reset_global_usage()
    sw._cached_ocr.clear()
    yield store
    sw.llm_cache.reset_global_usage()


def _read(sw, data: bytes):
    return sw._extract_image_text_best_effort(data)


def test_a_file_the_app_cannot_open_is_not_a_scan_and_says_so(sw, state, fake_bb):
    """It used to cost one of 15 hourly scans and read "the AI label reader returned no text (busy / too blurry)": no AI call was made."""
    assert _read(sw, _heic()) == ("", "")
    assert fake_bb.requests == []
    assert state["_suppswipe_llm_usage"]["vision"] == [] and sw.llm_cache._usage_times == []
    message = sw._ai_unavailable_message("photo")
    assert "(HEIC)" in message and "nothing was sent to the AI" in message and "Paste" in message
    assert "returned no text" not in message and "blurry" not in message
    assert sw._photo_details_lines(sw._photo_report())[0] == "stage image, no AI call · unsupported-format HEIC"


@pytest.mark.parametrize(
    "name, setup, expected, not_expected",
    [
        ("busy", _http503, "turned your photo down", "blurry"),
        ("timeout", _timeout, "didn't answer in time", "blurry"),
        ("unreachable", _unreachable, "couldn't be reached", "blurry"),
        ("service-error", _service_error, "reported an error", "blurry"),
        ("auth", _http_403, "The AI helper is unavailable right now", "blurry"),
        ("platform", _platform, "The AI helper is unavailable right now", "blurry"),
        ("model-blind", _blind, "The AI helper is unavailable right now", "blurry"),
        ("empty", _empty, "the AI label reader returned no text", "unavailable"),
        ("refusal", _refusal, "the AI label reader returned no text", "unavailable"),
    ],
)
def test_each_class_gets_the_truthful_sentence_and_advice(sw, state, fake_bb, monkeypatch, name, setup, expected, not_expected):
    setup(fake_bb, monkeypatch)
    assert _read(sw, _label_photo(sum(map(ord, name)))) == ("", "")
    message = sw._ai_unavailable_message("photo")
    assert expected in message and not_expected not in message
    assert "Paste" in message
    # an answer with nothing readable keeps the old advice: retake it sharp, straight
    assert ("sharp, straight photo" in message) == (name in {"empty", "refusal"})
    # the photo was sent in every class but the files above: it costs its scan
    assert len(state["_suppswipe_llm_usage"]["vision"]) == 1


def test_a_refusal_on_the_small_picture_is_tried_again_with_the_sharper_one(sw, state, fake_bb):
    """A refusal can be about resolution; a call that FAILED (down, timed out) is not retried with a second picture."""
    fake_bb.stream_script = [SORRY]
    fake_bb.completion_script = [SORRY, FACTS]
    text, route = _read(sw, _label_photo(5, size=(2400, 1800)))
    assert text == FACTS and "detail_jpeg" in route
    fake_bb.stream_script = [{"http": 500}]
    fake_bb.completion_script = [{"http": 500}]
    fake_bb.requests.clear()
    assert _read(sw, _label_photo(6, size=(2400, 1800))) == ("", "")
    assert len(fake_bb.json_bodies("/v2/api/agents")) == 1  # one picture, not two


def test_an_unexpected_error_in_the_photo_step_is_not_blamed_on_the_photo(sw):
    report = {"stage": "app", "kind": "app_error", "group": "app", "facts": {}, "lines": ["stage app · error ValueError"]}
    assert "Something went wrong while reading the photo" in sw._photo_failure_message(report)
    assert "blurry" not in sw._photo_failure_message(report)


def test_the_scan_is_charged_once_per_photo_even_when_the_run_restarts(sw, state, fake_bb):
    """A dropped connection or a late component value restarts the same analysis: it charged the 15-a-session allowance again."""
    fake_bb.stream_script = [FACTS]
    photo = _label_photo(8)
    assert _read(sw, photo)[0] == FACTS
    assert _read(sw, photo)[0] == FACTS  # (served from the cache: the run restarted)
    assert len(state["_suppswipe_llm_usage"]["vision"]) == 1
    state.pop(sw._VISION_CHARGED_KEY)  # the analysis ended; the same photo again is a new scan
    _read(sw, photo)
    assert len(state["_suppswipe_llm_usage"]["vision"]) == 2


def test_the_app_wide_limit_is_not_called_this_sessions_limit(sw, state, monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_MAX_LLM_CALLS_PER_HOUR_GLOBAL", "1")
    assert sw._consume_llm_quota("vision") is True
    state.clear()  # another visitor: her own allowance is untouched, the whole app's is used up
    assert sw._consume_llm_quota("vision") is False
    assert "this session's limit" not in sw._quota_message() and "many visitors" in sw._quota_message()
    assert "many visitors" in sw._ai_unavailable_message("quota")
    state.clear()
    monkeypatch.setenv("SUPPSWIPE_MAX_LLM_CALLS_PER_HOUR_GLOBAL", "600")
    monkeypatch.setenv("SUPPSWIPE_MAX_SCANS_PER_HOUR", "1")
    sw.llm_cache.reset_global_usage()
    assert sw._consume_llm_quota("vision") is True and sw._consume_llm_quota("vision") is False
    assert sw._quota_message() == sw._QUOTA_MESSAGE


def test_the_short_error_keeps_the_words_of_a_platform_error(sw, monkeypatch):
    """"[Agent customAgent] - Failed to resolve model configuration" was cut at the "[": nothing was left, and Diagnostics looked
    healthy after exactly the failure it exists to show."""
    assert sw._short_error(PLATFORM) == "Agent customAgent - Failed to resolve model configuration"
    assert sw._short_error('stream: HTTP 404 {"error":"Agent abc not found"}') == "stream: HTTP 404"
    assert sw._short_error("") == ""
    secret = f"[Agent {fb.ORG_ID}] key {fb.API_KEY}"
    assert fb.API_KEY not in sw._short_error(secret)
    monkeypatch.setenv("BLOCKBRAIN_ORG_ID", fb.ORG_ID)
    monkeypatch.setenv("BLOCKBRAIN_BOT_ID", fb.BOT_ID)
    shown = sw._short_error(f"unknown organisation {fb.ORG_ID} for bot {fb.BOT_ID}")  # an error text that echoes the ids
    assert fb.ORG_ID not in shown and fb.BOT_ID not in shown and "unknown organisation" in shown


def test_the_first_history_read_does_not_wipe_a_failure_card(sw, state, monkeypatch):
    """The history component's one value triggers a rerun; when it arrives after the analysis failed, that rerun erased the card
    and the visitor landed on the welcome screen with no word."""
    reruns = []
    monkeypatch.setattr(sw.st, "rerun", lambda *a, **k: reruns.append(1))
    monkeypatch.setattr(sw, "_history_store", lambda **kw: {"history": [], "scan": None})
    state["_suppswipe_failure_shown"] = True
    sw._sync_scan_history_with_browser()
    assert reruns == [] and "_suppswipe_failure_shown" not in state
    state.pop("_suppswipe_history_loaded")
    sw._sync_scan_history_with_browser()
    assert reruns == [1]  # without a card to protect, it reruns as before


# ---------------------------------------------------------------- the whole page (Streamlit AppTest) against the fake
@pytest.fixture
def booted_app():
    """One plain run first: the entry script reloads our own modules once, which would undo the fake server's addresses."""
    from streamlit.testing.v1 import AppTest

    AppTest.from_file(APP, default_timeout=60).run()


def _page_text(at) -> str:
    parts = [e.value for kind in (at.error, at.caption, at.markdown, at.warning, at.info, at.text) for e in kind]
    parts += [str(e.value) for e in at.get("json")]
    return "\n".join(str(p) for p in parts)


def _run_photo(data: bytes, *, debug: bool = False):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=90)
    if debug:
        at.query_params["debug"] = "1"
    at.session_state["swipe_pending_request"] = {"upload_bytes": data, "camera_bytes": b"", "manual": "", "camera_barcode": ""}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


PAGE_CASES = [
    # id, bytes or None (a scenario), setup, sentence of the card, lines of the details
    ("heic", _heic, None, "isn't a picture type we can open (HEIC), so nothing was sent to the AI", ["stage image, no AI call · unsupported-format HEIC"]),
    ("too-large", _too_large_png, None, "too big to open here (9 megapixels), so nothing was sent to the AI", ["too-large 9.0 MP, limit 8.5 MP"]),
    ("busy", None, _http503, "turned your photo down", ["stage service · busy", "fast: agentic error", "create conversation: HTTP 503"]),
    ("timeout", None, _timeout, "didn't answer in time", ["stage service · timeout", "agentic timeout @1."]),
    ("auth", None, _http_403, "The AI helper is unavailable right now", ["stage service · auth", "stream: HTTP 403"]),
    ("platform", None, _platform, "The AI helper is unavailable right now", ["stage service · platform", "Failed to resolve model configuration"]),
    ("empty", None, _empty, "Your photo couldn't be read: the AI label reader returned no text", ["stage answer · empty"]),
    ("refusal", None, _refusal, "Your photo couldn't be read: the AI label reader returned no text", ["stage answer · refusal", "agentic refusal"]),
]


@pytest.mark.parametrize("name, make, setup, sentence, lines", PAGE_CASES, ids=[c[0] for c in PAGE_CASES])
def test_the_failure_card_says_what_happened_and_shows_the_details(booted_app, fake_bb, monkeypatch, sw, name, make, setup, sentence, lines):
    sw.llm_cache.reset_global_usage()
    if setup:
        setup(fake_bb, monkeypatch)
    at = _run_photo(make() if make else _label_photo(100 + len(name)))
    errors = [e.value for e in at.error]
    assert len(errors) == 1 and sentence in errors[0], errors
    captions = [c.value for c in at.caption if c.value.startswith("Technical details")]
    assert len(captions) == 1, [c.value for c in at.caption]
    for expected in lines:
        assert expected in captions[0], (expected, captions[0])
    assert f"build {sw.BUILD_TAG}" in captions[0]
    page = _page_text(at)
    assert not any(secret in page for secret in SECRETS), "a key, organisation or bot id reached the page"
    if make:
        assert fake_bb.requests == []  # a file that cannot be opened is never sent


def test_a_server_error_that_echoes_the_ids_does_not_put_them_on_the_page(booted_app, fake_bb):
    echo = f"unknown organisation {fb.ORG_ID} for bot {fb.BOT_ID} with key {fb.API_KEY}"
    fake_bb.stream_script = [{"error": echo}]
    fake_bb.completion_script = [{"error": echo}]
    at = _run_photo(_label_photo(321), debug=True)
    page = _page_text(at)
    assert "stage service" in page
    assert not any(secret in page for secret in SECRETS)


def test_diagnostics_show_the_same_details_and_keep_them_after_the_card_is_gone(booted_app, fake_bb):
    """?debug=1 used to show the error of the failing run only, and nothing at all for a platform error (it starts with "[")."""
    fake_bb.stream_script = [PLATFORM]
    fake_bb.completion_script = [PLATFORM]
    at = _run_photo(_label_photo(322), debug=True)
    panel = json.loads(at.get("json")[0].value)
    assert "Failed to resolve model configuration" in panel["last_error"]
    assert panel["last_photo"]["kind"] == "platform" and panel["last_photo"]["lines"][0] == "stage service · platform"
    at.run()  # the visitor taps something: a new run, the card is gone, last_error with it
    assert not at.error
    again = json.loads(at.get("json")[0].value)
    assert again["last_error"] == ""
    assert again["last_photo"]["lines"][0] == "stage service · platform" and "agentic platform-error" in again["last_photo"]["lines"][1]
    assert again["call_slots_free"]["photo"] >= 1


def test_an_unexpected_error_in_the_photo_step_shows_its_class_only(booted_app, fake_bb, monkeypatch):
    def boom():
        raise RuntimeError("secret detail that must not be shown " + fb.API_KEY)

    monkeypatch.setattr(bb, "reset_vision_trace", boom)
    at = _run_photo(_label_photo(323))
    assert "Something went wrong while reading the photo" in at.error[0].value
    details = [c.value for c in at.caption if c.value.startswith("Technical details")][0]
    assert "stage app · error RuntimeError" in details and "secret detail" not in _page_text(at)


def test_a_readable_photo_shows_no_failure_card(booted_app, fake_bb):
    fake_bb.stream_script = [FACTS]
    at = _run_photo(_label_photo(324))
    assert at.session_state["swipe_cards"]
    assert not [c for c in at.caption if c.value.startswith("Technical details")]


# ================================================================ 5. review fixes: ids, the model's words, flat pictures, swallowed bugs
KNOWN_BOT = "6ab65f469ef45cfc6d1a2a05"  # KNOWN_MODELS["claude-sonnet-5"], a constant of the client file


def _model_key_config(monkeypatch):
    """The documented configuration: BLOCKBRAIN_MODEL only. No variable holds the bot id; the client takes it from KNOWN_MODELS."""
    import blockbrain_llm_client as client

    monkeypatch.delenv("BLOCKBRAIN_BOT_ID", raising=False)
    monkeypatch.setenv("BLOCKBRAIN_MODEL", "claude-sonnet-5")
    assert client.Blockbrain().bot_id == KNOWN_BOT


def _everything_kept(sw, caplog) -> str:
    """Everything a photo failure leaves behind that a visitor, the owner or the Cloud log can see."""
    parts = [sw._ai_unavailable_message("photo"), *sw._photo_details_lines(sw._photo_report()), sw._short_error(bb.last_call_error())]
    parts += [str(sw._photo_report()), "\n".join(r.getMessage() for r in caplog.records)]
    parts += [bb.LAST_BLOCKBRAIN_ERROR, "\n".join(bb.LAST_VISION_ATTEMPT_LOG)]
    return "\n".join(parts)


def test_a_bot_id_taken_from_the_model_key_is_scrubbed_wherever_a_server_echoes_it(sw, state, fake_bb, monkeypatch, caplog):
    """With BLOCKBRAIN_MODEL only (the documented configuration) no variable holds the bot id, so the old scrubber, which only knew
    environment values, let the id through into the card, Diagnostics and the log. Echoed at the START of the text it was whole."""
    _model_key_config(monkeypatch)
    echo = f"[Agent customAgent] - bot {KNOWN_BOT}: Failed to resolve model configuration"
    fake_bb.stream_script = [echo]
    fake_bb.completion_script = [echo]
    with caplog.at_level(logging.WARNING):
        assert _read(sw, _label_photo(601)) == ("", "")
    kept = _everything_kept(sw, caplog)
    assert fake_bb.requests, "the fake server was never reached: the test proves nothing"
    assert "Failed to resolve model configuration" in kept  # the useful words survive ...
    assert KNOWN_BOT[:12] not in kept and KNOWN_BOT[:12].upper() not in kept  # ... without the id, not even a piece of it


def test_every_bot_id_is_scrubbed_in_any_case_and_any_24_digit_hex_token_too(monkeypatch):
    _model_key_config(monkeypatch)
    for text in (f"bot {KNOWN_BOT} failed", f"BOT {KNOWN_BOT.upper()} FAILED", f"x-bot-id={KNOWN_BOT},"):
        assert KNOWN_BOT not in bb._scrub_secrets(text).lower() and "***" in bb._scrub_secrets(text)
        assert KNOWN_BOT not in bb._detail_text(text).lower()
    other = "6ab66ab79ef45cfc6d1a2bb3"  # another entry of KNOWN_MODELS (kimi-k3): this deployment does not use it, the client file lists it
    assert other not in bb._scrub_secrets(f"bot {other}")
    unknown = "0123456789abcdef01234567"  # nothing configured anywhere: shaped like an id, so it goes
    assert unknown not in bb._detail_text(f"conversation {unknown} not found") and "id" in bb._detail_text(f"conversation {unknown} not found")
    assert bb._detail_text("create conversation: HTTP 404") == "create conversation: HTTP 404"  # ordinary words stay


PERSON = "Max Mustermann, Hauptstrasse 5, Berlin - Rx Nature Made Vitamin D3 gummies"


@pytest.mark.parametrize(
    "name, answer, outcome",
    [
        ("refusal", f"I'm sorry, I can't transcribe this. {PERSON}", "refusal"),
        ("image-missing", "I don't see any image attached to your message. Please send it again, Max Mustermann, Hauptstrasse 5, Berlin.", "image-missing"),
    ],
)
def test_the_models_own_words_about_the_picture_never_reach_the_card_the_state_or_the_log(
    sw, state, fake_bb, caplog, name, answer, outcome
):
    """A refusal quotes what it saw. The details line goes to every visitor and to the Cloud log: the kind and the route are enough."""
    fake_bb.stream_script = [answer]
    fake_bb.completion_script = [answer]
    with caplog.at_level(logging.WARNING):
        assert _read(sw, _label_photo(610 + len(name))) == ("", "")
    lines = sw._photo_details_lines(sw._photo_report())
    assert f"agentic {outcome}" in "\n".join(lines) and f"cortex {outcome}" in "\n".join(lines)  # still says what each route did
    kept = _everything_kept(sw, caplog)
    for word in ("Mustermann", "Hauptstrasse", "Berlin", "Nature Made", "gummies", "transcribe"):
        assert word not in kept, (word, lines)
    assert "photo not read" in kept  # (the log line is there, without the words)


def test_only_blockbrains_own_error_text_is_shown_for_a_platform_error(sw, state, fake_bb, caplog):
    """Anything else the app took for an error (here a made-up AI_APICallError that quotes a person) is the model's text: a fixed phrase."""
    own = "[Agent customAgent] - Failed to resolve model configuration"
    other = f"AI_APICallError: {PERSON}"
    assert bb.looks_like_agent_error(other)
    for answer, shown in ((own, "Failed to resolve model configuration"), (other, "error text hidden")):
        fake_bb.stream_script = [answer]
        fake_bb.completion_script = [answer]
        sw._cached_ocr.clear()
        with caplog.at_level(logging.WARNING):
            _read(sw, _label_photo(620 + len(answer)))
        kept = _everything_kept(sw, caplog)
        assert shown in "\n".join(sw._photo_details_lines(sw._photo_report()))
        if answer is other:
            assert "Mustermann" not in "\n".join(sw._photo_details_lines(sw._photo_report()) + [str(sw._photo_report())])
        caplog.clear()


@pytest.mark.parametrize("text", [
    "I'm sorry, Zinc 10 milligrams and Vitamin D 5 mgs", "Vitamin D 5 mgs", "Zinc 10 Milligram", "80 micrograms of selenium",
    "2 grams of fibre", "400 IU, 100 %", "10mg", "5 µgs",
])
def test_a_dose_is_a_label_value_however_the_unit_is_spelled(text):
    assert bb._detail_text(text) == "model text hidden"


def _text_photo(dark: bool, px: int, size=(4032, 3024), lines: int = 14) -> bytes:
    """A table photographed from a distance: a few lines of small text on a big plain background (well under 1 % of the pixels)."""
    font = ImageFont.load_default(size=px)
    img = Image.new("RGB", size, (10, 10, 10) if dark else (245, 245, 240))
    draw = ImageDraw.Draw(img)
    for i in range(lines):
        draw.text((size[0] // 3, size[1] // 3 + i * int(px * 1.6)), f"Vitamin C {80 + i} mg      {100 + i} %", fill=(200, 200, 200) if dark else (20, 20, 20), font=font)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@pytest.mark.parametrize("dark, px", [(False, 22), (False, 12), (True, 24), (True, 12)], ids=["light-22", "light-12", "dark-24", "dark-12"])
def test_a_far_away_photo_with_small_text_is_sent_and_is_not_called_flat(dark, px):
    """The old measure (spread of a 64x64 box average) called a light photo with text up to ~13 px "one flat colour" and refused a dark
    one with 24 px text as black before any AI call."""
    assert bb.build_vision_image_variants(_text_photo(dark, px))
    notes = bb.image_notes()
    assert notes.get("flat") is False and "reason" not in notes


def test_a_refusal_on_a_far_away_photo_is_a_refusal_not_a_flat_colour(sw, state, fake_bb):
    fake_bb.stream_script = [SORRY]
    fake_bb.completion_script = [SORRY]
    assert _read(sw, _text_photo(False, 22)) == ("", "")
    report = sw._photo_report()
    assert report["kind"] == "refusal" and "flat colour" not in sw._ai_unavailable_message("photo")
    assert fake_bb.requests  # it was sent


def test_a_really_flat_picture_still_gets_the_flat_colour_sentence(sw, state, fake_bb):
    """The label stays for what it is true of: a white page (sent, the AI may answer with nothing) ..."""
    fake_bb.stream_script = [SORRY]
    fake_bb.completion_script = [SORRY]
    assert _read(sw, _jpeg((900, 700))) == ("", "")
    assert sw._photo_report()["kind"] == "blank_photo" and "one flat colour" in sw._ai_unavailable_message("photo")


def _dark_noise(sigma: float, size=(1200, 900)) -> bytes:
    noise = Image.effect_noise(size, sigma)  # centred on 128
    img = ImageChops.subtract(noise, Image.new("L", size, 122)).convert("RGB")  # centred on 6: a covered lens in a dark room
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def test_a_covered_lens_is_not_sent_but_a_noisy_dark_photo_is():
    assert bb.build_vision_image_variants(_dark_noise(1.5)) == [] and bb.image_notes()["reason"] == "blank"
    assert bb.build_vision_image_variants(_dark_noise(14))  # sensor noise of a real night photo: not flat, so not refused


def test_a_bug_inside_the_ocr_step_is_reported_as_one_not_as_no_text(sw, state, fake_bb, monkeypatch, caplog):
    """`except Exception: text = ""` turned every bug in the adapter into "the AI label reader returned no text (busy / blurry)"."""
    def boom(*a, **k):
        raise KeyError("Zinc 10 mg 100%")  # (the message may hold what the photo held: it must not be shown)

    monkeypatch.setattr(bb, "extract_image_text_with_blockbrain", boom)
    with caplog.at_level(logging.WARNING):
        assert _read(sw, _label_photo(630)) == ("", "")
    report = sw._photo_report()
    assert (report["stage"], report["kind"], report["group"]) == ("app", "app_error", "app")
    assert report["lines"][0] == "stage app · error KeyError"
    message = sw._ai_unavailable_message("photo")
    assert "Something went wrong while reading the photo" in message and "returned no text" not in message and "blurry" not in message
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "the photo step raised KeyError at" in log and "boom" in log  # the code place is in the log (file:line in function)
    assert "Zinc" not in log and "Zinc" not in str(report) and "Zinc" not in sw._short_error(bb.last_call_error())


def test_an_ordinary_runtime_error_is_a_bug_too_only_the_ocr_steps_own_failures_are_not(sw, state, fake_bb, monkeypatch):
    def bug(*a, **k):
        raise RuntimeError("bug")

    with monkeypatch.context() as patch:
        patch.setattr(bb, "extract_image_text_with_blockbrain", bug)
        _read(sw, _label_photo(631))
    assert sw._photo_report()["group"] == "app"
    fake_bb.stream_script = [""]
    fake_bb.completion_script = [""]
    sw._cached_ocr.clear()
    _read(sw, _label_photo(632))
    assert sw._photo_report()["group"] == "answer"  # an empty answer is the AI's, not the app's


def test_a_bug_after_a_good_read_does_not_discard_the_read_silently(sw, state, fake_bb, monkeypatch):
    """The number check raised after the AI had read the table: the card said the AI returned no text and the details showed a
    successful "agentic text"."""
    def boom(text):
        raise ValueError("mask failed")

    monkeypatch.setattr(bb, "_mask_unreadable_numbers", boom)
    fake_bb.stream_script = [FACTS]
    assert _read(sw, _label_photo(633)) == ("", "")
    report = sw._photo_report()
    assert report["group"] == "app" and report["lines"][0] == "stage app · error ValueError"
    assert "agentic text" in "\n".join(report["lines"])  # what the AI did is still on the card
    assert fake_bb.requests and len(state["_suppswipe_llm_usage"]["vision"]) == 1  # it was sent, so it costs its scan


def test_a_bug_while_the_picture_is_prepared_is_reported_and_costs_no_scan(sw, state, fake_bb, monkeypatch):
    def boom(*a, **k):
        raise OSError("encoder failed")

    monkeypatch.setattr(bb, "_jpeg_bytes", boom)
    assert _read(sw, _label_photo(634)) == ("", "")
    report = sw._photo_report()
    assert report["group"] == "app" and report["lines"][0] == "stage app · error OSError"
    assert "Something went wrong while reading the photo" in sw._ai_unavailable_message("photo")
    assert fake_bb.requests == []  # nothing was sent ...
    assert state["_suppswipe_llm_usage"]["vision"] == [] and sw.llm_cache._usage_times == []  # ... so no scan was used


def test_a_bug_in_the_ocr_step_shows_its_class_on_the_page(booted_app, fake_bb, monkeypatch):
    def boom(*a, **k):
        raise KeyError("detail that must not be shown " + fb.API_KEY)

    monkeypatch.setattr(bb, "extract_image_text_with_blockbrain", boom)
    at = _run_photo(_label_photo(635))
    assert "Something went wrong while reading the photo" in at.error[0].value and "returned no text" not in at.error[0].value
    details = [c.value for c in at.caption if c.value.startswith("Technical details")][0]
    assert "stage app · error KeyError" in details and "detail that must not be shown" not in _page_text(at)

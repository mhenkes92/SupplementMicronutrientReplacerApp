"""Browser check of the photo failure card (Playwright + Chromium, opt-in: SUPPSWIPE_BROWSER_TESTS=1).

The owner saw "the AI label reader returned no text (it may be busy, or the photo too blurry)" on a real iPhone, twice, and nothing on
the screen said which of many causes it was. Here the real app is driven the way a visitor does (Scan -> Analyze -> Upload -> a file)
against the local fake Blockbrain, one failure class at a time, and what a screenshot of the card would show is read from the page:
the sentence for that class and the "Technical details" under it, with no key, organisation or bot id anywhere on the page."""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest
from PIL import Image, ImageDraw

from test_ux_browser import ROOT, _FAKE_ROUTING, _free_port, browser, card, settle  # noqa: F401  (fixtures are used by name)

import fake_blockbrain as fb

SECRETS = (fb.API_KEY, fb.ORG_ID, fb.BOT_ID)
FACTS = "Supplement Facts\nVitamin C 80 mg 100%\nVitamin D3 20 µg 400%\nZinc 10 mg 100%\nSelenium 55 µg 100%"
PLATFORM = "[Agent customAgent] - Failed to resolve model configuration"


@pytest.fixture(scope="module")
def photo_server(tmp_path_factory):
    """One app server for the module, talking to the fake only. A stalled platform ends after 4 s (3 s for the first route)."""
    fake = fb.FakeBlockbrain().start()
    routing = tmp_path_factory.mktemp("fake_routing")
    (routing / "sitecustomize.py").write_text(_FAKE_ROUTING)
    port = _free_port()
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(routing), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
        SUPPSWIPE_TEST_FAKE_BB=fake.url,
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        BLOCKBRAIN_API_KEY=fb.API_KEY,
        BLOCKBRAIN_ORG_ID=fb.ORG_ID,
        BLOCKBRAIN_BOT_ID=fb.BOT_ID,
        BLOCKBRAIN_VISION_BUDGET_S="4",
        BLOCKBRAIN_VISION_FIRST_ROUTE_S="3",
        SUPPSWIPE_PREFETCH_MEALS="0",
    )
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy", "BLOCKBRAIN_MODEL"):
        env.pop(name, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "swipe_mobile_app" / "app.py"),
         "--server.port", str(port), "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{port}/"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "_stcore/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    yield url, fake
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    fake.stop()


@pytest.fixture
def page(browser):
    """A phone-sized page of its own (the module's server is the one with the fake behind it, so nothing is opened here)."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _reset(fake) -> None:
    fake.stream_script = ["Hello"]
    fake.completion_script = ["cortex fake answer"]
    fake.stream_calls = fake.completion_calls = 0
    fake.convo_status = 200
    fake.attachment_polls_before_ready = 1
    fake.requests.clear()


def _label_jpeg(path, seed: int) -> None:
    img = Image.new("RGB", (900, 700), "white")
    draw = ImageDraw.Draw(img)
    for i in range(24):
        draw.text((20 + seed, 20 + i * 26), f"Vitamin {i} {seed} mg", fill="black")
    img.save(path, "JPEG")


def _upload(page, url: str, path) -> None:
    """Scan -> Analyze -> Upload -> choose the file, as a visitor does."""
    page.goto(url, wait_until="networkidle")
    page.locator('[class~="st-key-hero_scan"] button').click()
    page.get_by_role("dialog").wait_for(timeout=10000)
    settle(page, 0.5)
    page.get_by_role("button", name="Analyze my supplement").click()
    dialog = page.get_by_role("dialog")
    dialog.locator("button", has_text="Upload").click()
    settle(page)
    dialog.locator('input[type="file"]').set_input_files(str(path))


def _failure_card(page) -> tuple[str, str]:
    """(the sentence of the error card, the Technical details under it) as a visitor sees them."""
    alert = page.locator('[data-testid="stAlert"]').first
    alert.wait_for(timeout=60000)
    details = page.locator('[data-testid="stCaptionContainer"]', has_text="Technical details")
    try:
        details.wait_for(timeout=10000)
    except Exception:
        raise AssertionError("no Technical details under the card; the page says: " + page.locator("body").inner_text()[:1500]) from None
    return alert.inner_text(), details.inner_text()


def _assert_no_secret_on_the_page(page) -> None:
    for text in (page.locator("body").inner_text(), page.content()):
        assert not any(secret in text for secret in SECRETS), "a key, organisation or bot id is on the page"


def test_a_file_the_app_cannot_open_says_so_and_nothing_is_sent(photo_server, page, tmp_path):
    url, fake = photo_server
    _reset(fake)
    heic = tmp_path / "IMG_0001.jpg"  # a HEIC picture named .jpg: the uploader checks the extension only
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64)
    _upload(page, url, heic)
    sentence, details = _failure_card(page)
    assert "isn't a picture type we can open (HEIC)" in sentence and "nothing was sent to the AI" in sentence
    assert "returned no text" not in sentence
    assert "stage image, no AI call · unsupported-format HEIC" in details and "build " in details
    assert fake.requests == []
    _assert_no_secret_on_the_page(page)


def test_a_picture_that_is_too_big_says_how_big(photo_server, page, tmp_path):
    url, fake = photo_server
    _reset(fake)
    big = tmp_path / "screenshot.png"
    Image.new("1", (3000, 3000)).save(big, "PNG", optimize=True)  # 9 MP
    _upload(page, url, big)
    sentence, details = _failure_card(page)
    assert "too big to open here (9 megapixels)" in sentence and "nothing was sent to the AI" in sentence
    assert "too-large 9.0 MP, limit 8.5 MP" in details and "image PNG 3000x3000" in details
    assert fake.requests == []


def test_a_busy_service_is_called_busy_and_the_route_errors_are_shown(photo_server, page, tmp_path):
    url, fake = photo_server
    _reset(fake)
    fake.convo_status = 503
    photo = tmp_path / "label.jpg"
    _label_jpeg(photo, 1)
    _upload(page, url, photo)
    sentence, details = _failure_card(page)
    assert "turned your photo down" in sentence and "blurry" not in sentence
    assert "stage service · busy" in details
    assert details.count("create conversation: HTTP 503") == 2  # both routes, each with its own reason
    assert "image JPEG 900x700" in details and "fast 900x700" in details
    _assert_no_secret_on_the_page(page)


def test_a_setting_problem_is_not_called_a_bad_photo(photo_server, page, tmp_path):
    url, fake = photo_server
    _reset(fake)
    fake.stream_script = [PLATFORM]
    fake.completion_script = [PLATFORM]
    photo = tmp_path / "label.jpg"
    _label_jpeg(photo, 3)
    _upload(page, url, photo)
    sentence, details = _failure_card(page)
    assert sentence.startswith("The AI helper is unavailable right now.") and "blurry" not in sentence
    assert "stage service · platform" in details and "Failed to resolve model configuration" in details


def test_an_answer_with_nothing_readable_keeps_the_retake_advice(photo_server, page, tmp_path):
    url, fake = photo_server
    _reset(fake)
    fake.stream_script = [""]
    fake.completion_script = [""]
    photo = tmp_path / "label.jpg"
    _label_jpeg(photo, 4)
    _upload(page, url, photo)
    sentence, details = _failure_card(page)
    assert "Your photo couldn't be read: the AI label reader returned no text" in sentence and "sharp, straight photo" in sentence
    assert "stage answer · empty" in details


def test_what_the_model_says_about_the_picture_is_not_put_on_the_page(photo_server, page, tmp_path):
    """A refusal can quote the label or a name and an address; the card says which route refused and nothing of what it said."""
    url, fake = photo_server
    _reset(fake)
    said = "I'm sorry, I can't transcribe this. Max Mustermann, Hauptstrasse 5, Berlin - Rx Nature Made Vitamin D3 gummies"
    fake.stream_script = [said]
    fake.completion_script = [said]
    photo = tmp_path / "label.jpg"
    _label_jpeg(photo, 11)
    _upload(page, url + "?debug=1", photo)
    sentence, details = _failure_card(page)
    assert "stage answer · refusal" in details and "agentic refusal" in details and "cortex refusal" in details
    page.get_by_text("Diagnostics").click()
    page.locator('[data-testid="stJson"]').wait_for(timeout=10000)
    for text in (page.locator("body").inner_text(), page.content()):
        for word in ("Mustermann", "Hauptstrasse", "Berlin", "Nature Made", "gummies"):
            assert word not in text, f"the model's words reached the page: {word}"


def test_an_error_that_echoes_the_ids_never_puts_them_on_the_page(photo_server, page, tmp_path):
    url, fake = photo_server
    _reset(fake)
    echo = f"unknown organisation {fb.ORG_ID} for bot {fb.BOT_ID} with key {fb.API_KEY}"
    fake.stream_script = [{"error": echo}]
    fake.completion_script = [{"error": echo}]
    photo = tmp_path / "label.jpg"
    _label_jpeg(photo, 5)
    _upload(page, url + "?debug=1", photo)
    _failure_card(page)
    page.get_by_text("Diagnostics").click()  # the panel shows the same details (and the last error)
    page.locator('[data-testid="stJson"]').wait_for(timeout=10000)
    assert "last_photo" in page.locator('[data-testid="stJson"]').inner_text()
    _assert_no_secret_on_the_page(page)


def test_an_empty_file_is_reported_in_the_window(photo_server, page, tmp_path):
    url, fake = photo_server
    _reset(fake)
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    _upload(page, url, empty)
    warning = page.get_by_role("dialog").locator('[data-testid="stAlert"]').first
    warning.wait_for(timeout=20000)
    assert "That file is empty (0 bytes)" in warning.inner_text()
    assert fake.requests == []


def test_a_transparent_picture_reaches_the_model_on_white(photo_server, page, tmp_path):
    """A transparent PNG used to be sent as a black square (convert("RGB") shows the colour hidden under the alpha channel)."""
    url, fake = photo_server
    _reset(fake)
    fake.stream_script = [FACTS]
    png = tmp_path / "label.png"
    img = Image.new("RGBA", (900, 700), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((30, 30), "Zinc 10 mg 100% " * 8, fill=(0, 0, 0, 255))
    img.save(png, "PNG")
    _upload(page, url, png)
    card(page).locator("#card .name").wait_for(timeout=60000)
    part = json.loads(fake.calls("POST", "/v2/api/agents")[0]["body"])["messages"][0]["parts"][1]
    sent = Image.open(io.BytesIO(base64.b64decode(part["url"].split(",", 1)[1]))).convert("L")
    pixels = sent.tobytes()
    assert sum(pixels) / len(pixels) > 200 and min(pixels) < 80  # a white page with the text on it, not a black square
    assert "couldn't be read" not in page.locator("body").inner_text()


def test_a_service_that_does_not_answer_says_how_long_it_waited(photo_server, page, tmp_path):
    """LAST in this file: the call that ran out of its budget keeps polling the fake in the server process for a while."""
    url, fake = photo_server
    _reset(fake)
    fake.stream_script = [{"delay": 8, "text": "far too late"}]
    fake.attachment_polls_before_ready = 100_000  # the cortex route stalls too: its picture is never "processed"
    photo = tmp_path / "label.jpg"
    _label_jpeg(photo, 2)
    _upload(page, url, photo)
    sentence, details = _failure_card(page)
    assert "didn't answer in time" in sentence and "your photo was sent" in sentence
    assert "stage service · timeout" in details
    assert "agentic timeout @3." in details and "cortex timeout @4." in details  # the first route got its 3 s, cortex the rest of the 4 s
    _assert_no_secret_on_the_page(page)


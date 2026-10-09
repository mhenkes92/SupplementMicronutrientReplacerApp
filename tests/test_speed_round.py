"""The speed round: what it must not change, and what it changes.

SP0  what is sent to the model is pinned (pixels, JPEG quality, sharpness): no speed work may lower it.
SP1  a manual generation (Ask AI, Generate my meals, Different meals) is written by a background job, so its partial text is
     painted from the script's own thread and a finished answer is cached even when the script is interrupted.
SP2  the polls that show a background answer are twice as fast (0.5 s fragments, a 0.1 s wait loop), and only while a job runs.
SP3  two pure nutrition helpers are memoised; their results are byte-equal to the uncached originals.

Offline: the model is tests/fake_blockbrain.py. The opt-in browser half lives in tests/test_speed_round_browser.py.
"""
from __future__ import annotations

import base64
import io
import re

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat, JpegImagePlugin

import blockbrain.app as bb


# =====================================================================================================================
# SP0 - the picture that reaches the model
# =====================================================================================================================
# Today (measured, then pinned here):
#   * build_vision_image_variants decodes an upload ONCE (upright, EXIF applied), and gives a "fast_jpeg" of at most 1400 px
#     on the long side at JPEG quality 80, and - only when the picture is larger than 1400 px - a "detail_jpeg" of at most
#     2000 px at quality 88 (both optimize=True, Pillow's default 4:2:0 sampling, LANCZOS reduction, never enlarged).
#   * call_blockbrain_vision sends an upright JPEG of <= 2000 px and <= 1.5 MB unchanged, and the client does not
#     re-encode a picture of <= 2000 px: the model receives the variant's bytes as they are.
# Only the long side and the quantisation tables are compared (not byte hashes): the tables are the quality, and they do not
# depend on the Pillow / libjpeg build; the exact sizes are the pixel count.
FAST_SIDE, DETAIL_SIDE, FAST_QUALITY, DETAIL_QUALITY = 1400, 2000, 80, 88


def _synthetic_label(size: tuple[int, int]) -> Image.Image:
    """A deterministic stand-in for a label photo: fine text-sized lines and boxes on a paper-coloured ground."""
    width, height = size
    image = Image.new("RGB", size, (236, 232, 222))
    draw = ImageDraw.Draw(image)
    for y in range(0, height, max(2, height // 60)):
        draw.line([(0, y), (width, y + (y % 7))], fill=(40 + y % 90, 60, 80), width=max(1, height // 500))
    for i in range(24):
        x, y = (i * 97) % max(1, width - 320), (i * 211) % max(1, height - 90)
        draw.rectangle([x, y, x + 300, y + 70], outline=(20, 20, 20), width=3)
        draw.text((x + 8, y + 10), f"Vitamin D3 {20 + i} ug {100 + i}%", fill=(10, 10, 10))
    return image


def _jpeg_file(size: tuple[int, int], quality: int = 92) -> bytes:
    buffer = io.BytesIO()
    _synthetic_label(size).save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _phone_mpo(size: tuple[int, int] = (4032, 3024), orientation: int = 1) -> bytes:
    """What an iPhone / Samsung writes: a JPEG with a second (preview) frame, which Pillow reports as format MPO."""
    image = _synthetic_label(size)
    exif = Image.Exif()
    exif[0x0112] = orientation
    buffer = io.BytesIO()
    image.save(buffer, format="MPO", save_all=True, append_images=[image.resize((size[0] // 10, size[1] // 10))], exif=exif, quality=92)
    return buffer.getvalue()


def _png_file(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    _synthetic_label(size).save(buffer, format="PNG")
    return buffer.getvalue()


def _reference(quality: int) -> tuple[dict, int]:
    """(quantisation tables, chroma sampling) of a plain Pillow JPEG at `quality`: the tables depend on the quality alone."""
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (120, 130, 140)).save(buffer, format="JPEG", quality=quality, optimize=True)
    probe = Image.open(io.BytesIO(buffer.getvalue()))
    return {k: list(v) for k, v in probe.quantization.items()}, JpegImagePlugin.get_sampling(probe)


def _tables(data: bytes) -> tuple[dict, int]:
    probe = Image.open(io.BytesIO(data))
    return {k: list(v) for k, v in probe.quantization.items()}, JpegImagePlugin.get_sampling(probe)


def test_the_reference_qualities_really_differ():
    """If Pillow ever gave q75, q80 and q88 the same tables, the pin below would pin nothing."""
    assert _reference(FAST_QUALITY)[0] != _reference(DETAIL_QUALITY)[0] != _reference(75)[0]
    assert _reference(FAST_QUALITY)[0] != _reference(75)[0]


@pytest.mark.parametrize(
    "make,label",
    [
        pytest.param(lambda: _jpeg_file((4032, 3024)), "JPEG", id="jpeg"),
        pytest.param(lambda: _phone_mpo(), "MPO", id="mpo-phone"),
        pytest.param(lambda: _phone_mpo((3024, 4032), orientation=6), "MPO", id="mpo-phone-rotated"),
        pytest.param(lambda: _png_file((2400, 3200)), "PNG", id="png"),
    ],
)
def test_the_variants_keep_their_size_and_their_jpeg_quality(make, label):
    data = make()
    assert Image.open(io.BytesIO(data)).format == label  # the phone case really is the MPO case Pillow reports for phones
    variants = bb.build_vision_image_variants(data)
    assert [name for name, _ in variants] == ["fast_jpeg", "detail_jpeg"]
    (_, fast), (_, detail) = variants
    fast_image, detail_image = Image.open(io.BytesIO(fast)), Image.open(io.BytesIO(detail))
    assert fast_image.format == detail_image.format == "JPEG"
    # The long side is the pixel count: never fewer than today (1400 / 2000), never more than the limits.
    assert max(fast_image.size) == FAST_SIDE and max(detail_image.size) == DETAIL_SIDE
    # Same picture, different resolution: the aspect ratio is kept.
    assert abs(fast_image.size[0] / fast_image.size[1] - detail_image.size[0] / detail_image.size[1]) < 0.01
    # The quality: the quantisation tables (and the chroma sampling) of a plain Pillow re-encode at q80 / q88.
    assert _tables(fast) == _reference(FAST_QUALITY)
    assert _tables(detail) == _reference(DETAIL_QUALITY)


@pytest.mark.parametrize("size,expected", [((1400, 900), ["fast_jpeg"]), ((900, 1400), ["fast_jpeg"]),
                                           ((1401, 900), ["fast_jpeg", "detail_jpeg"]), ((640, 480), ["fast_jpeg"]),
                                           ((2000, 1200), ["fast_jpeg", "detail_jpeg"])])
def test_the_detail_variant_exists_only_for_pictures_larger_than_the_fast_side(size, expected):
    variants = bb.build_vision_image_variants(_jpeg_file(size))
    assert [name for name, _ in variants] == expected
    sizes = [Image.open(io.BytesIO(b)).size for _, b in variants]
    assert max(sizes[0]) == min(FAST_SIDE, max(size))  # a small picture is never enlarged
    if len(sizes) == 2:
        assert max(sizes[1]) == min(DETAIL_SIDE, max(size))


def _sent_jpegs(fake, route: str) -> list[bytes]:
    """The picture bytes the fake received, in order, from the wire (agentic: a base64 data URL; cortex: a multipart part)."""
    out: list[bytes] = []
    for entry in fake.requests:
        if entry["method"] != "POST":
            continue
        if route == "agentic" and entry["path"] == "/v2/api/agents/customAgent/stream":
            for m in re.finditer(rb"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", entry["body"]):
                out.append(base64.b64decode(m.group(1)))
        if route == "cortex" and re.match(r"^/cortex/conversation/[^/]+/attachment$", entry["path"]):
            body = entry["body"]
            start = body.find(b"\xff\xd8")
            end = body.rfind(b"\xff\xd9")
            out.append(body[start : end + 2])
    return out


@pytest.mark.parametrize("route", ["agentic", "cortex"])
@pytest.mark.parametrize("make", [pytest.param(lambda: _jpeg_file((4032, 3024)), id="jpeg"), pytest.param(lambda: _phone_mpo(), id="mpo-phone")])
def test_the_model_receives_both_variants_unchanged(fake_bb, monkeypatch, route, make):
    monkeypatch.setenv("BLOCKBRAIN_OCR_ROUTE", route)
    fake_bb.stream_script = ["Magnesium 300 mg 80%"]
    fake_bb.completion_script = ["Magnesium 300 mg 80%"]
    variants = bb.build_vision_image_variants(make())
    assert len(variants) == 2
    for name, data in variants:
        fake_bb.requests.clear()
        assert bb.call_blockbrain_vision(data) == "Magnesium 300 mg 80%", name
        sent = _sent_jpegs(fake_bb, route)
        assert len(sent) == 1 and sent[0] == data, (name, route, len(sent), [len(s) for s in sent], len(data))


def test_the_app_sends_the_fast_variant_first_and_the_detail_variant_when_the_read_is_weak(sw, fake_bb, monkeypatch):
    """The whole path of one photo (quota, variants, reads, quality gate), as the app runs it."""
    sw.st.session_state.pop("_suppswipe_llm_usage", None)
    fake_bb.stream_script = ["Some Brand Name"]  # a read without a nutrient table: the gate asks for the sharper picture
    upload = _phone_mpo()
    expected = [data for _, data in bb.build_vision_image_variants(upload)]
    text, _route = sw._extract_image_text_best_effort(upload)
    assert text == "Some Brand Name"
    sent = _sent_jpegs(fake_bb, "agentic")
    assert sent == expected  # exactly the two variants, fast then detail, byte for byte
    assert [max(Image.open(io.BytesIO(s)).size) for s in sent] == [FAST_SIDE, DETAIL_SIDE]
    assert [_tables(s) for s in sent] == [_reference(FAST_QUALITY), _reference(DETAIL_QUALITY)]


# ---------------------------------------------------------------- the pixels themselves: sharpness, orientation, colour
# The sizes and tables above cannot see a weaker reduction filter (LANCZOS -> BILINEAR keeps ~20 % less edge energy on digit
# strokes), a label that reaches the model sideways or flipped, or a picture that lost its colour. These compare the pixels the
# model receives with an independent reference: the upright source (rotated here, from the EXIF value, not by the app's code),
# reduced with LANCZOS to the same size and encoded with the same JPEG settings. Pillow does the same work on both sides, so
# the comparison does not depend on the Pillow / libjpeg build; the thresholds sit between the measured spread of the shipped
# code (edge ratio 0.985-1.008, mean difference 2.2-3.2) and the nearest weaker variant (BICUBIC 0.91-0.92, BILINEAR 0.80-0.83).
EDGE_RATIO_WINDOW = (0.96, 1.04)
MAX_MEAN_DIFFERENCE = 4.0
MAX_CHANNEL_MEAN_SHIFT = 2.0


def _coloured_label(size: tuple[int, int]) -> Image.Image:
    """The synthetic label plus three saturated, off-centre panels: lost colour, swapped channels, a flip or a turn all show."""
    image = _synthetic_label(size)
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle([width // 20, height // 10, width // 3, height // 4], fill=(200, 30, 30))
    draw.rectangle([width // 2, height // 2, width * 3 // 4, height * 3 // 5], fill=(30, 60, 200))
    draw.rectangle([width // 10, height * 3 // 4, width // 4, height * 9 // 10], fill=(30, 160, 60))
    return image


def _encode_label(image: Image.Image, fmt: str, orientation: int = 1) -> bytes:
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = orientation
    if fmt == "MPO":
        image.save(buffer, format="MPO", save_all=True, append_images=[image.resize((image.width // 10, image.height // 10))], exif=exif, quality=92)
    elif fmt == "JPEG":
        image.save(buffer, format="JPEG", quality=92, exif=exif)
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _edge_energy(image: Image.Image) -> float:
    """Mean absolute difference between neighbouring pixels (horizontal + vertical): how much edge the strokes still have."""
    grey = image.convert("L")
    width, height = grey.size
    horizontal = ImageChops.difference(grey.crop((0, 0, width - 1, height)), grey.crop((1, 0, width, height)))
    vertical = ImageChops.difference(grey.crop((0, 0, width, height - 1)), grey.crop((0, 1, width, height)))
    return ImageStat.Stat(horizontal).mean[0] + ImageStat.Stat(vertical).mean[0]


def _reference_pixels(upright: Image.Image, size: tuple[int, int], quality: int,
                      resample: "Image.Resampling" = Image.Resampling.LANCZOS) -> Image.Image:
    buffer = io.BytesIO()
    upright.resize(size, resample).save(buffer, format="JPEG", quality=quality, optimize=True)
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def _pixel_problems(got: Image.Image, reference: Image.Image) -> list[str]:
    """What is wrong with `got` compared with `reference` (same size): [] when it is as sharp, upright and coloured."""
    if got.size != reference.size:
        return [f"size {got.size} != {reference.size}"]
    problems = []
    ratio = _edge_energy(got) / _edge_energy(reference)
    if not EDGE_RATIO_WINDOW[0] <= ratio <= EDGE_RATIO_WINDOW[1]:
        problems.append(f"edge energy x{ratio:.3f}")
    difference = sum(ImageStat.Stat(ImageChops.difference(got, reference)).mean) / 3
    if difference > MAX_MEAN_DIFFERENCE:
        problems.append(f"mean difference {difference:.2f}")
    shifts = [abs(a - b) for a, b in zip(ImageStat.Stat(got).mean, ImageStat.Stat(reference).mean)]
    if max(shifts) > MAX_CHANNEL_MEAN_SHIFT:
        problems.append(f"channel means shifted by {max(shifts):.1f}")
    return problems


# id, container, stored size, EXIF orientation, the turn that makes the stored picture upright, (fast, detail) size to be sent
PIXEL_CASES = [
    pytest.param("JPEG", (4032, 3024), 1, None, (1400, 1050), (2000, 1500), id="jpeg"),
    pytest.param("MPO", (4032, 3024), 1, None, (1400, 1050), (2000, 1500), id="mpo-phone"),
    pytest.param("MPO", (4032, 3024), 6, Image.Transpose.ROTATE_270, (1050, 1400), (1500, 2000), id="mpo-exif6-portrait"),
    pytest.param("MPO", (4032, 3024), 8, Image.Transpose.ROTATE_90, (1050, 1400), (1500, 2000), id="mpo-exif8-portrait"),
    pytest.param("MPO", (4032, 3024), 3, Image.Transpose.ROTATE_180, (1400, 1050), (2000, 1500), id="mpo-exif3-upside-down"),
    pytest.param("JPEG", (4032, 3024), 6, Image.Transpose.ROTATE_270, (1050, 1400), (1500, 2000), id="jpeg-exif6-portrait"),
    pytest.param("PNG", (2400, 3200), 1, None, (1050, 1400), (1500, 2000), id="png"),
]


@pytest.mark.parametrize("fmt,size,orientation,turn,fast_size,detail_size", PIXEL_CASES)
def test_the_pixels_the_model_receives_are_as_sharp_upright_and_coloured_as_a_plain_reduction(fmt, size, orientation, turn, fast_size, detail_size):
    source = _coloured_label(size)
    upright = source if turn is None else source.transpose(turn)
    variants = bb.build_vision_image_variants(_encode_label(source, fmt, orientation))
    assert [name for name, _ in variants] == ["fast_jpeg", "detail_jpeg"]
    for (name, payload), expected_size, quality in zip(variants, (fast_size, detail_size), (FAST_QUALITY, DETAIL_QUALITY)):
        got = Image.open(io.BytesIO(payload))
        assert got.size == expected_size, (name, got.size, "an EXIF-rotated photo must be sent upright")
        assert _pixel_problems(got.convert("RGB"), _reference_pixels(upright, expected_size, quality)) == [], name


def test_the_pixel_guard_tells_the_shipped_picture_from_a_worse_one():
    """The guard itself: it passes the plain reduction and fails each way the model could be given less than today."""
    source = _coloured_label((4032, 3024))
    size = (1400, 1050)
    reference = _reference_pixels(source, size, FAST_QUALITY)
    assert _pixel_problems(_reference_pixels(source, size, FAST_QUALITY), reference) == []
    worse = {
        "nearest": _reference_pixels(source, size, FAST_QUALITY, Image.Resampling.NEAREST),
        "bilinear": _reference_pixels(source, size, FAST_QUALITY, Image.Resampling.BILINEAR),
        "box": _reference_pixels(source, size, FAST_QUALITY, Image.Resampling.BOX),
        "hamming": _reference_pixels(source, size, FAST_QUALITY, Image.Resampling.HAMMING),
        "bicubic": _reference_pixels(source, size, FAST_QUALITY, Image.Resampling.BICUBIC),
        "grey": reference.convert("L").convert("RGB"),
        "channels swapped": Image.merge("RGB", reference.split()[::-1]),
        "mirrored": reference.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        "upside down": reference.transpose(Image.Transpose.ROTATE_180),
        "blurred": reference.resize((700, 525), Image.Resampling.LANCZOS).resize(size, Image.Resampling.BILINEAR),
    }
    for label, candidate in worse.items():
        assert _pixel_problems(candidate, reference), f"the pixel guard does not notice: {label}"
    assert _pixel_problems(reference.rotate(90, expand=True), reference)  # a sideways picture has another size
    # The JPEG quality is not what this guard is for: q70 is nearly invisible in the pixels of a label. The quantisation tables
    # (test_the_variants_keep_their_size_and_their_jpeg_quality, and the re-encode test below) pin it.
    assert _tables(_jpeg_file((64, 64), 70)) != _reference(FAST_QUALITY)


def _noisy_jpeg(size: tuple[int, int], quality: int = 95) -> bytes:
    import random

    noise = Image.frombytes("RGB", size, random.Random(7).randbytes(size[0] * size[1] * 3))
    buffer = io.BytesIO()
    noise.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


# call_blockbrain_vision sends a small upright JPEG as it is, and re-encodes anything else (a PNG, an EXIF-rotated phone photo, a
# JPEG over 1.5 MB, which includes a detail variant of a busy photo) at quality 88, upright, at most 2000 px.
def test_a_picture_that_is_re_encoded_before_it_is_sent_keeps_quality_88_its_size_its_orientation_and_its_colour():
    sent_as_is = _jpeg_file((1800, 1200))
    assert len(sent_as_is) <= 1_500_000
    assert bb._vision_jpeg_payload(sent_as_is) == sent_as_is  # upright, <= 2000 px, <= 1.5 MB: not touched

    noisy = _noisy_jpeg((2000, 1500))
    assert len(noisy) > 1_500_000  # the case that takes the re-encode branch without being resized
    cases = {
        "noisy-big-jpeg": (noisy, (2000, 1500), None, None),
        "png": (_encode_label(_coloured_label((3000, 2000)), "PNG"), (2000, 1333), _coloured_label((3000, 2000)), None),
        "mpo-exif6": (_encode_label(_coloured_label((4032, 3024)), "MPO", 6), (1500, 2000), _coloured_label((4032, 3024)), Image.Transpose.ROTATE_270),
    }
    for label, (data, expected_size, source, turn) in cases.items():
        payload = bb._vision_jpeg_payload(data)
        got = Image.open(io.BytesIO(payload))
        assert got.format == "JPEG" and got.size == expected_size, (label, got.format, got.size)
        assert _tables(payload) == _reference(DETAIL_QUALITY), label  # quality 88, 4:2:0
        if source is None:  # nothing is resized: the reference is the decoded picture, encoded once more at q88
            decoded = Image.open(io.BytesIO(data)).convert("RGB")
            buffer = io.BytesIO()
            decoded.save(buffer, format="JPEG", quality=DETAIL_QUALITY, optimize=True)
            reference = Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")
        else:
            reference = _reference_pixels(source if turn is None else source.transpose(turn), expected_size, DETAIL_QUALITY)
        assert _pixel_problems(got.convert("RGB"), reference) == [], label


# =====================================================================================================================
# SP1 - a manual generation is a background job that the script thread waits for
# =====================================================================================================================
# Before: on_text=_show painted the placeholder from the "blockbrain-call" worker thread, where Streamlit has no
# ScriptRunContext and drops the call ("missing ScriptRunContext" x3 per generation): nothing showed until the whole answer
# was back, and a rerun in the middle lost a paid answer (llm_cache.put ran on the script thread). Now llm_cache.submit starts
# the call, the script thread paints what the job has written so far, and the job caches its own result.
import threading
import time
from types import SimpleNamespace

ANSWER = (
    "**Meal 1: Kiwi oat bowl** - 60 g oats, 2 kiwis, 150 g yoghurt, a spoon of seeds.\n"
    "**Meal 2: Lentil curry** - 80 g red lentils, spinach, coconut milk, rice.\n"
    "**Meal 3: Brazil nut salad** - 1 nut, mixed greens, olive oil, lemon."
)
CURSOR = " ▌"


class _Interrupted(BaseException):
    """What a Streamlit rerun raises inside the running script (RerunException is a BaseException too)."""


class _Spy:
    """A stand-in for an st.empty(): every paint is recorded with the thread it came from."""

    def __init__(self, raise_on_render: int = 0) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.raise_on_render = raise_on_render
        self._renders = 0

    def _note(self, kind: str, text: str = "") -> None:
        self.calls.append((kind, text, threading.get_ident()))

    def markdown(self, text: str, *args, **kwargs) -> None:
        self._renders += 1
        self._note("markdown", text)
        if self.raise_on_render and self._renders == self.raise_on_render:
            raise _Interrupted("rerun")

    def empty(self) -> None:
        self._note("empty")

    def info(self, text: str, *args, **kwargs) -> None:
        self._note("info", text)

    @property
    def threads(self) -> set[int]:
        return {thread for _kind, _text, thread in self.calls}

    @property
    def partials(self) -> list[str]:
        """The distinct growing texts (with the cursor) that were painted, in order."""
        out: list[str] = []
        for kind, text, _thread in self.calls:
            if kind == "markdown" and text.endswith(CURSOR) and text not in out:
                out.append(text)
        return out


@pytest.fixture
def lab(sw, fake_bb, monkeypatch):
    """The app module, the fake model, a fresh allowance, and cache keys that are forgotten again afterwards."""
    sw.llm_cache.reset_global_usage()
    sw.st.session_state.pop("_suppswipe_llm_usage", None)
    bb.reset_call_error()
    keys: list[str] = []

    def key(name: str) -> str:
        made = sw.llm_cache.make_key("speed-round", name, time.time_ns())
        keys.append(made)
        return made

    yield SimpleNamespace(sw=sw, fake=fake_bb, key=key, monkeypatch=monkeypatch)
    for made in keys:
        sw.llm_cache.drop(made)
    sw.st.session_state.pop("_suppswipe_llm_usage", None)
    bb.reset_call_error()


def _units(sw) -> int:
    return len(sw.st.session_state.get("_suppswipe_llm_usage", {}).get("generate", []))


def _manual(lab, key: str, spy: _Spy | None = None, **kwargs) -> tuple[str, _Spy]:
    spy = spy or _Spy()
    bb.reset_call_error()
    text = lab.sw._stream_llm_text(key, "SYSTEM PROMPT", "USER PROMPT", placeholder=spy, **kwargs)
    return text, spy


def _wait_cached(sw, key: str, seconds: float = 20.0) -> str | None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = sw.llm_cache.get(key)
        if value is not None:
            return value
        time.sleep(0.02)
    return sw.llm_cache.get(key)


# ---------------------------------------------------------------- 1. painted from the caller's own thread, and growing
def test_the_answer_is_painted_from_the_callers_thread_and_grows_while_it_is_written(lab):
    lab.fake.stream_script = [ANSWER]
    lab.fake.write_delay = 0.03
    key = lab.key("grows")
    text, spy = _manual(lab, key)
    assert text == ANSWER
    assert spy.threads == {threading.get_ident()}, "a paint came from another thread: Streamlit drops those"
    assert len(spy.partials) > 1, spy.partials  # several distinct growing texts, not one jump
    assert all(ANSWER.startswith(p[: -len(CURSOR)]) for p in spy.partials)  # each is a prefix of the final answer
    assert [len(p) for p in spy.partials] == sorted(len(p) for p in spy.partials)
    assert spy.calls[-1][:2] == ("markdown", ANSWER)  # the last paint is the finished answer, without the cursor
    assert lab.sw.llm_cache.get(key) == text  # what is shown, what is returned and what is cached are one text


# ---------------------------------------------------------------- 2. a rerun in the middle does not lose the paid answer
def test_a_rerun_in_the_middle_of_the_answer_does_not_lose_it(lab):
    lab.fake.stream_script = [ANSWER]
    lab.fake.write_delay = 0.03
    key = lab.key("interrupted")
    spy = _Spy(raise_on_render=2)  # like a Streamlit rerun: the script is stopped from inside an st call
    with pytest.raises(_Interrupted):
        _manual(lab, key, spy)
    assert lab.sw.llm_cache.get(key) is None  # not finished yet
    assert _wait_cached(lab.sw, key) == ANSWER  # the job still finishes and caches it
    again, spy2 = _manual(lab, key)  # the next run is a free cache hit
    assert again == ANSWER and spy2.calls == [("markdown", ANSWER, threading.get_ident())]
    assert _units(lab.sw) == 1


# ---------------------------------------------------------------- 3. the allowance
def test_one_unit_per_manual_generation_none_for_a_repeat_or_a_joined_job(lab, monkeypatch):
    gate = threading.Event()
    calls: list[str] = []

    def slow_model(system, user, **kwargs):
        calls.append(user)
        on_text = kwargs.get("on_text")
        if on_text:
            on_text("Meal 1: oats")
        gate.wait(10)
        return "Meal 1: oats and kiwi"

    monkeypatch.setattr(bb, "call_blockbrain_text", slow_model)
    key = lab.key("quota")
    threading.Timer(0.4, gate.set).start()
    text, _ = _manual(lab, key)
    assert text == "Meal 1: oats and kiwi" and len(calls) == 1 and _units(lab.sw) == 1
    again, _ = _manual(lab, key)  # a cache hit
    assert again == text and len(calls) == 1 and _units(lab.sw) == 1

    joined_key = lab.key("quota-joined")
    gate.clear()
    started = lab.sw.llm_cache.submit(joined_key, lambda: slow_model("s", "background"))  # e.g. the prefetch of the default plan
    assert started is not None
    end = time.monotonic() + 5
    while len(calls) < 2 and time.monotonic() < end:  # the pool starts the job a moment later
        time.sleep(0.01)
    assert len(calls) == 2
    threading.Timer(0.4, gate.set).start()
    joined, spy = _manual(lab, joined_key)  # joins the running job instead of asking again
    assert joined == "Meal 1: oats and kiwi" and len(calls) == 2 and _units(lab.sw) == 1
    assert spy.threads == {threading.get_ident()}

    free_key = lab.key("quota-free")
    free, _ = _manual(lab, free_key, consume_quota=False)  # the caller counted this request already
    assert free == "Meal 1: oats and kiwi" and _units(lab.sw) == 1


def test_a_spent_allowance_asks_nothing_and_says_so(lab, monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_MAX_GENERATIONS_PER_HOUR", "1")
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: "an answer")
    first, _ = _manual(lab, lab.key("spent-1"))
    second, spy = _manual(lab, lab.key("spent-2"))
    assert first == "an answer" and second == ""
    assert spy.calls == [("info", lab.sw._QUOTA_MESSAGE, threading.get_ident())]
    assert _units(lab.sw) == 1


# ---------------------------------------------------------------- 4. failures: nothing cached, nothing shown, the same words
# The messages are what _ai_service_problem / _ai_retry_note / the Diagnostics panel / the Ask AI error say after the failure.
# EXPECTED was recorded from the code BEFORE this round (the call ran on the script thread, where last_call_error() lives).
FAILURE_CASES = ["401", "403", "404", "500", "empty", "timeout", "agent_error"]
RETRY_DEFAULT = "Couldn't generate meals right now — please try again."
ASK_DEFAULT = "Ask AI is unavailable right now — please try again in a moment."
EXPECTED: dict[str, dict[str, str]] = {
    '401': {
        'error': 'create conversation: HTTP 401 {"message": "Unauthorized"}',
        'problem': 'The AI helper is unavailable right now. ',
        'retry': 'The AI helper is unavailable right now. Please try again later.',
        'short': 'create conversation: HTTP 401',
        'ask': 'The AI helper is unavailable right now. Please try again later.',
    },
    '403': {
        'error': 'stream: HTTP 403 {"message": "stream refused"}',
        'problem': 'The AI helper is unavailable right now. ',
        'retry': 'The AI helper is unavailable right now. Please try again later.',
        'short': 'stream: HTTP 403',
        'ask': 'The AI helper is unavailable right now. Please try again later.',
    },
    '404': {
        'error': 'stream: HTTP 404 {"message": "stream refused"}',
        'problem': 'The AI helper is unavailable right now. ',
        'retry': 'The AI helper is unavailable right now. Please try again later.',
        'short': 'stream: HTTP 404',
        'ask': 'The AI helper is unavailable right now. Please try again later.',
    },
    '500': {
        'error': 'stream: HTTP 500 {"message": "stream refused"}',
        'problem': '',
        'retry': "Couldn't generate meals right now — please try again.",
        'short': 'stream: HTTP 500',
        'ask': 'Ask AI is unavailable right now — please try again in a moment.',
    },
    'empty': {
        'error': 'Blockbrain returned an empty answer twice',
        'problem': '',
        'retry': "Couldn't generate meals right now — please try again.",
        'short': 'Blockbrain returned an empty answer twice',
        'ask': 'Ask AI is unavailable right now — please try again in a moment.',
    },
    'timeout': {
        'error': 'Blockbrain did not answer within 1 s',
        'problem': '',
        'retry': "Couldn't generate meals right now — please try again.",
        'short': 'Blockbrain did not answer within 1 s',
    },
    'agent_error': {
        'error': '[Agent X] Failed to run: the model is unavailable',
        'problem': '',
        'retry': "Couldn't generate meals right now — please try again.",
        'short': '',
        'ask': 'Ask AI is unavailable right now — please try again in a moment.',
    },
}


def _arm_failure(lab, name: str) -> float | None:
    """Make the fake (or the key) fail like `name`; returns the call budget to use (None = the default)."""
    if name == "401":
        lab.monkeypatch.setenv("BLOCKBRAIN_API_KEY", "not-the-key")
    elif name in {"403", "404", "500"}:
        lab.fake.stream_script = [{"http": int(name)}]
    elif name == "empty":
        lab.fake.stream_script = [""]
    elif name == "timeout":
        lab.fake.stream_script = [{"delay": 3.0, "text": "far too late"}]
        return 1.0
    elif name == "agent_error":
        lab.fake.stream_script = ["[Agent X] Failed to run: the model is unavailable"]
    return None


def _failure_case(lab, name: str) -> dict:
    budget = _arm_failure(lab, name)
    key = lab.key("fail-" + name)
    text, spy = _manual(lab, key, budget_s=budget)
    sw = lab.sw
    error = bb.last_call_error()
    return {
        "text": text,
        "cached": sw.llm_cache.get(key),
        "last_paint": spy.calls[-1][0] if spy.calls else None,
        "units": _units(sw),
        "error": error,
        "problem": sw._ai_service_problem(),
        "retry": sw._ai_retry_note(RETRY_DEFAULT),
        "short": sw._short_error(error),
    }


@pytest.mark.parametrize("name", FAILURE_CASES)
def test_a_failed_generation_returns_nothing_caches_nothing_and_says_the_same_as_before(lab, name):
    got = _failure_case(lab, name)
    assert got["text"] == "" and got["cached"] is None
    assert got["last_paint"] == "empty"  # the placeholder is cleared: an error text is never shown as the answer
    assert got["units"] == 1  # the attempt used its unit, as before
    assert {k: got[k] for k in ("error", "problem", "retry", "short")} == {k: EXPECTED[name][k] for k in ("error", "problem", "retry", "short")}


@pytest.mark.parametrize("name", [n for n in FAILURE_CASES if n != "timeout"])
def test_the_ask_ai_error_text_is_the_same_as_before(lab, name):
    lab.monkeypatch.setattr(lab.sw, "_cached_rag_chunks", list)  # no local index: the model's failure is all there is
    _arm_failure(lab, name)
    spy = _Spy()
    bb.reset_call_error()
    answer = lab.sw._answer_ask_ai_question("Zinc", "Is this dose safe?", placeholder=spy, dose_label="10 mg")
    assert answer == (None, "")
    assert spy.calls[-1][0] == "empty"
    assert lab.sw._ai_retry_note(ASK_DEFAULT) == EXPECTED[name]["ask"]
    assert lab.sw._short_error(bb.last_call_error()) == EXPECTED[name]["short"]


def test_a_success_clears_the_error_a_failure_left_on_the_calling_thread(lab):
    _failure_case(lab, "500")
    assert "500" in bb.last_call_error()  # the failed attempt left its reason on this thread (Diagnostics shows it)
    lab.fake.stream_script = [ANSWER]
    bb._CALL_STATE.error = "stream: HTTP 500 left over from earlier"
    # Not through _manual(): it calls reset_call_error() first, which would hide whether _stream_llm_text clears the stale
    # reason itself (a successful call on this thread did, before the job ran on a worker).
    text = lab.sw._stream_llm_text(lab.key("recovers"), "SYSTEM PROMPT", "USER PROMPT", placeholder=_Spy())
    assert text == ANSWER and bb.last_call_error() == ""
    assert lab.sw._ai_service_problem() == "" and lab.sw._ai_retry_note(RETRY_DEFAULT) == RETRY_DEFAULT


def test_a_caller_that_joins_a_job_somebody_else_started_reports_that_jobs_reason(lab, monkeypatch):
    """The race: this caller's inflight() check ran just before another visitor's job was registered, so its submit() finds that
    job already running. The job is not this caller's (nothing ran here), so the reason it failed has to come from the cache,
    where the other worker put it - an empty reason would turn an auth failure into a generic "try again"."""
    sw = lab.sw
    key = lab.key("joined-failure")
    reason = EXPECTED["401"]["error"]
    model_calls: list[str] = []
    started, release = threading.Event(), threading.Event()

    def failing_model(system, user, **kwargs):
        model_calls.append(user)
        started.set()
        release.wait(10)
        bb._CALL_STATE.error = reason  # what the adapter notes, on the worker thread that made the call
        return ""

    monkeypatch.setattr(bb, "call_blockbrain_text", failing_model)
    other_visitor = threading.Thread(
        target=lambda: sw._stream_llm_text(key, "SYSTEM PROMPT", "USER PROMPT", consume_quota=False), daemon=True)
    other_visitor.start()
    assert started.wait(10), "the other visitor's job never started"
    monkeypatch.setattr(sw.llm_cache, "inflight", lambda _key: None)  # this caller's check saw no job
    threading.Timer(0.3, release.set).start()
    spy = _Spy()
    bb.reset_call_error()
    text = sw._stream_llm_text(key, "SYSTEM PROMPT", "USER PROMPT", placeholder=spy)
    other_visitor.join(10)
    assert text == "" and spy.calls[-1][0] == "empty"
    assert model_calls == ["USER PROMPT"], "this caller started a second call although the job was running"
    assert bb.last_call_error() == reason
    assert sw._ai_service_problem() == EXPECTED["401"]["problem"]
    assert sw._ai_retry_note(RETRY_DEFAULT) == EXPECTED["401"]["retry"]
    assert sw._short_error(bb.last_call_error()) == EXPECTED["401"]["short"]


def test_a_model_that_raises_gives_nothing_and_caches_nothing(lab, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("model blew up")

    monkeypatch.setattr(bb, "call_blockbrain_text", boom)
    key = lab.key("raises")
    text, spy = _manual(lab, key)
    assert text == "" and lab.sw.llm_cache.get(key) is None and spy.calls[-1][0] == "empty"


# ---------------------------------------------------------------- 5. the request itself did not change
# Recorded from the code before this round: what call_blockbrain_text is given (system, user, model, history, budget) and the
# cache key under which the answer is stored. Hashes, because the prompts are long; a changed prompt must be a decision.
GOLDEN: dict[str, dict] = {
    'ask_first': {
        'system': 'ba611c700e041aae96a7',
        'user': "Micronutrient / supplement component: Zinc\nDose in the user's supplement: 10 mg\nQuestion: Is this dose safe?",
        'model': 'ask-model-x',
        'history': [],
        'budget_s': 90,
        'key': 'f4d0842a2e64ec2350d0b7e5ae93662b10b6adf838800fc2c110d328e42e21c6',
    },
    'ask_follow_up': {
        'system': 'ba611c700e041aae96a7',
        'user': "Micronutrient / supplement component: Zinc\nDose in the user's supplement: 10 mg\nQuestion: And for vegans?",
        'model': 'ask-model-x',
        'history': [{'role': 'user', 'content': 'Is zinc safe?'}, {'role': 'assistant', 'content': 'In general, yes.'}],
        'budget_s': 90,
        'key': '0efa315d29a294c50f58e1ccc06dacdbac12462d4b6fe168f084fc177b7197ab',
    },
    'meal_plan': {
        'system': '83b2929c8052f2e217ce',
        'user': "Whole foods to include, with the daily amount to aim for:\n- Kiwi (eat ~100 g (~1 kiwi)) for Vitamin C\n- Pumpkin seeds (a normal portion (about 70 g); the full dose isn't practical from food) for Zinc Every meal must fit a Halal diet.\n\nWrite exactly 2 meals now.",
        'model': 'meal-model-x',
        'history': None,
        'budget_s': None,
        'key': '88e533ba61c60fe361b0b5c818a88fca5001386aaf5d7033d808ab00764e008d',
    },
}
MEAL_ITEMS = [
    {"component": "Vitamin C", "component_key": "vitamin c", "dose_value": 90.0, "dose_unit": "mg", "decision": "replace",
     "selected_food": {"food_description": "Kiwi", "amount_per_100g": 90, "unit": "mg"}},
    {"component": "Zinc", "component_key": "zinc", "dose_value": 10.0, "dose_unit": "mg", "decision": "replace",
     "selected_food": {"food_description": "Pumpkin seeds", "amount_per_100g": 7.8, "unit": "mg"}},
]
CHAT_HISTORY = [{"role": "user", "content": "Is zinc safe?"}, {"role": "assistant", "content": "In general, yes."}]


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:20]


def _request_golden(lab, monkeypatch) -> dict[str, dict]:
    """Run the three manual features with a stub model and record what the model is asked."""
    sw = lab.sw
    seen: list[dict] = []

    def stub(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        seen.append({"system": _sha(system), "user": user if len(user) < 400 else _sha(user), "model": model,
                     "history": history, "budget_s": budget_s})
        return "A stub answer."

    monkeypatch.setattr(bb, "call_blockbrain_text", stub)
    monkeypatch.setenv("BLOCKBRAIN_MODEL_MEAL", "meal-model-x")
    monkeypatch.setenv("BLOCKBRAIN_MODEL_ASK", "ask-model-x")
    monkeypatch.setattr(sw, "_cached_rag_chunks", list)
    out: dict[str, dict] = {}
    for label, run in (
        ("ask_first", lambda: sw._answer_ask_ai_question("Zinc", "Is this dose safe?", history=[], dose_label="10 mg")),
        ("ask_follow_up", lambda: sw._answer_ask_ai_question("Zinc", "And for vegans?", history=CHAT_HISTORY, dose_label="10 mg")),
        ("meal_plan", lambda: sw._generate_meal_plan(MEAL_ITEMS, "Halal", 2)),
    ):
        sw.llm_cache.clear()
        sw.st.session_state.pop("_suppswipe_llm_usage", None)
        seen.clear()
        run()
        assert len(seen) == 1, (label, seen)
        stored = list(sw.llm_cache._cache)
        assert len(stored) == 1, (label, stored)
        out[label] = dict(seen[0], key=stored[0])
    return out


def test_the_request_to_the_model_is_what_it_was(lab, monkeypatch):
    got = _request_golden(lab, monkeypatch)
    assert got == GOLDEN, "the system / user prompt, model, history, budget or cache key changed (no prompt edit in this round)"


def test_the_model_id_is_read_on_the_script_thread_not_on_the_worker(lab, monkeypatch):
    sw = lab.sw
    seen_threads: list[int] = []
    real = sw._feature_model

    def spy_feature_model(feature):
        seen_threads.append(threading.get_ident())
        return real(feature)

    monkeypatch.setattr(sw, "_feature_model", spy_feature_model)
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: "A stub answer.")
    monkeypatch.setattr(sw, "_cached_rag_chunks", list)
    sw._generate_meal_plan(MEAL_ITEMS, "Halal", 2)
    sw._answer_ask_ai_question("Magnesium", "Best time to take it?", history=[], dose_label="")
    assert seen_threads and set(seen_threads) == {threading.get_ident()}


# ---------------------------------------------------------------- 6. the wait is never shorter than the call
def test_the_wait_lasts_at_least_as_long_as_the_calls_own_budget(lab, monkeypatch):
    """The old wait gave up after BLOCKBRAIN_TOTAL_BUDGET_S; a call with its own, longer budget (Ask AI: 90 s) must not be
    abandoned while it is still allowed to run."""
    monkeypatch.setattr(bb, "BLOCKBRAIN_TOTAL_BUDGET_S", 1.0)
    lab.fake.stream_script = [{"delay": 2.2, "text": ANSWER}]
    key = lab.key("deadline")
    text, _ = _manual(lab, key, budget_s=6)
    assert text == ANSWER and lab.sw.llm_cache.get(key) == ANSWER


def test_ask_ai_waits_longer_than_its_90_second_budget(lab, monkeypatch):
    waits: list[float] = []
    real = lab.sw._await_background_text

    def spy_wait(cache_key, pending, placeholder=None, *args, **kwargs):
        waits.append(float(kwargs.get("wait_s", args[0] if args else 0.0) or 0.0))
        return real(cache_key, pending, placeholder, *args, **kwargs)

    monkeypatch.setattr(lab.sw, "_await_background_text", spy_wait)
    monkeypatch.setattr(bb, "BLOCKBRAIN_TOTAL_BUDGET_S", 60.0)  # smaller than Ask AI's own 90 s
    seen: list[float | None] = []
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: seen.append(k.get("budget_s")) or "A stub answer.")
    monkeypatch.setattr(lab.sw, "_cached_rag_chunks", list)
    answer, _sources = lab.sw._answer_ask_ai_question("Iron", "How much per day?", history=[], dose_label="")
    assert answer == "A stub answer." and seen == [90]
    assert waits and waits[0] >= 90 + 3, waits  # the call's budget plus a few seconds


def test_a_race_that_filled_the_cache_meanwhile_returns_the_cached_answer(lab, monkeypatch):
    key = lab.key("race")
    real_submit = lab.sw.llm_cache.submit

    def finished_meanwhile(k, fn):
        lab.sw.llm_cache.put(k, "answer written by someone else")
        return real_submit(k, fn)  # returns None: already cached

    monkeypatch.setattr(lab.sw.llm_cache, "submit", finished_meanwhile)
    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: pytest.fail("asked although the answer was cached"))
    text, spy = _manual(lab, key)
    assert text == "answer written by someone else"
    assert spy.calls[-1][:2] == ("markdown", "answer written by someone else")


def test_the_fake_receives_the_system_prompt_the_user_prompt_and_the_history(lab):
    lab.fake.stream_script = [ANSWER]
    text, _ = _manual(lab, lab.key("wire"), history=CHAT_HISTORY)
    assert text == ANSWER
    bodies = lab.fake.json_bodies("/v2/api/agents/customAgent/stream")
    assert len(bodies) == 1  # one request for one generation
    assert bodies[0]["instructions"] == "SYSTEM PROMPT"
    turns = [(m["role"], "".join(part.get("text", "") for part in m["parts"])) for m in bodies[0]["messages"]]
    assert turns == [("user", "Is zinc safe?"), ("assistant", "In general, yes."), ("user", "USER PROMPT")]


# =====================================================================================================================
# SP2 - the polls are twice as fast, and only while a job runs
# =====================================================================================================================
import ast
from pathlib import Path

APP_SOURCE = Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py"


def _tree() -> ast.Module:
    return ast.parse(APP_SOURCE.read_text(encoding="utf-8"))


def _is_attr(node: ast.AST, owner: str, name: str) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == name and isinstance(node.value, ast.Name) and node.value.id == owner


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _run_every(call: ast.Call) -> ast.expr:
    (keyword,) = [k for k in call.keywords if k.arg == "run_every"]
    return keyword.value


def test_the_live_meal_plan_polls_twice_a_second_and_is_drawn_only_while_its_job_runs():
    tree = _tree()
    (func,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_live_meal_plan"]
    (decorator,) = func.decorator_list
    assert isinstance(decorator, ast.Call) and _is_attr(decorator.func, "st", "fragment")
    assert isinstance(_run_every(decorator), ast.Constant) and _run_every(decorator).value == 0.5
    # No polling at rest: the fragment exists only inside `elif llm_cache.inflight(plan_key) is not None:`.
    parents = _parents(tree)
    sites = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_live_meal_plan"]
    assert len(sites) == 1
    node: ast.AST = sites[0]
    guard = None
    while node in parents:
        child, node = node, parents[node]
        if isinstance(node, ast.If) and child in node.body:
            guard = node.test
            break
    assert guard is not None and ast.unparse(guard) == "llm_cache.inflight(plan_key) is not None"


def test_the_benefits_box_polls_twice_a_second_while_pending_and_not_at_all_otherwise():
    tree = _tree()
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _is_attr(n.func, "st", "fragment") and n.args and isinstance(n.args[0], ast.Name)
        and n.args[0].id == "_benefits_box"
    ]
    assert len(calls) == 1
    every = _run_every(calls[0])
    assert isinstance(every, ast.IfExp)
    assert ast.unparse(every.test) == "pending is not None"
    assert isinstance(every.body, ast.Constant) and every.body.value == 0.5
    assert isinstance(every.orelse, ast.Constant) and every.orelse.value is None


def test_no_other_fragment_polls():
    tree = _tree()
    every = [ast.unparse(_run_every(n)) for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_attr(n.func, "st", "fragment")
             and any(k.arg == "run_every" for k in n.keywords)]
    assert sorted(every) == sorted(["0.5", "0.5 if pending is not None else None"]), every


def test_the_wait_loop_sleeps_a_tenth_of_a_second():
    tree = _tree()
    (func,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_await_background_text"]
    sleeps = [n for n in ast.walk(func) if isinstance(n, ast.Call) and _is_attr(n.func, "time", "sleep")]
    assert [ast.unparse(n) for n in sleeps] == ["time.sleep(0.1)"]


# =====================================================================================================================
# SP3 - two memoised helpers return what the uncached originals returned
# =====================================================================================================================
# Verbatim copies of the two functions as they were before this round (blockbrain/app.py at e1d6ea0). They use the module's own
# patterns and tables; the reference classifier calls the reference normaliser, so nothing in the reference is memoised.
def _ref_normalize_lookup_key(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s\-\+\(\)]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ref_classify_food_commonness(food_description: str, food_category: str = "") -> dict:
    """Return guardrail tier for a food.

    tier  1 = allowed (not on the blocklist)
    tier -1 = blocked (exotic / non-retail / heavily processed / empty)
    `food_category` (USDA) is optional; when given, whole categories such as
    "American Indian/Alaska Native Foods" are blocked.
    """
    raw = str(food_description or "")
    # Branded products (e.g. "Vitasoy USA, Nasoya Lite Firm Tofu") are not the
    # generic single-ingredient whole foods we want, even though USDA flags them
    # single-ingredient. In USDA SR Legacy they carry a brand marker such as
    # " USA" (distinct from "USDA"), a trademark symbol or a brand in capitals.
    if " USA" in raw or "®" in raw or "™" in raw:
        return {"tier": -1, "reason": "branded"}
    brand = next(
        (t for t in (x.strip("'-") for x in bb._BRAND_TOKEN_RE.findall(raw)) if len(t) >= 3 and t not in bb._BRAND_TOKEN_ALLOWLIST),
        "",
    )
    if brand:
        return {"tier": -1, "reason": f"branded: {brand}"}
    key = _ref_normalize_lookup_key(food_description)
    if not key:
        return {"tier": -1, "reason": "empty"}

    category = _ref_normalize_lookup_key(str(food_category or "")).replace("/", " ")
    category = re.sub(r"\s+", " ", category).strip()
    if category in bb._UNCOMMON_FOOD_CATEGORIES:
        return {"tier": -1, "reason": f"category: {food_category}"}
    if bb._INDIGENOUS_FOOD_TAG_RE.search(raw):
        return {"tier": -1, "reason": "indigenous dataset food"}

    m = bb._commonness_matchers()
    text = key
    for phrase_rx, word_rx in m["neutral"]:
        text = phrase_rx.sub(lambda mm, _w=word_rx: _w.sub(" ", mm.group(0)), text)

    hit = m["exotic"].search(text)
    if hit:
        return {"tier": -1, "reason": f"exotic: {hit.group(0)}"}
    hit = m["uncommon"].search(text)
    if hit:
        return {"tier": -1, "reason": f"not sold in shops: {hit.group(0)}"}
    hit = m["processed"].search(text)
    if hit:
        return {"tier": -1, "reason": f"processed: {hit.group(0)}"}

    return {"tier": 1, "reason": "allowed"}


def _usda_pairs() -> list[tuple[str, str]]:
    conn = bb.try_open_usda_db()
    if conn is None:
        pytest.skip("the local USDA database is not available")
    try:
        rows = conn.execute("SELECT DISTINCT food_description, food_category FROM nutrient_rankings").fetchall()
    finally:
        conn.close()
    pairs = [(str(d or ""), str(c or "")) for d, c in rows]
    assert len(pairs) > 500  # the whole table, not a sample
    return pairs


EDGE_TEXTS = [
    "", " ", "  Kiwi  FRUIT, raw ", "Vitasoy USA, Nasoya Lite Firm Tofu", "Tofu ®", "Soy™ milk", "SILK soymilk", "USDA choice beef",
    "Pigeon peas (red gram), mature seeds, raw", "Quail eggs, whole, fresh, raw", "Mori-Nu Tofu", "Kiwi\tfruit\n", "Müsli ünïcode",
    "Buffalo mozzarella", "Water chestnuts, chinese", "Bear s garlic", "Caviar, black and red, granular", "Beef, liver, raw",
    "Frankfurter / hot-dog (pork)", "x" * 400, "1+1 (2) - 3",
]
EDGE_CATEGORIES = ["", "Fruits and Fruit Juices", "American Indian/Alaska Native Foods", "american indian alaska native foods",
                   "Baby Foods", "Fast Foods", "Restaurant Foods", "  Beef Products  ", "Sausages and Luncheon Meats"]


def test_classify_food_commonness_equals_the_uncached_original_for_every_usda_row():
    pairs = _usda_pairs()
    bb.classify_food_commonness.cache_clear()
    bb.normalize_lookup_key.cache_clear()
    for description, category in pairs + [(d, c) for d in EDGE_TEXTS for c in EDGE_CATEGORIES]:
        expected = _ref_classify_food_commonness(description, category)
        assert bb.classify_food_commonness(description, category) == expected, (description, category)  # first call
        assert bb.classify_food_commonness(description, category) == expected, (description, category)  # from the memo
        assert bb.classify_food_commonness(description) == _ref_classify_food_commonness(description), description
        assert bb.normalize_lookup_key(description) == _ref_normalize_lookup_key(description)
        assert bb.normalize_lookup_key(category) == _ref_normalize_lookup_key(category)
    assert bb.classify_food_commonness.cache_info().hits > 0 and bb.normalize_lookup_key.cache_info().hits > 0


def _outcome(fn, *args):
    try:
        return ("ok", fn(*args))
    except Exception as exc:  # the type and the message are part of the behaviour
        return ("raised", type(exc), str(exc))


class _LoudStr(str):
    """A str subclass: not "real str input", so it must take the original path."""


ODD_VALUES = [None, "", 0, 5, 1.5, [], ["a"], {}, {"a": 1}, (), ("a",), b"", b"Vitamin", True, _LoudStr(" Ab C "), object, set(), {1}]


@pytest.mark.parametrize("value", ODD_VALUES, ids=[repr(v)[:24] for v in ODD_VALUES])
def test_normalize_lookup_key_behaves_exactly_as_before_for_anything_but_a_real_str(value):
    assert _outcome(bb.normalize_lookup_key, value) == _outcome(_ref_normalize_lookup_key, value)


@pytest.mark.parametrize("value", ODD_VALUES, ids=[repr(v)[:24] for v in ODD_VALUES])
@pytest.mark.parametrize("category", [None, "", "Baby Foods", 3, ["x"], _LoudStr("Baby Foods")], ids=repr)
def test_classify_food_commonness_behaves_exactly_as_before_for_anything_but_two_real_strings(value, category):
    assert _outcome(bb.classify_food_commonness, value, category) == _outcome(_ref_classify_food_commonness, value, category)
    assert _outcome(bb.classify_food_commonness, value) == _outcome(_ref_classify_food_commonness, value)


def test_a_caller_that_changes_the_verdict_does_not_change_the_next_one():
    bb.classify_food_commonness.cache_clear()
    for description, category in (("Kiwi fruit, raw", "Fruits and Fruit Juices"), ("Tofu ®", ""), ("Beef, liver, raw", "Beef Products")):
        for _ in range(3):  # the first call fills the memo, the others read it
            verdict = bb.classify_food_commonness(description, category)
            expected = _ref_classify_food_commonness(description, category)
            assert verdict == expected
            verdict["tier"] = 99
            verdict["reason"] = "changed by the caller"
            verdict["extra"] = [1]
            verdict.clear()
        again = bb.classify_food_commonness(description, category)
        assert again == expected and again is not verdict


def test_the_two_memos_are_bounded():
    for fn in (bb.normalize_lookup_key, bb.classify_food_commonness):
        info = fn.cache_info()
        assert info.maxsize is not None and 0 < info.maxsize <= 65536, (fn.__name__, info)


def test_the_normalize_memo_never_keeps_a_long_text_a_visitor_sent():
    """A pasted label can be 20,000 characters and reaches normalize_lookup_key whole. An entry-count bound alone would let 16,384
    of those stay in the shared process (0.6-1.3 GB): only short keys (USDA descriptions, nutrient names) may be memoised."""
    limit = bb._LOOKUP_KEY_MEMO_MAX_CHARS
    assert 64 <= limit <= 512  # long enough for every USDA description, far too short for a pasted label
    longest_usda = max(len(d) for d, _c in _usda_pairs())
    assert longest_usda <= limit, (longest_usda, limit)  # the pools still hit the memo for every real description
    bb.normalize_lookup_key.cache_clear()
    for i in range(40):  # distinct long texts: nothing stays behind
        text = f"Vitamin {i} " + ("Magnesium 300 mg 80% 中文 " * 800)
        assert len(text) > limit
        assert bb.normalize_lookup_key(text) == _ref_normalize_lookup_key(text)
    assert bb.normalize_lookup_key.cache_info().currsize == 0
    assert bb.normalize_lookup_key.cache_info().misses == 0  # the memo was never even asked
    # the boundary: exactly `limit` characters are memoised (and served from the memo the second time), one more is not
    at_limit, over_limit = "a" * limit, "a" * (limit + 1)
    for value in (at_limit, at_limit, over_limit, over_limit):
        assert bb.normalize_lookup_key(value) == value
    info = bb.normalize_lookup_key.cache_info()
    assert (info.currsize, info.misses, info.hits) == (1, 1, 1), info
    bb.normalize_lookup_key.cache_clear()


def test_a_pasted_label_of_20000_characters_leaves_no_long_key_in_the_memo(monkeypatch):
    """The real path: parse_components(<pasted text>) hands the whole text to normalize_lookup_key. The text is normalised (so
    the spy sees it), but no key longer than the limit ever reaches the memo."""
    from_function, to_memo = [], []
    real_function, real_memo = bb.normalize_lookup_key, bb._normalize_lookup_key_memo

    def spy_function(value):
        from_function.append(len(value) if isinstance(value, str) else 0)
        return real_function(value)

    def spy_memo(value):
        to_memo.append(len(value))
        return real_memo(value)

    spy_memo.cache_info, spy_memo.cache_clear = real_memo.cache_info, real_memo.cache_clear
    monkeypatch.setattr(bb, "_normalize_lookup_key_memo", spy_memo)
    monkeypatch.setattr(bb, "normalize_lookup_key", spy_function)
    lines = ["Magnesium 300 mg 80%", "Vitamin D3 20 mcg 400%", "Zinc 10 mg 100%", "Selenium 55 mcg 100%"]
    for i in range(6):
        text = "\n".join(lines + [f"Other ingredients {i}: " + "cellulose, silica, " * 1000])[:20_000]
        assert len(text) >= 19_000
        parsed = bb.parse_components(text)
        assert parsed  # the label was really read, not skipped
    assert max(from_function) >= 19_000, "the pasted text no longer reaches normalize_lookup_key: this test checks nothing"
    assert to_memo, "nothing was memoised: the pools would be slow again"
    assert max(to_memo) <= bb._LOOKUP_KEY_MEMO_MAX_CHARS, max(to_memo)


def test_the_food_pools_equal_the_pools_built_without_the_memos(monkeypatch):
    """All 41 lexicon keys: the ranked rows at two limits and the amount index, built once with the verbatim originals (nothing
    memoised) and once with the real, memoised functions from a cold start."""
    keys = sorted(bb._NUTRIENT_LEXICON)
    assert len(keys) == 41

    def build() -> dict:
        bb._lexicon_food_rows.cache_clear()
        bb._lexicon_food_amount_index.cache_clear()
        out = {}
        for key in keys:
            out[key] = (bb._lexicon_food_rows(key, 250), bb._lexicon_food_rows(key, 5000), dict(bb._lexicon_food_amount_index(key)))
        return out

    with monkeypatch.context() as patch:
        patch.setattr(bb, "normalize_lookup_key", _ref_normalize_lookup_key)
        patch.setattr(bb, "classify_food_commonness", _ref_classify_food_commonness)
        reference = build()
    bb.normalize_lookup_key.cache_clear()
    bb.classify_food_commonness.cache_clear()
    try:
        started = time.perf_counter()
        memoised = build()
        elapsed = time.perf_counter() - started
        assert memoised.keys() == reference.keys()
        for key in keys:
            assert memoised[key][0] == reference[key][0], (key, 250)
            assert memoised[key][1] == reference[key][1], (key, 5000)
            assert memoised[key][2] == reference[key][2], (key, "amount index")
        assert bb.classify_food_commonness.cache_info().hits > 0  # the pools share their foods: the memo is used
        assert elapsed < 60  # a sanity bound, not a benchmark
    finally:
        bb._lexicon_food_rows.cache_clear()
        bb._lexicon_food_amount_index.cache_clear()

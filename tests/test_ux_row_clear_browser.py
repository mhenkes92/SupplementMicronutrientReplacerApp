"""The Keep / Replace / Back row of EVERY swipe card clears the fixed bottom bar at first sight on short phones.

The source checks at the top run offline; the browser checks (real Chromium, a touch phone at 2x) are opt-in like
tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers they reuse.

Measured before the change (sample label, 7 cards, bar present), the most the bar covered of the row per viewport: 320x568 235 px,
320x640 163, 360x640 86, 360x670 56, 360x740 42, 375x667 59, 390x664 44, 393x700 8 and 0 at 390x844, 412x780, 412x915. Cards 2-7 carry
a long note, so their row started lower than the first card's. Now the component measures the room between its frame and the bar
(it is the same origin as the page): a short phone gets a tighter card (same texts, less spacing; on a touch screen no swipe hint after the first card a
page load draws) in a frame that fills the room, so the row sits ~4 px above the bar on every card, and a card taller than its frame scrolls
inside it with a fade and, as soon as text is cut off (about half a line), a "scroll for more" pill; a focused control is scrolled clear of
both, and neither catches a touch. Roomy phones keep today's frame sizes. Where the page cannot be read
the card behaves as before (no tight class, frames sized to the card's content) plus a blank strip under its buttons.

Fixed screens (tests/test_ux_fixed_screen_browser.py): the frame now FILLS the room on every phone, whatever the card, so the numbers
of "roomy phones keep today's frames" (475 / 603 px at 390x844) are gone; the row sits ~4-6 px above the bar on every card, and the
page does not scroll. The frame also holds the card's tools row (Swap food / Ask AI / More): about 50 px more than before."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "swipe_mobile_app" / "swipe_component" / "index.html").read_text(encoding="utf-8")
SCRIPT = SRC[SRC.index("<script>"):]

BROWSER = os.getenv("SUPPSWIPE_BROWSER_TESTS", "") == "1"
needs_browser = pytest.mark.skipif(not BROWSER, reason="browser tests are opt-in (SUPPSWIPE_BROWSER_TESTS=1)")
if BROWSER:  # these modules skip themselves when the opt-in is off: the source checks below must still run offline
    from test_ux_app_bar_browser import BAR, BAR_BUTTON, MAIN, rect
    from test_ux_browser import (  # noqa: F401  (fixtures are used by name)
        CARD, DIALOG, browser, card, card_name, change_choice, finish_all_cards, iframe_kept, mark_iframe, page, results_heading, server,
        open_scan_sheet, settle, start_own_label, start_sample, wait_name_change,
    )


# ------------------------------------------------------------------ source checks (offline)
def _hostroom_body() -> str:
    start = SCRIPT.index("function hostRoom()")
    end = SCRIPT.index("function setTight(")
    return SCRIPT[start:end]


def test_every_access_to_the_host_page_is_inside_a_try_that_falls_back_to_the_old_behaviour():
    """If a deployment ever serves the frame cross-origin (or sandboxes it), reading the host throws: nothing may break."""
    body = _hostroom_body()
    assert body.count("try {") == 1 and "} catch (e) { return Infinity; }" in body  # unreadable = no room limit = today's behaviour
    inside = body[body.index("try {"):body.index("} catch (e)")]
    for needle in ("window.parent", "frameElement", ".document", "innerHeight"):
        assert needle in inside
    rest = SCRIPT.replace(body, "")
    assert "parent.innerHeight" not in rest
    # The few other reads of the host (the busy check of the page, the title of this frame) are each a try / catch of their own.
    for needle in ("frameElement", r"parent\.document"):
        for found in re.finditer(needle, rest):
            assert rest.rfind("try {", 0, found.start()) > rest.rfind("catch (e)", 0, found.start()), rest[found.start() - 80:found.start() + 80]
    # Everything else that mentions the host only posts a message to it or compares the message source.
    others = [m.group(0) for m in re.finditer(r"window\.parent[.\w]*", rest)]
    assert set(others) <= {"window.parent.postMessage", "window.parent", "window.parent.addEventListener",
                           "window.parent.document.querySelector"}, others
    # The one other thing the frame does to the host: listen for its resize (the bar moves with the phone's toolbars), best effort.
    assert re.search(r"try \{\s+window\.parent\.addEventListener\(\"resize\".*?\}\);\s+\} catch \(e\) \{\}", rest, re.S)
    assert "return Infinity" in body and "isFinite(room)" in SCRIPT  # an unmeasurable room changes nothing downstream


def test_the_frame_rule_the_font_floor_and_the_cue_are_as_agreed():
    assert SCRIPT.count("Math.max(HEIGHT, Math.min(780, contentHeight + actionsHeight + 10), maxHeight)") == 1
    sizes = [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)rem", SRC)]
    assert min(sizes) >= 0.72  # the new "scroll for more" pill included (the audit floor is 0.7rem)
    assert '<div id="cue" aria-hidden="true">' in SRC  # a visual aid only: the card's aria-label already carries everything
    assert ".tcard > * { flex-shrink: 0; }" in SRC  # a card that scrolls must not squash its own rows (the progress bar)
    assert "hint-keep" in SRC and "(!canReplace || editing)" in SRC  # explanatory hints are never hidden


def test_the_tight_card_only_changes_spacing_and_the_name_and_badge_size():
    """Same texts, same body font sizes: nothing but spacing, the 40 px badge, the name and the swipe hint may differ."""
    rules = re.findall(r"#wrap\.tight([^{]*)\{([^}]*)\}", SRC)
    assert len(rules) >= 10
    for selector, declarations in rules:
        props = {p.split(":")[0].strip() for p in declarations.split(";") if ":" in p}
        sized = {p for p in props if p.startswith("font-size")}
        if sized:
            assert selector.strip() in (".badge", ".name"), (selector, props)  # body copy keeps its size
        assert not {"content", "visibility"} & props, (selector, props)
        assert not (props & {"display"}) or ".hint" in selector, (selector, props)  # only the swipe hint may be dropped
    # the hint is dropped only on a touch screen (a mouse / keyboard user keeps the only place the arrow keys are named) and only
    # after the first card this page load drew (a resumed scan opens on a later card)
    assert "#wrap.tight:not(.first):not(.fine) .hint:not(.hint-keep) { display: none; }" in SRC


def _rule(selector: str) -> str:
    """The declarations of the first CSS rule whose selector is exactly `selector`."""
    match = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", SRC)
    assert match, selector
    return match.group(1)


def test_the_scroll_cue_is_a_fade_and_a_pill_that_never_catch_a_touch():
    """The overlay sits above the card: without pointer-events none a swipe that starts in the card's lower 30 px, and a tap on a
    control under the fade (the athlete-guide button at large text), would land on the overlay and be swallowed."""
    fade = _rule("#stage.cue::after")
    assert "pointer-events: none" in fade and "linear-gradient" in fade and "height: 30px" in fade
    pill = _rule("#cue")
    assert "pointer-events: none" in pill and "display: none" in pill
    assert "display: block" in _rule("#stage.cue-strong #cue")


def test_the_pill_appears_as_soon_as_text_is_cut_and_the_fade_does_not_wash_out_a_card_that_is_only_short_of_its_padding():
    """Hidden text is counted without the card's bottom padding. A 23 px cut at 320x640 (the vitamin D 'ask your doctor' line) used
    to get the fade alone, because the pill needed 48 px."""
    fade, pill = (int(re.search(r"var CUE_%s = (\d+);" % name, SCRIPT).group(1)) for name in ("FADE", "PILL"))
    assert 0 < fade < pill <= 16, (fade, pill)  # about half a line of text cut = "scroll for more"
    body = SCRIPT[SCRIPT.index("function updateCue()"):SCRIPT.index('card.addEventListener("scroll"')]
    assert "paddingBottom" in body and 'toggle("cue", cut > CUE_FADE)' in body and 'toggle("cue-strong", cut > CUE_PILL)' in body


def test_a_focused_control_is_scrolled_clear_of_the_fade():
    """Tab to the athlete-guide button of a card that scrolls: the browser keeps it this far from the card's edge, so neither the
    fade (30 px) nor the pill (to 28 px) covers its last line or its focus ring."""
    padding = re.search(r"scroll-padding-bottom:\s*(\d+)px", _rule(".tcard"))
    assert padding and int(padding.group(1)) >= 40, padding


def test_the_first_card_a_page_load_draws_keeps_its_swipe_hint_even_when_it_is_not_card_one():
    """A resumed scan opens on card 3 or 4: its hint is the first the visitor sees. And a mouse / keyboard user never loses it."""
    assert "firstCard = cardIndex === 0 || !drawnBefore;" in SCRIPT and "drawnBefore = true;" in SCRIPT
    assert '(firstCard ? " first" : "")' in SCRIPT and '(finePointer ? " fine" : "")' in SCRIPT
    assert "cardIndex === 0 ? \" first\"" not in SCRIPT  # the old rule, which treated a resumed card as already explained


# ------------------------------------------------------------------ the sample label, as the card showed it BEFORE this change
# Captured from the unpatched component (390x844 and 320x640 gave identical texts): the texts of all 7 cards.
BASELINE = [
    {
        'count': 'Card 1 of 7',
        'name': 'Vitamin C',
        'aria': 'Card 1 of 7: Vitamin C, dose 80 mg. Whole-food replacement: Guavas. To match the dose: ~35 g. Notes: Whole foods pair vitamin C with bioflavonoids that support its absorption and antioxidant action.',
        'dose': 'In your pill: 80 mg',
        'food': 'Guavas',
        'portion': ['💊 Match this dose → eat ~35 g', '🏃 Target for very active people (200 mg) → eat ~88 g. Opens the athlete guide.›'],
        'warn': None,
        'note': '💡 Whole foods pair vitamin C with bioflavonoids that support its absorption and antioxidant action.',
    },
    {
        'count': 'Card 2 of 7',
        'name': 'Vitamin D3',
        'aria': 'Card 2 of 7: Vitamin D3, dose 20 mcg. Whole-food replacement: Mackerel, Atlantic. To match the dose: ~124 g. Notes: Few foods are rich in vitamin D: UV-exposed mushrooms give D2, which raises blood levels less than D3; oily fish and egg yolk give D3. Sunlight is the main source. ℹ️ October–March the sun in Germany is too weak for your skin to make vitamin D, and food alone rarely reaches the 20 µg/day reference. Many people in Germany take vitamin D in winter — ask your doctor whether that suits you.',
        'dose': 'In your pill: 20 mcg',
        'food': 'Mackerel, Atlantic',
        'portion': ['💊 Match this dose → eat ~124 g', '🏃 Target for very active people (25 mcg) → eat ~155 g. Opens the athlete guide.›'],
        'warn': None,
        'note': '💡 Few foods are rich in vitamin D: UV-exposed mushrooms give D2, which raises blood levels less than D3; oily fish and egg yolk give D3. Sunlight is the main source. ℹ️ October–March the sun in Germany is too weak for your skin to make vitamin D, and food alone rarely reaches the 20 µg/day reference. Many people in Germany take vitamin D in winter — ask your doctor whether that suits you.',
    },
    {
        'count': 'Card 3 of 7',
        'name': 'Vitamin B12',
        'aria': 'Card 3 of 7: Vitamin B12, dose 2.5 mcg. Whole-food replacement: Oysters, eastern (farmed). To match the dose: ~15 g. Notes: Only animal foods reliably supply active vitamin B12, bound to protein; liver and shellfish are the richest sources.',
        'dose': 'In your pill: 2.5 mcg',
        'food': 'Oysters, eastern (farmed)',
        'portion': ['💊 Match this dose → eat ~15 g', '🏃 Target for very active people (4 mcg) → eat ~25 g. Opens the athlete guide.›'],
        'warn': None,
        'note': '💡 Only animal foods reliably supply active vitamin B12, bound to protein; liver and shellfish are the richest sources.',
    },
    {
        'count': 'Card 4 of 7',
        'name': 'Folic acid',
        'aria': 'Card 4 of 7: Folic acid, dose 200 mcg. Whole-food replacement: Black-eyed peas (dry). To match the dose: ~54 g. Notes: Your pill is folic acid, which is absorbed ~1.7× better than food folate — so the food portion is sized to 1.7× the label amount (µg DFE).',
        'dose': 'In your pill: 200 mcg',
        'food': 'Black-eyed peas (dry)',
        'portion': ['💊 Match this dose → eat ~54 g', '🏃 Target for very active people (600 mcg) → eat ~95 g. Opens the athlete guide.›'],
        'warn': None,
        'note': '💡 Your pill is folic acid, which is absorbed ~1.7× better than food folate — so the food portion is sized to 1.7× the label amount (µg DFE).',
    },
    {
        'count': 'Card 5 of 7',
        'name': 'Magnesium',
        'aria': "Card 5 of 7: Magnesium, dose 56 mg. Whole-food replacement: Hemp seeds (hulled). To match the dose: ~8 g. Notes: Food magnesium comes bound to fibre and other minerals, so it's better tolerated than high-dose salts. ℹ️ Low dose: about 15% of the EU daily reference intake (NRV).",
        'dose': 'In your pill: 56 mg',
        'food': 'Hemp seeds (hulled)',
        'portion': ['💊 Match this dose → eat ~8 g', '🏃 Target for very active people (500 mg) → not practical from food alone (~71 g/day; realistic max ~70 g/day). Opens the athlete guide.›'],
        'warn': None,
        'note': "💡 Food magnesium comes bound to fibre and other minerals, so it's better tolerated than high-dose salts. ℹ️ Low dose: about 15% of the EU daily reference intake (NRV).",
    },
    {
        'count': 'Card 6 of 7',
        'name': 'Zinc',
        'aria': 'Card 6 of 7: Zinc, dose 10 mg. Whole-food replacement: Oysters, eastern (wild). To match the dose: ~25 g. Notes: Food zinc is balanced with copper; isolated zinc pills can deplete copper over time.',
        'dose': 'In your pill: 10 mg',
        'food': 'Oysters, eastern (wild)',
        'portion': ['💊 Match this dose → eat ~25 g', '🏃 Target for very active people (15 mg) → eat ~38 g. Opens the athlete guide.›'],
        'warn': None,
        'note': '💡 Food zinc is balanced with copper; isolated zinc pills can deplete copper over time.',
    },
    {
        'count': 'Card 7 of 7',
        'name': 'Selenium',
        'aria': 'Card 7 of 7: Selenium, dose 55 mcg. Whole-food replacement: Brazil nuts. To match the dose: ~2.9 g (~0.6 Brazil nuts). Notes: One Brazil nut (~5 g) holds roughly 50–100 µg selenium (it varies with soil; USDA reference ~96 µg) — one nut a day is plenty; 3 or more nuts a day can pass the 255 µg/day upper intake level.',
        'dose': 'In your pill: 55 mcg',
        'food': 'Brazil nuts',
        'portion': ['💊 Match this dose → eat ~2.9 g (~0.6 Brazil nuts)', '🏃 Target for very active people (70 mcg) → eat ~3.7 g (~0.7 Brazil nuts). Opens the athlete guide.›'],
        'warn': None,
        'note': '💡 One Brazil nut (~5 g) holds roughly 50–100 µg selenium (it varies with soil; USDA reference ~96 µg) — one nut a day is plenty; 3 or more nuts a day can pass the 255 µg/day upper intake level.',
    },
]

HINT = "Swipe ◀ keep the pill · eat the food ▶"
BUTTONS = [["btnBack", "Back to the previous card", "↩"], ["btnKeep", "Keep pill", "◀ Keep pill"], ["btnRepl", "Replace", "Replace ▶"]]

SIZES = [(320, 568), (320, 640), (360, 640), (360, 670), (360, 740), (375, 667), (390, 664), (390, 844), (393, 700), (412, 780), (412, 915)]
SHORT = [(320, 568), (320, 640), (360, 640), (390, 664)]
ROOMY = [(390, 844), (412, 915)]  # phones whose room is larger than the tallest card needs: the normal (not tight) card from the first card on
OWN_LABEL = "Vitamin C 80 mg 100%\nVitamin D3 20 µg 400%\nZinc 10 mg 100%\nSelenium 55 µg 100%"
WARN_LABEL = "Vitamin A 3000 µg 375%\nVitamin D3 100 µg 2000%\nIron 45 mg 321%\nZinc 40 mg 400%\nSelenium 300 µg 545%"
# A text-size setting of the phone (Android "Font size", iOS "Larger Text", a browser's default font size) scales the rem-based text of
# EVERY frame, the card's too. This init script reaches the page only (it does not run in the card's iframe: measured, the page's
# html font-size 32 px, the card's 16 px), so `open_page(init=TEXT_200)` also rewrites the card's html to carry the same rule
# (_zoom_card_frames); before that, every large-text measurement scaled the page chrome and left the card at 100 %.
TEXT_200 = ("document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style');"
            " s.textContent = 'html { font-size: 200% !important; }'; document.head.appendChild(s); });")
TEXT_150 = TEXT_200.replace("200%", "150%")
# frameElement of the card's frame throws, as it would for a cross-origin or sandboxed frame
BLOCK_HOST = ("try { if (window.top !== window) { Object.defineProperty(window, 'frameElement', { configurable: true,"
              " get() { throw new DOMException('Blocked a frame with origin', 'SecurityError'); } }); window.__blocked = true; } }"
              " catch (e) { window.__initerr = String(e); }")
CARD_TEXT_JS = """() => {
  const t = s => { const e = document.querySelector(s); return e ? e.textContent.replace(/\\s+/g, ' ').trim() : null; };
  const hint = document.querySelector('.hint');
  return { count: t('.count'), name: t('.name'), aria: document.getElementById('card').getAttribute('aria-label'), dose: t('.dose'),
           food: t('.food'), portion: [...document.querySelectorAll('.portion .pl')].map(e => e.textContent.replace(/\\s+/g, ' ').trim()),
           warn: t('.warn'), note: t('.bio'), hint: t('.hint'), hint_shown: hint ? getComputedStyle(hint).display !== 'none' : null,
           tight: document.getElementById('wrap').classList.contains('tight'),
           buttons: [...document.querySelectorAll('#actions button')].map(b => [b.id, b.getAttribute('aria-label'), b.textContent.trim()]),
           back_disabled: document.getElementById('btnBack').disabled }; }"""


# ------------------------------------------------------------------ browser helpers
def _text_zoom(init) -> int | None:
    """The percentage of an init script made like TEXT_200 / TEXT_150, else None."""
    match = re.search(r"html \{ font-size: (\d+)% !important; \}", init or "")
    return int(match.group(1)) if match else None


def _zoom_card_frames(ctx, percent: int) -> None:
    """The card's page, served with the same root font size as the zoomed page (what a phone's text-size setting does to every frame)."""
    style = f"<style>html {{ font-size: {percent}% !important; }}</style>"

    def handler(route):
        response = route.fetch()
        body = response.text().replace("</head>", style + "</head>", 1)
        headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding")}
        route.fulfill(status=response.status, headers=headers, body=body)

    ctx.route(re.compile(r".*/component/app\.tinder_swipe/index\.html.*"), handler)


def open_page(browser, server, size, touch=True, init=None):
    """A phone-sized page with the bar present; JS errors are collected in pg.errors. `init=TEXT_150 / TEXT_200`: a larger text size
    for the page AND the card's frame."""
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=touch, has_touch=touch, device_scale_factor=2)
    if init:
        ctx.add_init_script(init)
        if _text_zoom(init):
            _zoom_card_frames(ctx, _text_zoom(init))
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" and "tinder_swipe" in (m.location or {}).get("url", "") else None)
    pg.goto(server, wait_until="networkidle")
    pg.locator(BAR_BUTTON).nth(2).wait_for(timeout=20000)
    return ctx, pg


def card_frame(pg):
    return pg.frame(url=lambda u: "tinder_swipe" in u)


def watch_navigations(pg) -> list:
    navs: list = []
    pg.on("framenavigated", lambda f: navs.append(f.url) if "tinder_swipe" in f.url else None)
    return navs


def to_top(pg) -> None:
    pg.evaluate(f"{MAIN}.scrollTo(0, 0)")
    pg.wait_for_timeout(150)


def row_gap(pg) -> int:
    """Pixels between the bottom of the Keep / Replace / Back row and the top of the bar (negative = covered)."""
    bar = rect(pg, BAR)
    boxes = [card(pg).locator(s).bounding_box() for s in ("#btnBack", "#btnKeep", "#btnRepl")]
    return round(bar["y"] - max(b["y"] + b["height"] for b in boxes))


def frame_height(pg) -> int:
    return round(pg.locator(CARD).bounding_box()["height"])


def keep_and_wait(pg, name: str) -> str:
    card(pg).locator("#btnKeep").click(timeout=5000)  # Playwright refuses a covered target
    new = wait_name_change(pg, name)
    settle(pg, 0.3)
    return new


def walk(pg, count: int, measure) -> list:
    """Measure each of `count` cards at the top of the page, keeping the pill on all but the last."""
    seen = []
    for i in range(count):
        name = card_name(pg)
        to_top(pg)
        seen.append((name, measure()))
        if i < count - 1:
            keep_and_wait(pg, name)
    return seen


# ------------------------------------------------------------------ the row clears the bar on every card
@needs_browser
@pytest.mark.parametrize("size", SIZES)
def test_the_button_row_of_every_card_clears_the_bar_at_first_sight(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        mark_iframe(pg)
        navs = watch_navigations(pg)
        seen = walk(pg, 7, lambda: row_gap(pg))
        assert len(seen) == 7 and all(gap >= 0 for _, gap in seen), seen
        assert iframe_kept(pg) and navs == []  # one frame for the whole scan, never reloaded
        assert pg.errors == []
    finally:
        ctx.close()


@needs_browser
@pytest.mark.parametrize("size", SHORT)
def test_on_a_short_phone_the_row_sits_in_the_same_place_on_every_card_just_above_the_bar(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        tops, frames = [], []

        def measure():
            tops.append(round(card(pg).locator("#btnKeep").bounding_box()["y"]))
            frames.append(frame_height(pg))
            return row_gap(pg)

        gaps = [gap for _, gap in walk(pg, 7, measure)]
        assert max(tops) - min(tops) <= 2, tops  # the thumb finds the row where it left it
        assert max(gaps) - min(gaps) <= 2 and 0 <= min(gaps) and max(gaps) <= 12, gaps  # about 4 px above the bar, on every card
        assert frames == sorted(frames), frames  # and the frame never shrinks
    finally:
        ctx.close()


@needs_browser
@pytest.mark.parametrize("size", ROOMY)
def test_a_roomy_phone_fills_its_room_with_the_normal_card_and_the_row_sits_just_above_the_bar(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        assert "tight" not in card_frame(pg).evaluate("document.getElementById('wrap').className")
        first = frame_height(pg)
        room = round(rect(pg, BAR)["y"] - rect(pg, CARD)["y"])
        assert room - 12 <= first <= room + 6, (first, room)  # the frame fills the room down to the bar (900 px at most)
        name = card_name(pg)
        keep_and_wait(pg, name)
        to_top(pg)
        assert frame_height(pg) == first  # the same frame on card 2: the buttons stay where the thumb is
        assert "tight" not in card_frame(pg).evaluate("document.getElementById('wrap').className")
        assert 0 <= row_gap(pg) <= 12
    finally:
        ctx.close()


def test_the_decision_to_tighten_is_made_from_the_measured_room():
    """A new scan starts tight only when the room is short, and a card tightens only when its normal frame does not fit; the
    thresholds (TIGHT_BELOW, ROOM_FLOOR) are judgment values, so only the shape of the rule is pinned."""
    assert re.search(r"tight = hostRoom\(\) < TIGHT_BELOW", SCRIPT) and "ROOM_FLOOR" in SCRIPT
    assert "if (Math.max(HEIGHT, Math.min(780, contentHeight + actionsHeight + 10)) <= avail) { break; }" in SCRIPT  # fits: stays normal


WATCH_LIVE = """() => { window.__live = []; window.__renders = 0;
  window.addEventListener('message', e => { if (e.data && e.data.type === 'streamlit:render') { window.__renders += 1; } }, true);
  new MutationObserver(() => { const t = document.getElementById('live').textContent; if (t) { window.__live.push(t); } })
    .observe(document.getElementById('live'), { childList: true, characterData: true, subtree: true }); }"""


@needs_browser
@pytest.mark.parametrize("size,frame_grows", [((390, 844), False), ((320, 640), False)])
def test_the_announcement_of_the_choice_is_said_once_and_the_frame_does_not_move(browser, server, size, frame_grows):
    """Streamlit re-sends the same props when the frame height changes; the second draw used to replace the live region.

    The frame used to grow from card 1 to card 2 (475 -> 603 px at 390x844), which made Streamlit send two renders. The frame is the
    same on every card now (it fills the room), so after a swipe one render arrives and the announcement is said once; render() still
    ignores a second, identical one (the first draw of a frame and a changing chip above the card send one)."""
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        before = frame_height(pg)
        fr.evaluate(WATCH_LIVE)
        card(pg).locator("#btnKeep").click(timeout=5000)
        wait_name_change(pg, "Vitamin C")
        settle(pg, 0.8)
        after = frame_height(pg)
        history = fr.evaluate("window.__live")
        renders = fr.evaluate("window.__renders")
        assert (after > before) is frame_grows and after == before, (before, after)
        assert renders >= 1, renders
        assert len(history) == 1 and history[0].startswith("Kept the pill. Card 2 of 7"), history  # said once, and not overwritten
        assert fr.evaluate("document.getElementById('live').textContent").startswith("Kept the pill. Card 2 of 7")
    finally:
        ctx.close()


# ------------------------------------------------------------------ the card says what it said before
@needs_browser
@pytest.mark.parametrize("size", [(390, 844), (320, 640)])
def test_every_text_of_every_card_is_the_one_the_unpatched_component_showed(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        rows = walk(pg, 7, lambda: card_frame(pg).evaluate(CARD_TEXT_JS))
        for i, ((_, got), want) in enumerate(zip(rows, BASELINE)):
            for key, value in want.items():
                assert got[key] == value, (size, i + 1, key, got[key], value)
            assert got["hint"] == HINT and got["buttons"] == BUTTONS, (size, i + 1)  # the text is in the page on every card
            assert got["back_disabled"] == (i == 0), (size, i + 1)
            dropped = got["tight"] and i > 0  # the swipe hint is only shown on the first card of a tight scan
            assert got["hint_shown"] is (not dropped), (size, i + 1, got["tight"], got["hint_shown"])
        assert [got["tight"] for _, got in rows][0] == (size == (320, 640))  # the short phone is the one that was tightened
    finally:
        ctx.close()


@needs_browser
def test_the_progress_bar_of_a_card_that_scrolls_keeps_its_height(browser, server):
    ctx, pg = open_page(browser, server, (320, 640))
    try:
        start_sample(pg)
        card_frame_js = ("() => { const c = document.getElementById('card'); return { bar: document.querySelector('.bar').getBoundingClientRect().height,"
                         " hidden: c.scrollHeight - c.clientHeight }; }")
        seen = walk(pg, 3, lambda: card_frame(pg).evaluate(card_frame_js))
        assert [m["bar"] for _, m in seen] == [4, 4, 4], seen
        assert seen[1][1]["hidden"] > 0, seen  # card 2 (the long vitamin D note) is the one that scrolls inside its frame
    finally:
        ctx.close()


# ------------------------------------------------------------------ the fallback: a page that cannot be read
@needs_browser
def test_a_frame_that_cannot_read_its_page_behaves_exactly_as_before(browser, server):
    ctx, pg = open_page(browser, server, (320, 640), init=BLOCK_HOST)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        assert fr.evaluate("window.__blocked === true")
        assert fr.evaluate("(() => { try { return !!window.frameElement; } catch (e) { return 'THROWS ' + e.name; } })()").startswith("THROWS")
        seen = walk(pg, 3, lambda: (frame_height(pg), fr.evaluate("document.getElementById('wrap').classList.contains('tight')")))
        # Frames sized to the card's content as before (+ the tools row, + the blank strip under the buttons), never tight.
        assert [m for _, m in seen] == [(601, False), (748, False), (748, False)], seen
        assert fr.evaluate("document.getElementById('wrap').classList.contains('spacer')")
        # The page cannot be measured, so the row of the second card starts under the bar; the page's own padding is gone on this
        # screen, and the strip is what lets it be scrolled clear: it can be reached and tapped.
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(300)
        assert row_gap(pg) >= 0, row_gap(pg)
        assert pg.errors == []
    finally:
        ctx.close()


# ------------------------------------------------------------------ other screens of the same component
@needs_browser
def test_a_visitors_own_label_clears_the_bar_too_without_the_sample_caption(browser, server):
    ctx, pg = open_page(browser, server, (320, 640))
    try:
        start_own_label(pg)
        assert pg.get_by_text("Sample label").count() == 0  # no caption above the card: the room is a little larger
        seen = walk(pg, 4, lambda: row_gap(pg))
        assert len(seen) == 4 and all(0 <= gap <= 12 for _, gap in seen), seen
    finally:
        ctx.close()


@needs_browser
def test_a_card_reopened_from_the_results_clears_the_bar_and_keeps_its_hint(browser, server):
    ctx, pg = open_page(browser, server, (320, 640))
    try:
        start_sample(pg)
        finish_all_cards(pg)
        change_choice(pg, "keep", "Vitamin B12")
        card(pg).locator("#card .name").wait_for(timeout=20000)
        settle(pg, 0.8)
        to_top(pg)
        assert "Editing from your results" in card(pg).locator("#card").inner_text()
        assert card(pg).locator(".hint").is_visible()  # explanatory text is never dropped
        assert card(pg).locator("#btnBack").get_attribute("aria-label") == "Back to your results"
        assert 0 <= row_gap(pg) <= 12, row_gap(pg)
        card(pg).locator("#btnBack").click(timeout=5000)
        results_heading(pg).wait_for(timeout=20000)
    finally:
        ctx.close()


@needs_browser
def test_a_label_with_upper_limit_warnings_keeps_every_red_box_in_full_view(browser, server):
    ctx, pg = open_page(browser, server, (320, 640))
    try:
        open_scan_sheet(pg)  # the Scan item of the bar holds the three ways to start
        pg.get_by_role("button", name="Analyze my supplement").click()
        dialog = pg.get_by_role("dialog")
        dialog.locator("button", has_text="Paste").click()
        settle(pg)
        dialog.locator("textarea").fill(WARN_LABEL)
        dialog.get_by_role("button", name="Analyze").click()
        card(pg).locator("#card .name").wait_for(timeout=60000)
        settle(pg)
        count = int(card(pg).locator(".count").inner_text().split(" of ")[1])
        probe = ("() => { const w = document.querySelector('.warn'); if (!w) { return null; } const a = w.getBoundingClientRect(),"
                 " c = document.getElementById('card').getBoundingClientRect(); return { top: a.top - c.top, below: c.bottom - a.bottom, h: a.height }; }")
        seen = walk(pg, count, lambda: (row_gap(pg), card_frame(pg).evaluate(probe)))
        assert all(0 <= gap <= 12 for _, (gap, _) in seen), seen
        boxes = [box for _, (_, box) in seen if box]
        assert len(boxes) >= 2, seen  # the high doses of iron, zinc and selenium carry a warning
        assert all(b["top"] >= 0 and b["below"] >= 36 and b["h"] > 40 for b in boxes), boxes  # whole, and clear of the fade at the card's foot
    finally:
        ctx.close()


@needs_browser
def test_at_double_text_size_the_row_is_clear_and_the_card_scrolls_inside_its_frame(browser, server):
    """320x640 at 200 % text, the card's own frame zoomed too: its window would be a slit, so the frame grows to give it 240 px and the
    page scrolls (see MIN_WINDOW); scrolled to the end, the row is clear of the bar on every card."""
    ctx, pg = open_page(browser, server, (320, 640), init=TEXT_200)
    try:
        start_sample(pg)

        def measure():
            pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
            pg.wait_for_timeout(250)
            return row_gap(pg), card_frame(pg).evaluate(
                "(() => { const c = document.getElementById('card'); return c.scrollHeight - c.clientHeight; })()")

        seen = walk(pg, 7, measure)
        assert all(gap >= 0 for _, (gap, _) in seen), seen
        assert max(hidden for _, (_, hidden) in seen) > 40, seen  # what does not fit is reachable by scrolling the card
        assert pg.evaluate("document.scrollingElement.scrollWidth") <= 320  # no sideways page scroll
    finally:
        ctx.close()


@needs_browser
def test_a_turned_phone_starts_over_and_comes_back_to_the_normal_card(browser, server):
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))
        to_top(pg)
        portrait = frame_height(pg)
        assert row_gap(pg) >= 0 and "tight" not in card_frame(pg).evaluate("document.getElementById('wrap').className")
        pg.set_viewport_size({"width": 844, "height": 390})
        pg.wait_for_timeout(900)
        to_top(pg)
        assert frame_height(pg) >= 320  # landscape: the floor (the page scrolls there, as before)
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(900)
        to_top(pg)
        assert frame_height(pg) == portrait and row_gap(pg) >= 0  # back in portrait: the same fixed screen again
        assert "tight" not in card_frame(pg).evaluate("document.getElementById('wrap').className")
        assert pg.errors == []
    finally:
        ctx.close()


# ------------------------------------------------------------------ every way of answering still works
def cdp_swipe(ctx, pg, x0, y0, dx, dy=0, steps=8) -> None:
    cdp = ctx.new_cdp_session(pg)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
    for i in range(1, steps + 1):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x0 + dx * i / steps, "y": y0 + dy * i / steps}]})
        pg.wait_for_timeout(16)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def centre(pg) -> tuple:
    b = card(pg).locator("#card").bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + min(b["height"] / 2, 150)


def moves_to_another_card(pg, old: str, act):
    act()
    try:
        return wait_name_change(pg, old, timeout=6)
    except AssertionError:
        return None


@needs_browser
@pytest.mark.parametrize("size", [(320, 640), (320, 568), (390, 844)])
def test_touch_keys_and_buttons_still_choose_and_a_short_or_vertical_drag_does_not(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        to_top(pg)
        first = card_name(pg)
        fr.evaluate("document.getElementById('btnKeep').focus({preventScroll: true})")
        second = moves_to_another_card(pg, first, lambda: pg.keyboard.press("Enter"))  # Enter on the focused Keep button
        assert second, "Enter on Keep"
        settle(pg, 0.5)
        assert moves_to_another_card(pg, second, lambda: card(pg).locator("#btnBack").dispatch_event("click")) == first  # Back
        settle(pg, 0.5)
        assert card(pg).locator(".prev").count() == 1  # the choice made on that card is remembered
        assert moves_to_another_card(pg, first, lambda: card(pg).locator("#btnRepl").dispatch_event("click")) == second  # Replace
        settle(pg, 0.5)
        fr.evaluate("document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        card(pg).locator("#card").focus()
        third = moves_to_another_card(pg, second, lambda: pg.keyboard.press("ArrowRight"))
        assert third, "ArrowRight"
        settle(pg, 0.5)
        fourth = moves_to_another_card(pg, third, lambda: pg.keyboard.press("ArrowLeft"))
        assert fourth, "ArrowLeft"
        settle(pg, 0.6)
        to_top(pg)
        x, y = centre(pg)
        cdp_swipe(ctx, pg, x, y, 0, 80)  # a vertical drag is no swipe
        pg.wait_for_timeout(600)
        assert card_name(pg) == fourth
        x, y = centre(pg)
        cdp_swipe(ctx, pg, x, y, 40)  # a short drag springs back
        pg.wait_for_timeout(500)
        assert card_name(pg) == fourth
        x, y = centre(pg)
        fifth = moves_to_another_card(pg, fourth, lambda: cdp_swipe(ctx, pg, x - 60, y, 160))  # a finger swipe to the right replaces
        assert fifth, "touch swipe right"
        settle(pg, 0.6)
        x, y = centre(pg)
        sixth = moves_to_another_card(pg, fifth, lambda: cdp_swipe(ctx, pg, x + 60, y, -160))  # and to the left keeps
        assert sixth, "touch swipe left"
        assert pg.errors == []
    finally:
        ctx.close()


@needs_browser
def test_a_card_that_scrolls_inside_its_frame_still_swipes_sideways_and_scrolls_up_and_down(browser, server):
    ctx, pg = open_page(browser, server, (320, 568))
    try:
        start_sample(pg)
        name = card_name(pg)
        keep_and_wait(pg, name)  # card 2 carries the long vitamin D note: ~90 px of it are below the fold of the card
        to_top(pg)
        c = card(pg).locator("#card")
        state = c.evaluate("e => ({ hidden: e.scrollHeight - e.clientHeight, top: e.scrollTop })")
        assert state["hidden"] > 48 and state["top"] == 0, state
        assert card_frame(pg).evaluate("document.getElementById('stage').classList.contains('cue-strong')")  # the pill says "scroll for more"
        second = card_name(pg)
        x, y = centre(pg)
        cdp_swipe(ctx, pg, x, y + 60, 0, -90)  # a finger drag upwards moves the text, not the card
        pg.wait_for_timeout(500)
        assert card_name(pg) == second
        assert c.evaluate("e => e.scrollTop") > 20
        x, y = centre(pg)
        assert moves_to_another_card(pg, second, lambda: cdp_swipe(ctx, pg, x - 60, y, 160)), "swipe on a scrolled card"
        settle(pg, 0.5)
        assert c.evaluate("e => e.scrollTop") == 0  # the next card starts at its top
    finally:
        ctx.close()


@needs_browser
@pytest.mark.parametrize("size", [(320, 640), (320, 568), (390, 844)])
def test_a_mouse_can_still_drag_the_card_either_way(browser, server, size):
    ctx, pg = open_page(browser, server, size, touch=False)
    try:
        start_sample(pg)
        to_top(pg)
        x, y = centre(pg)
        first = card_name(pg)
        pg.mouse.move(x - 50, y)
        pg.mouse.down()
        pg.mouse.move(x + 20, y, steps=6)
        pg.mouse.move(x + 120, y, steps=6)
        pg.mouse.up()
        second = wait_name_change(pg, first, timeout=6)
        settle(pg, 0.6)
        x, y = centre(pg)
        pg.mouse.move(x + 50, y)
        pg.mouse.down()
        pg.mouse.move(x - 20, y, steps=6)
        pg.mouse.move(x - 120, y, steps=6)
        pg.mouse.up()
        assert wait_name_change(pg, second, timeout=6)
        assert pg.errors == []
    finally:
        ctx.close()


# ------------------------------------------------------------------ the scroll cue: fade, pill, touches, focus
def cue_thresholds() -> tuple[int, int]:
    """(CUE_FADE, CUE_PILL): px of text cut off from which the fade, then the "scroll for more" pill, show."""
    return tuple(int(re.search(r"var CUE_%s = (\d+);" % name, SCRIPT).group(1)) for name in ("FADE", "PILL"))  # type: ignore[return-value]


CUE_JS = """() => {
  const stage = document.getElementById('stage'), c = document.getElementById('card'), pill = document.getElementById('cue');
  const after = getComputedStyle(stage, '::after'), pad = parseFloat(getComputedStyle(c).paddingBottom) || 0;
  const pr = pill.getBoundingClientRect(), sr = stage.getBoundingClientRect();
  return { hidden: c.scrollHeight - c.clientHeight, pad, cut: Math.round(c.scrollHeight - c.clientHeight - c.scrollTop - pad),
           fade: after.content !== 'none' && after.content !== 'normal', fadeBg: after.backgroundImage, fadeH: parseFloat(after.height),
           fadePE: after.pointerEvents, pill: getComputedStyle(pill).display, pillPE: getComputedStyle(pill).pointerEvents,
           pillInside: pr.width > 0 && pr.left >= sr.left && pr.right <= sr.right && pr.bottom <= sr.bottom && pr.top >= sr.top,
           classes: stage.className }; }"""
# Shrink the card's box so that exactly `target` px of its content are cut off (font independent), then let the card see a scroll.
CUT_OFF_JS = """(target) => {
  const stage = document.getElementById('stage'), c = document.getElementById('card');
  c.style.bottom = (stage.clientHeight - 100) + 'px';   // a tiny box: scrollHeight is now the content's own height
  const content = c.scrollHeight;
  c.style.bottom = (stage.clientHeight - (content - target + 2)) + 'px';   // + the two 1 px borders
  c.scrollTop = 0;
  c.dispatchEvent(new Event('scroll'));
  return { content, hidden: c.scrollHeight - c.clientHeight }; }"""


@needs_browser
def test_the_fade_and_the_pill_follow_how_much_text_is_cut_off(browser, server):
    """Deterministic: the card's box is shrunk by the test, so the result does not depend on the font or the viewport."""
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        fr = card_frame(pg)
        fade_t, pill_t = cue_thresholds()
        pad = fr.evaluate(CUE_JS)["pad"]
        assert pad > 0
        for cut in (-4, 0, fade_t, fade_t + 1, pill_t, pill_t + 1, 40):
            put = fr.evaluate(CUT_OFF_JS, pad + cut)
            assert abs(put["hidden"] - (pad + cut)) <= 1, (cut, put)  # the harness really cut off that much
            pg.wait_for_timeout(250)  # the card's own 80 ms timer re-measures after a fit; the result must be the same
            m = fr.evaluate(CUE_JS)
            assert abs(m["cut"] - cut) <= 1, (cut, m)
            assert m["fade"] is (cut > fade_t), (cut, m)
            assert (m["pill"] == "block") is (cut > pill_t), (cut, m)
            if m["fade"]:  # it is really painted: a gradient over the foot of the card, which never catches a touch
                assert "linear-gradient" in m["fadeBg"] and m["fadeH"] == 30 and m["fadePE"] == "none", m
            if m["pill"] == "block":
                assert m["pillPE"] == "none" and m["pillInside"], m
            assert ("cue-strong" in m["classes"]) is (cut > pill_t) and ("cue" in m["classes"].split()) is (cut > fade_t), m
    finally:
        ctx.close()


@needs_browser
@pytest.mark.parametrize("size", [(320, 568), (320, 640)])
def test_a_clipped_card_shows_the_fade_and_the_pill_and_loses_both_at_its_end(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        fade_t, pill_t = cue_thresholds()

        def measure():
            top = fr.evaluate(CUE_JS)
            fr.evaluate("(() => { const c = document.getElementById('card'); c.scrollTop = c.scrollHeight; })()")
            pg.wait_for_timeout(250)
            end = fr.evaluate(CUE_JS)
            fr.evaluate("document.getElementById('card').scrollTop = 0")
            pg.wait_for_timeout(100)
            return top, end

        seen = walk(pg, 7, measure)
        for name, (top, end) in seen:
            assert top["fade"] is (top["cut"] > fade_t) and (top["pill"] == "block") is (top["cut"] > pill_t), (name, top)
            if top["fade"]:
                assert "linear-gradient" in top["fadeBg"] and top["fadeH"] == 30 and top["fadePE"] == "none", (name, top)
            assert not end["fade"] and end["pill"] == "none" and end["cut"] <= fade_t, (name, end)  # nothing left to scroll to: no cue
        tops = dict((name, top) for name, (top, _) in seen)
        # The card with the "ask your doctor whether that suits you" sentence is cut off on both short phones, and says so with the pill.
        assert tops["Vitamin D3"]["cut"] > pill_t and tops["Vitamin D3"]["pill"] == "block" and tops["Vitamin D3"]["pillInside"], tops["Vitamin D3"]
    finally:
        ctx.close()


@needs_browser
def test_a_card_that_is_only_short_of_its_bottom_padding_gets_no_fade(browser, server):
    """360x640: the vitamin D card overflows its frame by 5 px, all of it padding. The fade over the last 30 px would wash out a
    line of text that is entirely visible."""
    ctx, pg = open_page(browser, server, (360, 640))
    try:
        start_sample(pg)
        fr = card_frame(pg)
        seen = walk(pg, 7, lambda: fr.evaluate(CUE_JS))
        short = [(name, m) for name, m in seen if 0 < m["hidden"] <= m["pad"] + cue_thresholds()[0]]
        assert short, seen  # a card that overflows by no more than its padding exists at this size
        assert all(not m["fade"] and m["pill"] == "none" for _, m in short), short
    finally:
        ctx.close()


@needs_browser
def test_a_swipe_that_starts_in_the_lower_edge_of_a_scrolling_card_still_moves_the_card(browser, server):
    """The fade (30 px) must not catch the touch: it is drawn over the card, so only pointer-events: none lets the swipe through."""
    ctx, pg = open_page(browser, server, (320, 568))
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))
        to_top(pg)
        fr = card_frame(pg)
        assert fr.evaluate(CUE_JS)["fade"]  # card 2 is cut off at this size: the fade is on
        stage = card(pg).locator("#stage").bounding_box()
        y = stage["y"] + stage["height"] - 6 - 15  # 15 px above the card's foot, inside the fade
        x = stage["x"] + stage["width"] / 2
        frame_box = pg.locator(CARD).bounding_box()
        hit = fr.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return e ? { inCard: !!e.closest('#card'), id: e.id } : null; }",
                          [x - frame_box["x"], y - frame_box["y"]])
        assert hit and hit["inCard"], hit  # the point under the fade belongs to the card, not to the overlay
        second = card_name(pg)
        assert moves_to_another_card(pg, second, lambda: cdp_swipe(ctx, pg, x - 60, y, 160)), "swipe from the fade"
        assert pg.errors == []
    finally:
        ctx.close()


@needs_browser
def test_a_tap_on_the_athlete_button_under_the_fade_opens_the_guide(browser, server):
    """At double text size the athlete-guide button reaches under the fade when the card is scrolled: the tap must still reach it."""
    ctx, pg = open_page(browser, server, (320, 640), init=TEXT_200)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        # The card (its frame zoomed too) is taller than its window: scroll it until the lower part of the button is under the fade.
        place = """() => { const c = document.getElementById('card'), s = document.getElementById('stage').getBoundingClientRect(),
          b = document.querySelector('.pl-guide').getBoundingClientRect(); c.scrollTop += b.bottom - (s.bottom - 6 - 14); }"""
        probe = """() => { const st = document.getElementById('stage'), s = st.getBoundingClientRect(), b = document.querySelector('.pl-guide').getBoundingClientRect();
          const top = Math.max(b.top, s.bottom - 6 - 30), bottom = Math.min(b.bottom, s.bottom - 6);
          if (!st.classList.contains('cue') || bottom - top < 6) { return null; }
          const x = b.left + b.width / 2, y = (top + bottom) / 2, hit = document.elementFromPoint(x, y);
          return { x, y, overlap: bottom - top, onButton: !!(hit && hit.closest('.pl-guide')) }; }"""
        overlapped, tapped = [], False
        for i in range(7):
            name = card_name(pg)
            to_top(pg)
            fr.evaluate(place)
            pg.wait_for_timeout(200)  # the card's scroll event refreshes the fade
            got = fr.evaluate(probe)
            if got:
                overlapped.append((name, got))
                assert got["onButton"], (name, got)  # what is under the finger is the button, not the overlay
                if not tapped:
                    frame_box = pg.locator(CARD).bounding_box()
                    pg.touchscreen.tap(frame_box["x"] + got["x"], frame_box["y"] + got["y"])
                    pg.locator(DIALOG).wait_for(timeout=10000)  # the Athlete RDA guide
                    pg.keyboard.press("Escape")
                    settle(pg)
                    assert card_name(pg) == name  # nothing was decided by the tap
                    tapped = True
            if i < 6:
                keep_and_wait(pg, name)
        assert overlapped and tapped, "no card had its athlete button under the fade: this test checks nothing"
    finally:
        ctx.close()


@needs_browser
def test_a_focused_athlete_button_is_never_under_the_fade_or_the_pill(browser, server):
    """Tab to the button at double text size: the browser scrolls it clear of the overlay (scroll-padding), it does not park it at
    the card's edge with its last line and its focus ring covered."""
    ctx, pg = open_page(browser, server, (320, 640), init=TEXT_200)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        probe = """() => { const g = document.querySelector('.pl-guide'), st = document.getElementById('stage'), s = st.getBoundingClientRect(), r = g.getBoundingClientRect();
          return { focused: document.activeElement === g, cue: st.classList.contains('cue'), bottom: r.bottom, fadeTop: s.bottom - 6 - 30,
                   height: r.height, room: s.bottom - 6 - 30 - s.top, scrolled: document.getElementById('card').scrollTop }; }"""

        def measure():
            fr.evaluate("document.getElementById('card').focus({ preventScroll: true })")
            pg.keyboard.press("Tab")
            pg.wait_for_timeout(200)
            return fr.evaluate(probe)

        seen = walk(pg, 7, measure)
        assert all(m["focused"] for _, m in seen), seen
        assert any(m["scrolled"] > 3 for _, m in seen), seen  # focusing really scrolled a card (the case this guards)
        # (A button taller than the window above the fade, the magnesium and selenium notes at 200 % on a 246 px window, cannot be placed
        # clear of it: the browser then starts at its first line and the rest is a scroll away, so only the ones that fit are checked.)
        assert all((not m["cue"]) or m["bottom"] <= m["fadeTop"] + 1 or m["height"] > m["room"] for _, m in seen), seen
        assert sum(1 for _, m in seen if m["height"] <= m["room"]) >= 3, seen  # and most of the cards' buttons do fit
    finally:
        ctx.close()


# ------------------------------------------------------------------ the swipe hint
@needs_browser
def test_a_resumed_scan_explains_the_swipe_on_the_card_it_opens_on(browser, server):
    """The hint is dropped after the first card of a tight scan, because the visitor has seen it. A resumed scan opens on a later
    card in a fresh page: nobody has seen it yet."""
    ctx, pg = open_page(browser, server, (320, 640))
    try:
        start_own_label(pg)
        for _ in range(2):
            keep_and_wait(pg, card_name(pg))
        settle(pg, 1.2)
        resume_at = card_name(pg)
        pg.reload(wait_until="networkidle")
        open_scan_sheet(pg)
        pg.get_by_role("dialog").get_by_role("button", name="Resume last scan").click(timeout=20000)
        card(pg).locator("#card .name").wait_for(timeout=20000)
        settle(pg)
        assert card_name(pg) == resume_at
        fr = card_frame(pg)
        state = fr.evaluate(CARD_TEXT_JS)
        assert "Card 3 of 4" == state["count"] and state["tight"], state  # a later card of a tight scan
        assert state["hint_shown"] is True and state["hint"] == HINT, state
        keep_and_wait(pg, resume_at)  # and once it has been shown it gets out of the way again
        state = fr.evaluate(CARD_TEXT_JS)
        assert state["count"] == "Card 4 of 4" and state["tight"] and state["hint_shown"] is False, state
        assert pg.errors == []
    finally:
        ctx.close()


@needs_browser
def test_a_mouse_and_keyboard_visitor_keeps_the_hint_and_the_arrow_keys_on_every_card(browser, server):
    """A laptop-height window starts tight (the room above the bar is under 610 px). The hint is the only place the arrow keys are
    named, and a mouse user has not been taught the swipe by touching a card: it stays."""
    ctx, pg = open_page(browser, server, (1366, 768), touch=False)
    try:
        start_sample(pg)
        seen = walk(pg, 4, lambda: card_frame(pg).evaluate(CARD_TEXT_JS))
        assert seen[0][1]["tight"], seen[0]  # the window is the short one that tightens (otherwise this checks the old path only)
        for name, m in seen:
            assert m["hint_shown"] is True and m["hint"] == HINT + " (or ← →)", (name, m["hint"], m["hint_shown"])
        assert pg.errors == []
    finally:
        ctx.close()

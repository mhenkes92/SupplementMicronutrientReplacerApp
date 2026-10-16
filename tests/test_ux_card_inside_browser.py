"""Browser checks (real Chromium, a touch phone at 2x) of the swipe card as ONE box: the pinned Back / Keep / Replace footer, the Ask AI chip
in the header and the Swap food chip in the "Eat instead" box are all inside the card, and nothing of the frame is below it but the 6 px
strip for the shadow; the page's fixed bottom bar is the only thing under the card.

Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers are reused. The offline pins of the structure
are tests/test_ux_card_inside.py; the frame maths and the scroll cue: tests/test_ux_row_clear_browser.py and
tests/test_ux_fixed_screen_browser.py.

What is measured here, on the sample label's 7 cards (the last one included) at 320x568, 320x640, 360x640, 375x667, 390x844, 393x852 and
412x915, at 100 %, 150 % and 200 % text: the footer is whole and inside the card, the Ask AI chip is in view without scrolling the card
(at every size and text size), the Swap food chip too at 100 % and 150 % everywhere and at 200 % from 390x844 up (on a smaller phone at
200 % the "Eat instead" box starts below the 240-250 px window the body has: the chip is one scroll away, 20-100 px), the card ends
4 px above the bar, and no element of the frame is below the card. At 200 % on 320x568 the frame is taller than the screen (MIN_WINDOW)
and the page scrolls: there the footer is checked after the page is scrolled to its end."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.request

import pytest

if os.getenv("SUPPSWIPE_BROWSER_TESTS", "") != "1":
    pytest.skip("browser tests are opt-in (SUPPSWIPE_BROWSER_TESTS=1)", allow_module_level=True)

from test_ux_app_bar_browser import BAR, MAIN, rect  # noqa: E402
from test_ux_fixed_screen_browser import no_page_scroll, start_text  # noqa: E402
from test_ux_browser import (  # noqa: E402,F401  (fixtures are used by name)
    CARD, DIALOG, ROOT, _FAKE_ROUTING, _free_port, browser, card, card_name, close_sheet, server, settle, start_sample, wait_name_change,
)
from test_ux_row_clear_browser import (  # noqa: E402
    ROW_GAP_MAX, TEXT_150, TEXT_200, card_frame, card_gap, cdp_swipe, centre, keep_and_wait, open_page, row_gap, to_top,
)

SIZES = [(320, 568), (320, 640), (360, 640), (375, 667), (390, 844), (393, 852), (412, 915)]
PERCENTS = [("100", None), ("150", TEXT_150), ("200", TEXT_200)]
GLOW_REACH = 10  # px: how far the glow of a chip reaches past it at the most (inset + spread + blur of .chip::before; pinned offline)
SPY_EVENTS = """() => { const orig = window.parent.postMessage.bind(window.parent); window.__events = [];
  window.parent.postMessage = (m, t) => { if (m && m.type === 'streamlit:setComponentValue') { window.__events.push(m.value); } return orig(m, t); }; }"""


@pytest.fixture(scope="module")
def ai_card_server(tmp_path_factory):
    """A server with the AI configured (so the card offers the Ask AI chip) whose model is the local fake: opening the chips' sheets and
    tapping around never reaches a real service. One for the whole module: nothing here asks a question."""
    import fake_blockbrain as fb

    fake = fb.FakeBlockbrain().start()
    routing = tmp_path_factory.mktemp("fake_routing_card")
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
        SUPPSWIPE_PREFETCH_MEALS="0",
    )
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        env.pop(name, None)
    env.pop("BLOCKBRAIN_MODEL", None)
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
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    fake.stop()


# ------------------------------------------------------------------ geometry
GEOMETRY_JS = """() => {
  const g = id => document.getElementById(id), r = e => { if (!e) return null; const b = e.getBoundingClientRect(); return {x: b.left, y: b.top, right: b.right, bottom: b.bottom, w: b.width, h: b.height}; };
  const body = g('cbody'), stage = g('stage'), card = g('card'), cr = card.getBoundingClientRect(), br = body.getBoundingClientRect();
  const lim = br.bottom - (stage.classList.contains('cue') ? 30 : 0);   // the fade covers the last 30 px of the window when the text is cut
  const shown = e => { if (!e) return null; const b = e.getBoundingClientRect(); return b.top >= br.top - 0.5 && b.bottom <= lim + 0.5; };
  const strays = [...document.querySelectorAll('body *')].filter(e => { const b = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return b.height > 1 && b.width > 1 && cs.visibility !== 'hidden' && cs.display !== 'none' && b.top >= cr.bottom + 0.5 && !e.closest('#card') && !e.closest('#live'); }).map(e => e.id || e.className || e.tagName);
  const inside = (e, box) => !e || (e.left >= box.left - 0.5 && e.right <= box.right + 0.5 && e.top >= box.top - 0.5 && e.bottom <= box.bottom + 0.5);
  const parts = ['btnBack', 'btnKeep', 'btnRepl', 'btnAsk', 'btnSwap'].filter(id => g(id)).map(id => [id, inside(g(id).getBoundingClientRect(), cr)]);
  // a chip never overlaps the text it sits beside, and its tap area (6 px above and below, 4 px beside; 7 px on a tight card) never reaches another control
  const hit = (a, b) => a.left < b.right - 0.5 && a.right > b.left + 0.5 && a.top < b.bottom - 0.5 && a.bottom > b.top + 0.5;
  const chips = [...document.querySelectorAll('#card .chip')], texts = ['.name', '.dose', '.badge', '.foodlbl', '.food', '.ficon', '.src', '.warn', '.bio'].map(s => [s, document.querySelector(s)]).filter(x => x[1]);
  const vpad = g('wrap').classList.contains('tight') ? 7 : 6;
  const others = [...document.querySelectorAll('#card button')];
  const overlaps = chips.flatMap(c => { const b = c.getBoundingClientRect(), t = {left: b.left - 4, right: b.right + 4, top: b.top - vpad, bottom: b.bottom + vpad};
    // what a finger can reach is the part of the tap area inside the body's window (the rest is scrolled out of view and clipped)
    const tap = {left: Math.max(t.left, br.left), right: Math.min(t.right, br.right), top: Math.max(t.top, br.top), bottom: Math.min(t.bottom, br.bottom)};
    const reach = tap.right > tap.left && tap.bottom > tap.top;
    return [...texts.filter(([s, e]) => hit(b, e.getBoundingClientRect())).map(([s]) => c.id + ' x ' + s),
            ...others.filter(o => reach && o !== c && hit(tap, o.getBoundingClientRect())).map(o => c.id + ' tap x ' + (o.id || o.className))]; });
  // where the chips sit: Ask AI beside the name ('inline'), on a row of its own under it ('chip-below-name') or beside the badge with the name under both
  // (large text, 'name-below-badge'); Swap food beside the "Eat instead" label or under it. The tap areas (the ::after) and the room the glow has to the body's edge.
  const R = e => e.getBoundingClientRect(), head = document.querySelector('.head'), nameBox = head.querySelector('div'), label = document.querySelector('.foodlbl');
  const headLayout = g('btnAsk') ? (R(g('btnAsk')).top >= R(nameBox).bottom - 2 ? 'chip-below-name' : (R(nameBox).top >= R(document.querySelector('.badge')).bottom - 2 ? 'name-below-badge' : 'inline')) : null;
  const foodLayout = g('btnSwap') ? (R(g('btnSwap')).top >= R(label).bottom - 2 ? 'swap-below-label' : 'inline') : null;
  const tapArea = e => { if (!e) return null; const a = getComputedStyle(e, '::after'), k = R(e); return [k.width - parseFloat(a.left) - parseFloat(a.right), k.height - parseFloat(a.top) - parseFloat(a.bottom)]; };
  const glowRoom = chips.length ? Math.min(...chips.map(c => br.right - R(c).right)) : null;
  return { card: r(card), body: r(body), footer: r(g('actions')), ask: r(g('btnAsk')), swap: r(g('btnSwap')), askShown: shown(g('btnAsk')), swapShown: shown(g('btnSwap')),
           strays, overlaps, allInside: parts.every(p => p[1]), parts, frame: innerHeight, cue: stage.classList.contains('cue'), tight: g('wrap').classList.contains('tight'),
           headLayout, foodLayout, askArea: tapArea(g('btnAsk')), swapArea: tapArea(g('btnSwap')), glowRoom,
           spacer: g('wrap').classList.contains('spacer') }; }"""


def swap_in_view(size, percent: str) -> bool:
    """Is the Swap food chip in the body's first window? 100 % and 150 %: everywhere; 200 %: from 390x844 up (see the module docstring)."""
    return percent != "200" or size[1] >= 844


@pytest.mark.parametrize("percent,init", PERCENTS, ids=[p for p, _ in PERCENTS])
@pytest.mark.parametrize("size", SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_the_footer_and_both_chips_are_inside_the_cards_box_whole_on_every_card_and_nothing_is_below_the_card(browser, ai_card_server, size, percent, init):
    ctx, pg = open_page(browser, ai_card_server, size, init=init)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        seen = []
        for i in range(7):
            name = card_name(pg)
            to_top(pg)
            fr.evaluate("document.getElementById('cbody').scrollTop = 0")
            m = fr.evaluate(GEOMETRY_JS)
            iframe = pg.locator(CARD).bounding_box()
            bar = rect(pg, BAR)
            seen.append((name, m, iframe, bar))
            card_box, foot, body = m["card"], m["footer"], m["body"]
            # the footer is whole, inside the card's box, under the body: it is the card's last child and it is pinned there
            assert foot["bottom"] <= card_box["bottom"] + 0.5 and foot["y"] >= body["bottom"] - 0.5 and foot["x"] >= card_box["x"] - 0.5, (name, m)
            assert foot["right"] <= card_box["right"] + 0.5 and m["allInside"], (name, m["parts"])  # Back, Keep, Replace and both chips: inside the card
            assert m["ask"] and m["askShown"] is True, (name, m["ask"], m["body"])  # Ask AI: in view without scrolling the card
            assert m["swap"] and (m["swapShown"] is True or not swap_in_view(size, percent)), (name, m["swap"], m["body"])  # Swap food: likewise
            assert m["ask"]["bottom"] <= m["swap"]["y"], (name, m)  # the header's chip is above the food box's
            assert m["strays"] == [], (name, m["strays"])  # nothing of the frame is below the card: no row of tools, no row of buttons
            assert m["overlaps"] == [], (name, m["overlaps"])  # the chips overlap no text and their tap areas reach no other control
            assert min(m["askArea"] + m["swapArea"]) >= 44, (name, m["askArea"], m["swapArea"])  # a finger-sized tap area, with the icon and the bigger chip too
            assert m["glowRoom"] >= GLOW_REACH, (name, m["glowRoom"])  # the glow has room to the body's edge: the card's overflow never clips it
            # the icon must not make the header chip wrap needlessly: beside the name on every phone at normal text (on a 320 px phone without the icon);
            # at large text it takes the badge's row (or a row of its own, right-aligned), and never overlaps anything (above)
            assert m["headLayout"] in ("inline", "chip-below-name", "name-below-badge") and m["foodLayout"] in ("inline", "swap-below-label"), (name, m)
            if percent == "100":  # 320 px too: the icon is dropped there (a 288 px card), so the chip never takes a row of its own
                assert m["headLayout"] == "inline" and m["foodLayout"] == "inline", (name, size, m["headLayout"], m["foodLayout"])
            if not m["spacer"]:
                assert 5 <= round(m["frame"] - card_box["bottom"]) <= 7, (name, m["frame"], card_box)  # only the strip for the card's shadow
                assert 2 <= round(bar["y"] - (iframe["y"] + card_box["bottom"])) <= 6, (name, bar, iframe, card_box)  # the card's edge, 4 px above the bar
                assert no_page_scroll(pg)
                assert 0 <= row_gap(pg) <= ROW_GAP_MAX, (name, row_gap(pg))
                assert foot["bottom"] + iframe["y"] <= bar["y"] and foot["y"] + iframe["y"] >= 0, (name, foot, iframe, bar)  # whole on the screen
            else:  # the frame is taller than the screen (200 % on 320x568): the page scrolls to the footer
                pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
                pg.wait_for_timeout(250)
                after = pg.locator(CARD).bounding_box()
                assert after["y"] + foot["bottom"] <= rect(pg, BAR)["y"] + 0.5 and after["y"] + foot["y"] >= 0, (name, after, foot)
            if i < 6:
                keep_and_wait(pg, name)
        assert len({round(f["y"]) for _, m, _i, _b in seen for f in [m["footer"]]}) == 1  # the footer never moves from card to card
        assert seen[-1][0] == "Selenium"  # and the last card was one of them
    finally:
        ctx.close()


def test_there_is_no_tools_row_and_no_more_button_in_the_frame_any_more(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        start_sample(pg)
        fr = card_frame(pg)
        assert fr.evaluate("document.getElementById('extras') === null && document.getElementById('btnMore') === null")
        buttons = fr.evaluate("[...document.querySelectorAll('button')].map(b => [b.id || b.className, b.textContent.trim()])")
        assert [b[0] for b in buttons] == ["btnAsk", "btnSwap", "pl pl-guide", "btnBack", "btnKeep", "btnRepl"], buttons  # the tab order, which is the visual order
        assert [b[1] for b in buttons][:2] == ["Ask AI", "Swap food"] and not any("More" in b[1] for b in buttons)
        assert fr.evaluate("[...document.querySelectorAll('#actions button')].map(b => b.id)") == ["btnBack", "btnKeep", "btnRepl"]
        assert fr.evaluate("[...document.getElementById('wrap').children].map(e => e.id)") == ["stage", "live"]
    finally:
        ctx.close()


def test_a_chip_is_absent_with_no_gap_when_the_card_does_not_offer_it(browser, server, ai_card_server):
    from test_ux_fixed_screen_browser import start_text
    from test_ux_browser import choose_diet

    ctx, pg = open_page(browser, server, (390, 844))  # no AI: only Swap food
    try:
        start_sample(pg)
        counts = card_frame(pg).evaluate("({head: document.querySelector('.head').children.length, food: document.querySelector('.foodhead').children.length})")
        assert counts == {"head": 2, "food": 2}, counts  # the head: the badge and the name; the food box's header: its label and the Swap chip
        assert card(pg).locator("#btnAsk").count() == 0
    finally:
        ctx.close()
    ctx, pg = open_page(browser, ai_card_server, (390, 844))  # AI on, a card with no second food (vegan EPA / DHA): neither chip
    try:
        choose_diet(pg, "Vegan")
        start_text(pg, "Omega-3 EPA 300 mg DHA 200 mg")
        counts = card_frame(pg).evaluate("({head: document.querySelector('.head').children.length, food: document.querySelector('.foodhead').children.length})")
        assert counts["food"] == 1 and counts["head"] == 3, counts  # no Swap chip; Ask AI is in the head (badge, name, chip)
        assert card(pg).locator("#btnSwap").count() == 0 and card(pg).locator("#btnAsk").count() == 1
    finally:
        ctx.close()


# ------------------------------------------------------------------ a press on a button is a tap, a press elsewhere on the card still drags
def centre_of(pg, selector: str) -> tuple[float, float]:
    b = card(pg).locator(selector).bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def card_moved(pg) -> str:
    return card_frame(pg).evaluate("document.getElementById('card').style.transform")


@pytest.mark.parametrize("selector", ["#btnBack", "#btnKeep", "#btnRepl", "#btnSwap", "#btnAsk"])
def test_a_mouse_press_on_a_button_never_starts_a_drag_but_a_click_on_it_still_works(browser, ai_card_server, selector):
    ctx, pg = open_page(browser, ai_card_server, (390, 844), touch=False)
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))  # card 2: Back is enabled
        name = card_name(pg)
        fr = card_frame(pg)
        fr.evaluate(SPY_EVENTS)
        box = card(pg).locator("#card").bounding_box()
        x, y = centre_of(pg, selector)
        inward = 1 if x < box["x"] + box["width"] / 2 else -1
        pg.mouse.move(x, y)
        pg.mouse.down()
        for step in range(1, 9):
            pg.mouse.move(x + inward * 22 * step, y, steps=2)
            assert card_moved(pg) == "", (selector, step)  # the card never leaves its place while the press that began on a button is dragged
        pg.mouse.up()
        pg.wait_for_timeout(600)
        assert card_name(pg) == name and fr.evaluate("window.__events") == [], (selector, fr.evaluate("window.__events"))  # nothing swiped, nothing decided
        assert pg.get_by_role("dialog").count() == 0
        # a click is what each button is for
        fr.evaluate("window.__events.length = 0")
        card(pg).locator(selector).click(timeout=5000)
        if selector in ("#btnSwap", "#btnAsk"):
            pg.get_by_role("dialog").wait_for(timeout=10000)
            assert [e["kind"] for e in fr.evaluate("window.__events")] == [selector[4:].lower()]
            assert card_name(pg) == name
        else:
            dirs = {"#btnBack": "back", "#btnKeep": "left", "#btnRepl": "right"}
            assert wait_name_change(pg, name)
            assert [e.get("dir") for e in fr.evaluate("window.__events")] == [dirs[selector]]
    finally:
        ctx.close()


@pytest.mark.parametrize("selector", ["#btnBack", "#btnKeep", "#btnRepl", "#btnSwap", "#btnAsk"])
def test_a_finger_that_starts_on_a_button_and_slides_away_swipes_nothing_and_a_tap_still_reaches_it(browser, ai_card_server, selector):
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))
        name = card_name(pg)
        fr = card_frame(pg)
        fr.evaluate(SPY_EVENTS)
        box = card(pg).locator("#card").bounding_box()
        x, y = centre_of(pg, selector)
        inward = 1 if x < box["x"] + box["width"] / 2 else -1
        cdp_swipe(ctx, pg, x, y, inward * 170, 0)  # 170 px: far beyond the swipe threshold
        pg.wait_for_timeout(600)
        assert card_name(pg) == name and card_moved(pg) == "" and fr.evaluate("window.__events") == [], (selector, fr.evaluate("window.__events"))
        # a finger tap on it works (the card holds no pointer capture that would take the click)
        pg.touchscreen.tap(x, y)
        if selector in ("#btnSwap", "#btnAsk"):
            pg.get_by_role("dialog").wait_for(timeout=10000)
        else:
            assert wait_name_change(pg, name)
    finally:
        ctx.close()


def test_a_drag_that_starts_on_the_body_or_on_the_footers_padding_still_swipes_and_the_footer_travels_with_the_card(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (390, 844), touch=False)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        # mid-drag the footer is part of the moving card: its place inside the card does not change, the card moves
        before = fr.evaluate("(() => { const c = document.getElementById('card').getBoundingClientRect(), f = document.getElementById('actions').getBoundingClientRect(); return {cx: c.left, fx: f.left, fy: f.top, cy: c.top}; })()")
        x, y = centre(pg)
        pg.mouse.move(x, y)
        pg.mouse.down()
        pg.mouse.move(x + 30, y, steps=4)
        pg.mouse.move(x + 70, y + 6, steps=4)  # short of the 80 px that decides
        mid = fr.evaluate("(() => { const c = document.getElementById('card').getBoundingClientRect(), f = document.getElementById('actions').getBoundingClientRect(); return {cx: c.left, fx: f.left, fy: f.top, cy: c.top}; })()")
        assert mid["cx"] - before["cx"] > 40, (before, mid)  # the card follows the pointer ...
        assert abs((mid["fx"] - mid["cx"]) - (before["fx"] - before["cx"])) <= 1, (before, mid)  # ... and the footer with it
        assert mid["fy"] - mid["cy"] > 0 and abs((mid["fy"] - mid["cy"]) - (before["fy"] - before["cy"])) < 12, (before, mid)  # (tilted a little: still at the card's foot)
        pg.mouse.up()
        pg.wait_for_timeout(500)
        assert card_name(pg) == "Vitamin C"  # short: it springs back
        # a drag that starts in the footer's padding (the 8 px between Keep and Replace) swipes the card
        keep, repl = card(pg).locator("#btnKeep").bounding_box(), card(pg).locator("#btnRepl").bounding_box()
        gx, gy = (keep["x"] + keep["width"] + repl["x"]) / 2, keep["y"] + keep["height"] / 2
        assert repl["x"] - (keep["x"] + keep["width"]) >= 6
        frame_box = pg.locator(CARD).bounding_box()
        hit = fr.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return e ? (e.id || e.tagName) : null; }", [gx - frame_box["x"], gy - frame_box["y"]])
        assert hit == "actions", hit  # really the footer itself, not a button
        pg.mouse.move(gx, gy)
        pg.mouse.down()
        pg.mouse.move(gx - 40, gy, steps=5)
        pg.mouse.move(gx - 150, gy, steps=8)
        pg.mouse.up()
        assert wait_name_change(pg, "Vitamin C", timeout=6)  # swiped to the left: kept
    finally:
        ctx.close()


def test_a_finger_that_starts_in_the_footers_padding_swipes_too(browser, ai_card_server):
    """A finger is forgiving (the browser hands a touch that lands a few px beside a button to the button), so the touch starts at the
    very top of the footer, under its hairline, where no button is within reach; the mouse test above starts in the gap between two."""
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        start_sample(pg)
        footer = card(pg).locator("#actions").bounding_box()
        keep, repl = card(pg).locator("#btnKeep").bounding_box(), card(pg).locator("#btnRepl").bounding_box()
        gx, gy = (keep["x"] + keep["width"] + repl["x"]) / 2, footer["y"] + 1.5
        name = card_name(pg)
        cdp_swipe(ctx, pg, gx, gy, -170)
        assert wait_name_change(pg, name, timeout=6)
    finally:
        ctx.close()


def test_the_click_that_follows_a_drag_never_triggers_a_button(browser, ai_card_server):
    """The card holds the pointer from the first press on its body: a drag that ends right over Replace is the card's pointer-up (and
    click), not the button's."""
    ctx, pg = open_page(browser, ai_card_server, (390, 844), touch=False)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        fr.evaluate(SPY_EVENTS)
        x, y = centre(pg)
        rx, ry = centre_of(pg, "#btnRepl")
        pg.mouse.move(x, y)
        pg.mouse.down()
        pg.mouse.move(x, y + 200, steps=6)  # a vertical drag: no swipe
        pg.mouse.move(rx, ry, steps=6)  # ... and over the Replace button
        pg.mouse.up()
        pg.wait_for_timeout(700)
        assert card_name(pg) == "Vitamin C" and fr.evaluate("window.__events") == [], fr.evaluate("window.__events")
        assert pg.get_by_role("dialog").count() == 0
    finally:
        ctx.close()


def test_a_touch_tap_on_a_chip_opens_its_sheet_and_closing_it_leaves_the_card_untouched(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (360, 640))
    try:
        start_sample(pg)
        name = card_name(pg)
        for selector, title in (("#btnSwap", "Swap the food"), ("#btnAsk", "Ask AI")):
            x, y = centre_of(pg, selector)
            pg.touchscreen.tap(x, y - 21)  # 21 px above the middle of a 34 px chip (30 px on a tight card): inside its tap area, above the chip itself
            pg.get_by_role("dialog").wait_for(timeout=10000)
            assert pg.get_by_role("dialog").locator("h2").inner_text().strip() == title
            pg.wait_for_timeout(700)
            pg.keyboard.press("Escape")
            pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
            settle(pg)
            assert card_name(pg) == name
    finally:
        ctx.close()


# ------------------------------------------------------------------ the keyboard
def test_the_tab_order_is_the_visual_order_and_a_chip_can_be_used_with_the_keyboard(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (390, 844), touch=False)
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))  # card 2: Back is enabled
        fr = card_frame(pg)
        fr.evaluate("document.getElementById('card').focus({ preventScroll: true })")
        order = []
        for _ in range(6):
            pg.keyboard.press("Tab")
            order.append(fr.evaluate("document.activeElement.id || document.activeElement.className"))
        assert order == ["btnAsk", "btnSwap", "pl pl-guide", "btnBack", "btnKeep", "btnRepl"], order
        pg.keyboard.press("Shift+Tab")
        assert fr.evaluate("document.activeElement.id") == "btnKeep"
        name = card_name(pg)
        pg.keyboard.press("Enter")  # the focused footer button decides
        assert wait_name_change(pg, name)
        settle(pg, 0.5)
        assert fr.evaluate("document.activeElement.id") == "btnKeep"  # and the focus stays on it for the next card
    finally:
        ctx.close()


def test_the_focus_comes_back_to_the_swap_chip_after_the_food_was_changed_in_its_sheet(browser, ai_card_server):
    """The sheet's Done changes the food: the card's body (and the chip in it) is rebuilt, the focus still returns to the chip."""
    from test_ux_browser import choose_option

    ctx, pg = open_page(browser, ai_card_server, (390, 844), touch=False)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        before = fr.evaluate("document.querySelector('.food').textContent.trim()")
        fr.evaluate("document.getElementById('btnSwap').focus()")
        pg.keyboard.press("Enter")
        pg.get_by_role("dialog").wait_for(timeout=10000)
        settle(pg, 0.7)
        pg.locator('[data-testid="stDialog"] [data-testid="stSelectbox"]').first.click()
        options = pg.locator('[role="option"]')
        options.first.wait_for(timeout=5000)
        options.nth(1).click()
        settle(pg)
        pg.get_by_role("button", name="Done").click()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        settle(pg, 1.0)
        assert fr.evaluate("document.querySelector('.food').textContent.trim()") != before  # the card was rebuilt with the other food
        assert fr.evaluate("document.activeElement.id") == "btnSwap"  # and the new chip has the focus
    finally:
        ctx.close()


# A focused chip is rebuilt with every card and the body goes back to its top: at 200 % text on a small phone the Swap food chip is
# below the body's first window (the "Eat instead" box starts under it), so a focus that does not scroll leaves it out of view.
SMALL_PHONES = [(320, 568), (360, 640), (375, 667)]
CHIP_VIEW_JS = """() => { const e = document.activeElement, body = document.getElementById('cbody'), b = body.getBoundingClientRect(), r = e.getBoundingClientRect(),
  fade = document.getElementById('stage').classList.contains('cue') ? 30 : 0, pill = document.getElementById('cue'),
  pillTop = getComputedStyle(pill).display === 'none' ? b.bottom : pill.getBoundingClientRect().top;
  return { id: e.id, above: r.top - b.top, below: Math.min(b.bottom - fade, pillTop) - r.bottom, scrolled: body.scrollTop, window: [b.top, b.bottom], chip: [r.top, r.bottom] }; }"""


def assert_chip_in_view(m, chip: str, where) -> None:
    """The focused chip is the one asked for, inside the body's window, above the fade and the "scroll for more" pill."""
    assert m["id"] == chip, (where, m)
    assert m["above"] >= -0.5 and m["below"] >= -0.5, (where, m)


def tab_to_the_swap_chip(pg, fr) -> None:
    fr.evaluate("document.getElementById('card').focus({ preventScroll: true })")
    for _ in range(2):  # the tab order: the card, Ask AI, Swap food
        pg.keyboard.press("Tab")
        pg.wait_for_timeout(150)
    assert fr.evaluate("document.activeElement.id") == "btnSwap"


@pytest.mark.parametrize("size", SMALL_PHONES, ids=[f"{w}x{h}" for w, h in SMALL_PHONES])
def test_a_focused_swap_chip_stays_in_view_after_each_arrow_key_decision_at_double_text_size(browser, ai_card_server, size):
    """Tab to Swap food (the body scrolls to show it), then decide with the arrow key: the next card is drawn with the chip focused again
    and the body at its top, and the chip is scrolled into the window clear of the fade, as the Tab had it."""
    ctx, pg = open_page(browser, ai_card_server, size, touch=False, init=TEXT_200)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        tab_to_the_swap_chip(pg, fr)
        first = fr.evaluate(CHIP_VIEW_JS)
        assert_chip_in_view(first, "btnSwap", "tab")
        assert first["scrolled"] > 0, first  # the case this guards: the chip was not in the first window, the Tab scrolled to it
        for step in range(4):
            name = card_name(pg)
            pg.keyboard.press("ArrowRight" if step % 2 == 0 else "ArrowLeft")
            wait_name_change(pg, name)
            settle(pg, 0.5)
            assert_chip_in_view(fr.evaluate(CHIP_VIEW_JS), "btnSwap", (step, card_name(pg)))
    finally:
        ctx.close()


@pytest.mark.parametrize("size", SMALL_PHONES, ids=[f"{w}x{h}" for w, h in SMALL_PHONES])
def test_a_focused_swap_chip_stays_in_view_after_its_sheet_changed_the_food_or_was_closed(browser, ai_card_server, size):
    """The sheet makes the page inert and the card is rebuilt behind it when Done changes the food; the chip the focus returns to is in
    view at double text size on a small phone (not scrolled out below the window, not under the fade)."""
    ctx, pg = open_page(browser, ai_card_server, size, touch=False, init=TEXT_200)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        before = fr.evaluate("document.querySelector('.food').textContent.trim()")
        tab_to_the_swap_chip(pg, fr)
        pg.keyboard.press("Enter")
        pg.get_by_role("dialog").wait_for(timeout=10000)
        settle(pg, 0.7)
        pg.keyboard.press("Escape")  # closed without a change: the page hands the focus back to the frame
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        settle(pg, 1.0)
        assert_chip_in_view(fr.evaluate(CHIP_VIEW_JS), "btnSwap", "escape")
        pg.keyboard.press("Enter")  # again, and this time another food is chosen
        pg.get_by_role("dialog").wait_for(timeout=10000)
        settle(pg, 0.7)
        pg.locator('[data-testid="stDialog"] [data-testid="stSelectbox"] input').first.focus()  # opened with the keyboard: a click on the select
        pg.keyboard.press("ArrowDown")  # of a 320 px sheet at 200 % text does not always open its list
        options = pg.locator('[role="option"]')
        options.first.wait_for(timeout=5000)
        options.nth(1).click()
        settle(pg)
        pg.get_by_role("button", name="Done").click()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        settle(pg, 1.0)
        assert fr.evaluate("document.querySelector('.food').textContent.trim()") != before  # the card was rebuilt with the other food
        assert_chip_in_view(fr.evaluate(CHIP_VIEW_JS), "btnSwap", "done")
    finally:
        ctx.close()


def test_the_keys_that_scroll_a_page_scroll_the_cards_body_when_the_card_has_the_focus(browser, server):
    ctx, pg = open_page(browser, server, (320, 568), touch=False)
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))  # card 2: the long vitamin D note, cut off at this size
        fr = card_frame(pg)
        fr.evaluate("document.getElementById('card').focus({ preventScroll: true })")
        top = lambda: fr.evaluate("document.getElementById('cbody').scrollTop")  # noqa: E731
        assert top() == 0
        pg.keyboard.press("ArrowDown")
        assert top() > 0
        pg.keyboard.press("End")
        end = top()
        assert end >= fr.evaluate("(() => { const b = document.getElementById('cbody'); return b.scrollHeight - b.clientHeight - 2; })()")
        pg.keyboard.press("PageUp")
        assert top() < end
        pg.keyboard.press("Home")
        assert top() == 0
        footer = fr.evaluate("document.getElementById('actions').getBoundingClientRect().top")
        pg.keyboard.press("End")
        assert fr.evaluate("document.getElementById('actions').getBoundingClientRect().top") == footer  # the footer stays where it is: it is pinned
        assert card_name(pg) == "Vitamin D3"
    finally:
        ctx.close()


@pytest.mark.parametrize("percent,init", PERCENTS[:2], ids=["100", "150"])
def test_no_focus_ring_is_clipped_by_the_cards_box(browser, ai_card_server, percent, init):
    """A ring is 3 px wide and 2 px away: the card's overflow must leave 5 px around each button. The chips' rings are checked against
    the body's window, the footer buttons' against the footer."""
    ctx, pg = open_page(browser, ai_card_server, (320, 640), touch=False, init=init)
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))
        fr = card_frame(pg)
        probe = """() => { const e = document.activeElement, b = e.getBoundingClientRect(), cs = getComputedStyle(e);
          const grow = parseFloat(cs.outlineWidth) + parseFloat(cs.outlineOffset);
          const box = e.closest('#actions') ? document.getElementById('actions').getBoundingClientRect() : document.getElementById('cbody').getBoundingClientRect();
          const card = document.getElementById('card').getBoundingClientRect();
          return { id: e.id, ring: grow, shown: cs.outlineStyle !== 'none', left: b.left - grow - box.left, right: box.right - (b.right + grow), top: b.top - grow - box.top,
                   bottom: box.bottom - (b.bottom + grow), cardLeft: b.left - grow - card.left, cardRight: card.right - (b.right + grow), cardBottom: card.bottom - (b.bottom + grow) }; }"""
        fr.evaluate("document.getElementById('card').focus({ preventScroll: true })")
        seen = {}
        for _ in range(6):
            pg.keyboard.press("Tab")
            pg.wait_for_timeout(120)
            m = fr.evaluate(probe)
            seen[m["id"] or "guide"] = m
        assert set(seen) >= {"btnAsk", "btnSwap", "btnBack", "btnKeep", "btnRepl"}, seen
        for ident, m in seen.items():
            assert m["shown"] and m["ring"] == 5, (ident, m)
            assert m["left"] >= 0 and m["right"] >= 0, (ident, m)  # the ring is inside the window (or the footer) sideways ...
            assert m["cardLeft"] >= 0 and m["cardRight"] >= 0 and m["cardBottom"] >= 0, (ident, m)  # ... and inside the card's box
            if ident in ("btnBack", "btnKeep", "btnRepl"):
                assert m["top"] >= 0 and m["bottom"] >= 0, (ident, m)  # the footer's top edge is a hairline: the ring clears it
    finally:
        ctx.close()


# ------------------------------------------------------------------ the fade and the pill sit above the footer
def test_the_fade_and_the_pill_end_where_the_footer_begins_and_the_footer_is_never_washed_out(browser, server):
    ctx, pg = open_page(browser, server, (320, 568))
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))  # card 2 is cut off at this size: the fade and the pill are on
        fr = card_frame(pg)
        m = fr.evaluate("""() => { const st = document.getElementById('stage'), main = document.querySelector('.cmain').getBoundingClientRect(),
          foot = document.getElementById('actions').getBoundingClientRect(), pill = document.getElementById('cue').getBoundingClientRect();
          const hits = ['btnBack', 'btnKeep', 'btnRepl'].map(id => { const b = document.getElementById(id).getBoundingClientRect();
            return document.elementFromPoint(b.left + b.width / 2, b.top + 3).id === id; });
          return { cue: st.classList.contains('cue'), strong: st.classList.contains('cue-strong'), mainBottom: main.bottom, footTop: foot.top, pillBottom: pill.bottom,
                   pillDisplay: getComputedStyle(document.getElementById('cue')).display, hits }; }""")
        assert m["cue"] and m["strong"] and m["pillDisplay"] == "block", m
        assert m["mainBottom"] <= m["footTop"] + 0.5 and m["pillBottom"] <= m["footTop"], m  # the overlays end above the footer
        assert all(m["hits"]), m  # nothing is over the footer's buttons
        # the footer's colours are the real ones: the filled Replace is the green of the page, not a faded one
        shot = pg.screenshot(clip={"x": 0, "y": 0, "width": 320, "height": 568})
        from io import BytesIO
        from PIL import Image

        image = Image.open(BytesIO(shot)).convert("RGB")
        repl = card(pg).locator("#btnRepl").bounding_box()
        scale = image.width / 320
        px = image.getpixel((int((repl["x"] + 8) * scale), int((repl["y"] + repl["height"] / 2) * scale)))
        assert px[1] > px[0] + 60 and px[1] > 90 and px[0] < 40, px  # #047857 = (4, 120, 87)
    finally:
        ctx.close()


# ------------------------------------------------------------------ a landscape phone
def test_a_landscape_phone_scrolls_the_page_to_the_footer_and_the_chips_are_in_the_card(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (844, 390))
    try:
        start_sample(pg)
        fr = card_frame(pg)
        m = fr.evaluate(GEOMETRY_JS)
        assert m["allInside"] and m["strays"] == [], m  # the chips and the footer are in the card; only the blank strip is below it
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(300)
        foot = card(pg).locator("#actions").bounding_box()
        assert foot["y"] + foot["height"] <= rect(pg, BAR)["y"] + 1, (foot, rect(pg, BAR))  # the footer can be scrolled clear of the bar
    finally:
        ctx.close()


# ------------------------------------------------------------------ the chips look like buttons and breathe
# Both chips carry a soft halo (the box-shadow of .chip::before) whose OPACITY breathes, 0.3 -> 0.95 -> 0.3 every 3 s, on every card that has
# them, for everyone, always. It holds still while the card is dragged and while the page is busy, is gone on a chip that is not live, and with
# "reduce motion" it is a still halo. The offline pins: tests/test_ux_card_inside.py.
GLOW_LOW, GLOW_HIGH, GLOW_STILL = 0.3, 0.95, 0.55   # the keyframes' opacities, and reduced motion's constant one
GLOW_SEEN_MAX = 9.5  # px: the farthest a halo pixel may be seen from the chip (the stylesheet's reach is 9.5 px; the faint end of it is not seen)
GLOW_JS = """() => [...document.querySelectorAll('#card .chip')].map(c => { const b = getComputedStyle(c, '::before');
  const anims = document.getAnimations().filter(a => a.animationName === 'chip-glow' && a.effect.target === c), t = anims.length ? anims[0].effect.getComputedTiming() : null;
  return { id: c.id, name: b.animationName, count: b.animationIterationCount, duration: b.animationDuration, opacity: +b.opacity, shadow: b.boxShadow,
           anims: anims.length, play: anims.length ? anims[0].playState : null, iterations: t ? String(t.iterations) : null, period: t ? t.duration : null,
           pseudo: anims.length ? anims[0].effect.pseudoElement : null }; })"""
OPACITY_JS = """(ms) => new Promise(done => { const chips = [...document.querySelectorAll('#card .chip')], out = [], t0 = performance.now();
  const glow = () => document.getAnimations().filter(a => a.animationName === 'chip-glow').map(a => a.currentTime);
  const first = glow();
  const step = () => { out.push(chips.map(c => +getComputedStyle(c, '::before').opacity)); if (performance.now() - t0 < ms) { requestAnimationFrame(step); } else { done({ frames: out, clock: [first, glow()] }); } };
  step(); })"""
SEEK_JS = "t => document.getAnimations().filter(a => a.animationName === 'chip-glow').forEach(a => { a.pause(); a.currentTime = t; })"
RESUME_JS = "() => document.getAnimations().filter(a => a.animationName === 'chip-glow').forEach(a => a.play())"
CHIP_JS = """() => [...document.querySelectorAll('#card .chip')].map(c => { const cs = getComputedStyle(c), ico = c.querySelector('svg.ico');
  const k = c.getBoundingClientRect(), i = ico && ico.getBoundingClientRect();
  return { id: c.id, text: c.textContent.trim(), aria: c.getAttribute('aria-label'), color: cs.color, bg: cs.backgroundImage, border: cs.borderTopColor, borderW: cs.borderTopWidth,
           shadow: cs.boxShadow, weight: cs.fontWeight,
           ico: ico ? { w: i.width, h: i.height, hidden: ico.getAttribute('aria-hidden'), inside: i.left >= k.left && i.right <= k.right && i.top >= k.top && i.bottom <= k.bottom, gap: i.left - k.left } : null }; })"""
RECTS_JS = """() => { const q = s => document.querySelector(s), r = e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map(v => Math.round(v * 100) / 100); };
  const chips = [...document.querySelectorAll('#card .chip')], body = q('#cbody');
  return { chips: chips.map(r), taps: chips.map(c => { const a = getComputedStyle(c, '::after'); return [a.top, a.right, a.bottom, a.left].join(' '); }), card: r(q('#card')), body: r(body), head: r(q('.head')),
           foodwrap: r(q('.foodwrap')), footer: r(q('#actions')), scroll: [body.scrollHeight, body.clientHeight, body.scrollWidth, body.clientWidth, body.scrollTop],
           page: [document.documentElement.scrollWidth, document.documentElement.scrollHeight] }; }"""
HALO_OFF = "() => { const s = document.createElement('style'); s.dataset.nohalo = '1'; s.textContent = '.chip::before { display: none !important; }'; document.head.appendChild(s); }"
HALO_ONLY = "id => { const s = document.createElement('style'); s.dataset.nohalo = '1'; s.textContent = `.chip:not(#${id})::before { display: none !important; }`; document.head.appendChild(s); }"   # every halo but one chip's
HALO_ON = "() => document.querySelectorAll('style[data-nohalo]').forEach(s => s.remove())"


def rgb_values(css: str) -> list[tuple[float, ...]]:
    return [tuple(float(v) for v in m.split(",")[:3]) for m in re.findall(r"rgba?\(([^)]*)\)", css)]


def shadow_alpha(shadow: str) -> float:
    """The alpha of a computed box-shadow ("rgba(99, 102, 241, 0.85) 0px 0px 7px 1.5px"); 1 for a plain rgb(), 0 for none."""
    alphas = [float(m.split(",")[3]) if m.count(",") == 3 else 1.0 for m in re.findall(r"rgba?\(([^)]*)\)", shadow)]
    return max(alphas) if alphas else 0.0


def assert_breathing(pg, fr, where, ids, live_ms: int = 1700) -> None:
    """The chips `ids` (and no others) carry the endless 3 s breathing of their ::before and it really runs, within its bounds, both chips
    together: the animations' clocks advance over `live_ms` and the opacity is read on every frame of it. A window of 1.6 s or more is more
    than half a breath, so the opacity then varies by a third at least, whatever the phase it starts in."""
    state = fr.evaluate(GLOW_JS)
    assert [c["id"] for c in state] == ids, (where, state)
    for c in state:
        assert (c["name"], c["count"], c["duration"]) == ("chip-glow", "infinite", "3s"), (where, c)  # computed style: never a finite count
        assert c["anims"] == 1 and c["play"] == "running" and c["pseudo"] == "::before", (where, c)  # and the browser runs it, on the ::before
        assert c["iterations"] == "Infinity" and c["period"] == 3000, (where, c)
        assert abs(shadow_alpha(c["shadow"]) - 0.85) < 0.01 and "7px 1.5px" in c["shadow"], (where, c["shadow"])  # the halo itself is drawn at full strength
    got = fr.evaluate(OPACITY_JS, live_ms)
    frames, (before, after) = got["frames"], got["clock"]
    assert len(frames) >= 5 and len(before) == len(after) == len(ids), (where, len(frames), before, after)
    assert all(b is not None and a - b >= live_ms * 0.6 for b, a in zip(before, after)), (where, before, after)  # the clocks run (they do not stand still)
    for chip in range(len(ids)):
        series = [f[chip] for f in frames]
        assert min(series) >= GLOW_LOW - 0.02 and max(series) <= GLOW_HIGH + 0.02, (where, ids[chip], min(series), max(series))  # within its bounds
        if live_ms >= 1600:
            assert max(series) - min(series) >= 0.25, (where, ids[chip], min(series), max(series))  # and it varies over time: it is not a still halo
    assert all(max(f) - min(f) < 0.02 for f in frames), (where, [f for f in frames if max(f) - min(f) >= 0.02][:3])  # both chips in phase on every frame


def test_both_chips_breathe_on_every_card_of_a_scan_after_back_and_after_a_sheet_was_used(browser, ai_card_server):
    """The first card, cards 2 and 3, the same card after Back, and every card after the visitor tapped a chip: the old pulse stopped for good
    at the first tap and cards 2+ never had it."""
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        start_sample(pg)
        fr = card_frame(pg)
        assert card_name(pg) == "Vitamin C"
        assert_breathing(pg, fr, "card 1", ["btnAsk", "btnSwap"])
        keep_and_wait(pg, card_name(pg))
        assert_breathing(pg, fr, "card 2", ["btnAsk", "btnSwap"], 900)
        keep_and_wait(pg, card_name(pg))
        assert card(pg).locator(".count").inner_text() == "Card 3 of 7"
        assert_breathing(pg, fr, "card 3", ["btnAsk", "btnSwap"])
        card(pg).locator("#btnBack").click(timeout=5000)
        assert wait_name_change(pg, card_name(pg)) == "Vitamin D3"
        settle(pg, 0.3)
        assert_breathing(pg, fr, "card 2 after Back", ["btnAsk", "btnSwap"])
        # a visitor who used a chip: its sheet opens and closes, and the card still glows (nothing is remembered, nothing is switched off)
        card(pg).locator("#btnSwap").click(timeout=5000)
        pg.get_by_role("dialog").wait_for(timeout=10000)
        pg.wait_for_timeout(700)
        pg.keyboard.press("Escape")
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        settle(pg, 0.8)
        assert_breathing(pg, fr, "after a tap on Swap food", ["btnAsk", "btnSwap"], 900)
        assert fr.evaluate("Object.keys(window.localStorage).filter(k => k.toLowerCase().includes('suppswipe')).length") == 0  # no flag was written
        keep_and_wait(pg, card_name(pg))
        assert_breathing(pg, fr, "the next card", ["btnAsk", "btnSwap"], 900)
        # and a new visit of the same browser, a new scan: the first card glows again
        pg.reload(wait_until="networkidle")
        pg.locator('[class~="st-key-appbar_scan"] button').wait_for(timeout=20000)
        start_sample(pg)
        fr = card_frame(pg)
        assert card_name(pg) == "Vitamin C"
        assert_breathing(pg, fr, "a new scan", ["btnAsk", "btnSwap"], 900)
        assert pg.errors == [], pg.errors
    finally:
        ctx.close()


def test_a_card_opened_from_the_results_glows_too(browser, ai_card_server):
    from test_ux_browser import finish_all_cards, change_choice

    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        start_sample(pg)
        finish_all_cards(pg)
        change_choice(pg, "keep", "Vitamin B12")
        card(pg).locator("#card .name").wait_for(timeout=20000)
        settle(pg, 0.8)
        fr = card_frame(pg)
        assert "Editing from your results" in card(pg).locator("#card").inner_text()  # edit mode: not the first card of a scan
        assert_breathing(pg, fr, "edit mode", ["btnAsk", "btnSwap"])
    finally:
        ctx.close()


def test_a_card_with_one_chip_glows_that_one_and_a_card_without_chips_runs_nothing(browser, server, ai_card_server):
    """No AI: only Swap food, and it breathes alone. Vegan EPA / DHA with the AI on: no second food, so only Ask AI, alone. Neither: no chip
    to glow, and nothing of the glow runs."""
    from test_ux_browser import choose_diet

    ctx, pg = open_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        assert_breathing(pg, card_frame(pg), "Swap food alone", ["btnSwap"], 900)
    finally:
        ctx.close()
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        choose_diet(pg, "Vegan")
        start_text(pg, "Omega-3 EPA 300 mg DHA 200 mg")
        assert_breathing(pg, card_frame(pg), "Ask AI alone", ["btnAsk"], 900)
    finally:
        ctx.close()
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        choose_diet(pg, "Vegan")
        start_text(pg, "Omega-3 EPA 300 mg DHA 200 mg")
        fr = card_frame(pg)
        assert fr.evaluate("document.querySelectorAll('#card .chip').length") == 0
        pg.wait_for_timeout(800)
        assert fr.evaluate("document.getAnimations().filter(a => a.animationName === 'chip-glow').length") == 0
    finally:
        ctx.close()


@pytest.mark.parametrize("size,percent,init", [((320, 568), "100", None), ((390, 844), "100", None), ((360, 640), "150", TEXT_150), ((320, 640), "200", TEXT_200)],
                         ids=["320x568", "390x844", "360x640-150", "320x640-200"])
def test_the_glow_moves_nothing_while_it_breathes(browser, ai_card_server, size, percent, init):
    """Held at 0 / 750 / 1500 (the peak) / 2250 ms of a breath, and again in the second one, and live at three moments: no chip rect, tap area,
    body, header, food box, footer, scroll height / width or page size differs from the same card with the halo removed (display: none)."""
    ctx, pg = open_page(browser, ai_card_server, size, init=init)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        fr.evaluate(HALO_OFF)
        pg.wait_for_timeout(120)
        baseline = fr.evaluate(RECTS_JS)
        assert baseline["scroll"][4] == 0 and len(baseline["chips"]) == 2
        fr.evaluate(HALO_ON)
        samples = {}
        for at in (0, 750, 1500, 2250, 3000 + 400):
            fr.evaluate(SEEK_JS, at)
            pg.wait_for_timeout(60)
            samples[f"held {at}"] = fr.evaluate(RECTS_JS)
        fr.evaluate(RESUME_JS)
        for i in range(3):
            pg.wait_for_timeout(430)
            samples[f"live {i}"] = fr.evaluate(RECTS_JS)
        for at, got in samples.items():
            assert got == baseline, (at, {k: (got[k], baseline[k]) for k in got if got[k] != baseline[k]})
    finally:
        ctx.close()


@pytest.mark.parametrize("touch", [False, True], ids=["mouse", "finger"])
def test_the_glow_holds_still_while_the_card_is_held_and_goes_on_when_it_is_let_go(browser, ai_card_server, touch):
    ctx, pg = open_page(browser, ai_card_server, (390, 844), touch=touch)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        name = card_name(pg)
        assert [c["play"] for c in fr.evaluate(GLOW_JS)] == ["running", "running"]
        box = card(pg).locator("#card").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.62  # the empty part of the body: no button, so the press is the card's
        cdp = ctx.new_cdp_session(pg) if touch else None

        def hold_and_move() -> None:
            if touch:
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
                for i in range(1, 5):
                    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + 8 * i, "y": y + i}]})
                    pg.wait_for_timeout(16)
            else:
                pg.mouse.move(x, y)
                pg.mouse.down()
                pg.mouse.move(x + 32, y + 4, steps=4)  # short of the 80 px that decides

        def let_go() -> None:
            if touch:
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            else:
                pg.mouse.up()

        hold_and_move()
        wrap = lambda: fr.evaluate("document.getElementById('wrap').className.split(' ')")  # noqa: E731
        assert "dragging" in wrap()
        pg.wait_for_timeout(450)  # the halo's fade to the faint one takes 0.25 s
        held = []
        for _ in range(4):
            held.append(fr.evaluate(GLOW_JS))
            pg.wait_for_timeout(140)
        for state in held:
            assert [c["play"] for c in state] == ["paused", "paused"], state  # stopped where it was ...
            assert all(shadow_alpha(c["shadow"]) <= 0.36 for c in state), [c["shadow"] for c in state]  # ... as a faint halo, never a wider one
            assert all("7px 1.5px" in c["shadow"] for c in state)
        for i in range(2):
            assert len({round(state[i]["opacity"], 3) for state in held}) == 1, [state[i]["opacity"] for state in held]  # frozen: the opacity does not move
        assert fr.evaluate("document.getElementById('card').style.transform") != ""  # (and the card is really being dragged)
        fr.evaluate("window.dispatchEvent(new Event('resize'))")  # a repaint of the wrapper's classes does not drop the hold
        assert "dragging" in wrap() and [c["play"] for c in fr.evaluate(GLOW_JS)] == ["paused", "paused"]
        let_go()
        pg.wait_for_timeout(500)
        assert card_name(pg) == name and "dragging" not in wrap()  # it sprang back: nothing was decided
        again = fr.evaluate(GLOW_JS)
        assert [c["play"] for c in again] == ["running", "running"] and all(abs(shadow_alpha(c["shadow"]) - 0.85) < 0.01 for c in again), again
        assert_breathing(pg, fr, "after the drag", ["btnAsk", "btnSwap"], 900)
    finally:
        ctx.close()


def assert_resting(fr, where, wait_ms: int = 0, pg=None) -> None:
    """Both chips' glow is paused where it was, as a faint halo, and the opacity does not move (three reads, 150 ms apart)."""
    if wait_ms:
        pg.wait_for_timeout(wait_ms)
    held = []
    for _ in range(3):
        held.append(fr.evaluate(GLOW_JS))
        if pg is not None:
            pg.wait_for_timeout(150)
    for state in held:
        assert [c["play"] for c in state] == ["paused", "paused"] and all(shadow_alpha(c["shadow"]) <= 0.36 for c in state), (where, state)
    assert len({state[0]["opacity"] for state in held}) == 1 and len({state[1]["opacity"] for state in held}) == 1, (where, held)


def test_the_glow_holds_still_while_the_page_is_busy_or_a_sheet_is_open_and_goes_on_after(browser, ai_card_server):
    """The page is analysing a new label (an .analyze-loading-wrap in it): the old card stays on screen, dimmed, and nothing on it is live.
    And while a sheet (Swap the food here) is open over the card it is out of reach, so the glow rests too: a glow that went on behind the
    sheet made the select box in it close as it opened (5 of 12 tries at 320x568 and 200 % text, in this headless Chromium)."""
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        start_sample(pg)
        fr = card_frame(pg)
        pg.evaluate("document.body.insertAdjacentHTML('beforeend', '<div class=\"analyze-loading-wrap\" id=\"fake-busy\"></div>')")
        fr.wait_for_function("document.getElementById('wrap').classList.contains('busy')", timeout=3000)  # on the frame's 250 ms beat
        assert_resting(fr, "busy", 400, pg)
        pg.evaluate("document.getElementById('fake-busy').remove()")
        fr.wait_for_function("!document.getElementById('wrap').classList.contains('busy')", timeout=3000)
        pg.wait_for_timeout(400)
        assert_breathing(pg, fr, "after the page was busy", ["btnAsk", "btnSwap"], 900)
        # a sheet over the card: the glow rests while it is open and goes on when it is closed
        card(pg).locator("#btnSwap").click(timeout=5000)
        pg.get_by_role("dialog").wait_for(timeout=10000)
        fr.wait_for_function("document.getElementById('wrap').classList.contains('busy')", timeout=3000)
        assert_resting(fr, "a sheet is open", 500, pg)
        pg.keyboard.press("Escape")
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        fr.wait_for_function("!document.getElementById('wrap').classList.contains('busy')", timeout=3000)
        pg.wait_for_timeout(400)
        assert_breathing(pg, fr, "after the sheet was closed", ["btnAsk", "btnSwap"], 900)
    finally:
        ctx.close()


def test_a_chip_that_is_not_live_carries_no_glow_and_the_other_one_goes_on(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        start_sample(pg)
        fr = card_frame(pg)
        fr.evaluate("document.getElementById('btnAsk').setAttribute('aria-disabled', 'true')")
        state = {c["id"]: c for c in fr.evaluate(GLOW_JS)}
        assert state["btnAsk"]["name"] == "none" and state["btnAsk"]["anims"] == 0 and state["btnAsk"]["opacity"] == 0, state["btnAsk"]
        assert state["btnSwap"]["name"] == "chip-glow" and state["btnSwap"]["play"] == "running", state["btnSwap"]
        fr.evaluate("document.getElementById('btnAsk').removeAttribute('aria-disabled')")
        back = {c["id"]: c for c in fr.evaluate(GLOW_JS)}
        assert back["btnAsk"]["name"] == "chip-glow" and back["btnAsk"]["play"] == "running", back["btnAsk"]  # (it starts its own breath again)
        # the real thing: a decision (the arrow key) dims both chips as a whole; in that very moment neither has a glow, and the next card's chips have
        gone = fr.evaluate("""() => { document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true}));
          return [...document.querySelectorAll('#card .chip')].map(c => [c.getAttribute('aria-disabled'), getComputedStyle(c, '::before').animationName, +getComputedStyle(c, '::before').opacity]); }""")
        assert gone == [["true", "none", 0], ["true", "none", 0]], gone
        assert wait_name_change(pg, "Vitamin C") == "Vitamin D3"
        settle(pg, 0.3)
        assert_breathing(pg, fr, "the next card", ["btnAsk", "btnSwap"], 900)
    finally:
        ctx.close()


def test_reduced_motion_shows_a_still_soft_halo_and_nothing_moves(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (390, 844), reduced_motion="reduce")
    try:
        start_sample(pg)
        fr = card_frame(pg)
        assert fr.evaluate("window.matchMedia('(prefers-reduced-motion: reduce)').matches") is True
        state = fr.evaluate(GLOW_JS)
        assert [c["id"] for c in state] == ["btnAsk", "btnSwap"]
        for c in state:
            assert c["name"] == "none" and c["anims"] == 0, c  # no animation at all
            assert abs(c["opacity"] - GLOW_STILL) < 0.01, c  # a constant, soft halo ...
            assert shadow_alpha(c["shadow"]) >= 0.5 and "7px 1.5px" in c["shadow"], c["shadow"]  # ... really drawn, the cue these visitors keep
        got = fr.evaluate(OPACITY_JS, 700)
        assert all(abs(v - GLOW_STILL) < 0.01 for f in got["frames"] for v in f) and got["clock"] == [[], []], got["frames"][:3]  # and it never moves
        assert fr.evaluate("document.getAnimations().filter(a => a.animationName === 'chip-glow').length") == 0
        chips = fr.evaluate(CHIP_JS)
        assert all("linear-gradient" in c["bg"] and c["shadow"] != "none" for c in chips)  # the raised, tinted pill is as before
        # a held card (the faint halo) is still: nothing to pause, nothing animates
        box = card(pg).locator("#card").bounding_box()
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.62)
        pg.mouse.down()
        pg.mouse.move(box["x"] + box["width"] / 2 + 30, box["y"] + box["height"] * 0.62, steps=3)
        assert fr.evaluate("document.getAnimations().filter(a => a.animationName === 'chip-glow').length") == 0
        pg.mouse.up()
    finally:
        ctx.close()


@pytest.mark.parametrize("size,init", [((390, 844), None), ((320, 568), None), ((320, 640), TEXT_200)], ids=["390x844", "320x568", "320x640-200"])
def test_the_halo_is_drawn_around_the_chip_only_and_is_never_clipped_by_the_cards_body(browser, ai_card_server, size, init):
    """Pixels, one chip at a time, at the peak of the breath against the same card with every halo removed: nothing differs on the chip's
    face (its text and tint are untouched, so the contrast measured from the colours is the contrast on screen), the halo is plainly there on
    every side (its last pixel that differs by more than 2/255 is 7.5-8.5 px from the chip at the peak, 10.5-12 px for a halo of
    9 px blur and 3 px spread) and reaches no farther than 9.5 px, and it ends inside the body's box (the Ask AI chip is the nearest to the edge: 12-14 px from it; at 200 % text on a 320 px phone
    a chip is scrolled into the window first)."""
    from io import BytesIO

    from PIL import Image, ImageChops, ImageDraw

    ctx, pg = open_page(browser, ai_card_server, size, init=init)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        for number in (1, 2, 3):
            for ident in ("btnAsk", "btnSwap"):
                frame_box = pg.locator(CARD).bounding_box()
                fr.evaluate("id => { document.getElementById(id).scrollIntoView({block: 'center'}); }", ident)  # (only the body scrolls)
                pg.wait_for_timeout(100)
                geo = fr.evaluate("""id => { const body = document.getElementById('cbody').getBoundingClientRect(), b = document.getElementById(id).getBoundingClientRect();
                  return { body: [body.left, body.top, body.right, body.bottom], chip: [b.left, b.top, b.right, b.bottom] }; }""", ident)
                fr.evaluate(HALO_ONLY, ident)
                fr.evaluate(SEEK_JS, 1500)  # the peak of the breath
                pg.wait_for_timeout(120)
                lit = Image.open(BytesIO(pg.screenshot())).convert("RGB")
                fr.evaluate(HALO_ON)
                fr.evaluate(HALO_OFF)
                pg.wait_for_timeout(120)
                plain = Image.open(BytesIO(pg.screenshot())).convert("RGB")
                fr.evaluate(HALO_ON)
                fr.evaluate(RESUME_JS)
                fr.evaluate("document.getElementById('cbody').scrollTop = 0")
                dx, dy = frame_box["x"], frame_box["y"]
                left, top, right, bottom = geo["chip"][0] + dx, geo["chip"][1] + dy, geo["chip"][2] + dx, geo["chip"][3] + dy
                bl, bt, br_, bb = geo["body"][0] + dx, geo["body"][1] + dy, geo["body"][2] + dx, geo["body"][3] + dy
                margin = 14
                box = tuple(int(v * 2) for v in (left - margin, top - margin, right + margin, bottom + margin))
                diff = ImageChops.difference(lit.crop(box), plain.crop(box)).convert("L").point(lambda v: 255 if v > 2 else 0)
                pill = Image.new("L", diff.size, 0)  # the chip's face: its pill shape, 3 px in from the border box (the corners of its rectangle are halo)
                ImageDraw.Draw(pill).rounded_rectangle([(margin + 3) * 2, (margin + 3) * 2, (margin + right - left - 3) * 2, (margin + bottom - top - 3) * 2], radius=(bottom - top - 6), fill=255)
                assert ImageChops.multiply(diff, pill).getbbox() is None, (number, ident, "the halo is drawn over the chip itself")
                hit = diff.getbbox()
                assert hit, (number, ident, "no halo at the peak")
                found = (left - margin + hit[0] / 2, top - margin + hit[1] / 2, left - margin + hit[2] / 2, top - margin + hit[3] / 2)
                reach = (left - found[0], top - found[1], found[2] - right, found[3] - bottom)
                assert all(4 <= r <= GLOW_SEEN_MAX for r in reach), (number, ident, reach)  # there on every side, and no farther than its reach (the crop's margin is 4 px more)
                assert found[0] >= bl + 1 and found[2] <= br_ - 1 and found[1] >= bt + 1 and found[3] <= bb - 1, (number, ident, found, (bl, bt, br_, bb))  # inside the body
            if number < 3:
                keep_and_wait(pg, card_name(pg))
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(390, 844), (320, 568)], ids=["390x844", "320x568"])
def test_the_chips_are_tinted_bordered_buttons_with_an_icon_and_enough_contrast_on_every_card_colour(browser, ai_card_server, size):
    """From the computed colours of the 7 cards of the sample (seven different card tints): a fill that is not white, a border of an
    accent colour (1.5 px as declared; Chromium floors a fractional border to 1 px, WebKit and Firefox draw 1.33-1.5), the text >= 4.5:1 on every stop of the fill, the border >= 3:1 against the card behind it (the Swap chip: the 82 % white box),
    a raised shadow, a bold label, an icon of the chip's own size inside it, and the two chips of two different families."""
    from test_ux_card_inside import _contrast, _mix

    ctx, pg = open_page(browser, ai_card_server, size)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        families = {}
        for i in range(7):
            name = card_name(pg)
            chips = fr.evaluate(CHIP_JS)
            ground = rgb_values(fr.evaluate("getComputedStyle(document.getElementById('card')).backgroundImage"))
            assert len(ground) == 3, ground  # white, then the card's two tints
            for chip in chips:
                stops = rgb_values(chip["bg"])
                assert len(stops) == 2 and all(st != (255.0, 255.0, 255.0) for st in stops) and stops[0] != stops[1], (name, chip["id"], chip["bg"])  # tinted, not white
                text, line = rgb_values(chip["color"])[0], rgb_values(chip["border"])[0]
                assert min(_contrast(text, st) for st in stops) >= 4.5, (name, chip["id"], chip["color"], chip["bg"])
                behind = [_mix(g, (255, 255, 255), 0.82) for g in ground] if chip["id"] == "btnSwap" else ground
                assert min(_contrast(line, g) for g in behind) >= 3.0, (name, chip["id"], chip["border"], behind)
                assert float(chip["borderW"][:-2]) >= 1.0 and chip["weight"] in ("800", "900") and chip["shadow"] != "none", chip  # (declared 1.5 px; Chromium draws 1 px)
                assert chip["ico"] and chip["ico"]["hidden"] == "true", chip  # decorative
                if size[0] <= 320:  # a 288 px card: no icon (the chip stays as narrow as it was)
                    assert chip["ico"]["w"] == 0, chip
                else:
                    assert chip["ico"]["inside"] and 10 <= chip["ico"]["w"] <= 20 and chip["ico"]["w"] == chip["ico"]["h"], chip
                assert chip["text"] in ("Ask AI", "Swap food") and chip["text"] in chip["aria"]
                families[chip["id"]] = (chip["color"], chip["border"], chip["bg"])
            if i < 6:
                keep_and_wait(pg, name)
        assert len({families["btnAsk"][1], families["btnSwap"][1]}) == 2 and families["btnAsk"][2] != families["btnSwap"][2]  # two families
    finally:
        ctx.close()

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
RING_SPREAD = 10  # px: how far the attention ring reaches past a chip at the most (the last keyframe of .chip-ring)
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
  // (large text, 'name-below-badge'); Swap food beside the "Eat instead" label or under it. The tap areas (the ::after) and the room the attention ring has to the body's edge.
  const R = e => e.getBoundingClientRect(), head = document.querySelector('.head'), nameBox = head.querySelector('div'), label = document.querySelector('.foodlbl');
  const headLayout = g('btnAsk') ? (R(g('btnAsk')).top >= R(nameBox).bottom - 2 ? 'chip-below-name' : (R(nameBox).top >= R(document.querySelector('.badge')).bottom - 2 ? 'name-below-badge' : 'inline')) : null;
  const foodLayout = g('btnSwap') ? (R(g('btnSwap')).top >= R(label).bottom - 2 ? 'swap-below-label' : 'inline') : null;
  const tapArea = e => { if (!e) return null; const a = getComputedStyle(e, '::after'), k = R(e); return [k.width - parseFloat(a.left) - parseFloat(a.right), k.height - parseFloat(a.top) - parseFloat(a.bottom)]; };
  const ringRoom = chips.length ? Math.min(...chips.map(c => br.right - R(c).right)) : null;
  return { card: r(card), body: r(body), footer: r(g('actions')), ask: r(g('btnAsk')), swap: r(g('btnSwap')), askShown: shown(g('btnAsk')), swapShown: shown(g('btnSwap')),
           strays, overlaps, allInside: parts.every(p => p[1]), parts, frame: innerHeight, cue: stage.classList.contains('cue'), tight: g('wrap').classList.contains('tight'),
           headLayout, foodLayout, askArea: tapArea(g('btnAsk')), swapArea: tapArea(g('btnSwap')), ringRoom,
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
            assert m["ringRoom"] >= RING_SPREAD, (name, m["ringRoom"])  # the attention ring has room to the body's edge: the card's overflow never clips it
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


# ------------------------------------------------------------------ the chips look like buttons and point at themselves once
# The ring is a box-shadow on the chip's ::before for 3 slow cycles (1.4 s each), starting 0.7 s after the first card of a scan is drawn, for a
# visitor who has never tapped a chip (localStorage "suppswipe_tools_used"). The offline pins: tests/test_ux_card_inside.py.
TOOLS_KEY = "suppswipe_tools_used"
PULSE_JS = "() => ({ lit: document.querySelectorAll('.chip.pulse').length, anims: document.getAnimations().filter(a => a.animationName === 'chip-ring').length, chips: document.querySelectorAll('#card .chip').length })"
FLAG_JS = f"() => {{ try {{ return window.localStorage.getItem('{TOOLS_KEY}'); }} catch (e) {{ return 'blocked'; }} }}"
CHIP_JS = """() => [...document.querySelectorAll('#card .chip')].map(c => { const cs = getComputedStyle(c), before = getComputedStyle(c, '::before'), ico = c.querySelector('svg.ico');
  const k = c.getBoundingClientRect(), i = ico && ico.getBoundingClientRect();
  return { id: c.id, text: c.textContent.trim(), aria: c.getAttribute('aria-label'), color: cs.color, bg: cs.backgroundImage, border: cs.borderTopColor, borderW: cs.borderTopWidth,
           shadow: cs.boxShadow, weight: cs.fontWeight, ring: [before.animationName, before.animationIterationCount, before.animationDuration],
           ico: ico ? { w: i.width, h: i.height, hidden: ico.getAttribute('aria-hidden'), inside: i.left >= k.left && i.right <= k.right && i.top >= k.top && i.bottom <= k.bottom, gap: i.left - k.left } : null }; })"""
RECTS_JS = """() => { const q = s => document.querySelector(s), r = e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map(v => Math.round(v * 100) / 100); };
  const chips = [...document.querySelectorAll('#card .chip')], body = q('#cbody');
  return { chips: chips.map(r), taps: chips.map(c => { const a = getComputedStyle(c, '::after'); return [a.top, a.right, a.bottom, a.left].join(' '); }), card: r(q('#card')), body: r(body), head: r(q('.head')),
           foodwrap: r(q('.foodwrap')), footer: r(q('#actions')), scroll: [body.scrollHeight, body.clientHeight, body.scrollWidth, body.clientWidth, body.scrollTop],
           page: [document.documentElement.scrollWidth, document.documentElement.scrollHeight] }; }"""


def start_sample_fast(pg, replacing=None):
    """Like start_sample, but returns the moment the first card is drawn (no settling), so the 0.7 s before the ring starts is still ahead.
    `replacing`: the frame of the scan on screen; the sample then replaces it with a new frame, and this waits for that one."""
    from test_ux_browser import open_scan_sheet

    open_scan_sheet(pg)
    pg.get_by_role("button", name="Try with a sample label").click()
    card(pg).locator("#card .name").wait_for(timeout=60000)
    end = time.time() + 30
    while replacing is not None and time.time() < end:
        fr = card_frame(pg)
        if fr is not None and fr is not replacing and fr.locator("#card .name").count() == 1:
            return fr
        pg.wait_for_timeout(50)  # (not time.sleep: the sync API only learns of the new frame while it is inside a call)
    return card_frame(pg)


def wait_for_pulse(fr) -> None:
    fr.wait_for_function("document.querySelectorAll('.chip.pulse').length === 2", timeout=6000)


def assert_quiet(fr, where, wait_ms: int = 0, pg=None) -> None:
    """No chip pulses and no animation runs in the card (after waiting `wait_ms`: longer than the 0.7 s before a ring would start)."""
    if wait_ms:
        pg.wait_for_timeout(wait_ms)
    state = fr.evaluate(PULSE_JS)
    assert state["lit"] == 0 and state["anims"] == 0, (where, state)


def rgb_values(css: str) -> list[tuple[float, ...]]:
    return [tuple(float(v) for v in m.split(",")[:3]) for m in re.findall(r"rgba?\(([^)]*)\)", css)]


def test_a_fresh_visit_pulses_both_chips_of_the_first_card_for_three_cycles_and_then_stops(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        fr = start_sample_fast(pg)
        drawn = time.time()
        wait_for_pulse(fr)
        assert 0.2 <= time.time() - drawn <= 4.0, time.time() - drawn  # not at once (about 0.7 s after the card is drawn): the card is read first
        chips = fr.evaluate(CHIP_JS)
        assert [c["id"] for c in chips] == ["btnAsk", "btnSwap"]
        for c in chips:
            assert c["ring"][0] == "chip-ring" and c["ring"][1] == "3" and c["ring"][2] == "1.4s", c  # three cycles, never "infinite"
        # what the browser runs: both rings are one finite animation of 3 iterations of 1.4 s; follow the iteration it is in until it is over
        timing = fr.evaluate("() => document.getAnimations().filter(a => a.animationName === 'chip-ring').map(a => { const t = a.effect.getComputedTiming(); return [t.iterations, t.duration]; })")
        assert timing == [[3, 1400], [3, 1400]], timing
        seen, end = set(), time.time() + 8
        while time.time() < end:
            now = fr.evaluate("() => ({ lit: document.querySelectorAll('.chip.pulse').length, it: document.getAnimations().filter(a => a.animationName === 'chip-ring').map(a => a.effect.getComputedTiming().currentIteration) })")
            seen.update(i for i in now["it"] if i is not None)
            if now["lit"] == 0:
                break
            pg.wait_for_timeout(100)
        assert fr.evaluate(PULSE_JS)["lit"] == 0, "it never loops: the class is gone after the third ring"
        assert seen <= {0, 1, 2} and 2 in seen, seen  # never a fourth cycle, and the third was seen
        assert_quiet(fr, "after the ring", 600, pg)
        assert fr.evaluate(FLAG_JS) is None  # watching is not using: only a tap on a chip sets the flag
    finally:
        ctx.close()


@pytest.mark.parametrize("selector", ["#btnAsk", "#btnSwap"])
def test_tapping_a_chip_sets_the_flag_and_a_new_scan_of_that_visitor_never_pulses(browser, ai_card_server, selector):
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        fr = start_sample_fast(pg)
        wait_for_pulse(fr)
        card(pg).locator(selector).click(timeout=5000)
        pg.get_by_role("dialog").wait_for(timeout=10000)
        assert fr.evaluate(FLAG_JS) == "1"
        assert fr.evaluate("document.getElementById('btnAsk').classList.contains('pulse') || document.getElementById('btnSwap').classList.contains('pulse')") is False
        pg.wait_for_timeout(700)
        pg.keyboard.press("Escape")
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        pg.reload(wait_until="networkidle")  # a new visit of the same browser, a new scan: the first card again
        pg.locator('[class~="st-key-appbar_scan"] button').wait_for(timeout=20000)
        fr = start_sample_fast(pg)
        assert card_name(pg) == "Vitamin C"
        assert_quiet(fr, "a new scan after a chip was used", 2000, pg)
        assert fr.evaluate(FLAG_JS) == "1"
    finally:
        ctx.close()


def test_without_a_tap_a_new_scan_pulses_again_but_cards_after_the_first_and_a_return_to_it_never_do(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (390, 844))
    try:
        fr = start_sample_fast(pg)
        wait_for_pulse(fr)
        box = card(pg).locator("#card").bounding_box()
        pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.62)  # a press on the empty body of the card: not a chip, not a decision
        assert fr.evaluate(PULSE_JS)["lit"] == 0
        # a new scan in this page (the sample replaces the cards on screen): its first card is a new first card, and this visitor has used no chip yet
        fr = start_sample_fast(pg, replacing=fr)
        assert card_name(pg) == "Vitamin C"
        wait_for_pulse(fr)
        name = card_name(pg)
        card(pg).locator("#btnKeep").click(timeout=5000)  # the press stops the ring for good
        assert fr.evaluate(PULSE_JS)["lit"] == 0
        assert wait_name_change(pg, name)
        assert_quiet(fr, "card 2", 1600, pg)  # longer than the 0.7 s a ring would wait: cards after the first never get one
        card(pg).locator("#btnBack").click(timeout=5000)
        assert wait_name_change(pg, card_name(pg)) == "Vitamin C"
        assert_quiet(fr, "back on card 1: it had its turn", 1600, pg)  # the same card of the same scan is not given a second turn
        for number in (2, 3, 4):
            card(pg).locator("#btnKeep").click(timeout=5000)
            wait_name_change(pg, card_name(pg))
            assert_quiet(fr, f"card {number}", 1300, pg)
    finally:
        ctx.close()


def test_a_card_opened_from_the_results_and_a_card_without_chips_never_pulse(browser, server):
    """Edit mode (a card opened from the results) is not a first card; and a server with no AI and a card with a second food has one chip only
    (the ring is on that one); the vegan EPA / DHA card has none and nothing runs."""
    from test_ux_browser import choose_diet

    ctx, pg = open_page(browser, server, (390, 844))  # no AI: only Swap food
    try:
        fr = start_sample_fast(pg)
        fr.wait_for_function("document.querySelectorAll('.chip.pulse').length === 1", timeout=6000)  # the one chip the card offers
        assert fr.evaluate("document.querySelector('.chip.pulse').id") == "btnSwap"
    finally:
        ctx.close()
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        choose_diet(pg, "Vegan")
        start_text(pg, "Omega-3 EPA 300 mg DHA 200 mg")  # no second food: no chip at all
        fr = card_frame(pg)
        assert fr.evaluate(PULSE_JS)["chips"] == 0
        assert_quiet(fr, "a card without chips", 1500, pg)
    finally:
        ctx.close()


def test_reduced_motion_shows_no_ring_at_all_and_the_static_look_stays(browser, ai_card_server):
    ctx, pg = open_page(browser, ai_card_server, (390, 844), reduced_motion="reduce")
    try:
        fr = start_sample_fast(pg)
        assert fr.evaluate("window.matchMedia('(prefers-reduced-motion: reduce)').matches") is True
        pg.wait_for_timeout(1800)  # long past the 0.7 s a ring would wait
        chips = fr.evaluate(CHIP_JS)
        assert [c["id"] for c in chips] == ["btnAsk", "btnSwap"]
        assert all(c["ring"][0] == "none" for c in chips), chips  # no animation at all
        assert fr.evaluate(PULSE_JS)["anims"] == 0
        assert all("linear-gradient" in c["bg"] and c["shadow"] != "none" for c in chips)  # the raised, tinted pill is the cue
    finally:
        ctx.close()


@pytest.mark.parametrize("how", ["press on the card", "key", "press on the page", "drag"])
def test_the_first_press_or_key_anywhere_stops_the_ring_at_once(browser, ai_card_server, how):
    ctx, pg = open_page(browser, ai_card_server, (390, 844), touch=False)
    try:
        fr = start_sample_fast(pg)
        wait_for_pulse(fr)
        assert fr.evaluate(PULSE_JS)["anims"] == 2
        box = card(pg).locator("#card").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.62  # the empty part of the card's body, no button there
        if how == "press on the card":
            pg.mouse.move(x, y)
            pg.mouse.down()
        elif how == "key":
            fr.evaluate("document.getElementById('card').focus({ preventScroll: true })")
            pg.keyboard.press("Shift")
        elif how == "press on the page":
            pg.locator(BAR + " button").first.click()  # the bar of the page around the card: a press outside the frame
        else:
            pg.mouse.move(x, y)
            pg.mouse.down()
            pg.mouse.move(x + 30, y + 4, steps=4)
        state = fr.evaluate(PULSE_JS)
        assert state["lit"] == 0 and state["anims"] == 0, (how, state)  # gone before the next frame, not at the end of the cycle
        if how in ("press on the card", "drag"):
            pg.mouse.up()
        assert fr.evaluate(FLAG_JS) is None  # a press is not a tap on a chip: the visitor may be shown the ring on the next scan
    finally:
        ctx.close()


def test_a_blocked_local_storage_changes_nothing_but_the_memory_of_a_tap(browser, ai_card_server):
    """A private window or blocked site data makes localStorage throw: the ring still runs, a tap on a chip still opens its sheet, and nothing
    throws."""
    ctx, pg = open_page(browser, ai_card_server, (390, 844), init="Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new DOMException('denied', 'SecurityError'); } });")
    try:
        fr = start_sample_fast(pg)
        wait_for_pulse(fr)  # the read of the flag threw and was caught: the ring runs
        assert fr.evaluate(FLAG_JS) == "blocked"
        card(pg).locator("#btnSwap").click(timeout=5000)  # the write throws and is caught: the sheet opens as usual
        pg.get_by_role("dialog").wait_for(timeout=10000)
        assert pg.errors == [], pg.errors
        assert fr.evaluate(PULSE_JS)["lit"] == 0
    finally:
        ctx.close()


@pytest.mark.parametrize("size,percent,init", [((320, 568), "100", None), ((390, 844), "100", None), ((360, 640), "150", TEXT_150), ((320, 640), "200", TEXT_200)],
                         ids=["320x568", "390x844", "360x640-150", "320x640-200"])
def test_the_ring_moves_nothing_while_it_runs(browser, ai_card_server, size, percent, init):
    """Sampled with the animation held at 0 / 400 / 800 ms of a cycle (the ring is visible in each) and again in the second cycle: no chip
    rect, tap area, body, header, food box, footer, scroll height / width or page size differs from the same card without the ring."""
    ctx, pg = open_page(browser, ai_card_server, size, init=init)
    try:
        fr = start_sample_fast(pg)
        wait_for_pulse(fr)
        samples = {}
        for at in (0, 400, 800, 1400 + 300):
            fr.evaluate("t => document.getAnimations().filter(a => a.animationName === 'chip-ring').forEach(a => { a.pause(); a.currentTime = t; })", at)
            pg.wait_for_timeout(60)
            samples[at] = fr.evaluate(RECTS_JS)
            shadows = fr.evaluate("() => [...document.querySelectorAll('#card .chip')].map(c => getComputedStyle(c, '::before').boxShadow)")
            if at in (0, 400):
                assert all(sh != "none" and rgb_alpha(sh) > 0.05 for sh in shadows), (at, shadows)  # the ring really is drawn in this sample
        fr.evaluate("() => document.querySelectorAll('.chip.pulse').forEach(c => c.classList.remove('pulse'))")
        pg.wait_for_timeout(60)
        baseline = fr.evaluate(RECTS_JS)
        assert baseline["scroll"][4] == 0
        for at, got in samples.items():
            assert got == baseline, (at, {k: (got[k], baseline[k]) for k in got if got[k] != baseline[k]})
    finally:
        ctx.close()


def rgb_alpha(shadow: str) -> float:
    """The largest alpha of the colours of a computed box-shadow ("rgba(99, 102, 241, 0.3) 0px 0px 0px 3px"); 1 for a plain rgb()."""
    alphas = [float(m.split(",")[3]) if m.count(",") == 3 else 1.0 for m in re.findall(r"rgba?\(([^)]*)\)", shadow)]
    return max(alphas) if alphas else 0.0


@pytest.mark.parametrize("size", [(390, 844), (320, 568)], ids=["390x844", "320x568"])
def test_the_chips_are_tinted_bordered_buttons_with_an_icon_and_enough_contrast_on_every_card_colour(browser, ai_card_server, size):
    """From the computed colours of the 7 cards of the sample (seven different card tints): a fill that is not white, a border of an
    accent colour (1.5 px as declared; Chromium floors a fractional border to 1 px, WebKit and Firefox draw 1.33-1.5), the text >= 4.5:1 on every stop of the fill, the border >= 3:1 against the card behind it (the Swap chip: the 82 % white box),
    a raised shadow, a bold label, an icon of the chip's own size inside it, and the two chips of two different families."""
    from test_ux_card_inside import _contrast, _mix

    ctx, pg = open_page(browser, ai_card_server, size)
    try:
        fr = start_sample_fast(pg)
        fr.evaluate("() => document.querySelectorAll('.chip.pulse').forEach(c => c.classList.remove('pulse'))")
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

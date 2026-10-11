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
  return { card: r(card), body: r(body), footer: r(g('actions')), ask: r(g('btnAsk')), swap: r(g('btnSwap')), askShown: shown(g('btnAsk')), swapShown: shown(g('btnSwap')),
           strays, overlaps, allInside: parts.every(p => p[1]), parts, frame: innerHeight, cue: stage.classList.contains('cue'), tight: g('wrap').classList.contains('tight'),
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

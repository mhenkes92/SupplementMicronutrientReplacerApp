"""Browser checks of the fixed screens (real Chromium, a touch phone at 2x): the welcome card and the cards screen do not scroll, the card
carries its own tools (Swap food / Ask AI / More) that open sheets, and a card that does not fit keeps its own scroll.

Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers are reused. The offline half (the events,
the sheets' content, the props) is tests/test_ux_fixed_screen.py.

Measured when this was built (sample label, 7 cards; "upper limit" label = 5 cards, three with a red warning box; 100 % text): with
the tools row and the Keep / Replace row inside the frame, the frame fills the room between the brand and the bar. Every card of the
sample label fits its frame without the card's own scroll from 360x640 up (320x640: the vitamin D3 card scrolls; 320x568: D3, magnesium,
selenium); every card of the upper-limit label fits from 412x680, 390x720 and 360x760 up (360x640: the selenium card scrolls by 64 px; 320x640:
vitamin D3, zinc and selenium scroll; 320x568: all five). The page itself never scrolls, on any of them.

Larger text, with the card's own frame zoomed too (tests/test_ux_row_clear_browser.py: an init script reaches the page only, so the first
large-text figures measured the page chrome at 150 % / 200 % and the card at 100 %, and were wrong): the page still does not scroll at
150 %, but the cards no longer fit. Sample label at 150 %: 390x844 five of the seven cards scroll inside themselves (15-237 px), 412x915
only vitamin D3 (108 px), 360x640 and every smaller phone all seven; upper-limit label at 150 %: all five cards on every phone up to
390x844 (96-335 px) and four of five at 412x915. At 200 % every card of both labels scrolls inside itself on every phone up to 412x915
(sample 226-1396 px, upper-limit up to 1862 px). The card is never left a slit, though: its window (the stage between the tools and the
Keep / Replace row) is at least MIN_WINDOW = 240 px. Where the room would leave less (200 % on 320x568, and on 320x640 / 360x640 with the
sample caption's second line; before: windows of 152 / 201 px, the tools in two rows and Keep / Replace broken inside the words) the frame
grows to give it that and the page scrolls to the buttons (117 px at 320x568, 45 px at 320x640 and 360x640, the row then clears the bar
by 16 px): test_at_a_large_text_size_the_card_keeps_a_readable_window_and_the_page_scrolls_only_when_it_must. With the Vegan chip and
the pregnancy toggle the vitamin D3 and B12 notes are longer: they scroll up to 390x720 and fit from 390x844 (the review's measurement;
here at 360x640 with the finger: test_with_the_vegan_filter_and_pregnancy_mode_the_longer_notes_scroll_and_the_pregnancy_sentence_is_reachable)."""
from __future__ import annotations

import os

import pytest

if os.getenv("SUPPSWIPE_BROWSER_TESTS", "") != "1":
    pytest.skip("browser tests are opt-in (SUPPSWIPE_BROWSER_TESTS=1)", allow_module_level=True)

from test_ux_app_bar_browser import BAR, BAR_BUTTON, MAIN, rect  # noqa: E402
from test_ux_browser import (  # noqa: E402,F401  (fixtures are used by name)
    CARD, DIALOG, ai_server, browser, card, card_name, close_sheet, finish_all_cards, open_scan_sheet, open_swap_sheet, page,
    results_heading, server, settle, start_sample, wait_name_change,
)
from test_ux_row_clear_browser import (  # noqa: E402,F401
    TEXT_150, TEXT_200, WARN_LABEL, card_frame, cdp_swipe, centre, frame_height, keep_and_wait, open_page, row_gap,
)

SAMPLE_SIZES = [(320, 568), (320, 640), (360, 640), (375, 667), (390, 664), (390, 844), (393, 700), (412, 780), (412, 915)]
FIT_JS = """() => { const c = document.getElementById('card'); const pad = parseFloat(getComputedStyle(c).paddingBottom) || 0;
  const s = document.getElementById('stage');
  return { cut: Math.round(c.scrollHeight - c.clientHeight - pad), fade: s.classList.contains('cue'), pill: s.classList.contains('cue-strong'),
           tight: document.getElementById('wrap').classList.contains('tight') }; }"""


def scroll_state(pg) -> dict:
    return pg.evaluate(
        "() => { const m = document.querySelector('[data-testid=stMain]'); return {main: m.scrollHeight - m.clientHeight,"
        " doc: document.scrollingElement.scrollHeight - innerHeight, width: document.scrollingElement.scrollWidth - innerWidth}; }"
    )


def no_page_scroll(pg) -> bool:
    state = scroll_state(pg)
    return state["main"] <= 0 and state["doc"] <= 0 and state["width"] <= 0


def start_text(pg, text: str) -> int:
    """A visitor's own pasted label (the Scan sheet's Analyze, the Paste tab); returns the number of cards."""
    pg.locator('[class~="st-key-appbar_scan"] button').click()
    pg.get_by_role("dialog").wait_for(timeout=10000)
    settle(pg, 0.5)
    pg.get_by_role("button", name="Analyze my supplement").click()
    dialog = pg.get_by_role("dialog")
    dialog.locator("button", has_text="Paste").click()
    settle(pg)
    dialog.locator("textarea").fill(text)
    dialog.get_by_role("button", name="Analyze").click()
    card(pg).locator("#card .name").wait_for(timeout=60000)
    settle(pg)
    return int(card(pg).locator(".count").inner_text().split(" of ")[1])


def walk_fit(pg, count: int) -> list[dict]:
    """Per card: the page scroll, the row's gap to the bar, the frame and whether the card fits its frame without its own scroll."""
    seen = []
    for i in range(count):
        name = card_name(pg)
        fit = card_frame(pg).evaluate(FIT_JS)
        fit.update(name=name, page=no_page_scroll(pg), gap=row_gap(pg), frame=frame_height(pg))
        seen.append(fit)
        if i < count - 1:
            keep_and_wait(pg, name)
    return seen


# ------------------------------------------------------------------ the welcome card
@pytest.mark.parametrize("size", [(320, 568), (320, 640), (360, 640), (375, 667), (390, 844), (412, 915)])
def test_the_welcome_card_needs_no_scrolling_and_its_big_scan_button_is_inside_it(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        assert no_page_scroll(pg), scroll_state(pg)  # not even an empty strip to scroll
        hero, button, bar = rect(pg, '[class~="st-key-hero_card"]'), rect(pg, '[class~="st-key-hero_card"] button'), rect(pg, BAR)
        assert pg.locator('[class~="st-key-hero_card"] button').inner_text().strip() == "Scan a supplement"  # no icon whose ligature name is read aloud
        assert pg.get_by_role("button", name="Scan a supplement", exact=True).count() == 1  # the accessible name is the label alone
        assert button["h"] >= 44 and button["w"] >= 0.7 * hero["w"], button  # big: the card's one action
        assert hero["y"] <= button["y"] and button["bottom"] <= hero["bottom"], (hero, button)  # inside the card
        assert button["bottom"] <= bar["y"] - 6, (button, bar)
        caption = pg.evaluate("() => [...document.querySelectorAll('[data-testid=stCaptionContainer]')].pop().getBoundingClientRect().bottom")
        assert caption <= bar["y"] - 6, (caption, bar)  # the disclaimer stays, clear above the bar
        assert pg.locator(".hero-hint, .brand-foot").count() == 0
        pg.locator('[class~="st-key-hero_card"] button').tap()
        dialog = pg.get_by_role("dialog")
        dialog.wait_for(timeout=10000)
        assert dialog.locator("h2").inner_text().strip() == "Scan a supplement"
        names = " | ".join(b.inner_text().strip() for b in dialog.locator("button").all())
        assert "Analyze my supplement" in names and "Try with a sample label" in names, names  # the bar's Scan sheet, the same options
    finally:
        ctx.close()


def test_the_welcome_card_needs_no_scrolling_at_the_in_between_phone_sizes_too(browser, server):
    """The six phones above are not the only ones: the long text under the title is hidden by thresholds, and a width between two
    phones wraps it onto another line (measured every 3-4 px: 341-358 px wide scrolled up to 24 px between 604 and 676 px high, 320-344
    wide up to 18 px between 701 and 716, 360-420 wide 2 px at 604). Resizing one page across the grid; with a filter chip above as well."""
    from test_ux_browser import choose_diet

    ctx, pg = open_page(browser, server, (390, 844))
    try:
        for chip in (False, True):
            if chip:
                choose_diet(pg, "Vegan")
                assert pg.locator(".diet-note").count() == 1
            bad = []
            for width in (320, 330, 338, 342, 344, 346, 350, 354, 358, 360, 366, 375, 390, 412, 420):
                for height in (568, 580, 596, 604, 610, 620, 640, 660, 668, 676, 690, 704, 712, 716, 730, 760, 844, 915):
                    pg.set_viewport_size({"width": width, "height": height})
                    pg.wait_for_timeout(40)
                    if not no_page_scroll(pg):
                        bad.append((width, height, scroll_state(pg)["main"]))
            assert not bad, (chip, bad)
    finally:
        ctx.close()


# ------------------------------------------------------------------ the cards screen: one fixed screen
@pytest.mark.parametrize("size", SAMPLE_SIZES)
def test_no_card_of_the_sample_label_scrolls_the_page_and_nothing_is_drawn_under_the_card(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        seen = walk_fit(pg, 7)
        assert all(s["page"] for s in seen), seen  # the page itself never scrolls (sideways neither)
        assert all(0 <= s["gap"] <= 12 for s in seen), seen  # the row is just above the bar, and never under it
        assert len({s["frame"] for s in seen}) == 1, seen  # one frame for the whole scan: the buttons stay where the thumb is
        frame, iframe = seen[0]["frame"], rect(pg, CARD)
        room = rect(pg, BAR)["y"] - iframe["y"]
        assert room - 12 <= frame <= room + 6, (frame, room)  # and it fills the room between the brand (and its caption) and the bar
        assert pg.locator('[data-testid="stSelectbox"], [data-testid="stPopover"], .brand-foot, [class*="st-key-swipe_report"]').count() == 0
        below = pg.evaluate("([top]) => [...document.querySelectorAll('[data-testid=stMain] *')].filter(e => { const r = e.getBoundingClientRect();"
                            " return r.height > 4 && r.width > 4 && r.top >= top && !e.closest('iframe') && getComputedStyle(e).visibility !== 'hidden'"
                            " && !e.closest('[class~=\"st-key-appbar\"]'); }).length", [iframe["bottom"] + 2])
        assert below == 0, below  # nothing but the bar is under the card's frame
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(320, 568), (320, 640), (360, 640), (375, 667), (390, 844), (412, 915)])
def test_no_card_of_an_upper_limit_label_scrolls_the_page_either(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        count = start_text(pg, WARN_LABEL)
        seen = walk_fit(pg, count)
        assert count == 5 and all(s["page"] for s in seen) and all(0 <= s["gap"] <= 12 for s in seen), seen
        assert len({s["frame"] for s in seen}) == 1, seen
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(360, 640), (375, 667), (390, 844), (412, 915)])
def test_every_card_of_the_sample_label_fits_without_the_cards_own_scroll_from_360x640_up(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        start_sample(pg)
        seen = walk_fit(pg, 7)
        assert all(s["cut"] <= 0 and not s["fade"] and not s["pill"] for s in seen), [(s["name"], s["cut"]) for s in seen]
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(360, 760), (393, 700), (390, 720), (412, 680), (390, 844), (412, 915)])
def test_every_card_of_an_upper_limit_label_fits_without_the_cards_own_scroll_from_these_phones_up(browser, server, size):
    ctx, pg = open_page(browser, server, size)
    try:
        count = start_text(pg, WARN_LABEL)
        seen = walk_fit(pg, count)
        assert all(s["cut"] <= 0 and not s["fade"] and not s["pill"] for s in seen), [(s["name"], s["cut"]) for s in seen]
    finally:
        ctx.close()


LAST_VISIBLE_JS = """() => { const c = document.getElementById('card'); const k = c.getBoundingClientRect();
  const shown = [...c.children].filter(e => getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().height > 0);
  const last = shown.pop(); const r = last.getBoundingClientRect();
  return {last: last.className, text: last.textContent.trim().slice(-40), gap: Math.round(k.bottom - r.bottom), top: Math.round(r.top - k.top),
          scrollTop: Math.round(c.scrollTop), cut: Math.round(c.scrollHeight - c.clientHeight - c.scrollTop)}; }"""


@pytest.mark.parametrize("size,text", [
    pytest.param((320, 568), None, id="320x568"), pytest.param((320, 640), None, id="320x640"),
    pytest.param((320, 640), TEXT_150, id="320x640-150"), pytest.param((360, 640), TEXT_150, id="360x640-150"),
])
def test_a_card_that_does_not_fit_keeps_its_own_scroll_with_the_fade_and_the_pill_and_a_finger_reaches_every_line(browser, server, size, text):
    ctx, pg = open_page(browser, server, size, init=text)
    try:
        start_sample(pg)
        seen = walk_fit(pg, 7)
        clipped = [s for s in seen if s["cut"] > 0]
        assert clipped, seen  # on these screens some cards are taller than their frame ...
        for s in clipped:
            assert s["fade"] and (s["pill"] or s["cut"] <= 8), s  # ... and say so: a fade, and a pill as soon as text is cut
        assert all(s["page"] for s in seen)  # the page still does not scroll: the card does
        # Go to the vitamin D3 card (the one with the long note and "ask your doctor ..."), and reach its last line with a real finger.
        for _ in range(len(seen) - 1 - 1):  # the walk ended on the last card: Back to card 2
            name = card_name(pg)
            card(pg).locator("#btnBack").click(timeout=5000)
            wait_name_change(pg, name)
            settle(pg, 0.3)
        assert card_name(pg) == "Vitamin D3" and next(s for s in seen if s["name"] == "Vitamin D3")["cut"] > 0
        fr = card_frame(pg)
        assert "ask your doctor whether that suits you" in fr.evaluate("document.getElementById('card').textContent")  # IN the card ...
        before = fr.evaluate(LAST_VISIBLE_JS)
        assert before["scrollTop"] == 0 and before["gap"] < 0, before  # its last line is cut off to start with
        x, y = centre(pg)
        for _ in range(30):  # ... and dragging the card up brings it into view (a swipe sideways would decide; this must not)
            if fr.evaluate(LAST_VISIBLE_JS)["cut"] <= 1:
                break
            cdp_swipe(ctx, pg, x, y + 90, 0, -150)
            pg.wait_for_timeout(120)
        end = fr.evaluate(LAST_VISIBLE_JS)
        assert end["scrollTop"] > 0 and end["cut"] <= 1, end  # the finger really scrolled the card
        assert end["gap"] >= -1, end  # ... to its last line, whose end is inside the card (a note taller than a large-text window starts above it)
        assert card_name(pg) == "Vitamin D3" and no_page_scroll(pg)  # and it neither swiped nor scrolled the page
    finally:
        ctx.close()


def test_with_the_vegan_filter_and_pregnancy_mode_the_longer_notes_scroll_and_the_pregnancy_sentence_is_reachable(browser, server):
    """"Every card fits from 360x640" holds without a filter. With the Vegan chip and the pregnancy toggle (the audience those exist for)
    the vitamin D3 and B12 notes are longer: measured, they scroll up to 390x720 and fit from 390x844. They keep the fade and the pill, the
    page still does not scroll, and the sentence "ask your doctor or midwife ..." is reached with a finger."""
    from test_ux_browser import open_diet_sheet

    ctx, pg = open_page(browser, server, (360, 640))
    try:
        open_diet_sheet(pg)
        pg.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button', has_text="Vegan").click()
        settle(pg)
        pg.get_by_text("Pregnant or breastfeeding").click()
        settle(pg)
        close_sheet(pg)
        start_sample(pg)
        seen = walk_fit(pg, 7)
        clipped = [s for s in seen if s["cut"] > 0]
        assert clipped and all(s["fade"] and (s["pill"] or s["cut"] <= 8) for s in clipped), seen  # what does not fit says so
        assert all(s["page"] for s in seen) and len({s["frame"] for s in seen}) == 1  # the page does not scroll, one frame
        for _ in range(7):  # Back to the vitamin B12 card
            if card_name(pg) == "Vitamin B12":
                break
            name = card_name(pg)
            card(pg).locator("#btnBack").click(timeout=5000)
            wait_name_change(pg, name)
            settle(pg, 0.3)
        assert card_name(pg) == "Vitamin B12"
        fr = card_frame(pg)
        assert "ask your doctor or midwife" in fr.evaluate("document.getElementById('card').textContent")  # in the card ...
        x, y = centre(pg)
        for _ in range(30):
            if fr.evaluate(LAST_VISIBLE_JS)["cut"] <= 1:
                break
            cdp_swipe(ctx, pg, x, y + 90, 0, -150)
            pg.wait_for_timeout(120)
        end = fr.evaluate(LAST_VISIBLE_JS)
        assert end["cut"] <= 1 and end["gap"] >= -1, end  # ... and a finger brings it into view
        assert card_name(pg) == "Vitamin B12"
    finally:
        ctx.close()


def test_the_safety_sentence_of_a_long_card_stays_in_the_card_on_the_smallest_phone(browser, server):
    ctx, pg = open_page(browser, server, (320, 568))
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))  # card 2: vitamin D3
        text = card_frame(pg).evaluate("document.getElementById('card').textContent")
        assert "ask your doctor whether that suits you" in text
        assert "Opens the athlete guide" in text  # and the screen-reader text of the athlete line
    finally:
        ctx.close()


@pytest.mark.parametrize("percent", ["100", "150"])
@pytest.mark.parametrize("size", [(320, 640), (360, 640), (390, 844)])
def test_the_three_tools_are_finger_sized_labelled_and_on_one_line_at_100_and_150_percent_text(browser, ai_server, size, percent):
    url, _fake = ai_server
    ctx, pg = open_page(browser, url, size, init=TEXT_150 if percent == "150" else None)
    try:
        start_sample(pg)
        info = card_frame(pg).evaluate(
            """() => [...document.querySelectorAll('#extras button')].filter(b => !b.hidden).map(b => { const r = document.createRange(); r.selectNodeContents(b);
              const k = b.getBoundingClientRect(); const hit = document.elementFromPoint(k.left + k.width / 2, k.top + k.height / 2);
              return {text: b.textContent.trim(), aria: b.getAttribute('aria-label'), lines: r.getClientRects().length, h: k.height, w: k.width, top: k.top,
                      right: k.right, left: k.left, hit: hit === b, vw: innerWidth}; })""")
        assert [i["text"] for i in info] == ["Swap food", "Ask AI", "More"], info
        assert all(i["h"] >= 44 and i["w"] >= 44 for i in info), info
        assert all(i["lines"] == 1 for i in info), info  # each label on one line
        assert len({round(i["top"]) for i in info}) == 1, info  # and the three side by side
        assert all(i["hit"] and 0 <= i["left"] and i["right"] <= i["vw"] for i in info), info
        assert all(i["text"] in i["aria"] for i in info)  # the accessible name contains the visible label
        assert no_page_scroll(pg) and 0 <= row_gap(pg) <= 12
    finally:
        ctx.close()


def test_ask_ai_is_not_offered_without_an_ai_and_swap_is_offered_on_a_card_with_foods_to_swap_between(browser, server):
    ctx, pg = open_page(browser, server, (390, 844))  # this server has no AI settings
    try:
        start_sample(pg)
        names = [b.inner_text().strip() for b in card(pg).locator("#extras button:visible").all()]
        assert names == ["Swap food", "More"], names  # no button that only ends in an error
    finally:
        ctx.close()


def test_the_swap_tool_is_not_offered_on_a_card_without_a_second_food_and_the_card_says_why(browser, server):
    """A vegan diet has no whole food for EPA / DHA: the card says so (in its food block, where the Replace button's state is), Replace
    is off, and there is nothing to swap to, so no Swap food button."""
    from test_ux_browser import choose_diet

    ctx, pg = open_page(browser, server, (390, 844))
    try:
        choose_diet(pg, "Vegan")
        start_text(pg, "Omega-3 EPA 300 mg DHA 200 mg")
        names = [b.inner_text().strip() for b in card(pg).locator("#extras button:visible").all()]
        assert names == ["More"], names
        assert card(pg).locator("#btnRepl").get_attribute("aria-disabled") == "true" or card(pg).locator("#btnRepl").is_disabled()
        assert "Keeping the supplement is recommended" in card(pg).locator("#card .foodwrap").inner_text()
    finally:
        ctx.close()


# ------------------------------------------------------------------ the sheets
TOOLS = [("#btnSwap", "Swap the food"), ("#btnAsk", "Ask AI"), ("#btnMore", "Report a problem")]


def open_tool(pg, selector: str, title: str):
    card(pg).locator(selector).click(timeout=10000)
    dialog = pg.get_by_role("dialog")
    dialog.wait_for(timeout=10000)
    assert dialog.locator("h2").inner_text().strip() == title
    pg.wait_for_timeout(700)  # a sheet ignores taps for its first 450 ms
    return dialog


def close_how(pg, how: str) -> None:
    if how == "x":
        pg.get_by_role("dialog").locator('button[aria-label="Close"]').click()
    elif how == "escape":
        pg.keyboard.press("Escape")
    else:  # a tap on the dimmed page above the sheet
        pg.mouse.click(190, 30)
    pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
    settle(pg)


@pytest.mark.parametrize("how", ["x", "escape", "outside"])
@pytest.mark.parametrize("selector,title", TOOLS)
def test_each_sheet_opens_from_the_card_closes_three_ways_and_returns_to_the_same_card(browser, ai_server, selector, title, how):
    url, _fake = ai_server
    ctx, pg = open_page(browser, url, (390, 844))
    try:
        start_sample(pg)
        name = card_name(pg)
        sheet = open_tool(pg, selector, title)
        box = sheet.bounding_box()
        assert box["y"] + box["height"] >= 844 - 2 and box["width"] >= 388  # a bottom sheet on a phone
        close_how(pg, how)
        assert card_name(pg) == name and "Card 1 of 7" in card(pg).locator(".count").inner_text()  # the same card ...
        assert card(pg).locator("#btnKeep").is_enabled() and card(pg).locator("#btnBack").is_disabled()  # ... nothing decided, still the first
        assert no_page_scroll(pg) and 0 <= row_gap(pg) <= 12
        keep_and_wait(pg, name)  # and it swipes on
    finally:
        ctx.close()


def test_the_keyboard_is_put_back_on_the_tool_it_used_when_its_sheet_closes_and_the_frame_has_a_name(browser, ai_server):
    """A sheet makes the page behind it inert: the focused button lost the focus, and the page gave it back to the bare frame (named by
    the component's own id, "app.tinder_swipe"). Now the frame is titled, and the button the visitor used has the focus again."""
    url, _fake = ai_server
    ctx, pg = open_page(browser, url, (390, 844), touch=False)
    try:
        start_sample(pg)
        assert pg.locator(CARD).get_attribute("title") == "Nutrient card: swipe, or use the buttons"
        for selector, title in TOOLS:
            card_frame(pg).evaluate("sel => document.querySelector(sel).focus()", selector)
            pg.keyboard.press("Enter")
            pg.get_by_role("dialog").wait_for(timeout=10000)
            assert pg.get_by_role("dialog").locator("h2").inner_text().strip() == title
            pg.wait_for_timeout(700)
            close_how(pg, "escape")
            assert card_frame(pg).evaluate("document.activeElement.id") == selector[1:], selector
            assert pg.evaluate("document.activeElement.tagName") == "IFRAME"
    finally:
        ctx.close()


SPY_EVENTS = """() => { const orig = window.parent.postMessage.bind(window.parent); window.__events = [];
  window.parent.postMessage = (m, t) => { if (m && m.type === 'streamlit:setComponentValue') { window.__events.push(m.value); } return orig(m, t); }; }"""


def test_a_double_tap_on_a_tool_sends_one_event_and_nothing_is_sent_while_a_swipe_is_on_its_way(browser, ai_server):
    """The page ignores a duplicate or stale event too, so these guards of the component were untested (removing them changed nothing a
    test could see). What the component posts is counted here: one event per tap, none after a swipe was committed."""
    url, _fake = ai_server
    ctx, pg = open_page(browser, url, (390, 844))
    try:
        start_sample(pg)
        fr = card_frame(pg)
        fr.evaluate(SPY_EVENTS)
        fr.evaluate("() => { const b = document.getElementById('btnMore'); b.click(); b.click(); document.getElementById('btnMore').click(); }")
        events = fr.evaluate("window.__events")
        assert [e["kind"] for e in events] == ["report"], events  # three taps within 0.7 s: one event, stamped with the card, its index and the scan
        assert events[0]["card"] == "vitamin c" and events[0]["index"] == 0 and str(events[0]["scan"]).isdigit(), events
        pg.get_by_role("dialog").wait_for(timeout=10000)
        close_how(pg, "escape")
        pg.wait_for_timeout(800)
        fr.evaluate("window.__events.length = 0")
        fr.evaluate("() => { document.getElementById('btnKeep').click(); document.getElementById('btnMore').click(); document.getElementById('btnAsk').click(); }")
        events = fr.evaluate("window.__events")
        assert [e.get("dir") for e in events] == ["left"], events  # the swipe went; the tools sent nothing after it
        assert wait_name_change(pg, "Vitamin C")
        assert pg.get_by_role("dialog").count() == 0
    finally:
        ctx.close()


def test_a_tool_tap_never_swipes_and_a_swipe_never_opens_a_sheet(browser, ai_server):
    url, _fake = ai_server
    ctx, pg = open_page(browser, url, (390, 844))
    try:
        start_sample(pg)
        name = card_name(pg)
        for selector, title in TOOLS:
            open_tool(pg, selector, title)
            close_how(pg, "escape")
            assert card_name(pg) == name
        # A drag that starts on the card swipes it; no sheet comes up.
        box = card(pg).locator("#card").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + 120
        cdp_swipe(ctx, pg, x, y, -190)
        assert wait_name_change(pg, name)
        assert pg.get_by_role("dialog").count() == 0
        # The keyboard still decides: an arrow key on the card.
        second = card_name(pg)
        card(pg).locator("#card").focus()
        pg.keyboard.press("ArrowLeft")
        assert wait_name_change(pg, second)
        assert pg.get_by_role("dialog").count() == 0
    finally:
        ctx.close()


def test_the_food_picked_in_the_swap_sheet_shows_on_the_card_and_reaches_the_plan(browser, server):
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        before = card_frame(pg).evaluate("document.querySelector('.food').textContent.trim()")
        open_swap_sheet(pg)
        pg.locator('[data-testid="stDialog"] [data-testid="stSelectbox"]').first.click()
        options = pg.locator('[role="option"]')
        options.first.wait_for(timeout=5000)
        label = options.nth(1).inner_text()
        options.nth(1).click()
        settle(pg)
        assert "USDA:" in pg.locator(DIALOG).inner_text()  # the source line moved here from under the card
        pg.get_by_role("button", name="Done").click()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        settle(pg)
        food = card_frame(pg).evaluate("document.querySelector('.food').textContent.trim()")
        assert food != before and food.lower() in label.lower().replace(",", ",")  # the card shows the picked food
        assert card_frame(pg).evaluate("document.querySelector('.src') ? document.querySelector('.src').textContent : ''").startswith("USDA: ")
        card(pg).locator("#btnRepl").click()  # replace: the plan holds exactly that food
        wait_name_change(pg, "Vitamin C")
        finish_all_cards(pg)
        row = pg.locator('[class*="st-key-planbtn_food_"] button', has_text=food)
        row.first.wait_for(timeout=10000)
        assert row.count() == 1
    finally:
        ctx.close()


def test_the_report_sheet_thanks_and_returns_to_the_card(browser, server):
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        name = card_name(pg)
        open_tool(pg, "#btnMore", "Report a problem")
        pg.get_by_role("button", name="🚩 Report a problem with this card").click()
        pg.locator('[data-testid="stToast"]').first.wait_for(timeout=10000)
        assert "Thanks — logged for review" in pg.locator('[data-testid="stToast"]').first.inner_text()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        settle(pg)
        assert card_name(pg) == name and no_page_scroll(pg)
    finally:
        ctx.close()


def test_the_ask_sheet_answers_inside_the_sheet_and_the_card_behind_is_not_rebuilt(browser, ai_server):
    url, fake = ai_server
    fake.stream_script = [{"delay": 1, "text": "Vitamin C supports the immune system."}]
    ctx, pg = open_page(browser, url, (390, 844))
    try:
        start_sample(pg)
        pg.frame(url=lambda u: "tinder_swipe" in u).evaluate("window.__kept = true")
        dialog = open_tool(pg, "#btnAsk", "Ask AI")
        chat = dialog.get_by_test_id("stChatInput").locator("textarea")
        chat.fill("What does vitamin C do?")
        chat.press("Enter")
        dialog.get_by_text("Vitamin C supports the immune system.").first.wait_for(timeout=30000)
        settle(pg)
        assert pg.get_by_role("dialog").count() == 1  # the answer redrew the sheet, did not close it
        assert fake.stream_calls == 1
        assert "General AI answer" in dialog.inner_text()  # the sources label
        close_how(pg, "escape")
        assert pg.frame(url=lambda u: "tinder_swipe" in u).evaluate("window.__kept === true")  # the card's frame was never reloaded
        assert card_name(pg) == "Vitamin C"
    finally:
        ctx.close()


def test_the_ask_sheet_keeps_its_input_in_view_as_the_chat_grows(browser, ai_server):
    """From the second answer on the input was below the fold of a 640 px phone (y 716-744) and the sheet did not scroll to it."""
    url, fake = ai_server
    fake.stream_script = [{"delay": 0, "text": "Vitamin C supports the immune system. " * 4}]
    ctx, pg = open_page(browser, url, (360, 640))
    try:
        start_sample(pg)
        dialog = open_tool(pg, "#btnAsk", "Ask AI")
        box = "() => { const i = document.querySelector('[role=dialog] [data-testid=stChatInput]').getBoundingClientRect(); return [Math.round(i.top), Math.round(i.bottom), innerHeight]; }"
        for n, question in enumerate(("How much vitamin C is too much?", "And what about guavas?", "Any interaction with iron?"), start=1):
            chat = dialog.get_by_test_id("stChatInput").locator("textarea")
            chat.fill(question)
            chat.press("Enter")
            dialog.locator('[data-testid="stChatMessage"]').nth(2 * n - 1).wait_for(timeout=30000)
            settle(pg, 0.8)
            top, bottom, viewport = pg.evaluate(box)
            assert 0 <= top and bottom <= viewport, (n, top, bottom, viewport)  # the input is on screen, ready for a follow-up
            assert dialog.locator('[data-testid="stChatMessage"]').count() == 2 * n
    finally:
        ctx.close()


# ------------------------------------------------------------------ other situations
def test_a_filter_chip_appearing_above_the_card_keeps_the_screen_fixed(browser, server):
    ctx, pg = open_page(browser, server, (320, 640))
    try:
        start_sample(pg)
        before = frame_height(pg)
        from test_ux_browser import choose_diet, close_sheet, open_diet_sheet

        choose_diet(pg, "Vegan")
        open_diet_sheet(pg)
        pg.locator('[data-testid="stDialog"] label', has_text="Pregnant or breastfeeding").click()  # the longest chip: it wraps to a second line
        settle(pg)
        close_sheet(pg)
        pg.locator(".diet-note").wait_for(timeout=10000)
        settle(pg, 0.8)
        after = frame_height(pg)
        assert after < before, (before, after)  # a line more above the card: the frame gives back that room, and the page still does not scroll
        assert no_page_scroll(pg) and 0 <= row_gap(pg) <= 12, (before, after)
    finally:
        ctx.close()


def test_a_phones_toolbars_coming_and_going_refit_the_frame_and_the_row_stays_clear_of_the_bar(browser, server):
    """A phone browser's own toolbars take ~100 px of the screen and come back after the card was drawn: nothing tells the frame
    (Streamlit sends no new props for an unchanged card), so it looks at its room again (the host's resize, plus a poll)."""
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        tall = frame_height(pg)
        pg.set_viewport_size({"width": 390, "height": 744})
        pg.wait_for_timeout(1200)
        short = frame_height(pg)
        room = rect(pg, BAR)["y"] - rect(pg, CARD)["y"]
        assert short < tall - 60 and room - 12 <= short <= room + 6, (tall, short, room)  # it gave the room back ...
        assert 0 <= row_gap(pg) <= 12 and no_page_scroll(pg), (row_gap(pg), scroll_state(pg))  # ... the row is clear of the bar, nothing scrolls
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(1200)
        assert abs(frame_height(pg) - tall) <= 2 and 0 <= row_gap(pg) <= 12  # and takes it again
        name = card_name(pg)
        keep_and_wait(pg, name)
    finally:
        ctx.close()


def test_a_room_that_was_short_for_a_moment_does_not_leave_the_card_cramped(browser, server):
    """Within one room the density only ever tightens (every card of a scan has one look); when the ROOM changes (a window, the
    toolbars, a soft keyboard that resizes the page) it is chosen again, so a transient shrink does not leave the cramped card and
    the missing swipe hint behind for the rest of the scan."""
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        keep_and_wait(pg, card_name(pg))  # card 2: the swipe hint is not shown on the first card only
        state = "() => { const h = document.querySelector('.hint'); return {tight: document.getElementById('wrap').classList.contains('tight'), hint: h ? getComputedStyle(h).display : null}; }"
        before = card_frame(pg).evaluate(state)
        assert before == {"tight": False, "hint": "block"}, before
        pg.set_viewport_size({"width": 390, "height": 700})
        pg.wait_for_timeout(1500)
        assert card_frame(pg).evaluate(state)["tight"] is True  # short room: the cramped card
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(1500)
        assert card_frame(pg).evaluate(state) == before  # room back: the card as it was
        assert no_page_scroll(pg) and 0 <= row_gap(pg) <= 12
    finally:
        ctx.close()


def test_a_landscape_phone_scrolls_the_page_and_the_row_can_be_scrolled_clear_of_the_bar(browser, server):
    ctx, pg = open_page(browser, server, (844, 390))
    try:
        start_sample(pg)
        assert scroll_state(pg)["main"] > 0  # there is no room for a fixed screen: the page scrolls, as before
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(400)
        keep = card(pg).locator("#btnKeep").bounding_box()
        assert keep["y"] + keep["height"] <= rect(pg, BAR)["y"] + 1, (keep, rect(pg, BAR))  # the blank strip of the frame made the room
        name = card_name(pg)
        card(pg).locator("#btnKeep").click(timeout=5000)  # and the tap works
        assert wait_name_change(pg, name)
    finally:
        ctx.close()


WINDOW_JS = """() => { const words = [];
  for (const id of ['btnKeep', 'btnRepl']) { const b = document.getElementById(id); const walker = document.createTreeWalker(b, NodeFilter.SHOW_TEXT); let n;
    while ((n = walker.nextNode())) { if (n.parentElement.classList.contains('arr')) { continue; } const re = /\\S+/g; let m;
      while ((m = re.exec(n.nodeValue))) { const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length); words.push([m[0], r.getClientRects().length]); } } }
  const c = document.getElementById('card'), pad = parseFloat(getComputedStyle(c).paddingBottom) || 0;
  return { window: Math.round(document.getElementById('stage').getBoundingClientRect().height), root: parseFloat(getComputedStyle(document.documentElement).fontSize),
           spacer: document.getElementById('wrap').classList.contains('spacer'), cut: Math.round(c.scrollHeight - c.clientHeight - pad),
           pill: document.getElementById('stage').classList.contains('cue-strong'), words: words }; }"""


@pytest.mark.parametrize("size,percent,grows", [
    pytest.param((320, 568), "200", True, id="320x568-200"), pytest.param((360, 640), "200", True, id="360x640-200"),
    pytest.param((390, 844), "200", False, id="390x844-200"), pytest.param((412, 915), "200", False, id="412x915-200"),
    pytest.param((320, 568), "150", False, id="320x568-150"), pytest.param((360, 640), "150", False, id="360x640-150"),
])
def test_at_a_large_text_size_the_card_keeps_a_readable_window_and_the_page_scrolls_only_when_it_must(browser, server, size, percent, grows):
    """The text size reaches the card's own frame (tests/test_ux_row_clear_browser.py _zoom_card_frames: the page's 32 px root and the
    card's 16 px used to be what "200 %" measured, so every large-text figure was the page chrome alone). With it: the card is never
    left a slit between its tools and its buttons (its window is at least MIN_WINDOW = 240 px: below that the frame grows and the page
    scrolls, as for a landscape phone), the labels of Keep / Replace are never broken inside a word, and the row can be reached."""
    ctx, pg = open_page(browser, server, size, init=TEXT_200 if percent == "200" else TEXT_150)
    try:
        start_sample(pg)
        fr = card_frame(pg)
        for i in range(7):
            info = fr.evaluate(WINDOW_JS)
            assert info["root"] == (32 if percent == "200" else 24), info  # the card's frame is zoomed too
            assert info["window"] >= 239, (i, info)  # the card's own window: readable, not a slit
            assert info["spacer"] is grows, (i, info)
            assert all(lines == 1 for _word, lines in info["words"]), info["words"]  # "Keep pill" / "Replace": no word is broken in two
            if info["cut"] > 2:
                assert fr.evaluate("document.getElementById('stage').classList.contains('cue')"), info  # what is cut says so
            if grows:
                assert scroll_state(pg)["main"] > 0 and scroll_state(pg)["width"] <= 0  # the page scrolls, never sideways
                pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
                pg.wait_for_timeout(300)
                assert row_gap(pg) >= 0, (i, row_gap(pg))  # scrolled to the end, the row is clear of the bar
            else:
                assert no_page_scroll(pg) and 0 <= row_gap(pg) <= 12, (i, scroll_state(pg), row_gap(pg))
            if i < 6:
                keep_and_wait(pg, card_name(pg))  # Playwright refuses a covered target: the Keep button is reachable
                if grows:
                    pg.evaluate(f"{MAIN}.scrollTo(0, 0)")
    finally:
        ctx.close()


def test_a_big_bar_at_200_percent_text_on_the_smallest_phone_lets_the_page_scroll_the_row_clear(browser, server):
    """320x568 at 200 % text: the brand takes three lines and the bar is 108 px tall. The card's window would be a slit (152 px) between
    its tools (two rows) and its buttons, so the frame grows to give it 240 px and the page scrolls: it is the one place a phone at 100 %
    text never does. The row can be scrolled clear of the bar and tapped."""
    ctx, pg = open_page(browser, server, (320, 568), init=TEXT_200)
    try:
        start_sample(pg)
        assert scroll_state(pg)["main"] > 0 and scroll_state(pg)["width"] <= 0
        assert card_frame(pg).evaluate("document.getElementById('wrap').classList.contains('spacer')")
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(400)
        assert row_gap(pg) >= 0, row_gap(pg)
        name = card_name(pg)
        card(pg).locator("#btnKeep").click(timeout=5000)  # Playwright refuses a covered target
        assert wait_name_change(pg, name)
    finally:
        ctx.close()


def test_the_results_screen_still_scrolls_like_a_long_page(browser, server):
    ctx, pg = open_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        finish_all_cards(pg, replace=True)
        results_heading(pg).wait_for(timeout=10000)
        # the heading paints before the rest of the dashboard: wait for the page to grow instead of measuring the first frame
        pg.wait_for_function(f"{MAIN}.scrollHeight - {MAIN}.clientHeight > 0", timeout=10000)
        assert scroll_state(pg)["main"] > 0  # the dashboard is long on purpose
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(300)
        foot, bar = rect(pg, ".brand-foot"), rect(pg, BAR)
        assert foot and foot["bottom"] <= bar["y"] + 1, (foot, bar)  # the © line closes the page, clear of the bar
    finally:
        ctx.close()


# ------------------------------------------------------------------ interruptions
@pytest.mark.parametrize("how", ["x", "escape", "outside"])
def test_closing_the_ask_sheet_in_the_middle_of_an_answer_keeps_the_question_and_the_answer(browser, ai_server, how):
    """The old popover kept the answer when it was closed while the model was writing; the sheet's dismissal reruns the app, which
    stopped the script before the turn was stored: the question and the answer were gone (and the allowance spent)."""
    url, fake = ai_server
    fake.stream_script = [{"delay": 4, "text": "Vitamin C supports the immune system. Cut-off answer."}]
    ctx, pg = open_page(browser, url, (390, 844))
    try:
        start_sample(pg)
        dialog = open_tool(pg, "#btnAsk", "Ask AI")
        chat = dialog.get_by_test_id("stChatInput").locator("textarea")
        chat.fill("What does vitamin C do for me?")
        chat.press("Enter")
        pg.locator('[data-testid="stSpinner"]').first.wait_for(timeout=15000)
        pg.wait_for_timeout(800)
        close_how(pg, how)  # the answer is still being written
        assert fake.stream_calls + fake.completion_calls == 1
        pg.wait_for_timeout(5000)  # the model finishes in the background
        dialog = open_tool(pg, "#btnAsk", "Ask AI")
        dialog.get_by_text("Cut-off answer.").first.wait_for(timeout=15000)
        text = dialog.inner_text()
        assert "What does vitamin C do for me?" in text and "General AI answer" in text, text
        assert fake.stream_calls + fake.completion_calls == 1  # not asked a second time
    finally:
        ctx.close()


@pytest.mark.parametrize("selector", ["#btnMore", "#btnKeep"])
def test_a_tap_on_the_old_card_while_a_new_label_is_analysed_goes_nowhere(browser, ai_server, tmp_path, selector):
    """The card of the scan before stays on screen, dimmed, while the next label is read. A tap on it used to be read by the first run
    of the new scan: a Report sheet nobody asked for, or the new scan's first card decided without being seen."""
    from PIL import Image
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    url, fake = ai_server
    fake.stream_script = [{"delay": 6, "text": "Supplement Facts\nVitamin C 90 mg\nZinc 11 mg"}]
    photo = tmp_path / "label.jpg"
    Image.new("RGB", (640, 480), (231, 240, 250)).save(photo, "JPEG")
    ctx, pg = open_page(browser, url, (390, 844))
    try:
        start_sample(pg)
        open_scan_sheet(pg)
        pg.get_by_role("button", name="Analyze my supplement").click()
        dialog = pg.get_by_role("dialog")
        dialog.locator("button", has_text="Upload").click()
        settle(pg)
        dialog.locator('input[type="file"]').set_input_files(str(photo))
        pg.locator(".analyze-loading-wrap").first.wait_for(timeout=20000)
        pg.wait_for_timeout(1200)
        assert card(pg).locator(".count").inner_text().endswith("of 7")  # the old card is still there
        assert pg.locator(CARD).evaluate("e => getComputedStyle(e).pointerEvents") == "none"  # and out of reach of a finger ...
        with pytest.raises(PlaywrightTimeout):
            card(pg).locator(selector).click(timeout=1500)
        card(pg).locator(selector).evaluate("e => e.click()")  # ... and, were it reached some other way, dead in the component too
        card(pg).locator("#card").evaluate("e => e.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true}))")
        document_keys = pg.frame(url=lambda u: "tinder_swipe" in u)
        document_keys.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true}))")
        for _ in range(120):
            pg.wait_for_timeout(250)
            if pg.locator(".analyze-loading-wrap").count() == 0 and card(pg).locator(".count").inner_text().endswith("of 2"):
                break
        pg.wait_for_timeout(1500)
        assert card(pg).locator(".count").inner_text() == "Card 1 of 2" and card_name(pg) == "Vitamin C"  # the first card, not decided
        assert pg.get_by_role("dialog").count() == 0  # and no sheet nobody asked for
        assert card(pg).locator("#btnBack").is_disabled()
        assert fake.stream_calls + fake.completion_calls == 1  # the analysis was not restarted by the tap
    finally:
        ctx.close()

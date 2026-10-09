"""Browser checks of the fixed screens (real Chromium, a touch phone at 2x): the welcome card and the cards screen do not scroll, the card
carries its own tools (Swap food / Ask AI / More) that open sheets, and a card that does not fit keeps its own scroll.

Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers are reused. The offline half (the events,
the sheets' content, the props) is tests/test_ux_fixed_screen.py.

Measured when this was built (sample label, 7 cards; "upper limit" label = 5 cards, three with a red warning box; 100 % text): with
the tools row and the Keep / Replace row inside the frame, the frame fills the room between the brand and the bar. Every card of the
sample label fits its frame without the card's own scroll from 360x640 up (320x640: the vitamin D3 card scrolls; 320x568: D3, magnesium,
selenium); every card of the upper-limit label fits from 412x680, 390x720 and 360x760 up (360x640: the selenium card scrolls by 64 px; 320x640:
vitamin D3, zinc and selenium scroll; 320x568: all five). The page itself never scrolls, on any of them.

Larger text (sample label): 150 % fits from 390x844; 360x640 scrolls D3, magnesium and selenium, 320x640 also B12 by 8 px, 320x568 all
seven. 200 % fits at 412x915, scrolls vitamin D3 only (22 px) at 390x844 and every card on 360x640 and below. The page still does
not scroll, except at 320x568 with 200 % text, where the room (~295 px) is below a frame's floor: the page then scrolls ~35 px so the
row can be reached (test_a_big_bar_at_200_percent_text_on_the_smallest_phone_lets_the_page_scroll_the_row_clear)."""
from __future__ import annotations

import os

import pytest

if os.getenv("SUPPSWIPE_BROWSER_TESTS", "") != "1":
    pytest.skip("browser tests are opt-in (SUPPSWIPE_BROWSER_TESTS=1)", allow_module_level=True)

from test_ux_app_bar_browser import BAR, BAR_BUTTON, MAIN, rect  # noqa: E402
from test_ux_browser import (  # noqa: E402,F401  (fixtures are used by name)
    CARD, DIALOG, ai_server, browser, card, card_name, close_sheet, finish_all_cards, open_swap_sheet, page, results_heading, server,
    settle, start_sample, wait_name_change,
)
from test_ux_row_clear_browser import (  # noqa: E402,F401
    TEXT_200, WARN_LABEL, card_frame, cdp_swipe, frame_height, keep_and_wait, open_page, row_gap,
)

SAMPLE_SIZES = [(320, 568), (320, 640), (360, 640), (375, 667), (390, 664), (390, 844), (393, 700), (412, 780), (412, 915)]
TEXT_150 = TEXT_200.replace("200%", "150%")
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
        assert pg.locator('[class~="st-key-hero_card"] button').inner_text().strip().endswith("Scan a supplement")  # after its icon's name
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


@pytest.mark.parametrize("size,text", [((320, 568), None), ((320, 640), None), ((320, 640), TEXT_150)])
def test_a_card_that_does_not_fit_keeps_its_own_scroll_with_the_fade_and_the_pill_and_every_line_is_reachable(browser, server, size, text):
    ctx, pg = open_page(browser, server, size, init=text)
    try:
        start_sample(pg)
        seen = walk_fit(pg, 7)
        clipped = [s for s in seen if s["cut"] > 0]
        assert clipped, seen  # on these screens some cards are taller than their frame ...
        for s in clipped:
            assert s["fade"] and (s["pill"] or s["cut"] <= 8), s  # ... and say so: a fade, and a pill as soon as text is cut
        assert all(s["page"] for s in seen)  # the page still does not scroll: the card does
        # Reach the end of the tallest one (the long vitamin D note, with "ask your doctor ..."): the last line ends inside the card.
        worst = max(seen, key=lambda s: s["cut"])
        for _ in range(len(seen) - 1 - seen.index(worst)):  # the walk ended on the last card: Back to the tallest one
            name = card_name(pg)
            card(pg).locator("#btnBack").click(timeout=5000)
            wait_name_change(pg, name)
            settle(pg, 0.3)
        fr = card_frame(pg)
        assert fr.evaluate("document.getElementById('card').textContent").count("ask your doctor") <= 1  # the sentence is IN the card ...
        end = fr.evaluate(
            "() => { const c = document.getElementById('card'); c.scrollTop = c.scrollHeight; const last = [...c.children].pop(); const r = last.getBoundingClientRect();"
            " const k = c.getBoundingClientRect(); return {gap: k.bottom - r.bottom, top: r.top - k.top}; }")
        assert end["gap"] >= -1 and end["top"] >= 0, end  # ... and scrolling brings the last line fully into view
        fr.evaluate("document.getElementById('card').scrollTop = 0")
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


def test_the_swap_tool_is_not_offered_without_a_second_food_and_ask_ai_not_without_an_ai(browser, server):
    ctx, pg = open_page(browser, server, (390, 844))  # this server has no AI settings
    try:
        start_sample(pg)
        names = [b.inner_text().strip() for b in card(pg).locator("#extras button:visible").all()]
        assert names == ["Swap food", "More"], names  # no button that only ends in an error
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


def test_a_big_bar_at_200_percent_text_on_the_smallest_phone_lets_the_page_scroll_the_row_clear(browser, server):
    """320x568 at 200 % text: the brand takes three lines and the bar is 108 px tall, so the room (~295 px) is below the 320 px floor
    of a frame. The frame then keeps a blank strip under its buttons as long as the bar needs (measured), the page scrolls (it is the
    one place a phone at 100 % text never does) and the Keep / Replace row can be scrolled clear of the bar and tapped."""
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
        assert scroll_state(pg)["main"] > 0  # the dashboard is long on purpose
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(300)
        foot, bar = rect(pg, ".brand-foot"), rect(pg, BAR)
        assert foot and foot["bottom"] <= bar["y"] + 1, (foot, bar)  # the © line closes the page, clear of the bar
    finally:
        ctx.close()

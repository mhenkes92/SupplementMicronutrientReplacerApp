"""Browser checks of the fixed bottom bar and its three sheets.

Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers are reused. What AppTest cannot see:
docking, one row, the Cloud badge, the page never hiding behind the bar, the sheets' size, Esc and focus, the busy bar."""
from __future__ import annotations

import pytest

from test_ux_browser import (  # noqa: F401  (fixtures are used by name)
    CARD, DIALOG, _mixed_results, ai_server, browser, card, card_name, choose_option, finish_all_cards, page, results_heading,
    server, settle, shot, start_own_label, start_sample, wait_name_change,
)

BAR = '[class~="st-key-appbar"]'
BUSY = '[class~="st-key-appbar_busy"]'
BAR_BUTTON = '[class~="st-key-appbar"] button'
LABELS = ("Guide", "Scans", "About")
SIZES = [(390, 844), (320, 640)]
MAIN = "document.querySelector('[data-testid=stMain]')"


def bar_button(page, index: int):
    return page.locator(BAR_BUTTON).nth(index)


def rect(page, selector: str, index: int = 0) -> dict:
    return page.evaluate(
        "([s, i]) => { const e = document.querySelectorAll(s)[i]; if (!e) return null; const r = e.getBoundingClientRect();"
        " return {x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom, cx: r.x + r.width / 2,"
        " cy: r.y + r.height / 2, vw: innerWidth, vh: innerHeight}; }",
        [selector, index],
    )


def on_screen(page, name: str) -> None:
    if name == "card":
        start_sample(page)
    elif name == "results":
        _mixed_results(page)


def new_page(browser, server, size):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(server, wait_until="networkidle")
    return ctx, pg


def top_is_bar(pg, x: float, y: float) -> bool:
    return pg.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return !!(e && e.closest('[class~=\"st-key-appbar\"]')); }", [x, y])


# ------------------------------------------------------------------ docked, in one row, finger-sized
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("screen", ["welcome", "card", "results"])
def test_the_bar_is_docked_at_the_bottom_in_one_row(browser, server, size, screen):
    ctx, pg = new_page(browser, server, size)
    try:
        on_screen(pg, screen)
        pg.locator(BAR_BUTTON).nth(2).wait_for(timeout=10000)
        boxes = [rect(pg, BAR_BUTTON, i) for i in range(3)]
        assert [pg.locator(BAR_BUTTON).nth(i).inner_text().strip() for i in range(3)] == list(LABELS)  # the label is the accessible name
        assert max(b["cy"] for b in boxes) - min(b["cy"] for b in boxes) <= 2, boxes  # side by side, not stacked
        assert all(b["h"] >= 44 and b["w"] >= 44 for b in boxes), boxes  # finger-sized
        assert all(0 <= b["x"] and b["right"] <= b["vw"] for b in boxes), boxes  # inside the phone
        assert pg.evaluate("document.scrollingElement.scrollWidth") <= size[0]  # no sideways scroll
        bar = rect(pg, BAR)
        assert 0 < bar["vh"] - bar["bottom"] <= 16 and bar["h"] <= 60, bar  # floating just above the bottom edge
        # Docked: it does not move when the page scrolls.
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(300)
        after = rect(pg, BAR)
        assert abs(after["y"] - bar["y"]) <= 1 and after["h"] == bar["h"], (bar, after)
        # The brand did not move down: the bar is out of the flow (its zero-height wrapper must not add a gap).
        pg.evaluate(f"{MAIN}.scrollTo(0, 0)")
        assert rect(pg, ".brand")["y"] < 30
        shot(pg, f"bar_{screen}_{size[0]}")
    finally:
        ctx.close()


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("screen", ["welcome", "results"])
def test_the_end_of_the_page_is_not_hidden_behind_the_bar(browser, server, size, screen):
    ctx, pg = new_page(browser, server, size)
    try:
        on_screen(pg, screen)
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(400)
        bar = rect(pg, BAR)
        foot = rect(pg, ".brand-foot")
        assert foot and foot["bottom"] <= bar["y"] + 1, (foot, bar)  # the last line sits above the bar
        main_button = pg.get_by_role("button", name="Analyze my supplement" if screen == "welcome" else "Scan another supplement")
        main_button.scroll_into_view_if_needed()
        box = main_button.bounding_box()
        assert box["y"] + box["height"] <= bar["y"] + 1 or box["y"] >= bar["y"] + bar["h"], (box, bar)
        main_button.click()  # not intercepted by the bar
        pg.get_by_role("dialog").wait_for(timeout=10000)
    finally:
        ctx.close()


def test_the_bar_hides_while_a_field_is_being_typed_in(page):
    page.get_by_role("button", name="Try it with a sample label").wait_for()
    assert page.locator(BAR).is_visible()
    page.locator('[class~="st-key-appbar_guide"] button').focus()
    assert page.locator(BAR).is_visible()  # a focused button is no keyboard
    page.evaluate("() => { const i = document.createElement('input'); i.id = 'probe'; document.querySelector('[data-testid=stMain]').appendChild(i); i.focus(); }")
    page.wait_for_timeout(200)
    assert not page.locator(BAR).is_visible()  # the on-screen keyboard would put it over the field
    page.evaluate("document.getElementById('probe').blur()")
    page.wait_for_timeout(200)
    assert page.locator(BAR).is_visible()


def active_kind(page) -> str:
    return page.evaluate("() => { const a = document.activeElement; return a ? a.tagName + ':' + (a.getAttribute('role') || a.type || '') : ''; }")


def test_the_bar_stays_after_a_toggle_a_dropdown_and_a_radio_took_the_focus(page):
    """These widgets keep the focus after a tap but open no keyboard: only a text field hides the bar."""
    page.get_by_role("button", name="Try it with a sample label").wait_for()
    page.get_by_text("Pregnant or breastfeeding").tap()
    settle(page)
    assert active_kind(page).startswith("INPUT"), active_kind(page)
    assert page.locator(BAR).is_visible()
    start_sample(page)
    choose_option(page, 1)
    assert active_kind(page) == "INPUT:combobox", active_kind(page)
    assert page.locator(BAR).is_visible()
    name = card_name(page)
    card(page).locator("#btnRepl").click()  # one food swapped, so the Meals tab has its radio
    wait_name_change(page, name)
    finish_all_cards(page)
    page.get_by_role("tab", name="🍽️ Meals").click()
    settle(page)
    page.get_by_text("2 meals", exact=True).tap()
    settle(page)
    assert active_kind(page) == "INPUT:radio", active_kind(page)
    assert page.locator(BAR).is_visible()


def test_the_bar_hides_for_the_chat_box_of_the_cards_popover_and_is_dead_while_it_answers(ai_server, page):
    """The popover's body is mounted outside the app root: the field and the answer's spinner are there, not in the page."""
    url, fake = ai_server
    fake.stream_script = [{"delay": 5, "text": "Zinc supports immune function."}]
    page.goto(url, wait_until="networkidle")
    start_sample(page)
    page.get_by_role("button", name="Ask AI").first.click()
    body = page.locator('[data-testid="stPopoverBody"]')
    body.wait_for(timeout=10000)
    chat = body.get_by_test_id("stChatInput").locator("textarea")
    chat.fill("What does zinc do?")
    page.wait_for_timeout(200)
    assert not page.locator(BAR).is_visible()  # typing there: the keyboard would cover the bar
    chat.press("Enter")
    page.locator('[data-testid="stSpinner"]').first.wait_for(timeout=10000)
    assert page.evaluate("([s]) => getComputedStyle(document.querySelector(s)).pointerEvents", [BAR]) == "none"
    body.get_by_text("Zinc supports immune function.").first.wait_for(timeout=30000)
    settle(page)
    assert page.evaluate("([s]) => getComputedStyle(document.querySelector(s)).pointerEvents", [BAR]) != "none"
    assert fake.stream_calls == 1


# ------------------------------------------------------------------ never over the swipe buttons
@pytest.mark.parametrize("size", [(390, 844), (360, 640), (320, 640)])
def test_the_swipe_buttons_can_always_be_scrolled_clear_of_the_bar_and_tapped(browser, server, size):
    """A phone browser's visible height is ~100-180 px less than the screen, so on short viewports the Keep / Replace row
    may start under any bar: the contract is that the page leaves room to scroll it clear and that a tap works."""
    ctx, pg = new_page(browser, server, size)
    try:
        start_sample(pg)
        for selector in ("#btnKeep", "#btnRepl", "#btnBack"):
            bar = rect(pg, BAR)
            inner = card(pg).locator(selector).bounding_box()  # page coordinates (Playwright adds the frame offset)
            need = inner["y"] + inner["height"] + 8 - bar["y"]  # how far the button reaches into the bar
            if need > 0:
                room = pg.evaluate(f"{MAIN}.scrollHeight - {MAIN}.clientHeight - {MAIN}.scrollTop")
                assert room >= need, (selector, need, room)  # no bottom padding, no way to reach it
                pg.evaluate(f"{MAIN}.scrollBy(0, {need})")
                pg.wait_for_timeout(300)
            inner = card(pg).locator(selector).bounding_box()
            bar = rect(pg, BAR)
            assert inner["y"] + inner["height"] <= bar["y"] + 1, (selector, inner, bar)
        pg.evaluate(f"{MAIN}.scrollTo(0, 0)")
        before = card_name(pg)
        card(pg).locator("#btnKeep").click(timeout=5000)  # Playwright refuses a covered target: it scrolls or fails
        wait_name_change(pg, before)
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(390, 844), (360, 640), (320, 640)])
def test_the_swipe_buttons_are_uncovered_at_the_start_on_a_typical_phone(browser, server, size):
    """On a short screen the page above the card is tightened so the Keep / Replace row of the first card clears the bar at
    first sight, 320 x 640 included."""
    ctx, pg = new_page(browser, server, size)
    try:
        start_sample(pg)
        bar = rect(pg, BAR)
        for selector in ("#btnKeep", "#btnRepl", "#btnBack"):
            inner = card(pg).locator(selector).bounding_box()
            assert inner["y"] + inner["height"] <= bar["y"], (selector, inner, bar)  # fully above the bar without scrolling
    finally:
        ctx.close()


@pytest.mark.parametrize("size, most", [((390, 664), 50), ((360, 640), 90)])
def test_a_taller_card_loses_less_of_its_button_row_to_the_bar_on_a_short_screen(browser, server, size, most):
    """Cards 2-7 carry a long note, so their row starts lower than the first card's (below the fold on the smallest phones
    whatever the bar does): the tightened page keeps what the bar covers of it at first sight to these measured bounds."""
    ctx, pg = new_page(browser, server, size)
    try:
        start_sample(pg)
        name = card_name(pg)
        card(pg).locator("#btnKeep").click()
        wait_name_change(pg, name)
        settle(pg)
        pg.evaluate(f"{MAIN}.scrollTo(0, 0)")
        pg.wait_for_timeout(200)
        bar = rect(pg, BAR)
        row = card(pg).locator("#btnKeep").bounding_box()
        assert row["y"] + row["height"] - bar["y"] <= most, (row, bar)
    finally:
        ctx.close()


# ------------------------------------------------------------------ the Streamlit Cloud badge
@pytest.mark.parametrize("size", SIZES + [(412, 915), (600, 900)])
def test_the_bar_clears_a_simulated_manage_app_badge(browser, server, size):
    """Cloud draws a ~50 px tall, ~15 % wide pill at the very bottom-right above everything (owner only, so no test sees the real one)."""
    ctx, pg = new_page(browser, server, size)
    try:
        pg.evaluate(
            "() => { const b = document.createElement('div'); b.id = 'fake-badge';"
            " b.style.cssText = 'position:fixed;right:0;bottom:0;width:15vw;height:50px;z-index:2147483647;background:red';"
            " document.body.appendChild(b); }"
        )
        badge = rect(pg, "#fake-badge")
        bar = rect(pg, BAR)
        assert bar["right"] <= badge["x"] - 8 or bar["bottom"] <= badge["y"], (bar, badge)  # the whole pill, with air
        for i in range(3):
            box = rect(pg, BAR_BUTTON, i)
            assert top_is_bar(pg, box["cx"], box["cy"]), i  # what is under the finger is the button itself, not the badge
    finally:
        ctx.close()


# ------------------------------------------------------------------ the three sheets
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("screen", ["welcome", "card", "results"])
@pytest.mark.parametrize("index, title", [(0, "Athlete RDA guide"), (1, "Recent scans"), (2, "About & privacy")])
def test_each_sheet_opens_fits_the_phone_and_closes_without_touching_the_page(browser, server, size, screen, index, title):
    ctx, pg = new_page(browser, server, size)
    try:
        on_screen(pg, screen)
        name_before = card_name(pg) if screen == "card" else ""
        button = bar_button(pg, index)
        button.tap()
        dialog = pg.locator(DIALOG)
        dialog.wait_for(timeout=10000)
        settle(pg)
        assert dialog.get_by_text(title).count() >= 1
        box = pg.evaluate(
            "() => { const r = document.querySelector('[data-testid=stDialog] [role=dialog]').getBoundingClientRect();"
            " return {x: r.x, right: r.right, y: r.y, bottom: r.bottom, vw: innerWidth, vh: innerHeight, page: document.scrollingElement.scrollWidth}; }"
        )
        assert box["x"] >= 0 and box["right"] <= box["vw"] and box["page"] <= box["vw"], box
        assert box["bottom"] <= box["vh"] + 1 and box["y"] >= 40, box  # a sheet under the top edge (the scrim above it is the way out)
        close = dialog.get_by_role("button", name="Close", exact=True).bounding_box()
        assert close["width"] >= 44 and close["height"] >= 44, close  # the X is a finger-sized target
        shot(pg, f"sheet_{index}_{screen}_{size[0]}")
        pg.keyboard.press("Escape")
        settle(pg)
        assert pg.locator(DIALOG).count() == 0
        pg.wait_for_function("document.activeElement && document.activeElement.closest('[class~=\"st-key-appbar\"]') !== null", timeout=5000)  # focus is back on the bar
        # The flag is cleared by the dismiss: the next run (a swipe, a tab) does not bring the sheet back ...
        if screen == "card":
            card(pg).locator("#btnKeep").click()
            wait_name_change(pg, name_before)
            settle(pg)
        elif screen == "results":
            pg.get_by_role("tab", name="🍽️ Meals").click()
            settle(pg)
        assert pg.locator(DIALOG).count() == 0
        # ... and the same button opens it again.
        bar_button(pg, index).tap()
        pg.locator(DIALOG).wait_for(timeout=10000)
    finally:
        ctx.close()


@pytest.mark.parametrize("size", SIZES)
def test_the_guide_scrolls_inside_the_sheet_and_keeps_its_title_and_x(browser, server, size):
    ctx, pg = new_page(browser, server, size)
    try:
        bar_button(pg, 0).tap()
        dialog = pg.locator(DIALOG)
        dialog.get_by_text("All nutrients").wait_for(timeout=10000)
        settle(pg)
        height = pg.evaluate("document.querySelector('[data-testid=stDialog] [role=dialog] > div:last-child').scrollHeight")
        assert height > 2000  # all 31 nutrients
        pg.evaluate("() => { const b = document.querySelector('[data-testid=stDialog] [role=dialog] > div:last-child'); b.scrollTop = b.scrollHeight; }")
        pg.wait_for_timeout(300)
        close = dialog.get_by_role("button", name="Close", exact=True).bounding_box()
        assert 0 <= close["y"] < 120, close  # still in view at the end of the list
        last = dialog.get_by_text("Omega-3 ALA").first.bounding_box()
        assert last["y"] + last["height"] <= size[1] - 56, last  # the last row can leave the corner of the Cloud badge
        shot(pg, f"guide_end_{size[0]}")
    finally:
        ctx.close()


def test_the_guide_shows_the_scan_first_with_bars_and_no_sideways_scroll(browser, server):
    ctx, pg = new_page(browser, server, (320, 640))
    try:
        start_sample(pg)
        bar_button(pg, 0).tap()
        dialog = pg.locator(DIALOG)
        dialog.get_by_text("In your scan").wait_for(timeout=10000)
        settle(pg)
        first = dialog.locator("ul.gd-list").first
        assert "Vitamin C" in first.inner_text() and first.locator(".gd-bar").count() >= 5
        track = first.locator(".gd-bar").first.bounding_box()
        assert track["width"] > 150  # readable at 320
        assert pg.evaluate("document.querySelector('[data-testid=stDialog] [role=dialog]').scrollWidth") <= 320
        shot(pg, "guide_scan_320")
    finally:
        ctx.close()


def test_the_page_behind_a_sheet_is_not_reloaded(page):
    """The swipe iframe must survive opening and closing a sheet (a reload would lose the card's drag state)."""
    start_sample(page)
    page.frame(url=lambda u: "tinder_swipe" in u).evaluate("window.__kept = true")
    bar_button(page, 0).click()
    page.locator(DIALOG).wait_for(timeout=10000)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    assert page.frame(url=lambda u: "tinder_swipe" in u).evaluate("window.__kept === true")


def test_the_bar_is_behind_an_open_sheet_and_cannot_open_a_second_one(page):
    start_sample(page)
    bar_button(page, 0).click()
    page.locator(DIALOG).wait_for(timeout=10000)
    settle(page)
    box = rect(page, BAR_BUTTON, 1)
    assert page.evaluate("([x, y]) => !!document.elementFromPoint(x, y).closest('[data-testid=stDialog]')", [box["cx"], box["cy"]])  # the scrim is on top
    with pytest.raises(Exception):
        bar_button(page, 1).click(timeout=1500)  # Playwright refuses a covered target
    bar_button(page, 1).dispatch_event("click")  # but a stray click event must not give two dialogs or an exception box
    page.wait_for_timeout(1500)
    assert page.locator(DIALOG).count() == 1
    assert page.locator('[data-testid="stException"]').count() == 0


def test_no_popover_is_left_at_the_bottom_of_any_screen(page):
    assert page.locator('[data-testid="stPopover"]').count() == 0  # welcome
    start_sample(page)
    assert page.locator('[data-testid="stPopover"]').count() == 0  # cards (the AI is off on this server: no "Ask AI" popover)
    finish_all_cards(page)
    assert page.locator('[data-testid="stPopover"]').count() == 0  # results
    assert page.get_by_role("button", name="🏃 Athlete RDA guide").count() == 0


# ------------------------------------------------------------------ the item window points to the guide
def test_the_item_window_opens_the_guide_at_its_nutrient(page):
    _mixed_results(page)
    page.locator('[class*="st-key-planbtn_food_"] button').first.click()
    dialog = page.locator(DIALOG)
    dialog.wait_for(timeout=10000)
    settle(page)
    dialog.get_by_text("Athlete targets").click()
    dialog.get_by_role("button", name="Open the Athlete guide").click()
    settle(page, 1.2)
    assert page.locator(DIALOG).count() == 1 and page.locator(DIALOG).get_by_text("Athlete RDA guide").count() >= 1
    assert page.locator(DIALOG).locator("li[data-focus]").count() == 1  # its row is marked
    page.keyboard.press("Escape")
    settle(page)
    assert page.locator(DIALOG).count() == 0  # closing the guide lands on the results, not back in the window
    results_heading(page).wait_for(timeout=5000)


# ------------------------------------------------------------------ a tap on the athlete line of a card
def test_a_tap_on_the_athlete_line_opens_the_guide_without_swiping(page):
    start_sample(page)
    name = card_name(page)
    line = card(page).locator("button.pl-guide")
    line.wait_for(timeout=10000)
    box = line.bounding_box()
    assert box["height"] >= 32
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)  # a mouse: the card holds pointer capture
    page.locator(DIALOG).wait_for(timeout=10000)
    settle(page)
    assert page.locator(DIALOG).locator("li[data-focus]").count() == 1
    page.keyboard.press("Escape")
    settle(page)
    assert card_name(page) == name and "Card 1 of" in card(page).locator("#card .count").inner_text()  # still the same card, nothing decided
    line.focus()
    page.keyboard.press("Enter")  # keyboard
    page.locator(DIALOG).wait_for(timeout=10000)
    page.keyboard.press("Escape")
    settle(page)
    assert card_name(page) == name


def test_a_drag_that_starts_on_the_athlete_line_still_swipes_the_card(page):
    start_sample(page)
    name = card_name(page)
    box = card(page).locator("button.pl-guide").bounding_box()
    x, y = box["x"] + 40, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 12):
        page.mouse.move(x - 20 * step, y + step)
    page.mouse.up()
    wait_name_change(page, name)
    assert page.locator(DIALOG).count() == 0


# ------------------------------------------------------------------ Recent scans in the browser
def test_recent_scans_shows_a_finished_scan_then_clears_it_after_a_question_and_survives_a_reload(page):
    bar_button(page, 1).click()
    page.locator(DIALOG).get_by_text("No scans yet").wait_for(timeout=10000)  # empty state, still a button
    page.keyboard.press("Escape")
    settle(page)
    start_own_label(page)
    finish_all_cards(page)
    settle(page, 1.2)
    assert page.evaluate("localStorage.getItem('suppswipe_scan_history_v1')")
    page.reload(wait_until="networkidle")
    bar_button(page, 1).click()  # tapped right after the load: the stored history arrives a moment later and must show up
    dialog = page.locator(DIALOG)
    dialog.locator(".sc-card").first.wait_for(timeout=15000)
    dialog.get_by_text("See your choices").first.click()
    assert dialog.locator(".sc-li").count() >= 1
    dialog.get_by_role("button", name="Clear history").click()
    dialog.get_by_text("Delete all 1 saved scan?").wait_for(timeout=5000)
    dialog.get_by_role("button", name="Keep them").click()
    assert page.evaluate("localStorage.getItem('suppswipe_scan_history_v1')")  # nothing deleted yet
    dialog.get_by_role("button", name="Clear history").click()
    dialog.get_by_role("button", name="Delete", exact=True).click()
    settle(page, 1.5)
    assert page.evaluate("localStorage.getItem('suppswipe_scan_history_v1')") is None
    assert page.evaluate("localStorage.getItem('suppswipe_current_scan_v1')") is None
    page.locator(DIALOG).get_by_text("No scans yet").wait_for(timeout=10000)


# ------------------------------------------------------------------ the analysing screen
def test_the_bar_is_dead_while_a_photo_is_analysed_and_alive_again_after_an_error(ai_server, page, tmp_path):
    url, fake = ai_server
    fake.stream_script = [{"delay": 6, "text": "Supplement Facts\nVitamin C 90 mg\nZinc 11 mg"}]
    from PIL import Image

    photo = tmp_path / "label.jpg"
    Image.new("RGB", (640, 480), "white").save(photo, "JPEG")
    page.goto(url, wait_until="networkidle")
    page.get_by_role("button", name="Analyze my supplement").click()
    dialog = page.get_by_role("dialog")
    dialog.locator("button", has_text="Upload").click()
    settle(page)
    dialog.locator('input[type="file"]').set_input_files(str(photo))
    page.locator(".analyze-loading-wrap").first.wait_for(timeout=20000)
    assert page.locator(f"{BUSY} button").count() == 3 and page.locator(BAR).count() == 0  # present on the analysing screen too ...
    assert page.locator(f"{BUSY} button:disabled").count() == 3  # ... but dead: a tap would interrupt the run and start the OCR again
    started = len(fake.requests)
    page.locator(f"{BUSY} button").first.dispatch_event("click")  # even a stray click event does nothing
    card(page).locator("#card .name").wait_for(timeout=60000)
    assert len(fake.requests) - started <= 2  # no second OCR conversation
    assert page.locator(BUSY).count() == 0 and page.locator(f"{BAR} button:disabled").count() == 0  # live again on the cards
    assert page.locator(DIALOG).count() == 0


def test_the_bar_is_alive_again_when_an_analysis_ends_in_an_error(page):
    page.get_by_role("button", name="Analyze my supplement").click()
    dialog = page.get_by_role("dialog")
    dialog.locator("button", has_text="Paste").click()
    settle(page)
    dialog.locator("textarea").fill("hello world asdf qwerty")
    dialog.get_by_role("button", name="Analyze").click()
    page.locator('[data-testid="stAlert"]').first.wait_for(timeout=30000)
    settle(page)
    assert page.locator(BUSY).count() == 0 and page.locator(f"{BAR} button").count() == 3
    assert page.locator(f"{BAR} button:disabled").count() == 0
    bar_button(page, 2).tap()
    page.locator(DIALOG).wait_for(timeout=10000)


# ------------------------------------------------------------------ every results tab, and the chat box
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("tab", ["🥗 Plan", "🍽️ Meals", "🛒 Shopping", "💬 Ask AI", "📤 Share"])
def test_the_bar_stays_and_the_end_of_every_results_tab_is_clear_of_it(browser, server, size, tab):
    ctx, pg = new_page(browser, server, size)
    try:
        _mixed_results(pg)
        pg.get_by_role("tab", name=tab).click()
        settle(pg)
        assert pg.locator(BAR_BUTTON).count() == 3  # tabs do not rerun the page: the bar is the same one
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(400)
        bar = rect(pg, BAR)
        foot = rect(pg, ".brand-foot")
        assert foot and foot["bottom"] <= bar["y"] + 1, (tab, foot, bar)
        box = rect(pg, BAR_BUTTON, 1)
        assert top_is_bar(pg, box["cx"], box["cy"])
        assert pg.evaluate("document.scrollingElement.scrollWidth") <= size[0]
    finally:
        ctx.close()


def test_the_ask_ai_chat_box_is_not_under_the_bar(ai_server, page):
    """Streamlit pins a chat_input that sits at the top level of the page to the bottom; this one is inside a tab, so it must
    stay in the flow (and reachable above the bar) - the AI-on server is the only place that draws it."""
    url, _fake = ai_server
    page.goto(url, wait_until="networkidle")
    _mixed_results(page)
    page.get_by_role("tab", name="💬 Ask AI").click()
    box = page.get_by_test_id("stChatInput")
    box.wait_for(timeout=10000)
    assert page.evaluate("e => getComputedStyle(e).position", box.element_handle()) not in ("fixed", "sticky")
    page.evaluate(f"{MAIN}.scrollTo(0, 100000)")
    page.wait_for_timeout(300)
    r = box.bounding_box()
    bar = rect(page, BAR)
    assert r["y"] + r["height"] <= bar["y"] + 1, (r, bar)
    box.locator("textarea").click(timeout=5000)  # a real tap lands on the box, not on the bar


# ------------------------------------------------------------------ geometry, the long Ask AI run, desktop, the tab
@pytest.mark.parametrize("size", [(390, 844), (360, 640), (320, 640)])
def test_the_bar_is_a_centred_pill_with_room_for_the_badge_corner_on_both_sides(browser, server, size):
    ctx, pg = new_page(browser, server, size)
    try:
        bar = rect(pg, BAR)
        assert abs(bar["x"] - (bar["vw"] - bar["right"])) <= 1, bar  # centred
        assert bar["x"] >= 0.15 * bar["vw"] + 11, bar  # 15 % badge width + 12 px, on both sides
        widths = [round(rect(pg, BAR_BUTTON, i)["w"]) for i in range(3)]
        assert max(widths) - min(widths) <= 1, widths  # equal thirds
    finally:
        ctx.close()


def test_the_bar_adds_no_gap_above_the_page(page):
    """The bar is drawn right after the header and its layout wrapper takes back the 1rem gap a hidden element would add:
    without that rule the whole page sits 16 px lower on every screen."""
    brand = rect(page, ".brand")
    hero = rect(page, ".hero")
    assert brand["y"] < 30, brand
    assert hero["y"] - (brand["y"] + brand["h"]) <= 12, (brand, hero)


def test_the_ask_ai_bar_is_inert_while_the_answer_is_written(ai_server, page):
    """An answer is written inside one script run: a tap on the bar would rerun the script and lose it."""
    url, fake = ai_server
    fake.stream_script = [{"delay": 5, "text": "Zinc supports immune function."}]
    page.goto(url, wait_until="networkidle")
    _mixed_results(page)
    page.get_by_role("tab", name="💬 Ask AI").click()
    chat = page.get_by_test_id("stChatInput").locator("textarea")
    chat.fill("What does zinc do?")
    chat.press("Enter")
    page.locator('[data-testid="stSpinner"]').first.wait_for(timeout=10000)
    assert page.evaluate("([s]) => getComputedStyle(document.querySelector(s)).pointerEvents", [BAR]) == "none"
    page.get_by_text("Zinc supports immune function.").first.wait_for(timeout=30000)
    settle(page)
    assert page.evaluate("([s]) => getComputedStyle(document.querySelector(s)).pointerEvents", [BAR]) != "none"


@pytest.mark.parametrize("size", SIZES + [(1000, 700)])  # the last one is a desktop window: a centred card, not a bottom sheet
def test_the_guide_scrolls_inside_its_sheet_and_keeps_the_title_and_the_close_button(browser, server, size):
    ctx, pg = new_page(browser, server, size)
    try:
        bar_button(pg, 0).click()
        dialog = pg.locator(DIALOG)
        dialog.wait_for(timeout=10000)
        settle(pg)
        info = pg.evaluate(
            "() => { const d = document.querySelector('[data-testid=stDialog] [role=dialog]'); const body = d.lastElementChild;"
            " const r = d.getBoundingClientRect(); body.scrollTop = body.scrollHeight;"
            " return {top: r.top, bottom: r.bottom, vh: innerHeight, scrolls: body.scrollHeight > body.clientHeight + 200, at_end: body.scrollTop > 0,"
            " outer: document.querySelector('[data-testid=stDialog]').scrollTop}; }"
        )
        assert info["scrolls"] and info["at_end"] and info["outer"] == 0, info  # the body scrolls, the sheet itself stays put
        assert info["top"] >= 0 and info["bottom"] <= info["vh"] + 1, info  # the whole sheet fits the screen
        close = dialog.get_by_role("button", name="Close", exact=True).bounding_box()
        assert 0 <= close["y"] and close["y"] + close["height"] <= info["vh"], close  # the X is still there at the end of the list
        assert dialog.get_by_role("heading", name="Athlete RDA guide").is_visible()
        last = dialog.locator(".gd-fine").last.bounding_box()
        # The last line clears Cloud's 50 px badge (it floats above dialogs): 72 px of padding, less the 16 px Streamlit pulls a markdown block up.
        assert last["y"] + last["height"] <= info["bottom"] - 50, (last, info)
    finally:
        ctx.close()


@pytest.mark.parametrize("tab", ["🛒 Shopping", "💬 Ask AI", "📤 Share"])
def test_opening_and_closing_a_sheet_keeps_the_selected_results_tab(page, tab):
    _mixed_results(page)
    page.get_by_role("tab", name=tab).click()
    settle(page)
    bar_button(page, 0).click()
    page.locator(DIALOG).wait_for(timeout=10000)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    assert page.get_by_role("tab", name=tab).get_attribute("aria-selected") == "true"


def test_the_primary_analyze_button_is_above_the_bar_on_the_smallest_phone(browser, server):
    ctx, pg = new_page(browser, server, (320, 568))
    try:
        button = pg.get_by_role("button", name="Analyze my supplement")
        button.wait_for(timeout=10000)
        box = button.bounding_box()
        bar = rect(pg, BAR)
        assert box["y"] + box["height"] <= bar["y"], (box, bar)  # measured 385 against 511 when the bar was built
    finally:
        ctx.close()


def test_the_delete_button_of_the_clear_history_question_is_red(page):
    start_own_label(page)
    finish_all_cards(page)
    settle(page, 1.2)
    bar_button(page, 1).click()
    dialog = page.locator(DIALOG)
    dialog.get_by_role("button", name="Clear history").click()
    delete = dialog.get_by_role("button", name="Delete", exact=True)
    delete.wait_for(timeout=5000)
    assert page.evaluate("e => getComputedStyle(e).backgroundColor", delete.element_handle()) == "rgb(185, 28, 28)"


# ------------------------------------------------------------------ keyboard: the sheets scroll, the bar is never remounted
@pytest.mark.parametrize("index, title", [(0, "Athlete RDA guide"), (2, "About & privacy")])
def test_a_sheet_can_be_scrolled_with_the_keyboard(browser, server, index, title):
    """Streamlit focuses the dialog and its focus trap leaves Tab only the X: arrow keys scroll just the ancestors of the focused
    element, so the sheet's first block is a Tab stop inside the scroller."""
    ctx, pg = new_page(browser, server, (390, 640))
    try:
        button = bar_button(pg, index)
        button.focus()
        pg.keyboard.press("Enter")
        pg.locator(DIALOG).wait_for(timeout=10000)
        settle(pg)
        scroller = "document.querySelector('[data-testid=stDialog] [role=dialog] > div:last-child')"
        assert pg.evaluate(f"{scroller}.scrollHeight > {scroller}.clientHeight + 200")  # there is something below the fold
        for _ in range(4):
            pg.keyboard.press("Tab")
            if pg.evaluate("document.activeElement.matches('[tabindex=\"0\"]') && !!document.activeElement.closest('[role=dialog]')"):
                break
        assert pg.evaluate("document.activeElement.matches('[tabindex=\"0\"]')"), active_kind(pg)
        pg.keyboard.press("PageDown")
        pg.wait_for_timeout(300)
        assert pg.evaluate(f"{scroller}.scrollTop") > 100
        pg.keyboard.press("End")
        pg.wait_for_timeout(300)
        assert pg.evaluate(f"{scroller}.scrollTop + {scroller}.clientHeight >= {scroller}.scrollHeight - 2")  # the last card is reachable
    finally:
        ctx.close()


def test_opening_and_closing_sheets_never_remounts_the_bar_and_gives_the_focus_back(browser, server):
    """A remounted bar drops the focused button (a closing dialog can then not return the focus) and may blink for a frame:
    the bar is one plain container that every run draws again unchanged."""
    ctx, pg = new_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        pg.evaluate(
            "() => { window.__removed = 0; new MutationObserver(ms => { for (const m of ms) for (const n of m.removedNodes)"
            " if (n.nodeType === 1 && (n.matches('[class~=\"st-key-appbar\"]') || n.querySelector('[class~=\"st-key-appbar\"]'))) window.__removed++; })"
            ".observe(document.body, {childList: true, subtree: true}); }"
        )
        for rep in range(6):
            index = rep % 3
            bar_button(pg, index).focus()
            pg.keyboard.press("Space" if rep % 2 else "Enter")
            pg.locator(DIALOG).wait_for(timeout=10000)
            settle(pg, 0.4)
            pg.keyboard.press("Escape")
            pg.locator(DIALOG).wait_for(state="detached", timeout=10000)
            pg.wait_for_timeout(400)
            assert pg.evaluate(f"document.activeElement === document.querySelectorAll('{BAR_BUTTON}')[{index}]"), (rep, active_kind(pg))
        assert pg.evaluate("window.__removed") == 0
    finally:
        ctx.close()


def test_the_clear_history_question_is_announced(page):
    start_own_label(page)
    finish_all_cards(page)
    settle(page, 1.2)
    bar_button(page, 1).click()
    dialog = page.locator(DIALOG)
    dialog.get_by_role("button", name="Clear history").click()
    dialog.locator("[role=alert]").get_by_text("Delete all 1 saved scan?").wait_for(timeout=5000)

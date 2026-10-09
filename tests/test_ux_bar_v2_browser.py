"""Browser checks of bar v2 (Guide | Diet | Scan | Recent | About): what AppTest cannot see.

Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers are reused: the five tabs beside the
simulated Cloud badge, labels on one line, the welcome screen that must not scroll, the Scan and Diet sheets, and the safety
regression (Resume must never switch a filter off, and the Diet sheet must show the filter that is really on)."""
from __future__ import annotations

import time

import pytest

from test_ux_browser import (  # noqa: F401  (fixtures are used by name)
    CARD, DIALOG, _active_chips, _mixed_results, ai_server, browser, card, card_name, choose_diet, close_sheet, finish_all_cards, food_options,
    open_diet_sheet, open_scan_sheet, page, results_heading, server, settle, shot, start_own_label, start_sample, wait_filter_chip, wait_name_change,
)

BAR = '[class~="st-key-appbar"]'
BUSY = '[class~="st-key-appbar_busy"]'
BAR_BUTTON = '[class~="st-key-appbar"] button'
LABELS = ["Guide", "Diet", "Scan", "Recent", "About"]
MAIN = "document.querySelector('[data-testid=stMain]')"
WELCOME_SIZES = [(320, 568), (320, 640), (360, 640), (390, 844), (412, 915)]
BADGE = (
    "() => { const b = document.createElement('div'); b.id = 'fake-badge';"
    " b.style.cssText = 'position:fixed;right:0;bottom:0;width:15vw;height:50px;z-index:2147483647;background:red';"
    " document.body.appendChild(b); }"
)


def rect(page, selector: str, index: int = 0) -> dict | None:
    return page.evaluate(
        "([s, i]) => { const e = document.querySelectorAll(s)[i]; if (!e) return null; const r = e.getBoundingClientRect();"
        " return {x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom, cx: r.x + r.width / 2,"
        " cy: r.y + r.height / 2, vw: innerWidth, vh: innerHeight}; }",
        [selector, index],
    )


def new_page(browser, server, size):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(server, wait_until="networkidle")
    pg.locator(BAR_BUTTON).nth(4).wait_for(timeout=15000)
    return ctx, pg


def item(page, name: str):
    return page.locator(f'[class~="st-key-appbar_{name}"] button')


def top_is_bar(pg, x: float, y: float) -> bool:
    return pg.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return !!(e && e.closest('[class~=\"st-key-appbar\"]')); }", [x, y])


def scroll_state(pg) -> dict:
    return pg.evaluate(
        "() => { const m = document.querySelector('[data-testid=stMain]'); return {main: m.scrollHeight - m.clientHeight,"
        " doc: document.scrollingElement.scrollHeight - innerHeight, width: document.scrollingElement.scrollWidth - innerWidth}; }"
    )


def watch_toasts(pg) -> None:
    """Record the text of every toast that appears from now on in window.__toasts (a toast is gone after about 4 s)."""
    pg.evaluate(
        "() => { window.__toasts = []; const seen = () => document.querySelectorAll('[data-testid=stToast]').forEach(t => {"
        " const x = t.innerText; if (x && !window.__toasts.includes(x)) window.__toasts.push(x); });"
        " new MutationObserver(seen).observe(document.body, {childList: true, subtree: true, characterData: true}); seen(); }"
    )


def reload_and_wait_for_the_stored_scan(pg) -> None:
    pg.reload(wait_until="networkidle")
    pg.locator(BAR_BUTTON).nth(4).wait_for(timeout=15000)
    settle(pg, 1.0)


# ------------------------------------------------------------------ geometry: five finger-sized tabs beside the badge
@pytest.mark.parametrize("size", [(320, 568), (320, 640), (360, 640), (375, 667), (390, 844), (412, 915)])
def test_five_tabs_of_at_least_44_px_sit_beside_the_simulated_badge_and_each_hits_its_own_button(browser, server, size):
    ctx, pg = new_page(browser, server, size)
    try:
        pg.evaluate(BADGE)
        badge, bar = rect(pg, "#fake-badge"), rect(pg, BAR)
        assert bar["right"] <= badge["x"] - 11.5, (bar, badge)  # the badge's width and the 12 px gap
        assert abs(bar["x"] - 16) <= 1, bar  # flush with the page gutter on the left
        boxes = [rect(pg, BAR_BUTTON, i) for i in range(5)]
        assert [pg.locator(BAR_BUTTON).nth(i).inner_text().strip() for i in range(5)] == LABELS
        assert all(b["w"] >= 44 and b["h"] >= 44 for b in boxes), [(round(b["w"], 1), round(b["h"], 1)) for b in boxes]
        assert max(b["cy"] for b in boxes) - min(b["cy"] for b in boxes) <= 2  # one row
        assert all(abs(b["w"] - boxes[0]["w"]) <= 1 for b in boxes)  # equal tabs
        for i, box in enumerate(boxes):
            assert top_is_bar(pg, box["cx"], box["cy"]), i  # under the finger is the tab, not the badge
        # Scan is the one filled tab.
        colours = pg.evaluate("() => [...document.querySelectorAll('[class~=\"st-key-appbar\"] button')].map(b => getComputedStyle(b).backgroundColor)")
        assert colours[2] == "rgb(4, 120, 87)" and all(c in ("rgba(0, 0, 0, 0)", "transparent") for i, c in enumerate(colours) if i != 2), colours
        shot(pg, f"bar_v2_{size[0]}x{size[1]}")
    finally:
        ctx.close()


@pytest.mark.parametrize("percent", [100, 150, 200])
@pytest.mark.parametrize("size", [(320, 568), (320, 640), (360, 640), (390, 844), (412, 915)])
def test_labels_are_on_one_line_and_inside_their_tab_at_100_150_and_200_percent_text(browser, server, size, percent):
    ctx, pg = new_page(browser, server, size)
    try:
        if percent != 100:
            pg.add_style_tag(content=f"html {{ font-size: {percent}% !important; }}")
            pg.wait_for_timeout(400)
        info = pg.evaluate(
            "() => [...document.querySelectorAll('[class~=\"st-key-appbar\"] button')].map(b => { const p = b.querySelector('p');"
            " const r = document.createRange(); r.selectNodeContents(p); const t = r.getBoundingClientRect(), k = b.getBoundingClientRect();"
            " return {lines: r.getClientRects().length, inside: t.left >= k.left - 0.5 && t.right <= k.right + 0.5, w: k.width}; })"
        )
        assert all(i["lines"] == 1 and i["inside"] for i in info), info
        assert min(i["w"] for i in info) >= 44, info  # the tabs stay finger-sized at larger text too (the left edge does not move with the text)
        assert scroll_state(pg)["width"] <= 0
    finally:
        ctx.close()


# ------------------------------------------------------------------ the welcome screen: the hero card, one hint, the bar
@pytest.mark.parametrize("size", WELCOME_SIZES)
def test_the_welcome_screen_needs_no_scrolling_and_nothing_is_under_the_bar(browser, server, size):
    ctx, pg = new_page(browser, server, size)
    try:
        state = scroll_state(pg)
        assert state["main"] <= 1 and state["doc"] <= 1 and state["width"] <= 0, state  # not even an empty strip to scroll
        bar = rect(pg, BAR)
        hero = rect(pg, '[class~="st-key-hero_card"]')
        button = rect(pg, '[class~="st-key-hero_card"] button')
        caption = pg.evaluate("() => { const c = [...document.querySelectorAll('[data-testid=stCaptionContainer]')].pop().getBoundingClientRect(); return c.bottom; }")
        assert button and hero["y"] <= button["y"] and button["bottom"] <= hero["bottom"]  # the Scan button is in the card (the hint line is gone)
        assert button["bottom"] <= bar["y"] - 6 and caption <= bar["y"] - 6, (button, caption, bar)  # the button and the disclaimer end clear above the bar
        assert pg.locator(".brand-foot, .hero-hint").count() == 0
        assert pg.locator('[data-testid="stButton"]:not([class*="st-key-appbar"] *)').count() == 1  # the one button of the page is the card's
        shot(pg, f"bar_v2_welcome_{size[0]}x{size[1]}")
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(320, 568), (320, 640), (360, 640)])
def test_the_welcome_screen_still_needs_no_scrolling_with_the_longest_filter_chip(browser, server, size):
    ctx, pg = new_page(browser, server, size)
    try:
        open_diet_sheet(pg)
        pg.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button', has_text="Low-sodium").click()
        pg.get_by_text("Pregnant or breastfeeding").click()
        settle(pg)
        pg.get_by_role("button", name="Done").click()
        pg.locator(".diet-note").wait_for(timeout=10000)
        settle(pg, 1.0)
        assert pg.locator(".diet-note").inner_text().strip() == "🥗 Diet: Low-sodium aware · Pregnancy"
        chip = rect(pg, ".diet-note")
        assert chip["h"] < 40, chip  # one line
        state = scroll_state(pg)
        assert state["main"] <= 1 and state["doc"] <= 1, state
        caption = pg.evaluate("() => [...document.querySelectorAll('[data-testid=stCaptionContainer]')].pop().getBoundingClientRect().bottom")
        assert caption <= rect(pg, BAR)["y"] - 6, (caption, rect(pg, BAR))
    finally:
        ctx.close()


# ------------------------------------------------------------------ the Scan item
@pytest.mark.parametrize("size", [(320, 568), (390, 844)])
def test_the_scan_sheet_offers_the_options_and_resume_only_with_a_saved_scan(browser, server, size):
    ctx, pg = new_page(browser, server, size)
    try:
        open_scan_sheet(pg)
        dialog = pg.locator(DIALOG)
        assert dialog.get_by_role("button", name="Analyze my supplement").count() == 1
        assert dialog.get_by_role("button", name="Try with a sample label").count() == 1
        assert dialog.get_by_role("button", name="Resume last scan").count() == 0  # no saved scan on this device
        close_sheet(pg)
        start_own_label(pg)
        name = card_name(pg)
        card(pg).locator("#btnKeep").click()
        wait_name_change(pg, name)
        settle(pg, 1.2)
        reload_and_wait_for_the_stored_scan(pg)
        open_scan_sheet(pg)
        resume = pg.locator(DIALOG).get_by_role("button", name="Resume last scan")
        resume.wait_for(timeout=20000)
        pg.get_by_text("1 of 4 cards done · saved today").wait_for(timeout=5000)  # what it brings back, and when
        buttons = [pg.locator(DIALOG).get_by_role("button", name=n).bounding_box() for n in ("Analyze my supplement", "Resume last scan", "Try with a sample label")]
        assert [b["y"] for b in buttons] == sorted(b["y"] for b in buttons)  # in this order
        for box in buttons:
            assert box["height"] >= 44 and 0 <= box["x"] and box["x"] + box["width"] <= size[0]
            assert box["y"] + box["height"] <= size[1], (box, size)  # all three in view, no scrolling inside the sheet to find one
        shot(pg, f"bar_v2_scan_sheet_{size[0]}")
    finally:
        ctx.close()


def test_scan_asks_first_in_the_middle_of_a_scan_and_on_the_results_and_the_questions_keep_the_work(page):
    start_sample(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    settle(page)
    item(page, "scan").click()  # half-way through: the question, no sheet
    dialog = page.locator(DIALOG)
    dialog.get_by_text("Start over?").wait_for(timeout=10000)
    assert "Scan a supplement" not in dialog.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    settle(page)
    assert page.locator(DIALOG).count() == 0 and "Card 2 of" in card(page).locator("#card .count").inner_text()
    finish_all_cards(page)
    results_heading(page).wait_for(timeout=10000)
    item(page, "scan").click()  # a finished plan: asks first
    dialog.get_by_text("Scan another supplement?").wait_for(timeout=10000)
    assert "This clears the sample plan on screen. The sample is not saved." in dialog.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    settle(page)
    assert page.locator(DIALOG).count() == 0 and results_heading(page).count() == 1  # the plan is still there
    item(page, "scan").click()
    dialog.get_by_role("button", name="Scan another").click()
    dialog.get_by_text("Analyze my supplement").first.wait_for(timeout=10000)
    assert results_heading(page).count() == 0  # cleared, and the Analyze window is open


# ------------------------------------------------------------------ the Diet item and the safety regressions
def test_the_diet_item_has_a_dot_and_the_page_a_one_line_chip_exactly_while_a_filter_is_on(page):
    dot = "([s]) => { const c = getComputedStyle(document.querySelector(s), '::after'); return [c.content, c.width, c.backgroundColor]; }"
    assert page.evaluate(dot, ['[class~="st-key-appbar_diet"] button'])[0] == "none"
    assert page.locator(".diet-note").count() == 0
    choose_diet(page, "Vegan")
    page.locator(".diet-note").wait_for(timeout=10000)
    assert page.locator(".diet-note").inner_text().strip() == "🥗 Diet: Vegan"
    content, width, colour = page.evaluate(dot, ['[class~="st-key-appbar_diet"] button'])
    assert content == '""' and width == "9px" and colour == "rgb(217, 119, 6)"
    assert page.evaluate(dot, ['[class~="st-key-appbar_guide"] button'])[0] == "none"  # only the Diet item
    choose_diet(page, "No restriction")  # the filter off: chip and dot go with it
    page.wait_for_function("document.querySelectorAll('.diet-note').length === 0", timeout=10000)
    assert page.evaluate(dot, ['[class~="st-key-appbar_diet"] button'])[0] == "none"


def test_resume_never_switches_a_filter_off_and_the_diet_sheet_shows_what_is_really_on(page):
    """The safety regression of bar v2: after Resume the Diet sheet showed "No restriction" and the toggle off while the filter was on
    (Streamlit ignores a value written into the key of a widget that is not on screen), and a filter set before Resume was overwritten."""
    start_own_label(page)  # a real scan: the demo label is never saved
    choose_diet(page, "Vegan")
    open_diet_sheet(page)
    page.get_by_text("Pregnant or breastfeeding").click()
    settle(page)
    close_sheet(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    settle(page, 1.5)
    # A new session: the saved scan comes back from the device with its filter, the page starts with none.
    reload_and_wait_for_the_stored_scan(page)
    assert page.locator(".diet-note").count() == 0
    open_scan_sheet(page)
    page.get_by_text("That scan used: Vegan · Pregnancy").wait_for(timeout=20000)  # said before the tap
    page.get_by_role("button", name="Resume last scan").click()
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page, 1.0)
    assert page.locator(".diet-note").inner_text().strip() == "🥗 Diet: Vegan · Pregnancy"  # the chip: nothing is drawn under the card
    open_diet_sheet(page)
    assert _active_chips(page) == ["Vegan"], _active_chips(page)  # not "No restriction"
    assert page.locator("label", has_text="Pregnant or breastfeeding").locator("input").is_checked()
    assert "Avoids all animal-derived foods" in page.locator(DIALOG).inner_text()  # what it does, in its own words
    shot(page, "bar_v2_diet_sheet_after_resume")
    close_sheet(page)
    # and the same after the next swipe
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    open_diet_sheet(page)
    assert _active_chips(page) == ["Vegan"] and page.locator("label", has_text="Pregnant or breastfeeding").locator("input").is_checked()


def test_resume_keeps_a_filter_set_before_it_and_says_what_the_scan_was_saved_with(page):
    start_own_label(page)  # saved with no filter (a real scan: the demo label is never saved)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    settle(page, 1.5)
    reload_and_wait_for_the_stored_scan(page)
    choose_diet(page, "Nut-free")  # the visitor sets a filter first ...
    page.locator(".diet-note").wait_for(timeout=10000)
    open_scan_sheet(page)
    watch_toasts(page)  # a toast lasts about 4 s: record it instead of racing it
    page.get_by_role("button", name="Resume last scan").click(timeout=20000)  # ... then resumes the scan saved without one
    page.wait_for_function(  # a toast sent from inside the sheet and followed by an app rerun was dropped 4 times in 6: it comes from the full run now
        "() => window.__toasts.some(t => t.includes('Resumed with your filter: Nut-free. That scan was saved with: no filter.'))", timeout=15000)
    card(page).locator("#card .name").wait_for(timeout=20000)
    assert page.locator(".diet-note").inner_text().strip() == "🥗 Diet: Nut-free"  # the filter is still on
    open_diet_sheet(page)
    assert _active_chips(page) == ["Nut-free"]


def test_the_diet_sheet_changes_the_cards_and_the_food_guard_when_it_closes(page):
    start_sample(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    assert "vitamin d" in wait_name_change(page, name).lower()  # card 2: vitamin D has fish and mushrooms
    settle(page)
    before = food_options(page)  # the card's food list is in its Swap food sheet
    assert any("salmon" in o.lower() or "fish" in o.lower() or "trout" in o.lower() for o in before), before
    open_diet_sheet(page)
    page.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button', has_text="Vegan").click()
    settle(page)
    assert page.locator(".diet-note").count() == 0  # the page follows when the sheet closes
    close_sheet(page)
    wait_filter_chip(page, "Diet: Vegan")
    after = food_options(page)
    # The dropdown is virtualised (about 11 rows whatever the list length): compare what is in it, not how many rows it shows.
    assert after and after != before and not any(w in o.lower() for o in after for w in ("salmon", "trout", "fish", "mackerel", "sardine", "tuna")), (before, after)


def test_a_filter_chosen_on_the_results_flags_the_swap_that_no_longer_fits_and_leaves_it_out_of_the_plan(page):
    start_sample(page)
    for _ in range(2):  # card 3 is vitamin B12: replace it with the top (animal) food
        name = card_name(page)
        card(page).locator("#btnKeep").click()
        wait_name_change(page, name)
    settle(page)
    name = card_name(page)
    card(page).locator("#btnRepl").click()
    wait_name_change(page, name)
    finish_all_cards(page)
    results_heading(page).wait_for(timeout=10000)
    assert page.locator('[data-testid="stExpander"]', has_text="Diet:").count() == 0  # no settings under the plan any more
    choose_diet(page, "Vegan")  # on the results page, through the Diet sheet
    flag = page.locator('[data-testid="stButton"] button', has_text="doesn't fit Vegan — tap to choose another")
    flag.first.wait_for(timeout=10000)
    assert page.locator(".diet-note").inner_text().strip() == "🥗 Diet: Vegan"
    page.get_by_role("tab", name="📤 Share").click()
    share = page.locator('[data-testid="stCode"]').first.inner_text()
    assert "Vitamin B12" not in share.split("Kept as a supplement")[0]  # the misfit is not in the shared plan


# ------------------------------------------------------------------ the dead twin
def test_the_dead_twin_has_five_dead_tabs_of_the_same_size_and_keeps_the_chip(ai_server, page, tmp_path):
    url, fake = ai_server
    fake.stream_script = [{"delay": 6, "text": "Supplement Facts\nVitamin C 90 mg\nZinc 11 mg"}]
    from PIL import Image

    photo = tmp_path / "label.jpg"
    Image.new("RGB", (640, 480), "white").save(photo, "JPEG")
    page.goto(url, wait_until="networkidle")
    page.locator(BAR_BUTTON).nth(4).wait_for(timeout=15000)
    choose_diet(page, "Vegan")
    page.locator(".diet-note").wait_for(timeout=10000)
    live = [rect(page, BAR_BUTTON, i) for i in range(5)]
    open_scan_sheet(page)
    page.get_by_role("button", name="Analyze my supplement").click()
    dialog = page.get_by_role("dialog")
    dialog.locator("button", has_text="Upload").click()
    settle(page)
    dialog.locator('input[type="file"]').set_input_files(str(photo))
    page.locator(".analyze-loading-wrap").first.wait_for(timeout=20000)
    assert page.locator(f"{BUSY} button").count() == 5 and page.locator(f"{BUSY} button:disabled").count() == 5
    dead = [rect(page, f"{BUSY} button", i) for i in range(5)]
    for a, b in zip(live, dead):
        assert abs(a["x"] - b["x"]) <= 1 and abs(a["w"] - b["w"]) <= 1 and abs(a["y"] - b["y"]) <= 1 and abs(a["h"] - b["h"]) <= 1, (live, dead)
    assert [page.locator(f"{BUSY} button").nth(i).inner_text().strip() for i in range(5)] == LABELS
    assert page.locator(".diet-note").count() == 1  # the filter is still said in words while it runs
    assert page.evaluate("([s]) => getComputedStyle(document.querySelector(s), '::after').content", [f'{BUSY} [class~="st-key-appbar_busy_diet"] button']) == '""'  # the dot is on the dead Diet tab too
    page.locator(f"{BUSY} button").nth(2).dispatch_event("click")  # a stray click on the dead Scan tab does nothing
    card(page).locator("#card .name").wait_for(timeout=60000)
    assert page.locator(BUSY).count() == 0 and page.locator(f"{BAR} button:disabled").count() == 0


# ------------------------------------------------------------------ the bar never covers content
@pytest.mark.parametrize("size", [(320, 568), (360, 640), (390, 844), (412, 915)])
def test_the_bar_never_covers_the_keep_replace_row_or_the_end_of_the_page(browser, server, size):
    """The cards screen is one fixed screen: its frame ends where the bar begins, so the row is clear of the bar at first sight and the
    end of the page is the frame (the © line closes the results screen only: tests/test_ux_fixed_screen_browser.py)."""
    ctx, pg = new_page(browser, server, size)
    try:
        start_sample(pg)
        for selector in ("#btnKeep", "#btnRepl", "#btnBack"):
            bar = rect(pg, BAR)
            inner = card(pg).locator(selector).bounding_box()
            need = inner["y"] + inner["height"] - bar["y"]  # how far the button reaches into the bar (0 or less: clear)
            if need > 0:  # the page must then leave room to scroll the row clear of the bar
                room = pg.evaluate(f"{MAIN}.scrollHeight - {MAIN}.clientHeight - {MAIN}.scrollTop")
                assert room >= need, (selector, need, room)
                pg.evaluate(f"{MAIN}.scrollBy(0, {need})")
                pg.wait_for_timeout(300)
            inner = card(pg).locator(selector).bounding_box()
            assert inner["y"] + inner["height"] <= rect(pg, BAR)["y"] + 1, (selector, inner)
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(300)
        assert pg.locator(".brand-foot").count() == 0  # nothing under the card, not even the © line
        assert rect(pg, CARD)["bottom"] <= rect(pg, BAR)["y"] + 6, (rect(pg, CARD), rect(pg, BAR))  # the page ends with the frame, at the bar
        pg.evaluate(f"{MAIN}.scrollTo(0, 0)")
        name = card_name(pg)
        card(pg).locator("#btnKeep").click(timeout=5000)  # a real tap: Playwright refuses a covered target
        wait_name_change(pg, name)
    finally:
        ctx.close()


@pytest.mark.parametrize("name, title", [("guide", "Athlete RDA guide"), ("diet", "Diet & pregnancy"), ("scan", "Scan a supplement"),
                                         ("scans", "Recent scans"), ("about", "About & privacy")])
def test_each_of_the_five_tabs_opens_its_own_window_on_the_smallest_phone(browser, server, name, title):
    ctx, pg = new_page(browser, server, (320, 568))
    try:
        item(pg, name).tap()
        dialog = pg.locator(DIALOG)
        dialog.wait_for(timeout=10000)
        settle(pg)
        assert dialog.get_by_role("heading", name=title).is_visible()
        assert pg.locator(DIALOG).count() == 1
        box = pg.evaluate("() => { const r = document.querySelector('[data-testid=stDialog] [role=dialog]').getBoundingClientRect(); return {x: r.x, right: r.right, bottom: r.bottom, vw: innerWidth, vh: innerHeight}; }")
        assert box["x"] >= 0 and box["right"] <= box["vw"] and box["bottom"] <= box["vh"] + 1, box
    finally:
        ctx.close()


def test_the_diet_sheet_can_be_scrolled_to_its_last_line_on_the_smallest_phone(browser, server):
    """Chips, the note, the toggle, the fine print and Done do not fit 320 x 568 at once: the body scrolls, the title and X stay, and
    the last line clears the badge corner."""
    ctx, pg = new_page(browser, server, (320, 568))
    try:
        item(pg, "diet").tap()
        pg.locator(DIALOG).wait_for(timeout=10000)
        settle(pg)
        info = pg.evaluate(
            "() => { const d = document.querySelector('[data-testid=stDialog] [role=dialog]'), b = d.lastElementChild; b.scrollTop = b.scrollHeight;"
            " const done = [...d.querySelectorAll('button')].find(x => x.innerText.trim() === 'Done').getBoundingClientRect();"
            " return {done: done.bottom, dialog: d.getBoundingClientRect().bottom, vh: innerHeight, scrolls: b.scrollHeight > b.clientHeight}; }"
        )
        assert info["done"] <= info["dialog"] - 50 and info["done"] <= info["vh"] - 50, info  # Done is reachable and clears the badge's 50 px
        close = pg.locator(DIALOG).get_by_role("button", name="Close", exact=True).bounding_box()
        assert close["width"] >= 44 and close["height"] >= 44 and close["y"] >= 0
    finally:
        ctx.close()


# ================================================================== fixes after the independent review
# ------------------------------------------------------------------ nothing tappable under the bar item that opened a sheet
CONTROL = "button, a[href], input, select, textarea, label, summary, [role=button], [role=switch], [role=checkbox], [role=tab], [role=option]"
UNDER = (
    "([x, y]) => { const e = document.elementFromPoint(x, y); if (!e) return 'nothing'; const c = e.closest('%s');"
    " return c ? c.tagName + ':' + (c.innerText || c.getAttribute('aria-label') || '').trim().slice(0, 40) : ''; }" % CONTROL
)


def tab_centres(pg) -> list[tuple[float, float]]:
    boxes = [rect(pg, BAR_BUTTON, i) for i in range(5)]
    return [(b["cx"], b["cy"]) for b in boxes]


def controls_under_the_tabs(pg, centres) -> list[str]:
    return [f"{i}:{found}" for i, (x, y) in enumerate(centres) if (found := pg.evaluate(UNDER, [x, y])) != ""]


@pytest.mark.parametrize("size", [(320, 568), (320, 640), (360, 640), (390, 844)])
def test_no_control_of_the_diet_sheet_is_under_any_bar_tab_whatever_filter_and_pregnancy_setting_is_on(browser, server, size):
    """A sheet covers the bar. A second tap on the item that opened it (a double tap, an impatient re-tap, at any delay) lands on the
    sheet at the same point: there must be nothing there to change. Where the content sits moves with the chosen filter and with the
    toggle, so every state is checked, in one open sheet."""
    ctx, pg = new_page(browser, server, size)
    try:
        centres = tab_centres(pg)
        open_diet_sheet(pg)
        chips = pg.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button')
        names = [chips.nth(i).inner_text().strip() for i in range(chips.count())]
        assert len(names) >= 8, names
        for pregnant in (False, True):
            if pregnant:
                pg.get_by_text("Pregnant or breastfeeding").click()
                settle(pg, 0.6)
            for name in names:
                pg.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button', has_text=name).first.click()
                settle(pg, 0.5)
                assert controls_under_the_tabs(pg, centres) == [], (size, name, pregnant)
                pg.evaluate("() => { const b = document.querySelector('[role=dialog]').lastElementChild; b.scrollTop = 0; }")
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(320, 568), (360, 640)])
@pytest.mark.parametrize("name", ["guide", "scan", "scans", "about"])
def test_no_control_of_the_other_sheets_is_under_any_bar_tab_either(browser, server, size, name):
    ctx, pg = new_page(browser, server, size)
    try:
        centres = tab_centres(pg)
        item(pg, name).tap()
        pg.locator(DIALOG).wait_for(timeout=10000)
        settle(pg)
        assert controls_under_the_tabs(pg, centres) == []
    finally:
        ctx.close()


@pytest.mark.parametrize("gap", [0.0, 0.3, 0.9])
@pytest.mark.parametrize("size, filter_name, pregnant", [((320, 568), "No restriction", True), ((320, 640), "Nut-free", False), ((320, 640), "Gluten-free", True)])
def test_a_second_tap_on_the_diet_tab_never_flips_the_pregnancy_toggle(browser, server, size, filter_name, pregnant, gap):
    """The review's reproduction: on a 320 px phone the toggle sat exactly under the Diet tab (320x568 with no filter, 320x640 with
    Kosher, Gluten-free or Nut-free), a re-tap flipped it, ON as well as OFF, for delays up to 900 ms. A short pointer-events delay on
    the sheet would not cover that: the strip under the bar is simply not tappable."""
    ctx, pg = new_page(browser, server, size)
    try:
        open_diet_sheet(pg)
        if filter_name != "No restriction":
            pg.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button', has_text=filter_name).first.click()
            settle(pg, 0.6)
        if pregnant:
            pg.get_by_text("Pregnant or breastfeeding").click()
            settle(pg, 0.6)
        close_sheet(pg)
        box = item(pg, "diet").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        pg.touchscreen.tap(x, y)
        pg.locator(DIALOG).wait_for(timeout=10000)
        pg.wait_for_timeout(int(gap * 1000))
        pg.touchscreen.tap(x, y)  # the second tap of a double tap, or of an impatient visitor
        settle(pg, 1.0)
        toggle = pg.locator("label", has_text="Pregnant or breastfeeding").locator("input")
        assert toggle.is_checked() is pregnant, f"the pregnancy setting was changed by a second tap on the Diet tab (was {pregnant})"
        assert pg.locator(DIALOG).count() == 1  # still one sheet, never two
        close_sheet(pg)
        chip = pg.locator(".diet-note")
        assert (("Pregnan" in chip.inner_text()) if chip.count() else False) is pregnant
    finally:
        ctx.close()


def test_a_drag_that_starts_under_the_bar_still_scrolls_the_sheet(browser, server):
    """The strip that keeps taps off is part of the scroller: a thumb that starts a drag there (the natural place) still scrolls it."""
    ctx, pg = new_page(browser, server, (320, 568))
    try:
        open_diet_sheet(pg)
        box = rect(pg, BAR_BUTTON, 1)
        cdp = ctx.new_cdp_session(pg)
        x, y = box["cx"], box["cy"]
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        for i in range(1, 13):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y - 16 * i}]})
            pg.wait_for_timeout(16)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        pg.wait_for_timeout(600)
        top = pg.evaluate("() => document.querySelector('[role=dialog]').lastElementChild.scrollTop")
        assert top > 100, top
        pg.evaluate("() => { const b = document.querySelector('[role=dialog]').lastElementChild; b.scrollTop = b.scrollHeight; }")
        done = pg.locator(DIALOG).get_by_role("button", name="Done").bounding_box()
        assert done["y"] + done["height"] <= rect(pg, BAR_BUTTON, 1)["y"] + 1, done  # at the end of the content the last row is above the strip
    finally:
        ctx.close()


# ------------------------------------------------------------------ the welcome screen at every phone size, not only five
def _welcome_fit(pg) -> dict:
    return pg.evaluate(
        "() => { const m = document.querySelector('[data-testid=stMain]'); const cap = [...document.querySelectorAll('[data-testid=stCaptionContainer]')].pop().getBoundingClientRect();"
        " const bar = document.querySelector('[class~=\"st-key-appbar\"]').getBoundingClientRect();"
        " return {scroll: m.scrollHeight - m.clientHeight, doc: document.scrollingElement.scrollHeight - innerHeight, gap: bar.top - cap.bottom}; }"
    )


IN_BETWEEN_HEIGHTS = [568, 601, 620, 640, 660, 680, 701, 720, 740, 760, 800, 844, 915]


@pytest.mark.parametrize("width", [320, 341, 350, 360, 375, 393, 412])
def test_the_welcome_screen_needs_no_scrolling_at_in_between_phone_sizes_too(browser, server, width):
    """The long hero text goes at a height that depends on the width (and on the chip). Between the five measured phones the page used
    to scroll and the disclaimer hid under the bar (341x601 by 84 px, 350x620, 360x610, 320x701). Every width x height point is checked."""
    ctx, pg = new_page(browser, server, (width, 800))
    try:
        for height in IN_BETWEEN_HEIGHTS:
            pg.set_viewport_size({"width": width, "height": height})
            pg.wait_for_timeout(250)
            fit = _welcome_fit(pg)
            assert fit["scroll"] <= 1 and fit["doc"] <= 1 and fit["gap"] >= 6, (width, height, fit)
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [320, 341, 350, 360, 393, 412])
def test_the_welcome_screen_needs_no_scrolling_at_in_between_phone_sizes_with_the_longest_chip(browser, server, width):
    ctx, pg = new_page(browser, server, (width, 800))
    try:
        open_diet_sheet(pg)
        pg.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button', has_text="Low-sodium").click()
        pg.get_by_text("Pregnant or breastfeeding").click()
        settle(pg)
        close_sheet(pg)
        pg.locator(".diet-note").wait_for(timeout=10000)
        for height in IN_BETWEEN_HEIGHTS:
            pg.set_viewport_size({"width": width, "height": height})
            pg.wait_for_timeout(250)
            fit = _welcome_fit(pg)
            assert fit["scroll"] <= 1 and fit["doc"] <= 1 and fit["gap"] >= 6, (width, height, fit)
    finally:
        ctx.close()


# ------------------------------------------------------------------ the demo label is never saved and never replaces a scan
def test_the_sample_never_replaces_the_saved_scan_and_is_not_offered_as_one(page):
    start_own_label(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    settle(page, 1.5)
    reload_and_wait_for_the_stored_scan(page)
    open_scan_sheet(page)
    page.get_by_text("1 of 4 cards done · saved today").wait_for(timeout=20000)
    text = page.locator(DIALOG).inner_text()
    assert "It is not saved." in text and "Your saved scan stays." in text and "Starting a new scan replaces" not in text, text
    assert "It replaces the scan you can resume." in text  # said under Analyze, where it is true
    page.get_by_role("button", name="Try with a sample label").click()
    card(page).locator("#card .name").wait_for(timeout=60000)
    settle(page)
    # The sample is on screen, nothing decided: her scan is still offered, over it ...
    open_scan_sheet(page)
    page.get_by_role("button", name="Resume last scan").wait_for(timeout=10000)
    assert "Sample label" not in page.locator(DIALOG).inner_text()
    close_sheet(page)
    first = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, first)
    settle(page, 1.5)
    # ... and after a swipe on the sample and a reload nothing but her scan is there to resume.
    reload_and_wait_for_the_stored_scan(page)
    open_scan_sheet(page)
    page.get_by_text("1 of 4 cards done · saved today").wait_for(timeout=20000)
    assert "Sample label" not in page.locator(DIALOG).inner_text()
    page.get_by_role("button", name="Resume last scan").click()
    card(page).locator("#card .name").wait_for(timeout=20000)
    assert "Card 2 of 4" in card(page).locator("#card .count").inner_text()  # her scan, where she left it


def test_the_sample_over_the_visitors_own_undecided_cards_does_not_cost_her_the_scan(page):
    start_own_label(page)
    own = card_name(page)
    settle(page, 1.5)  # saved with no decision yet
    open_scan_sheet(page)
    assert page.get_by_role("button", name="Resume last scan").count() == 0  # these cards are the saved scan
    assert "your scan stays saved" in page.locator(DIALOG).inner_text()
    page.get_by_role("button", name="Try with a sample label").click()
    card(page).locator("#card .name").wait_for(timeout=60000)
    settle(page)
    page.get_by_text("Sample label, not your product").wait_for(timeout=10000)  # the demo is on screen now
    open_scan_sheet(page)
    page.get_by_role("button", name="Resume last scan").click(timeout=10000)  # offered over the sample, in the same session
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    assert card_name(page) == own
    assert page.get_by_text("Sample label, not your product").count() == 0


# ------------------------------------------------------------------ the welcome hint, the names of the buttons
def test_the_welcome_hint_says_resume_when_there_is_a_scan_to_resume_and_stays_on_one_line(browser, server):
    ctx, pg = new_page(browser, server, (320, 568))
    try:
        assert pg.locator(".hero-hint").inner_text().strip() == "Tap Scan below to start ↓"
        start_own_label(pg)
        name = card_name(pg)
        card(pg).locator("#btnKeep").click()
        wait_name_change(pg, name)
        settle(pg, 1.5)
        reload_and_wait_for_the_stored_scan(pg)
        pg.get_by_text("Tap Scan below to start or resume").wait_for(timeout=20000)
        info = pg.evaluate("() => { const e = document.querySelector('.hero-hint'); const r = document.createRange(); r.selectNodeContents(e); return {h: e.getBoundingClientRect().height, font: parseFloat(getComputedStyle(e).fontSize)}; }")
        assert info["h"] < info["font"] * 2, info  # one line (two would be about twice the font size): the page does not move when the saved scan arrives
        state = scroll_state(pg)
        assert state["main"] <= 1 and state["doc"] <= 1, state
    finally:
        ctx.close()


def test_the_sheet_buttons_are_named_by_their_label_alone(page):
    """A Material icon is a text span ("photo_camera"): screen readers read it out and voice control has to match it."""
    start_own_label(page)
    settle(page, 1.5)
    reload_and_wait_for_the_stored_scan(page)
    open_scan_sheet(page)
    page.get_by_role("button", name="Resume last scan").wait_for(timeout=20000)
    for label in ("Analyze my supplement", "Resume last scan", "Try with a sample label"):
        assert page.locator(DIALOG).get_by_role("button", name=label, exact=True).count() == 1, label
    assert page.locator(f'{DIALOG} [data-testid="stIconMaterial"]').count() == 0


# ------------------------------------------------------------------ the keyboard focus after a window closes
def keyboard_page(browser, server, size=(390, 844)):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    pg = ctx.new_page()
    pg.goto(server, wait_until="networkidle")
    pg.locator(BAR_BUTTON).nth(4).wait_for(timeout=15000)
    return ctx, pg


def focused_item(pg) -> str:
    return pg.evaluate(
        "() => { const a = document.activeElement; const k = a && a.closest ? a.closest('[class*=\"st-key-appbar_\"]') : null;"
        " return k ? [...k.classList].find(c => c.startsWith('st-key-appbar_')).slice(14) : (a ? a.tagName : 'none'); }"
    )


def wait_for_focus(pg, name: str, timeout: float = 5.0) -> str:
    end = time.time() + timeout
    got = ""
    while time.time() < end:
        got = focused_item(pg)
        if got == name:
            return got
        pg.wait_for_timeout(100)
    return got


@pytest.mark.parametrize("how", ["cancel", "escape", "x"])
def test_the_scan_item_gets_the_focus_back_when_the_analyze_window_is_closed_without_analysing(browser, server, how):
    ctx, pg = keyboard_page(browser, server)
    try:
        item(pg, "scan").focus()
        pg.keyboard.press("Enter")
        pg.get_by_role("dialog").wait_for(timeout=10000)
        settle(pg, 0.5)
        pg.get_by_role("button", name="Analyze my supplement").click()
        pg.get_by_role("dialog").get_by_role("button", name="Cancel").wait_for(timeout=10000)  # the Analyze window, not the sheet
        if how == "cancel":
            pg.get_by_role("dialog").get_by_role("button", name="Cancel").click()
        elif how == "escape":
            pg.keyboard.press("Escape")
        else:
            pg.get_by_role("dialog").get_by_role("button", name="Close").click()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        assert wait_for_focus(pg, "scan") == "scan", focused_item(pg)
    finally:
        ctx.close()


@pytest.mark.parametrize("screen", ["midscan", "results"])
def test_the_scan_item_gets_the_focus_back_after_cancel_on_its_questions(browser, server, screen):
    ctx, pg = keyboard_page(browser, server)
    try:
        start_sample(pg)
        if screen == "midscan":
            name = card_name(pg)
            card(pg).locator("#btnKeep").click()
            wait_name_change(pg, name)
            settle(pg)
        else:
            finish_all_cards(pg)
            results_heading(pg).wait_for(timeout=10000)
        item(pg, "scan").focus()
        pg.keyboard.press("Enter")
        pg.get_by_role("dialog").wait_for(timeout=10000)
        settle(pg, 0.5)
        pg.get_by_role("dialog").get_by_role("button", name="Cancel").click()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        assert wait_for_focus(pg, "scan") == "scan", focused_item(pg)
    finally:
        ctx.close()


def test_the_diet_item_gets_the_focus_back_every_time_after_done(browser, server):
    """Done is closed by the server: Streamlit asked for the focus back while the page was still inert in about 1 of 3 runs, and the
    focus was left on <body>."""
    ctx, pg = keyboard_page(browser, server)
    try:
        wrong = []
        for i in range(12):
            item(pg, "diet").focus()
            pg.keyboard.press("Enter")
            pg.get_by_role("dialog").wait_for(timeout=10000)
            settle(pg, 0.4)
            pg.locator(DIALOG).get_by_role("button", name="Done").focus()
            pg.keyboard.press("Enter")
            pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
            if wait_for_focus(pg, "diet") != "diet":
                wrong.append((i, focused_item(pg)))
        assert not wrong, wrong
    finally:
        ctx.close()


def test_a_focus_that_is_somewhere_already_is_not_taken_away(browser, server):
    """The script moves the focus only when it was left on <body>."""
    ctx, pg = keyboard_page(browser, server)
    try:
        item(pg, "scan").focus()
        pg.keyboard.press("Enter")
        pg.get_by_role("dialog").wait_for(timeout=10000)
        settle(pg, 0.5)
        pg.get_by_role("button", name="Analyze my supplement").click()
        pg.wait_for_timeout(800)
        pg.get_by_role("dialog").get_by_role("button", name="Cancel").click()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        item(pg, "about").focus()  # she moves on at once
        pg.wait_for_timeout(1500)
        assert focused_item(pg) == "about"
    finally:
        ctx.close()

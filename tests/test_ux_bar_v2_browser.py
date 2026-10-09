"""Browser checks of bar v2 (Guide | Diet | Scan | Recent | About): what AppTest cannot see.

Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers are reused: the five tabs beside the
simulated Cloud badge, labels on one line, the welcome screen that must not scroll, the Scan and Diet sheets, and the safety
regression (Resume must never switch a filter off, and the Diet sheet must show the filter that is really on)."""
from __future__ import annotations

import pytest

from test_ux_browser import (  # noqa: F401  (fixtures are used by name)
    DIALOG, _active_chips, _mixed_results, ai_server, browser, card, card_name, choose_diet, close_sheet, finish_all_cards, open_diet_sheet,
    open_scan_sheet, page, results_heading, server, settle, shot, start_own_label, start_sample, wait_name_change,
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
        hero = rect(pg, ".hero")
        hint = rect(pg, ".hero-hint")
        caption = pg.evaluate("() => { const c = [...document.querySelectorAll('[data-testid=stCaptionContainer]')].pop().getBoundingClientRect(); return c.bottom; }")
        assert hint and hint["bottom"] <= hero["bottom"]  # the hint is in the card
        assert caption <= bar["y"] - 6, (caption, bar)  # the medical disclaimer ends clear above the bar
        assert pg.locator(".brand-foot").count() == 0 and pg.locator('[data-testid="stButton"]:not([class*="st-key-appbar"] *)').count() == 0
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
    start_sample(page)
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
    assert page.locator(".diet-note").inner_text().strip() == "🥗 Diet: Vegan · Pregnancy"
    page.get_by_text("Filter: Vegan").wait_for(timeout=5000)
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
    start_sample(page)  # saved with no filter
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
    page.locator('[data-testid="stSelectbox"]').first.evaluate("e => e.scrollIntoView({block: 'center'})")
    page.locator('[data-testid="stSelectbox"]').first.click()
    before = [o.inner_text() for o in page.locator('[role="option"]').all()]
    page.keyboard.press("Escape")
    assert any("salmon" in o.lower() or "fish" in o.lower() or "trout" in o.lower() for o in before), before
    open_diet_sheet(page)
    page.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button', has_text="Vegan").click()
    settle(page)
    assert page.get_by_text("Filter: Vegan").count() == 0  # the page follows when the sheet closes
    close_sheet(page)
    page.get_by_text("Filter: Vegan").wait_for(timeout=10000)
    page.locator('[data-testid="stSelectbox"]').first.evaluate("e => e.scrollIntoView({block: 'center'})")
    page.locator('[data-testid="stSelectbox"]').first.click()
    after = [o.inner_text() for o in page.locator('[role="option"]').all()]
    page.keyboard.press("Escape")
    assert after and len(after) < len(before) and not any("salmon" in o.lower() or "trout" in o.lower() for o in after), (before, after)


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
    ctx, pg = new_page(browser, server, size)
    try:
        start_sample(pg)
        for selector in ("#btnKeep", "#btnRepl", "#btnBack"):
            bar = rect(pg, BAR)
            inner = card(pg).locator(selector).bounding_box()
            need = inner["y"] + inner["height"] + 8 - bar["y"]
            if need > 0:  # a short phone: the page leaves room to scroll the row clear of the bar
                room = pg.evaluate(f"{MAIN}.scrollHeight - {MAIN}.clientHeight - {MAIN}.scrollTop")
                assert room >= need, (selector, need, room)
                pg.evaluate(f"{MAIN}.scrollBy(0, {need})")
                pg.wait_for_timeout(300)
            inner = card(pg).locator(selector).bounding_box()
            assert inner["y"] + inner["height"] <= rect(pg, BAR)["y"] + 1, (selector, inner)
        pg.evaluate(f"{MAIN}.scrollTo(0, 100000)")
        pg.wait_for_timeout(300)
        foot = rect(pg, ".brand-foot")
        assert foot["bottom"] <= rect(pg, BAR)["y"] + 1, (foot, rect(pg, BAR))
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


def test_a_second_tap_on_the_diet_tab_never_flips_the_pregnancy_toggle(browser, server):
    """The Diet sheet covers the tab that opened it. On a short phone the pregnancy toggle sits right under that tab, so a double tap (or
    an impatient re-tap while the page is slow) used to land on it and switch pregnancy OFF, hiding its warnings. The sheet's body ignores
    taps while it slides in."""
    ctx, pg = new_page(browser, server, (320, 568))
    try:
        open_diet_sheet(pg)
        pg.get_by_text("Pregnant or breastfeeding").click()
        settle(pg)
        close_sheet(pg)
        box = item(pg, "diet").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        pg.touchscreen.tap(x, y)
        pg.locator(DIALOG).wait_for(timeout=10000)
        pg.touchscreen.tap(x, y)  # the second tap of a double tap, at once
        settle(pg, 1.0)
        toggle = pg.locator("label", has_text="Pregnant or breastfeeding").locator("input")
        assert toggle.is_checked(), "pregnancy was switched off by a second tap on the Diet tab"
        assert pg.locator(DIALOG).count() == 1  # still one sheet, never two
    finally:
        ctx.close()

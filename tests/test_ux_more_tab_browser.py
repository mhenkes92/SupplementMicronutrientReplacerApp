"""Browser checks of the More tab (the bar's right-most item, where About used to be) and its sheet: what AppTest cannot see.

Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers are reused. The tab sits beside the
simulated Cloud badge, is finger-sized and has its own icon; the sheet ("More": About & privacy, and the report of the card on screen)
fits every phone, keeps nothing tappable under the tab that opened it (a double tap does not hit an entry), closes three ways
(Esc, X, a tap outside), replaces itself by About or by the Report sheet (one dialog at a time), and gives the keyboard focus back to the
More tab after each of them. The tab is dead while a photo is read. The report journey that used to start at the card's own More button
is here too (the card sends no "report" event any more)."""
from __future__ import annotations

import time

import pytest

from test_ux_app_bar_browser import BAR, BAR_BUTTON, BUSY, bar_button, new_page, rect, top_is_bar
from test_ux_browser import (  # noqa: F401  (fixtures are used by name)
    DIALOG, _mixed_results, ai_server, browser, card, card_name, close_sheet, iframe_kept, mark_iframe, open_about_sheet, open_more_sheet,
    open_report_from_more, page, results_heading, server, settle, shot, start_sample, wait_name_change,
)

SIZES = [(320, 568), (320, 640), (360, 640), (375, 667), (390, 844), (412, 915)]
ABOUT, REPORT = "About & privacy", "Report a problem with this card"
BADGE = (
    "() => { const b = document.createElement('div'); b.id = 'fake-badge';"
    " b.style.cssText = 'position:fixed;right:0;bottom:0;width:' + (innerWidth >= 353 ? 122 : Math.round(innerWidth * 0.15)) + 'px;height:50px;z-index:2147483647;background:red';"
    " document.body.appendChild(b); }"
)
ENTRIES_JS = (
    "() => [...document.querySelectorAll('[data-testid=stDialog] [role=dialog] button')]"
    ".filter(b => ['About & privacy', 'Report a problem with this card'].includes(b.innerText.trim()))"
    ".map(b => { const r = b.getBoundingClientRect(); return {text: b.innerText.trim(), x: r.x, right: r.right, y: r.y, bottom: r.bottom, w: r.width, h: r.height}; })"
)
CAPTIONS_JS = (
    "() => [...document.querySelectorAll('[data-testid=stDialog] [data-testid=stCaptionContainer]')].map(c => { const p = c.querySelector('p') || c;"
    " const r = document.createRange(); r.selectNodeContents(p); return {text: p.innerText.trim(), lines: r.getClientRects().length, right: p.getBoundingClientRect().right}; })"
)
DIALOG_BOX_JS = (
    "() => { const r = document.querySelector('[data-testid=stDialog] [role=dialog]').getBoundingClientRect();"
    " return {x: r.x, right: r.right, y: r.y, bottom: r.bottom, w: r.width, vw: innerWidth, vh: innerHeight, page: document.scrollingElement.scrollWidth}; }"
)
CONTROL = "button, a[href], input, select, textarea, label, summary, [role=button], [role=switch], [role=checkbox], [role=tab], [role=option]"
UNDER = (
    "([x, y]) => { const e = document.elementFromPoint(x, y); if (!e) return 'nothing'; const c = e.closest('%s');"
    " return c ? c.tagName + ':' + (c.innerText || c.getAttribute('aria-label') || '').trim().slice(0, 40) : ''; }" % CONTROL
)


def tab_centres(pg) -> list[tuple[float, float]]:
    boxes = [rect(pg, BAR_BUTTON, i) for i in range(5)]
    return [(b["cx"], b["cy"]) for b in boxes]


def controls_under(pg, centres) -> list[str]:
    return [f"{i}:{found}" for i, (x, y) in enumerate(centres) if (found := pg.evaluate(UNDER, [x, y])) != ""]


def keyboard_page(browser, server, size=(390, 844)):
    """A desktop-style context (no touch): the keyboard and the focus are what these tests are about."""
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


def dialog_title(pg) -> str:
    return pg.evaluate("() => { const h = document.querySelector('[data-testid=stDialog] [role=dialog] h2'); return h ? h.innerText.trim() : ''; }")


def dismiss(pg, how: str) -> None:
    """Esc, the X, or a tap on the dimmed page above the sheet."""
    if how == "escape":
        pg.keyboard.press("Escape")
    elif how == "x":
        pg.get_by_role("dialog").get_by_role("button", name="Close").click()
    else:
        pg.mouse.click(12, 12)
    pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)


def open_more_with_the_keyboard(pg) -> None:
    bar_button(pg, "more").focus()
    pg.keyboard.press("Enter")
    pg.get_by_role("dialog").wait_for(timeout=10000)
    settle(pg, 0.6)


# ------------------------------------------------------------------ the tab: finger-sized, beside the badge, with its own icon
@pytest.mark.parametrize("size", SIZES)
def test_the_more_tab_is_the_fifth_tab_of_at_least_44_px_clear_of_the_simulated_badge_and_hits_its_own_button(browser, server, size):
    ctx, pg = new_page(browser, server, size)
    try:
        pg.evaluate(BADGE)
        badge, bar = rect(pg, "#fake-badge"), rect(pg, BAR)
        boxes = [rect(pg, BAR_BUTTON, i) for i in range(5)]
        assert [pg.locator(BAR_BUTTON).nth(i).inner_text().strip() for i in range(5)] == ["Guide", "Diet", "Scan", "Recent", "More"]
        more = rect(pg, '[class~="st-key-appbar_more"] button')
        assert more["x"] == boxes[4]["x"] == max(b["x"] for b in boxes)  # the right-most tab, the one nearest the Cloud badge
        assert more["w"] >= 44 and more["h"] >= 44, more
        assert more["right"] <= bar["right"] <= badge["x"] - (3.5 if size[0] >= 353 else 11.5), (more, bar, badge)  # clear of the badge, with air
        assert all(abs(b["w"] - more["w"]) <= 1 for b in boxes)  # five equal tabs: More took About's width, nothing else moved
        assert top_is_bar(pg, more["cx"], more["cy"])
        assert pg.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('button').innerText.trim()", [more["cx"], more["cy"]]) == "More"
        masks = pg.evaluate(
            "() => [...document.querySelectorAll('[class~=\"st-key-appbar\"] button')].map(b => getComputedStyle(b, '::before').webkitMaskImage)")
        assert "circle" in masks[4] and len(set(masks)) == 5, masks  # three dots, and no two tabs share an icon
        shot(pg, f"more_tab_{size[0]}x{size[1]}")
    finally:
        ctx.close()


@pytest.mark.parametrize("percent", [100, 150, 200])
@pytest.mark.parametrize("size", [(320, 568), (360, 640)])
def test_the_more_label_is_on_one_line_inside_its_tab_at_larger_text(browser, server, size, percent):
    ctx, pg = new_page(browser, server, size)
    try:
        if percent != 100:
            pg.add_style_tag(content=f"html {{ font-size: {percent}% !important; }}")
            pg.wait_for_timeout(400)
        info = pg.evaluate(
            "() => { const b = document.querySelector('[class~=\"st-key-appbar_more\"] button'), p = b.querySelector('p'); const r = document.createRange();"
            " r.selectNodeContents(p); const t = r.getBoundingClientRect(), k = b.getBoundingClientRect();"
            " return {text: p.innerText, lines: r.getClientRects().length, inside: t.left >= k.left - 0.5 && t.right <= k.right + 0.5, w: k.width, h: k.height}; }"
        )
        assert info["text"] == "More" and info["lines"] == 1 and info["inside"] and info["w"] >= 44 and info["h"] >= 44, info
    finally:
        ctx.close()


def test_the_more_tab_follows_the_forced_colors_and_typing_rules_of_the_other_tabs(browser, server):
    """The bar's rules are written for the container, not for a tab: the icon of More is drawn in the system's button colour in a
    forced-colors mode, and the whole bar (More included) goes while a text field has the focus (the on-screen keyboard)."""
    ctx = browser.new_context(viewport={"width": 360, "height": 640}, is_mobile=True, has_touch=True, forced_colors="active")
    pg = ctx.new_page()
    try:
        pg.goto(server, wait_until="networkidle")
        pg.locator(BAR_BUTTON).nth(4).wait_for(timeout=15000)
        colours = pg.evaluate(
            "() => ['guide', 'diet', 'scans', 'more'].map(n => { const c = getComputedStyle(document.querySelector('[class~=\"st-key-appbar_' + n + '\"] button'), '::before');"
            " return [c.backgroundColor, c.getPropertyValue('forced-color-adjust')]; })")
        assert len({tuple(c) for c in colours}) == 1 and colours[0][1] == "none", colours  # the same icon colour, set by the same rule
        pg.evaluate("() => { const i = document.createElement('input'); i.id = 'probe'; document.querySelector('[data-testid=stMain]').appendChild(i); i.focus(); }")
        pg.wait_for_timeout(200)
        assert not pg.locator(BAR).is_visible() and not pg.locator('[class~="st-key-appbar_more"] button').is_visible()
    finally:
        ctx.close()


# ------------------------------------------------------------------ the sheet: content, fit, nothing under the tab
@pytest.mark.parametrize("size", [(320, 568), (360, 640), (390, 844), (412, 915)])
@pytest.mark.parametrize("screen", ["welcome", "card", "results"])
def test_the_more_sheet_fits_the_phone_and_offers_the_report_only_while_a_card_is_on_screen(browser, server, size, screen):
    ctx, pg = new_page(browser, server, size)
    try:
        if screen == "card":
            start_sample(pg)
        elif screen == "results":
            _mixed_results(pg)
        centres = tab_centres(pg)
        bar = rect(pg, BAR)
        open_more_sheet(pg)
        dialog = pg.locator(DIALOG)
        assert pg.locator(DIALOG).count() == 1 and dialog_title(pg) == "More"
        box = pg.evaluate(DIALOG_BOX_JS)
        assert box["x"] >= 0 and box["right"] <= box["vw"] and box["page"] <= box["vw"], box  # inside the phone, no sideways scroll
        assert box["bottom"] <= box["vh"] + 1 and box["y"] >= 40, box  # under the top edge: the dimmed page above it is the way out
        entries = pg.evaluate(ENTRIES_JS)
        assert [e["text"] for e in entries] == ([ABOUT, REPORT] if screen == "card" else [ABOUT]), entries
        for e in entries:
            assert e["h"] >= 44, e  # a finger-sized target
            assert e["w"] >= box["w"] - 2 * 16 - 2, (e, box)  # full width of the sheet's content
            assert e["bottom"] <= bar["y"] + 1, (e, bar)  # above the strip that covers the bar: nothing to hit by a second tap on the tab
        captions = pg.evaluate(CAPTIONS_JS)
        assert [c["text"] for c in captions] == (
            ["What is sent, what stays on your device.", "Logged for review, nothing personal."] if screen == "card"
            else ["What is sent, what stays on your device."]), captions
        assert all(c["lines"] == 1 for c in captions), captions  # one line each at 100 % text
        close = dialog.get_by_role("button", name="Close", exact=True).bounding_box()
        assert close["width"] >= 44 and close["height"] >= 44, close
        assert controls_under(pg, centres) == []  # nothing tappable under any bar tab
        shot(pg, f"more_sheet_{screen}_{size[0]}x{size[1]}")
    finally:
        ctx.close()


@pytest.mark.parametrize("gap", [0.0, 0.3, 0.9])
def test_a_second_tap_on_the_more_tab_never_hits_an_entry(browser, server, gap):
    """The report entry would sit right under the tab on the smallest phone: the sheet's strip over the bar must keep a double tap
    (or an impatient re-tap, at any delay) from opening About or the report, and from stacking a second sheet."""
    ctx, pg = new_page(browser, server, (320, 568))
    try:
        start_sample(pg)
        name = card_name(pg)
        box = bar_button(pg, "more").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        pg.touchscreen.tap(x, y)
        pg.locator(DIALOG).wait_for(timeout=10000)
        pg.wait_for_timeout(int(gap * 1000))
        pg.touchscreen.tap(x, y)  # the second tap of a double tap
        settle(pg, 1.0)
        assert pg.locator(DIALOG).count() == 1 and dialog_title(pg) == "More"  # still the one sheet, still More
        assert [e["text"] for e in pg.evaluate(ENTRIES_JS)] == [ABOUT, REPORT]
        close_sheet(pg)
        assert card_name(pg) == name and pg.locator('[data-testid="stException"]').count() == 0
    finally:
        ctx.close()


# ------------------------------------------------------------------ closing it, and where the focus goes
@pytest.mark.parametrize("how", ["escape", "x", "outside"])
def test_the_more_sheet_closes_three_ways_and_the_focus_returns_to_the_more_tab(browser, server, how):
    ctx, pg = keyboard_page(browser, server)
    try:
        open_more_with_the_keyboard(pg)
        dismiss(pg, how)
        assert wait_for_focus(pg, "more") == "more", focused_item(pg)
        open_more_with_the_keyboard(pg)  # and the same tab opens it again
        assert dialog_title(pg) == "More" and pg.locator(DIALOG).count() == 1
    finally:
        ctx.close()


@pytest.mark.parametrize("how", ["escape", "x", "outside"])
@pytest.mark.parametrize("target", ["about", "report"])
def test_about_and_the_report_replace_the_more_sheet_and_closing_them_returns_the_focus_to_the_more_tab(browser, server, target, how):
    ctx, pg = keyboard_page(browser, server)
    try:
        start_sample(pg)
        mark_iframe(pg)
        name = card_name(pg)
        open_more_with_the_keyboard(pg)
        pg.get_by_role("dialog").get_by_role("button", name=ABOUT if target == "about" else REPORT).focus()
        pg.keyboard.press("Enter")  # the keyboard way through the entry
        want = "About & privacy" if target == "about" else "Report a problem"
        pg.get_by_role("dialog").get_by_role("heading", name=want, exact=True).wait_for(timeout=10000)
        settle(pg, 0.8)
        assert pg.locator(DIALOG).count() == 1 and dialog_title(pg) == want  # replaced, never stacked
        assert pg.locator('[data-testid="stException"]').count() == 0
        dismiss(pg, how)
        assert wait_for_focus(pg, "more") == "more", (target, how, focused_item(pg))  # the button that opened it left with the More sheet
        assert card_name(pg) == name and iframe_kept(pg)  # the card behind was never rebuilt
    finally:
        ctx.close()


def test_the_report_through_more_thanks_returns_to_the_same_card_and_hands_the_focus_to_the_more_tab(browser, server):
    """The journey that used to start at the card's own More button: More -> Report a problem with this card -> the report -> a toast,
    the card as it was, the page not scrolled."""
    ctx, pg = keyboard_page(browser, server)
    try:
        start_sample(pg)
        name = card_name(pg)
        open_report_from_more(pg)
        assert dialog_title(pg) == "Report a problem"
        pg.get_by_role("button", name="🚩 Report a problem with this card").click()
        pg.locator('[data-testid="stToast"]').first.wait_for(timeout=10000)
        assert "Thanks — logged for review" in pg.locator('[data-testid="stToast"]').first.inner_text()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        settle(pg)
        assert card_name(pg) == name and "Card 1 of" in card(pg).locator("#card .count").inner_text()  # nothing decided
        assert pg.evaluate("() => { const m = document.querySelector('[data-testid=stMain]'); return m.scrollHeight - m.clientHeight; }") <= 1
        assert wait_for_focus(pg, "more") == "more", focused_item(pg)
        # the same card can be reported again, and a swipe still works afterwards
        card(pg).locator("#btnKeep").click()
        assert wait_name_change(pg, name)
    finally:
        ctx.close()


def test_the_report_entry_is_for_the_card_that_is_on_screen_when_it_is_tapped(browser, server):
    ctx, pg = new_page(browser, server, (390, 844))
    try:
        start_sample(pg)
        first = card_name(pg)
        card(pg).locator("#btnKeep").click()
        second = wait_name_change(pg, first)
        settle(pg)
        open_report_from_more(pg)
        pg.get_by_role("button", name="🚩 Report a problem with this card").click()
        pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
        settle(pg)
        assert card_name(pg) == second and "Card 2 of" in card(pg).locator("#card .count").inner_text()  # still this card, nothing decided
    finally:
        ctx.close()


def test_about_through_more_is_the_whole_notice_and_its_end_clears_the_badge_corner_on_the_smallest_phone(browser, server):
    ctx, pg = new_page(browser, server, (320, 568))
    try:
        open_about_sheet(pg)
        box = pg.evaluate(DIALOG_BOX_JS)
        assert box["x"] >= 0 and box["right"] <= box["vw"] and box["bottom"] <= box["vh"] + 1 and box["y"] >= 40, box
        info = pg.evaluate(
            "() => { const d = document.querySelector('[data-testid=stDialog] [role=dialog]'), b = d.lastElementChild; b.scrollTop = b.scrollHeight;"
            " const f = d.querySelector('.ab-foot').getBoundingClientRect();"
            " return {foot: f.bottom, dialog: d.getBoundingClientRect().bottom, scrolls: b.scrollHeight > b.clientHeight, text: d.querySelector('.ab-foot').innerText}; }"
        )
        assert info["scrolls"] and "Build " in info["text"], info
        assert info["foot"] <= info["dialog"] - 50, info  # the last line clears Cloud's 50 px badge
        text = pg.locator(DIALOG).inner_text()
        for heading in ("Not medical advice", "What is sent to services", "What stays on your device", "On the server", "Sources"):
            assert heading in text, heading
    finally:
        ctx.close()


# ------------------------------------------------------------------ the keyboard
def test_tab_reaches_both_entries_inside_the_sheet_and_enter_opens_the_one_that_has_the_focus(browser, server):
    ctx, pg = keyboard_page(browser, server)
    try:
        start_sample(pg)
        open_more_with_the_keyboard(pg)
        seen = []
        for _ in range(8):
            pg.keyboard.press("Tab")
            seen.append(pg.evaluate(
                "() => { const a = document.activeElement; return a ? (a.getAttribute('aria-label') || a.innerText || a.tagName).trim().slice(0, 40) : ''; }"))
            assert pg.evaluate("() => !!document.activeElement.closest('[role=dialog]')"), seen  # the trap keeps the focus in the sheet
            if REPORT in seen:
                break
        assert "More content" in seen and ABOUT in seen and REPORT in seen, seen  # the first block, then the two entries, in that order
        assert seen.index("More content") < seen.index(ABOUT) < seen.index(REPORT), seen
        pg.keyboard.press("Enter")  # on the report entry
        pg.get_by_role("dialog").get_by_role("heading", name="Report a problem", exact=True).wait_for(timeout=10000)
        assert pg.locator(DIALOG).count() == 1
    finally:
        ctx.close()


def test_opening_and_closing_the_more_sheet_never_remounts_the_bar_or_the_card(browser, server):
    ctx, pg = keyboard_page(browser, server)
    try:
        start_sample(pg)
        mark_iframe(pg)
        pg.evaluate(
            "() => { window.__removed = 0; new MutationObserver(ms => { for (const m of ms) for (const n of m.removedNodes)"
            " if (n.nodeType === 1 && (n.matches('[class~=\"st-key-appbar\"]') || n.querySelector('[class~=\"st-key-appbar\"]'))) window.__removed++; })"
            ".observe(document.body, {childList: true, subtree: true}); }"
        )
        for rep in range(4):
            open_more_with_the_keyboard(pg)
            if rep % 2:
                pg.get_by_role("dialog").get_by_role("button", name=ABOUT).click()
                pg.get_by_role("dialog").get_by_role("heading", name="About & privacy").wait_for(timeout=10000)
                settle(pg, 0.6)
            pg.keyboard.press("Escape")
            pg.get_by_role("dialog").wait_for(state="detached", timeout=10000)
            assert wait_for_focus(pg, "more") == "more", (rep, focused_item(pg))
            settle(pg, 1.0)  # the run the dismissal started is over before the next round starts (a loaded machine overlapped them)
        assert pg.evaluate("window.__removed") == 0 and iframe_kept(pg)
    finally:
        ctx.close()


# ------------------------------------------------------------------ dead while a photo is read
def test_the_more_tab_is_dead_while_a_photo_is_analysed_and_alive_again_with_both_entries_afterwards(ai_server, page, tmp_path):
    url, fake = ai_server
    fake.stream_script = [{"delay": 6, "text": "Supplement Facts\nVitamin C 90 mg\nZinc 11 mg"}]
    from PIL import Image

    photo = tmp_path / "label.jpg"
    Image.new("RGB", (640, 480), "white").save(photo, "JPEG")
    page.goto(url, wait_until="networkidle")
    page.locator(BAR_BUTTON).nth(4).wait_for(timeout=15000)
    page.locator('[class~="st-key-appbar_scan"] button').click()
    page.get_by_role("dialog").wait_for(timeout=10000)
    settle(page, 0.5)
    page.get_by_role("button", name="Analyze my supplement").click()
    dialog = page.get_by_role("dialog")
    dialog.locator("button", has_text="Upload").click()
    settle(page)
    dialog.locator('input[type="file"]').set_input_files(str(photo))
    page.locator(".analyze-loading-wrap").first.wait_for(timeout=20000)
    dead = page.locator(f'{BUSY} [class~="st-key-appbar_busy_more"] button')
    assert dead.count() == 1 and dead.is_disabled() and dead.inner_text().strip() == "More"  # the same tab, drawn dead
    started = len(fake.requests)
    dead.dispatch_event("click")  # even a stray click event opens nothing
    page.wait_for_timeout(1200)
    assert page.locator(DIALOG).count() == 0
    card(page).locator("#card .name").wait_for(timeout=60000)
    assert len(fake.requests) - started <= 2  # and started no second conversation
    settle(page)
    assert page.locator(BUSY).count() == 0 and page.locator(f"{BAR} button:disabled").count() == 0
    open_more_sheet(page)
    assert dialog_title(page) == "More" and [e["text"] for e in page.evaluate(ENTRIES_JS)] == [ABOUT, REPORT]

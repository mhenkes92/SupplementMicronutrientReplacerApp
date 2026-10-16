"""Browser checks (real Chromium, a touch phone at 2x) of "How to combine": the "Pairs well with" row of a swipe card (its height budget on every
phone size and text size, that it is a label and not a control, that it never makes a card scroll that fits without it) and the "Your day"
block of the Meals tab (a closed strip below the plan controls: the plan button stays where it was; every state, 44 px strips, keyboard,
contrast, no horizontal scroll, no motion, the table, the source links, the plan row's window, and that an opened tip survives the meal
plan's completion rerun).

Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1), whose fixtures and helpers are reused. The offline half (HTML builders,
AppTest over the results page, the prop of the card) is tests/test_combine_ui.py. Set SUPPSWIPE_COMBINE_SHOTS=<dir> to also write the
screenshots of the block (390x844 and 320x568) there.

Numbers measured here on the sample label (7 cards), see swipe_mobile_app/README.md: the row costs 28 px (normal density) / 24 px (tight) at
100 % text on a card that has a note, 0 px on every card without a tag; it is one line at 100 % down to 320 px; at 150 % and 200 % text it
scales with the text (one line where it fits, two where it does not)."""
from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path

import pytest

if os.getenv("SUPPSWIPE_BROWSER_TESTS", "") != "1":
    pytest.skip("browser tests are opt-in (SUPPSWIPE_BROWSER_TESTS=1)", allow_module_level=True)

from card_harness import SHAPES, Harness, card_args  # noqa: E402
from test_ux_browser import (  # noqa: E402,F401  (fixtures are used by name)
    CARD, DIALOG, ai_server, browser, card, server, settle, start_sample,
)
from test_ux_row_clear_browser import TEXT_150, TEXT_200, card_frame, open_page  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "swipe_mobile_app" / "swipe_component" / "index.html").read_text(encoding="utf-8")
SHOTS = os.getenv("SUPPSWIPE_COMBINE_SHOTS", "")
SIZES = [(390, 844), (393, 852), (412, 915), (375, 667), (360, 640), (320, 640), (320, 568)]
ZOOMS = [100, 150, 200]
INIT = {100: None, 150: TEXT_150, 200: TEXT_200}

D3 = dict(
    SHAPES["three digits"], index=1, cardId="vitamin d3", accent="#f59e0b", ink="#78350f",
    bioNote="Take vitamin D with a meal. Whole foods also contain vitamin K2. Ask your doctor before taking high doses.",
    pairs=[{"icon": "\U0001F951", "text": "some fat"}],
)
RICH = ("Iron 14 mg 100%\nVitamin D3 20 µg 400%\nCalcium (as calcium carbonate) 1000 mg 125%\nMagnesium 56 mg 15%\n"
        "Vitamin B12 2.5 µg 100%\nVitamin C 80 mg 100%")
TYPICAL = "Calcium (as calcium carbonate) 1000 mg 125%\nVitamin D3 20 µg 400%"  # one tip a meal: split calcium, D3 with fat, the pharmacist strip
EMPTY = "Selenium 55 µg 100%\nVitamin C 80 mg 100%"  # no rule about either: the calm empty card
OVER = "Iron 60 mg 428%\nVitamin D3 20 µg 400%"  # iron above its upper level: held
HELD_ONLY = "Iron 60 mg 428%\nZinc 60 mg 600%"  # both above their upper levels: only held pills
MINERAL = "Zinc 10 mg 100%\nMagnesium 56 mg 15%"
OMEGA = "Omega-3 (EPA+DHA) 500 mg\nVitamin E 15 mg 125%"
MAIN = '[data-testid="stMain"]'


# ------------------------------------------------------------------------------------------------------------------ the card row
# The content height of the card's body (what its text needs) with the row drawn and with it taken away, and what scrolls.
CARD_JS = """() => {
  const w = document.getElementById('wrap'), c = document.getElementById('cbody'), st = document.getElementById('stage');
  const pad = parseFloat(getComputedStyle(c).paddingBottom) || 0;
  const nat = hide => { const had = w.classList.contains('nopair'); w.classList.toggle('nopair', hide);
    const pr = c.cloneNode(true); pr.removeAttribute('id'); pr.querySelectorAll('[id]').forEach(n => n.removeAttribute('id'));
    pr.style.cssText = 'position:absolute;visibility:hidden;left:0;top:0;height:auto;overflow:visible;width:' + c.clientWidth + 'px;';
    st.appendChild(pr); const h = pr.scrollHeight; pr.remove(); w.classList.toggle('nopair', had); return h; };
  const cutOf = hide => { const had = w.classList.contains('nopair'); w.classList.toggle('nopair', hide);
    const cut = Math.max(0, Math.round(c.scrollHeight - c.clientHeight - pad)); w.classList.toggle('nopair', had); return cut; };
  const pair = document.querySelector('.pair'), src = document.querySelector('.src');
  const r = pair ? pair.getBoundingClientRect() : null;
  return { name: document.querySelector('.name').textContent, tight: w.classList.contains('tight'), big: w.classList.contains('big'), window: c.clientHeight,
    shown: nat(false), hidden: nat(true), cutShown: cutOf(false), cutHidden: cutOf(true), cutNow: Math.max(0, Math.round(c.scrollHeight - c.clientHeight - pad)), rowDrawn: !!pair && getComputedStyle(pair).display !== 'none',
    hasRow: !!pair, pill: st.classList.contains('cue-strong'), cue: st.classList.contains('cue'), src: src ? getComputedStyle(src).display !== 'none' : null,
    rowH: r ? Math.round(r.height * 10) / 10 : 0, rowW: r ? Math.round(r.width) : 0, scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth,
    aria: document.getElementById('card').getAttribute('aria-label') }; }"""


@pytest.fixture(scope="module")
def harness():
    with Harness() as h:
        yield h


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("zoom", ZOOMS, ids=lambda z: f"{z}pct")
def test_the_row_costs_28_px_normal_24_tight_at_100_percent_and_nothing_without_a_tag(browser, harness, size, zoom):
    ctx, page, frame = harness.open(browser, size, card_args(**D3), zoom=zoom)
    try:
        with_row = frame.evaluate(CARD_JS)
        cost = with_row["shown"] - with_row["hidden"]
        limit = 24 if with_row["tight"] else 28
        if zoom == 100:
            assert 0 < cost <= limit, (size, with_row)
            assert with_row["rowH"] < 34, with_row  # one line, down to a 320 px phone
        else:  # the row is text: it scales with the text size, and may take a second line where its two words do not fit side by side
            assert 0 < cost <= limit * zoom / 100 * 2 + 1, (size, zoom, with_row)
        assert page.errors == []
        # without a tag: no element, no gap, the same height as a card that never heard of the row
        for args in (card_args(**{**D3, "pairs": []}), {k: v for k, v in card_args(**D3).items() if k != "pairs"}):
            harness.redraw(page, args)
            bare = frame.evaluate(CARD_JS)
            assert not bare["hasRow"] and bare["shown"] == bare["hidden"] and "Pairs well with" not in bare["aria"], bare
    finally:
        ctx.close()


def test_two_tags_show_one_at_a_large_text_size_and_the_label_names_both(browser, harness):
    two = dict(D3, pairs=[{"icon": "\U0001F951", "text": "some fat"}, {"icon": "\U0001F37D️", "text": "a meal"}])
    ctx, page, frame = harness.open(browser, (390, 844), card_args(**two), zoom=100)
    try:
        counts = frame.evaluate("() => [...document.querySelectorAll('.pair-t')].map(e => getComputedStyle(e).display !== 'none')")
        assert counts == [True, True]
        assert "Pairs well with: some fat and a meal. Notes:" in frame.evaluate("document.getElementById('card').getAttribute('aria-label')")
        ctx2, page2, frame2 = harness.open(browser, (390, 844), card_args(**two), zoom=200)
        try:
            assert frame2.evaluate("() => [...document.querySelectorAll('.pair-t')].map(e => getComputedStyle(e).display !== 'none')") == [True, False]
            assert "some fat and a meal" in frame2.evaluate("document.getElementById('card').getAttribute('aria-label')")  # the label still names both
        finally:
            ctx2.close()
        # never more than two tags, whatever the props say
        harness.redraw(page, card_args(**dict(D3, pairs=[{"icon": "a", "text": "one"}, {"icon": "b", "text": "two"}, {"icon": "c", "text": "three"}])))
        assert frame.evaluate("document.querySelectorAll('.pair-t').length") == 2
    finally:
        ctx.close()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_row_never_makes_a_card_scroll_that_fits_without_it_and_the_cut_of_a_card_that_scrolls_grows_by_the_row_at_most(browser, harness, size):
    """The fit order of fitHeight(): the density ignores the row, then the first of (row, source line) / (row) / (source line) / (neither) that
    fits is drawn; a card that scrolls anyway shows the row. Cards of several heights (the long notes of the sample, a short one)."""
    notes = ["", "Take vitamin D with a meal.", D3["bioNote"], D3["bioNote"] + " " + D3["bioNote"], "A. " * 90]
    for zoom in (100, 150):
        for note in notes:
            args = card_args(**{**D3, "bioNote": note})
            ctx, page, frame = harness.open(browser, size, args, zoom=zoom)
            try:
                m = frame.evaluate(CARD_JS)
                assert m["hasRow"]
                limit = 24 if m["tight"] else 28
                if m["cutHidden"] <= 2:
                    assert m["cutNow"] <= 2, (size, zoom, len(note), m)  # a card that fits without the row never scrolls because of it
                else:
                    assert m["rowDrawn"], (size, zoom, len(note), m)  # a card that scrolls anyway shows the row
                    assert m["cutNow"] - m["cutHidden"] <= (limit if zoom == 100 else limit * zoom / 100 * 2) + 1, (size, zoom, m)
                # the "scroll for more" pill is never what the row introduces
                if m["pill"] and m["rowDrawn"]:
                    assert m["cutHidden"] > 8, m
            finally:
                ctx.close()


def test_the_row_is_a_label_and_not_a_control(browser, harness):
    ctx, page, frame = harness.open(browser, (390, 844), card_args(**D3), zoom=100)
    try:
        info = frame.evaluate("""() => {
          const p = document.querySelector('.pair'), chip = document.querySelector('.chip'), s = getComputedStyle(p), c = getComputedStyle(chip);
          const lum = a => { const l = a.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]; };
          const rgb = t => (t.match(/[\\d.]+/g) || []).map(Number).slice(0, 3);
          const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
          const tab = [...document.querySelectorAll('#card, #card *')].filter(e => e.tabIndex >= 0 && !e.disabled).map(e => e.id || e.className || e.tagName);
          return { controls: p.querySelectorAll('button, a, input, select, [role], [tabindex]').length, ownRole: p.getAttribute('role'), ownTab: p.getAttribute('tabindex'),
                   tag: p.tagName, cursor: s.cursor, shadow: s.boxShadow, bgImage: s.backgroundImage, radius: s.borderTopLeftRadius, chipShadow: c.boxShadow, chipBg: c.backgroundImage,
                   chipCursor: c.cursor, contrast: ratio(rgb(s.color), rgb(s.backgroundColor)), tab: tab, userSelect: s.userSelect, tabIndex: p.tabIndex }; }""")
        assert info["controls"] == 0 and info["ownRole"] is None and info["ownTab"] is None and info["tag"] == "DIV" and info["tabIndex"] == -1
        assert info["cursor"] != "pointer" and info["shadow"] == "none" and info["bgImage"] == "none"
        assert info["chipShadow"] != "none" and info["chipBg"] != "none" and info["chipCursor"] == "pointer"  # what the chips have, the row has not
        assert info["contrast"] >= 7, info["contrast"]
        # the tab order of the card is what it was: the card, Ask AI, Swap food, the athlete guide, Back, Keep, Replace
        assert info["tab"][0] == "card"
        harness.redraw(page, card_args(**{**D3, "pairs": []}))
        without = frame.evaluate("() => [...document.querySelectorAll('#card, #card *')].filter(e => e.tabIndex >= 0 && !e.disabled).map(e => e.id || e.className || e.tagName)")
        assert without == info["tab"]
    finally:
        ctx.close()


def test_the_note_keeps_its_text_and_its_rule_and_is_joined_to_the_row_only_while_the_row_is_drawn(browser, harness):
    assert (".bio { margin-top: 8px; padding: 7px 10px; border-radius: 12px; background: #eff6ff; border: 1px solid #bfdbfe; color: #1e3a5f; "
            "font-size: .82rem; line-height: 1.4; }") in SRC  # the rule the existing tests pin is untouched
    ctx, page, frame = harness.open(browser, (390, 844), card_args(**D3), zoom=100)
    try:
        assert frame.evaluate("document.querySelector('.bio').textContent") == "💡 " + D3["bioNote"]
        joined = frame.evaluate("() => { const b = getComputedStyle(document.querySelector('.bio')); return [b.marginTop, b.borderTopWidth, b.borderTopLeftRadius]; }")
        assert joined == ["0px", "0px", "0px"]
        frame.evaluate("document.getElementById('wrap').classList.add('nopair')")  # fitHeight took the row away: the note is the box it always was
        alone = frame.evaluate("() => { const b = getComputedStyle(document.querySelector('.bio')); return [b.marginTop, b.borderTopWidth, b.borderTopLeftRadius]; }")
        assert alone == ["8px", "1px", "12px"]
    finally:
        ctx.close()


# ------------------------------------------------------------------------------------------------------------------ the sample label
def _swipe(page, decisions: dict[str, str], default: str = "keep", on_card=None) -> list[str]:
    """Decide every card ("keep" / "repl" by the card's name); `on_card(name)` runs while a card is on screen. Returns the names."""
    fr = lambda: page.frame_locator(CARD)  # noqa: E731
    seen: list[str] = []
    for _ in range(20):
        if page.locator(".plan-kicker", has_text="Your plan").count():
            break
        name = fr().locator("#card .name").inner_text(timeout=20000)
        seen.append(name)
        settle(page, 0.5)
        if on_card:
            on_card(name)
        want = decisions.get(name, default)
        button = "#btnRepl" if want == "repl" and not fr().locator("#btnRepl").is_disabled() else "#btnKeep"
        fr().locator(button).click()
        end = time.time() + 15
        while time.time() < end:
            if page.locator(".plan-kicker", has_text="Your plan").count():
                break
            try:
                if fr().locator("#card .name").inner_text(timeout=300) != name:
                    break
            except Exception:
                pass
            time.sleep(0.05)
    page.locator(".plan-kicker", has_text="Your plan").wait_for(timeout=30000)
    settle(page, 1.2)
    return seen


def _label(page, text: str) -> None:
    page.locator('[class~="st-key-appbar_scan"] button').click()
    page.get_by_role("dialog").wait_for(timeout=10000)
    settle(page, 0.5)
    page.get_by_role("button", name="Analyze my supplement").click()
    dialog = page.get_by_role("dialog")
    dialog.locator("button", has_text="Paste").click()
    settle(page)
    dialog.locator("textarea").fill(text)
    dialog.get_by_role("button", name="Analyze").click()
    page.frame_locator(CARD).locator("#card .name").wait_for(timeout=60000)
    settle(page, 1.0)


def _meals(page, open_day: bool = True) -> None:
    """The Meals tab. The day is ONE CLOSED STRIP at first (the plan button must stay on the first screen); open_day taps it, as a visitor
    does, so the tests below can read the meals and the tips. The closed state has its own tests."""
    page.get_by_role("tab", name="🍽️ Meals").click()
    settle(page, 0.8)
    page.locator('.cb-card, .plan-idea, [data-testid="stAlert"]').first.wait_for(timeout=10000)
    if open_day and page.locator(".cb-day").count():
        if not page.locator(".cb-day[open]").count():
            page.locator(".cb-day > summary").click()
        page.locator(".cb-day[open]").wait_for(timeout=5000)
        page.wait_for_timeout(150)


def _phone(browser, server, size=(390, 844), zoom=100):
    return open_page(browser, server, size, init=INIT[zoom])


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.evaluate(f"(() => {{ const m = document.querySelector('{MAIN}'); const h = [...document.querySelectorAll('.plan-h')].find(e => e.textContent.includes('Your day')); if (h) m.scrollTop = h.getBoundingClientRect().top + m.scrollTop - 70; }})()")
        page.wait_for_timeout(250)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def test_sample_label_flow_the_d3_card_shows_the_row_and_the_meals_tab_the_day(browser, server):
    """Keep D3, magnesium and zinc, swap vitamin C and B12 (the sample label): the Vitamin D3 card showed "Pairs well with some fat"; the Meals
    tab shows Lunch with the D3 chip and the fat tip, "Any meal" with magnesium and zinc, and the pharmacist strip."""
    ctx, page = _phone(browser, server)
    try:
        start_sample(page)
        rows: dict[str, str | None] = {}

        def on_card(name: str) -> None:
            f = card_frame(page)
            rows[name] = f.evaluate("() => { const p = document.querySelector('.pair'); return p ? p.textContent.replace(/\\s+/g, ' ').trim() : null; }")
            if name == "Vitamin D3":
                aria = f.evaluate("document.getElementById('card').getAttribute('aria-label')")
                assert ". Pairs well with: some fat. Notes:" in aria

        _swipe(page, {"Vitamin C": "repl", "Vitamin B12": "repl"}, on_card=on_card)
        assert rows["Vitamin D3"] == "Pairs well with 🥑 some fat" and [n for n, v in rows.items() if v] == ["Vitamin D3"]  # D3 only
        _meals(page)
        block = page.locator(".cb-card")
        assert block.count() == 1
        lunch = block.locator(".cb-slot").filter(has_text="Lunch")
        assert lunch.count() == 1 and block.locator(".cb-slot").count() == 1  # the only meal with something in it
        assert "Vitamin D3 20 mcg" in lunch.locator(".cb-chip").first.inner_text()
        assert "Take vitamins D and E with a meal that has some fat" in lunch.locator("summary").first.inner_text()
        any_meal = block.locator(".cb-any")
        assert "Magnesium 56 mg" in any_meal.inner_text() and "Zinc 10 mg" in any_meal.inner_text()
        assert block.locator(".cb-med summary").count() == 1 and "pharmacist" in block.locator(".cb-med summary").inner_text()
        _shot(page, "sample_390x844")
    finally:
        ctx.close()


# ------------------------------------------------------------------------------------------------------------------ the block
BLOCK_JS = """() => {
  const R = e => e.getBoundingClientRect(), root = document.querySelector('.cb-card'), main = document.querySelector('[data-testid=stMain]');
  const head = [...document.querySelectorAll('.plan-h')].find(e => e.textContent.includes('Your day'));
  const sums = [...root.querySelectorAll('summary')].filter(s => R(s).height > 0 && !s.closest('details:not([open]) > .cb-rows, .cb-all:not([open]) .cb-rows'));
  const visible = [...root.querySelectorAll('summary')].filter(s => { let d = s.parentElement.parentElement; while (d && d !== root) { if (d.tagName === 'DETAILS' && !d.open) return false; d = d.parentElement; } return true; });
  const bgs = [...root.querySelectorAll('summary')].length;
  return { top: Math.round(R(head).top + main.scrollTop), height: Math.round(R(root).bottom - R(head).top), cardH: Math.round(R(root).height),
    summaries: visible.map(s => ({ h: Math.round(R(s).height * 10) / 10, w: Math.round(R(s).width), card: Math.round(R(root).width) - 26, top: Math.round(R(s).top + main.scrollTop), text: s.textContent.replace(/\\s+/g, ' ').trim().slice(0, 60) })),
    docW: document.scrollingElement.scrollWidth, mainW: main.scrollWidth, viewW: innerWidth,
    wide: [...root.querySelectorAll('*')].filter(e => !e.closest('.cb-sr') && !e.classList.contains('cb-sr') && R(e).width > 1 && R(e).right > innerWidth + 0.5).map(e => e.className || e.tagName).slice(0, 5),
    clipped: [...root.querySelectorAll('.cb-chip, .cb-t, .cb-ti, .cb-tx, .cb-why, .cb-note, .cb-src')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'visible').map(e => e.className) }; }"""

CONTRAST_JS = """() => {
  const root = document.querySelector('.cb-card');
  const lum = a => { const l = a.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]; };
  const rgba = s => (s.match(/[\\d.]+/g) || []).map(Number);
  const bgOf = e => { while (e) { const c = rgba(getComputedStyle(e).backgroundColor); if (c.length >= 3 && (c.length === 3 || c[3] > 0.99)) return c.slice(0, 3); e = e.parentElement; } return [255, 255, 255]; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const out = [];
  root.querySelectorAll('*').forEach(e => { const t = [...e.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).map(n => n.textContent.trim()).join(' ');
    if (!t || e.closest('.cb-sr')) return; const cs = getComputedStyle(e);
    out.push({ text: t.slice(0, 30), ratio: Math.round(ratio(rgba(cs.color), bgOf(e)) * 100) / 100, size: parseFloat(cs.fontSize) / parseFloat(getComputedStyle(document.documentElement).fontSize) }); });
  return out; }"""


def _open_all(page) -> None:
    """Open every disclosure of the block (the table's rows too): the worst case for width and contrast."""
    page.evaluate("() => { const r = document.querySelector('.cb-card'); r.querySelectorAll('details').forEach(d => { d.open = true; }); }")
    page.wait_for_timeout(250)


@pytest.mark.parametrize("size,zoom", [((390, 844), 100), ((390, 844), 150), ((390, 844), 200), ((320, 568), 100), ((320, 568), 150), ((320, 568), 200)],
                         ids=lambda v: str(v))
def test_the_rich_day_has_44_px_strips_in_visual_order_and_never_scrolls_sideways(browser, server, size, zoom):
    ctx, page = _phone(browser, server, size, zoom)
    try:
        _label(page, RICH)
        _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
        _meals(page)
        closed = page.evaluate(BLOCK_JS)
        for name, m in (("closed", closed), ("open", (_open_all(page), page.evaluate(BLOCK_JS))[1])):
            assert m["docW"] <= m["viewW"] and m["mainW"] <= m["viewW"] + 1, (name, m)  # no horizontal scroll
            assert m["wide"] == [] and m["clipped"] == [], (name, m)
            assert m["summaries"], name
            for s in m["summaries"]:
                assert s["h"] >= 44, (name, s)  # a 44 px target
                assert s["w"] >= s["card"] - 1, (name, s)  # full width
            tops = [s["top"] for s in m["summaries"]]
            assert tops == sorted(tops), (name, tops)  # the DOM order is the visual order
        assert page.evaluate("document.querySelectorAll('.cb-card details').length") >= 11 + 5
        if size == (390, 844) and zoom == 100:
            _shot(page, "rich_390x844")
        if size == (320, 568) and zoom == 100:
            _shot(page, "rich_320x568")
    finally:
        ctx.close()


@pytest.mark.parametrize("label,size,limit", [(TYPICAL, (390, 844), 700), (TYPICAL, (320, 568), 780), (EMPTY, (390, 844), 260)], ids=["typical390", "typical320", "empty390"])
def test_the_block_stays_short(browser, server, label, size, limit):
    """Three meals with one closed tip each (+ pharmacist strip + the opener) <= 700 px at 390x844 and <= 780 px at 320x568; the calm empty card <= 260 px."""
    ctx, page = _phone(browser, server, size)
    try:
        _label(page, label)
        _swipe(page, {})
        _meals(page)
        m = page.evaluate(BLOCK_JS)
        assert m["height"] <= limit, m
        if label == EMPTY:
            assert "No special pairing needed" in page.locator(".cb-day > summary").inner_text()
            assert "Nothing in your plan needs a particular order" in page.locator(".cb-empty").inner_text()
            _shot(page, "empty_" + f"{size[0]}x{size[1]}")
    finally:
        ctx.close()


def test_an_opened_tip_adds_one_screen_of_reading_at_the_most_and_the_source_is_a_44_px_link(browser, server):
    """Typical tips add 130-200 px (the why, the note, the source); the fat tip with its hint is the longest (about 225 px). Every
    source is a whole-line link of at least 44 px that opens in a new tab."""
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, RICH)
        _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
        _meals(page)
        tips = page.locator(".cb-slot details.cb-tip, .cb-med details.cb-tip")
        n = tips.count()
        assert n >= 5
        added = []
        for i in range(n):
            before = page.evaluate(BLOCK_JS)["cardH"]
            tips.nth(i).locator("summary").click()
            page.wait_for_timeout(150)
            after = page.evaluate(BLOCK_JS)["cardH"]
            added.append(after - before)
            link = tips.nth(i).locator("a.cb-src").first
            assert link.count() == 1
            assert link.get_attribute("target") == "_blank" and "noopener" in (link.get_attribute("rel") or "")  # B13: it survives the sanitizer
            assert (link.get_attribute("href") or "").startswith("https://")
            assert link.bounding_box()["height"] >= 44 and "opens in a new tab" in link.inner_text() + (link.evaluate("e => e.textContent") or "")
            tips.nth(i).locator("summary").click()
            page.wait_for_timeout(100)
        assert max(added) <= 240 and sorted(added)[len(added) // 2] <= 200, added
    finally:
        ctx.close()


def test_keyboard_tab_reaches_every_strip_enter_opens_space_closes_and_the_ring_is_visible(browser, server):
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, RICH)
        _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
        _meals(page)
        expected = page.evaluate("() => [...document.querySelectorAll('.cb-card summary')].filter(s => s.getBoundingClientRect().height > 0 && !s.closest('.cb-rows')).length")
        assert expected >= 6
        page.get_by_role("tab", name="🍽️ Meals").focus()
        reached = []
        for _ in range(expected + 6):
            page.keyboard.press("Tab")
            info = page.evaluate("() => { const a = document.activeElement; if (!a || a.tagName !== 'SUMMARY' || !a.closest('.cb-card')) return null; const cs = getComputedStyle(a); "
                                 "return { idx: [...document.querySelectorAll('.cb-card summary')].indexOf(a), ring: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor, off: cs.outlineOffset }; }")
            if info:
                reached.append(info)
            if len(reached) == expected:
                break
        assert len(reached) == expected, (expected, reached)
        assert [r["idx"] for r in reached] == sorted(r["idx"] for r in reached)  # in visual order
        assert all(r["ring"].startswith("solid 3px") for r in reached), reached
        assert all(float(r["off"].replace("px", "")) <= 0 for r in reached), reached  # inside the strip: the card's rounded edge cannot clip it
        assert page.evaluate("[...document.querySelectorAll('.cb-chip')].every(e => e.tabIndex === -1)")  # the pills are labels, not stops
        assert page.locator(".cb-chip", has_text="Calcium, half of 1000 mg").count() == 2  # split calcium: two meals, two pills
        first = page.locator(".cb-slot details.cb-tip").first
        first.locator("summary").focus()
        page.keyboard.press("Enter")
        assert first.evaluate("d => d.open") is True
        page.keyboard.press("Space")
        assert first.evaluate("d => d.open") is False
        # the opener too
        page.locator(".cb-all > summary").focus()
        page.keyboard.press("Enter")
        assert page.locator(".cb-all").evaluate("d => d.open") is True
        # and the day's own strip (the first stop of the block): Space closes it, Enter opens it again
        page.locator(".cb-day > summary").focus()
        page.keyboard.press("Space")
        assert page.locator(".cb-day").evaluate("d => d.open") is False
        page.keyboard.press("Enter")
        assert page.locator(".cb-day").evaluate("d => d.open") is True
        assert reached[0]["idx"] == 0  # the strip of the day was the first stop
    finally:
        ctx.close()


def test_opening_and_closing_tips_and_the_table_sends_nothing_to_the_server(browser, server):
    """A tip and the table are native disclosures: a tap is not a widget event, so no script run, no rerun, no flicker."""
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, RICH)
        _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
        _meals(page)
        page.evaluate("""() => { window.__sent = 0; const send = WebSocket.prototype.send;
          WebSocket.prototype.send = function (d) { window.__sent += 1; return send.call(this, d); };
          window.__mark = document.querySelector('.cb-card'); window.__status = 0;
          new MutationObserver(() => { if (document.querySelector('[data-testid="stStatusWidget"]')) { window.__status += 1; } }).observe(document.body, { childList: true, subtree: true }); }""")
        for summary in (page.locator(".cb-day > summary"), page.locator(".cb-day > summary"), page.locator(".cb-slot details.cb-tip > summary"),
                        page.locator(".cb-med details.cb-tip > summary"), page.locator(".cb-all > summary")):  # the strip twice: closed, then open again
            summary.first.click()
            page.wait_for_timeout(400)
        page.locator(".cb-rows details.cb-tip > summary").first.click()
        page.wait_for_timeout(600)
        assert page.evaluate("window.__sent") == 0, "a tap on a tip reached the server"
        assert page.evaluate("window.__status") == 0 and page.evaluate("window.__mark === document.querySelector('.cb-card')")
    finally:
        ctx.close()


def test_every_word_has_the_contrast_the_size_and_the_stillness_of_the_agreement(browser, server):
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, RICH)
        _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
        _meals(page)
        _open_all(page)
        rows = page.evaluate(CONTRAST_JS)
        assert len(rows) > 40
        worst = sorted(rows, key=lambda r: r["ratio"])[:3]
        assert all(r["ratio"] >= 4.5 for r in rows), worst
        assert all(r["size"] >= 0.72 - 1e-6 for r in rows), sorted(rows, key=lambda r: r["size"])[:3]
        # the focus ring (#1d4ed8) against the strip and the card
        ring = page.evaluate("""() => { const lum = a => { const l = a.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]; };
          const x = lum([29, 78, 216]); return [[248, 250, 252], [255, 255, 255]].map(b => { const y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }); }""")
        assert min(ring) >= 3
        # no motion: not a transition and not an animation anywhere in the block
        moving = page.evaluate("""() => [...document.querySelectorAll('.cb-card, .cb-card *')].filter(e => { const s = getComputedStyle(e);
          return (parseFloat(s.transitionDuration) || 0) > 0 || (s.animationName !== 'none' && (parseFloat(s.animationDuration) || 0) > 0); }).map(e => e.className || e.tagName)""")
        assert moving == []
    finally:
        ctx.close()


def test_forced_colors_outline_the_pills_and_the_strips_and_reduced_motion_changes_nothing(browser, server):
    """Windows high contrast drops backgrounds: the pills and the strips keep a 1 px border of their own (the system colour)."""
    for options in ({"forced_colors": "active"}, {"reduced_motion": "reduce"}):
        ctx, page = open_page(browser, server, (390, 844), **options)
        try:
            _label(page, RICH)
            _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
            _meals(page)
            borders = page.evaluate("""() => ({ chips: [...document.querySelectorAll('.cb-chip')].map(e => getComputedStyle(e).borderTopWidth),
              strips: [...document.querySelectorAll('.cb-tip > summary, .cb-day > summary')].filter(e => e.getBoundingClientRect().height > 0).map(e => getComputedStyle(e).borderTopWidth),
              forced: matchMedia('(forced-colors: active)').matches })""")
            assert borders["chips"] and borders["strips"]
            if "forced_colors" in options:
                assert borders["forced"] and set(borders["chips"]) == {"1px"} and set(borders["strips"]) == {"1px"}, borders
            else:
                assert not borders["forced"] and set(borders["chips"]) == {"0px"}, borders  # normal rendering: no border, a tint
            m = page.evaluate(BLOCK_JS)
            assert m["docW"] <= m["viewW"] and m["wide"] == []
        finally:
            ctx.close()


def test_the_table_lists_all_eleven_rules_the_visitors_own_first_and_has_every_source(browser, server):
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, RICH)
        _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
        _meals(page)
        assert page.locator(".cb-all[open]").count() == 0  # opt-in: closed at first
        page.locator(".cb-all > summary").click()
        page.wait_for_timeout(200)
        rows = page.locator(".cb-rows > details.cb-tip")
        assert rows.count() == 11
        tags = [rows.nth(i).locator(".cb-tag").count() for i in range(11)]
        assert tags == sorted(tags, reverse=True) and 0 < sum(tags) < 11  # the visitor's own first, each tagged "In your plan"
        assert page.locator(".cb-rows .cb-label").text_content() == "Also good to know"
        rows.nth(0).locator("summary").click()
        assert rows.nth(0).locator("a.cb-src").count() >= 2 and rows.nth(0).locator(".cb-srch").inner_text() == "Sources:"
        _shot(page, "table_390x844")
    finally:
        ctx.close()


def test_the_medicine_strip_is_there_for_calcium_iron_magnesium_zinc_only_and_has_no_digit(browser, server):
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, MINERAL)
        _swipe(page, {})
        _meals(page)
        strip = page.locator(".cb-med summary")
        assert strip.count() == 1 and not re.search(r"\d", strip.inner_text())
        assert "No special pairing needed" in page.locator(".cb-day > summary").inner_text()  # zinc and magnesium alone: nothing to order
        assert page.locator(".cb-slot").count() == 0
    finally:
        ctx.close()
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, EMPTY)
        _swipe(page, {})
        _meals(page)
        assert page.locator(".cb-med").count() == 0  # selenium and vitamin C: no strip (the table still has the row)
    finally:
        ctx.close()


def test_a_pill_over_its_upper_level_is_not_placed_and_the_card_has_no_row(browser, server):
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, OVER)
        rows: dict[str, int] = {}
        _swipe(page, {}, on_card=lambda name: rows.__setitem__(name, card_frame(page).locator(".pair").count()))
        assert rows == {"Iron": 0, "Vitamin D3": 1}  # the orange warning is the message on the iron card: no tag
        _meals(page)
        held = page.locator(".cb-held")
        assert held.count() == 1 and "Iron 60 mg" in held.inner_text() and "Heads-up above" in held.inner_text()
        assert "Not scheduled" not in page.locator(".cb-card").inner_text()
        assert held.locator("details").count() == 0  # no timing advice for it
        slots = page.locator(".cb-slot").all_inner_texts()
        assert slots and all("Iron" not in s for s in slots)
    finally:
        ctx.close()


def test_pills_the_heads_up_warns_about_get_no_reassurance_from_the_day(browser, server):
    """Iron 60 mg and zinc 60 mg, both kept: the orange box says they are above the upper level, so the day must not draw a green tick or
    'Enjoy your pills' above them (it once did): only the row that points to the box."""
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, HELD_ONLY)
        _swipe(page, {})
        _meals(page, open_day=False)
        assert page.locator(".plan-warn").count() == 1 and "above the upper intake level" in page.locator(".plan-warn").inner_text()
        strip = page.locator(".cb-day > summary")
        assert "See the Heads-up" in strip.inner_text() and "✓" not in strip.inner_text() and page.locator(".cb-day").get_attribute("data-state") == "held"
        strip.click()
        page.locator(".cb-day[open]").wait_for(timeout=5000)
        card = page.locator(".cb-card").inner_text()
        for reassurance in ("Enjoy", "No special pairing needed", "✓", "Nothing in your plan needs", "Not scheduled"):
            assert reassurance not in card, reassurance
        held = page.locator(".cb-held")
        assert "Iron 60 mg" in held.inner_text() and "Zinc 60 mg" in held.inner_text() and page.locator(".cb-empty").count() == 0
        assert page.locator(".cb-slot").count() == 0
        _shot(page, "held_only_390x844")
    finally:
        ctx.close()


def test_the_block_is_there_with_kept_pills_alone_and_below_the_swipe_right_hint(browser, server):
    ctx, page = _phone(browser, server, (390, 844))
    try:
        start_sample(page)
        _swipe(page, {})  # everything kept
        _meals(page)
        block = page.locator(".cb-card")
        hint = page.get_by_text("Swipe right on at least one nutrient to get meal ideas.")
        assert block.count() == 1 and hint.count() == 1
        assert block.bounding_box()["y"] > hint.bounding_box()["y"]
    finally:
        ctx.close()


GEN_JS = """() => { const btn = [...document.querySelectorAll('button')].find(b => /Generate my meals/.test(b.textContent));
  const tabs = [...document.querySelectorAll('[role=tab]')].find(t => /Meals/.test(t.textContent));
  const head = [...document.querySelectorAll('.plan-h')].find(e => e.textContent.includes('Your day')); const r = btn.getBoundingClientRect();
  return { below: Math.round(r.top - tabs.getBoundingClientRect().bottom), bottom: Math.round(r.bottom), vh: innerHeight, dayTop: Math.round(head.getBoundingClientRect().top) }; }"""


@pytest.mark.parametrize("size,zoom", [((390, 844), 100), ((320, 568), 100), ((390, 844), 200)], ids=lambda v: str(v))
def test_the_day_is_a_closed_strip_below_the_plan_button_which_stays_where_it_was(browser, ai_server, size, zoom):
    """The day above the plan pushed "Generate my meals" four times further down (255 -> 1029 px under the tab bar at 390x844): the tab
    opens on meals, and the day is ONE closed 44 px strip below the plan controls. The numbers of the same label with the feature off: 255
    px at 100 % text, 605 px at 200 %."""
    url, _fake = ai_server
    ctx, page = open_page(browser, url, size, init=INIT[zoom])
    try:
        _label(page, RICH)
        _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
        _meals(page, open_day=False)
        gen = page.evaluate(GEN_JS)
        assert page.locator(".cb-day[open]").count() == 0
        assert gen["below"] <= {100: 258, 200: 610}[zoom], gen  # where it was without the feature
        if size == (390, 844) and zoom == 100:
            assert gen["bottom"] <= gen["vh"], gen  # still on the first screen of the phone
        assert gen["dayTop"] > gen["bottom"] - 4, gen  # the day is below the plan button, never above it
        closed = page.evaluate(BLOCK_JS)
        assert len(closed["summaries"]) == 1 and closed["summaries"][0]["h"] >= 44  # only the strip is on the page
        assert closed["height"] <= {100: 100, 200: 200}[zoom], closed  # the heading and the strip
        assert closed["docW"] <= closed["viewW"] and closed["wide"] == [] and closed["clipped"] == []
        assert "pairing tip" in closed["summaries"][0]["text"] and "Lunch" in closed["summaries"][0]["text"]
        # nothing of the meals is drawn: Chromium still measures the boxes under a closed <details> (its content is only skipped), so ask
        # whether a closed disclosure sits above each one, as BLOCK_JS does
        assert page.locator(".cb-slot").evaluate_all(
            "els => els.filter(e => { for (let d = e.parentElement; d; d = d.parentElement) if (d.tagName === 'DETAILS' && !d.open) return false; return true; }).length"
        ) == 0
        if size == (390, 844) and zoom == 100:
            _shot(page, "closed_390x844")
    finally:
        ctx.close()


def test_a_plant_iron_food_alone_is_placed_with_its_two_tips_and_the_plan_is_written_around_it(browser, server):
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, "Iron 14 mg 100%")
        _swipe(page, {"Iron": "repl"})
        _meals(page)
        food = page.locator(".cb-chip[data-kind='food']")
        if food.count():  # whichever food the card offered: a plant-iron food is placed, an animal one needs no layout
            assert page.locator(".cb-slot").count() == 1 and page.locator(".cb-slot details.cb-tip").count() >= 1
            assert page.get_by_text("Written around your day", exact=False).count() == 1
        else:
            assert page.get_by_text("Written around your day", exact=False).count() == 0
    finally:
        ctx.close()


def test_the_dialog_part_is_there_for_a_kept_calcium_pill_and_only_the_pharmacist_line_for_magnesium(browser, server):
    ctx, page = _phone(browser, server, (390, 844))
    try:
        _label(page, RICH)
        _swipe(page, {"Vitamin B12": "repl", "Vitamin C": "repl"})
        page.locator(".plan-kicker", has_text="Your plan").wait_for()
        page.locator('[class*="st-key-planbtn_keep_"] button', has_text="Calcium").first.click()
        dialog = page.locator(DIALOG)
        dialog.wait_for(timeout=10000)
        settle(page)
        part = dialog.get_by_text("When and how to take it")
        assert part.count() == 1
        part.click()
        page.wait_for_timeout(300)
        text = dialog.inner_text()
        for takeaway in ("Split more than 500 mg of calcium over two meals", "Take calcium carbonate with a meal", "Take iron and calcium pills at different meals",
                         "Vitamin D helps your body absorb calcium"):
            assert takeaway in text, takeaway
        link = dialog.locator("a", has_text="NIH ODS").first
        assert link.get_attribute("target") == "_blank"  # Streamlit's own links open in a new tab
        page.keyboard.press("Escape")
        dialog.wait_for(state="detached", timeout=10000)
        settle(page)
        page.locator('[class*="st-key-planbtn_keep_"] button', has_text="Magnesium").first.click()
        dialog.wait_for(timeout=10000)
        settle(page)
        dialog.get_by_text("When and how to take it").click()
        page.wait_for_timeout(300)
        text = dialog.inner_text()
        assert "On regular medicine? Ask your pharmacist about timing" in text
        assert not any(t in text for t in ("Take iron with a vitamin C food", "meal that has some fat", "Split more than 500"))
    finally:
        ctx.close()


@pytest.fixture
def prefetch_server(tmp_path_factory):
    """The fake-AI server of test_ux_browser's `ai_server`, but with the background meal plan ON (the default): the plan starts when the results
    open and the page is redrawn once when it is done (the fragment of `_live_meal_plan`), which is the rerun this test is about."""
    import subprocess
    import sys
    import urllib.request

    import fake_blockbrain as fb
    from test_ux_browser import _FAKE_ROUTING, _free_port

    fake = fb.FakeBlockbrain().start()
    routing = tmp_path_factory.mktemp("fake_routing_prefetch")
    (routing / "sitecustomize.py").write_text(_FAKE_ROUTING)
    port = _free_port()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(routing), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
               SUPPSWIPE_TEST_FAKE_BB=fake.url, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
               BLOCKBRAIN_API_KEY=fb.API_KEY, BLOCKBRAIN_ORG_ID=fb.ORG_ID, BLOCKBRAIN_BOT_ID=fb.BOT_ID)
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy", "BLOCKBRAIN_MODEL", "SUPPSWIPE_PREFETCH_MEALS"):
        env.pop(name, None)
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(ROOT / "swipe_mobile_app" / "app.py"), "--server.port", str(port),
                             "--server.headless", "true", "--browser.gatherUsageStats", "false"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://localhost:{port}/"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "_stcore/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    yield url, fake
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    fake.stop()


def test_an_opened_tip_is_still_open_after_the_meal_plan_is_written(browser, prefetch_server):
    """The plan's completion redraws the page (one full run). A native disclosure must not snap shut under the reader. It did when the
    plan's controls drew one element more once the plan was done (the button and the finished plan are two, the live plan is one):
    the day below moved up by one in the page and Streamlit remounted it. The controls are now one container (_render_meals_tab)."""
    url, fake = prefetch_server
    fake.stream_script = [{"delay": 8, "text": "**Breakfast**\n- Oats with berries (80 g)\n\n**Lunch**\n- Lentil soup (120 g)\n\n**Dinner**\n- Baked fish (150 g)"}]
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="networkidle")
        start_sample(page)
        _swipe(page, {"Vitamin C": "repl", "Vitamin B12": "repl"})
        _meals(page)
        page.locator(".plan-writing").wait_for(timeout=15000)  # the background plan is being written
        assert page.locator(".cb-day").evaluate("d => d.open") is True  # (_meals opened the strip)
        first = page.locator(".cb-slot details.cb-tip").first
        first.locator("summary").click()
        assert first.evaluate("d => d.open") is True
        page.get_by_role("button", name="🔄 Different meals").wait_for(timeout=40000)  # the plan is done: the page has been redrawn once
        settle(page, 1.0)
        assert page.locator(".plan-writing").count() == 0
        assert page.locator(".cb-day").evaluate("d => d.open") is True, "the day snapped shut: its index in the Meals tab moved (the plan controls must stay ONE element)"
        assert page.locator(".cb-slot details.cb-tip").first.evaluate("d => d.open") is True, "the tip snapped shut: its day was remounted (the plan controls must stay ONE element)"
        assert page.locator(".cb-all").count() == 1
    finally:
        ctx.close()


def test_the_feature_switch_removes_everything_in_the_browser(browser, tmp_path_factory):
    import subprocess
    import sys
    import urllib.request

    from test_ux_browser import _free_port

    port = _free_port()
    env = dict(os.environ, BLOCKBRAIN_API_KEY="dummy-offline-key", BLOCKBRAIN_ORG_ID="", BLOCKBRAIN_MODEL="", BLOCKBRAIN_BOT_ID="",
               SUPPSWIPE_PREFETCH_MEALS="0", SUPPSWIPE_COMBINE="0")
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(ROOT / "swipe_mobile_app" / "app.py"), "--server.port", str(port),
                             "--server.headless", "true", "--browser.gatherUsageStats", "false"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://localhost:{port}/"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "_stcore/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        ctx, page = open_page(browser, url, (390, 844))
        try:
            rows: dict[str, int] = {}
            _label(page, RICH)
            _swipe(page, {}, on_card=lambda name: rows.__setitem__(name, card_frame(page).locator(".pair").count()))
            assert sum(rows.values()) == 0
            _meals(page)
            assert page.locator(".cb-card").count() == 0
        finally:
            ctx.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

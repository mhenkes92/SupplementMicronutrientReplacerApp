"""Browser checks (real Chromium, a touch phone at 2x) of the two food-amount panels of the swipe card: "Match your pill" and "Athlete target".

The owner asked for a clearer separation between "match this pill dose" and the athlete target. The offline pins (markup, colours, the kinds of
text the app builds) are tests/test_ux_dose_blocks.py; this file measures what only a browser can: both panels inside the card and the "Eat
instead" box with a gap between them, the figures right-aligned on one line, long verdicts that wrap under the sentence, the 44 px athlete
button that still opens the guide while a drag from it still swipes, a card with one amount or none (no empty panel, no stray gap), how much
taller the panels are than the old one-box lines (they are measured against that old markup, kept below as LEGACY, in the same browser and
fonts), the contrast of every text on its tint for every card colour the page draws, and 150 / 200 % text.

The card is drawn by tests/card_harness.py: the component's own page in a host page that plays Streamlit's part (no server, no scan), with the
props of REAL cards (SHAPES: every kind of text the app's builder produces). Opt-in like tests/test_ux_browser.py (SUPPSWIPE_BROWSER_TESTS=1)."""
from __future__ import annotations

import io
import os
import re

import pytest

if os.getenv("SUPPSWIPE_BROWSER_TESTS", "") != "1":
    pytest.skip("browser tests are opt-in (SUPPSWIPE_BROWSER_TESTS=1)", allow_module_level=True)
sync_api = pytest.importorskip("playwright.sync_api")

from card_harness import COMPONENT, SHAPES, Harness, card_args  # noqa: E402

ROOMY = [(390, 844), (412, 915)]  # the normal density
SMALL = [(320, 568), (320, 640), (360, 640), (375, 667)]  # the tight density
LONG_NOTE = ("Few foods are rich in vitamin D: UV-exposed mushrooms give D2, which raises blood levels less than D3; oily fish and egg yolk give D3. "
             "Sunlight is the main source. ℹ️ October–March the sun in Germany is too weak for your skin to make vitamin D, and food alone "
             "rarely reaches the 20 µg/day reference. Many people in Germany take vitamin D in winter — ask your doctor whether that suits you.")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def harness():
    with Harness() as h:
        yield h


def shape(name: str, **override):
    """The props of a card of the named kind (SHAPES), the rest as the sample label's first card has it."""
    return card_args(**{**SHAPES[name], **override})


# What the card shows of its two panels: geometry and text, in the frame's coordinates.
PANELS_JS = """() => {
  // The lines the TEXT of an element takes (`lines` counts boxes, and a flex item such as .pl-sent is one box however often it wraps).
  const textLines = e => { const g = document.createRange(); g.selectNodeContents(e); return new Set([...g.getClientRects()].map(b => Math.round(b.top))).size; };
  const r = e => { if (!e) { return null; } const b = e.getBoundingClientRect(); return { x: b.left, y: b.top, w: b.width, h: b.height, r: b.right, b: b.bottom }; };
  const one = (p, s) => { const e = p.querySelector(s); if (!e) { return null; } const cs = getComputedStyle(e);
    return { ...r(e), text: e.textContent.replace(/\\s+/g, ' ').trim(), fs: parseFloat(cs.fontSize), lh: parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.3,
             lines: e.getClientRects().length, display: cs.display, weight: cs.fontWeight, transform: cs.textTransform, tl: textLines(e) }; };
  const q = s => document.querySelector(s);
  const cs = e => getComputedStyle(e);
  const panels = [...document.querySelectorAll('.portion .pl')];
  const fw = q('.foodwrap'), kids = fw ? [...fw.children] : [];
  return {
    card: r(q('#card')), foodwrap: r(fw), foodrow: r(q('.foodrow')), portion: r(q('.portion')),
    tight: q('#wrap').classList.contains('tight'), body: { sw: q('#cbody').scrollWidth, cw: q('#cbody').clientWidth },
    fwPad: fw ? parseFloat(cs(fw).paddingBottom) + parseFloat(cs(fw).borderBottomWidth) : 0,
    lastKid: kids.length ? r(kids[kids.length - 1]) : null,
    portionMargin: q('.portion') ? parseFloat(cs(q('.portion')).marginTop) : null,
    prevB: q('.portion') && q('.portion').previousElementSibling ? q('.portion').previousElementSibling.getBoundingClientRect().bottom : null,
    panels: panels.map(p => ({
      ...r(p), cls: p.className, tag: p.tagName, text: p.textContent.replace(/\\s+/g, ' ').trim(), sw: p.scrollWidth, cw: p.clientWidth,
      padR: parseFloat(cs(p).paddingRight), borderR: parseFloat(cs(p).borderRightWidth), bg: cs(p).backgroundColor, bar: cs(p).borderLeftColor,
      head: one(p, '.pl-head'), amt: one(p, '.pl-amt'), fig: one(p, '.pl-amt b'), sent: one(p, '.pl-sent'), verdict: one(p, '.pl-verdict'),
      note: one(p, '.pl-note'), why: one(p, '.pl-why'), chev: one(p, '.chev'),
      kids: [...p.querySelectorAll('.pl-head, .pl-amt, .pl-sent, .pl-verdict, .pl-note')].map(r) })) };
}"""


def panels(fr) -> dict:
    return fr.evaluate(PANELS_JS)


def open_card(harness, browser, size, args, zoom=100, later=False, **kw):
    """The card on screen. `later`: it is not the first card of a scan, so on a tight touch card the swipe hint is gone (as on cards 2-7)."""
    if later:
        ctx, pg, fr = harness.open(browser, size, card_args(), zoom=zoom, **kw)
        Harness.redraw(pg, {**args, "index": 1})
    else:
        ctx, pg, fr = harness.open(browser, size, args, zoom=zoom, **kw)
    return ctx, pg, fr


def inside(inner: dict, outer: dict, slack: float = 0.6) -> bool:
    return inner["x"] >= outer["x"] - slack and inner["y"] >= outer["y"] - slack and inner["r"] <= outer["r"] + slack and inner["b"] <= outer["b"] + slack


# ------------------------------------------------------------------ two panels, a gap, a heading each
@pytest.mark.parametrize("size", [(390, 844), (360, 640), (320, 568)])
@pytest.mark.parametrize("name", ["plain", "three digits", "units in brackets", "athlete verdict", "two verdicts"])
def test_both_panels_are_inside_the_card_and_the_eat_instead_box_with_a_clear_gap(harness, browser, size, name):
    ctx, pg, fr = open_card(harness, browser, size, shape(name), later=True)
    try:
        m = panels(fr)
        match, guide = m["panels"]
        assert (match["cls"], match["tag"]) == ("pl pl-match", "DIV") and (guide["cls"], guide["tag"]) == ("pl pl-guide", "BUTTON"), m["panels"]
        for p in (match, guide):
            assert inside(p, m["foodwrap"]) and inside(p, m["card"]), (name, p)  # in the "Eat instead" box, which is in the card
            assert p["y"] >= m["foodrow"]["b"] - 0.5, (name, p["y"], m["foodrow"])  # under the food and its source line
        assert guide["y"] - match["b"] >= 8 - 0.01, guide["y"] - match["b"]  # a clear gap: two things, not one
        assert abs(match["x"] - guide["x"]) < 0.5 and abs(match["w"] - guide["w"]) < 0.5  # one column
        assert match["head"]["text"] == "Match your pill" and guide["head"]["text"] == "Athlete target"
        for p in (match, guide):
            head = p["head"]
            assert head["transform"] == "uppercase" and head["weight"] == "800" and head["fs"] >= 11.5, head  # the style of the "Eat instead" label
        assert match["bg"] != guide["bg"] and match["bar"] != guide["bar"]  # told apart by their tint and their accent bar, not by the words alone
        assert pg.errors == []
    finally:
        ctx.close()


@pytest.mark.parametrize("size", ROOMY + SMALL)
def test_each_sentence_says_what_its_amount_is_a_match_for(harness, browser, size):
    ctx, pg, fr = open_card(harness, browser, size, shape("plain"), later=True)
    try:
        match, guide = panels(fr)["panels"]
        assert match["sent"]["text"] == "Equivalent to your pill's 80 mg"
        assert guide["sent"]["text"] == "For very active people: 200 mg"
        assert match["sent"]["tl"] == 1 and guide["sent"]["tl"] == 1, (match["sent"], guide["sent"])  # one plain line on every phone
    finally:
        ctx.close()
    # "Equivalent to", not "the same as": the food amount is sized to the pill's dose, not always to the number printed on it (folic acid in
    # DFE, a weekly pill's one day, a range's low end), so the sentence never claims an equal quantity and never calls a range "the dose"
    for name, want_match, want_guide in [
        ("IU dose", "Equivalent to your pill's 1000 IU", "For very active people: 25 mcg"),
        ("no athlete target label", "Equivalent to your pill's 80 mg", "For very active people"),  # no target to name: no colon, no dangling words
        ("weekly dose", "Equivalent to one day of your pill", "For very active people: 25 mcg"),  # a long dose label is not repeated
        ("dose range", "Equivalent to the low end (100 mg)", "For very active people: 200 mg"),  # the portion is sized to the low end
    ]:
        ctx, pg, fr = open_card(harness, browser, size, shape(name), later=True)
        try:
            match, guide = panels(fr)["panels"]
            assert (match["sent"]["text"], guide["sent"]["text"]) == (want_match, want_guide), name
        finally:
            ctx.close()


# ------------------------------------------------------------------ the figures
@pytest.mark.parametrize("size", [(390, 844), (320, 568)])
@pytest.mark.parametrize("name", ["plain", "three digits", "IU dose", "athlete amount smaller", "units in brackets", "mushroom note"])
def test_the_figures_are_big_right_aligned_on_one_line_and_in_line_with_each_other(harness, browser, size, name):
    ctx, pg, fr = open_card(harness, browser, size, shape(name), later=True)
    try:
        match, guide = panels(fr)["panels"]
        for p, which in ((match, "matchDose"), (guide, "rdaAmount")):
            amt, fig = p["amt"], p["fig"]
            want = SHAPES[name][which].split(" (")[0]
            assert amt["text"] == "eat " + want and fig["text"] == want, (name, amt["text"])  # "eat ~35 g": the amount without its bracket
            assert amt["lines"] == 1 and amt["h"] < 1.6 * fig["fs"], amt  # one line, never broken
            assert fig["fs"] >= 1.4 * p["sent"]["fs"] and fig["fs"] >= 17, (fig["fs"], p["sent"]["fs"])  # the big figure
            assert fig["weight"] in ("800", "900", "700") and int(fig["weight"]) >= 700
            assert abs(amt["r"] - (p["r"] - p["borderR"] - p["padR"])) <= 1.5, (amt["r"], p["r"], p["padR"])  # right-aligned at the panel's inner edge
            assert amt["x"] > p["head"]["r"] - 0.5, (amt, p["head"])  # beside the heading, not over it
            assert abs(amt["y"] - p["head"]["y"]) < 14 and amt["b"] <= p["sent"]["y"] + 3, (amt, p["head"], p["sent"])  # on the heading's row, above the sentence
        assert abs(match["amt"]["r"] - guide["amt"]["r"]) <= 1, (match["amt"]["r"], guide["amt"]["r"])  # the two figures end on one line: compared at a glance
        assert abs(match["fig"]["fs"] - guide["fig"]["fs"]) < 0.1
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(390, 844), (412, 680), (320, 568)])
def test_a_bracket_after_an_amount_is_a_small_note_under_the_figure_and_not_part_of_it(harness, browser, size):
    """"(~0.6 Brazil nuts)" is the end of the sentence's row when it fits there and a row of its own when it does not; either way it is small,
    right-aligned under the figure it belongs to and clear of the sentence and the hint."""
    ctx, pg, fr = open_card(harness, browser, size, shape("units in brackets"), later=True)
    try:
        match, guide = panels(fr)["panels"]
        assert match["note"]["text"] == "(~0.6 Brazil nuts)" and guide["note"]["text"] == "(~0.7 Brazil nuts)"
        for p in (match, guide):
            note, sent = p["note"], p["sent"]
            assert note["fs"] < sent["fs"] and p["verdict"] is None and inside(note, p) and p["fig"]["fs"] > 1.4 * note["fs"]
            assert note["x"] >= sent["r"] - 1 or note["y"] >= sent["b"] - 1, (note, sent)  # beside the sentence or under it, never over it
            if p["why"] and p["why"]["display"] != "none":
                assert note["x"] >= p["why"]["r"] - 1 or note["y"] >= p["why"]["b"] - 1, (note, p["why"])  # and clear of the hint
            assert abs(note["r"] - p["amt"]["r"]) <= 1.5, (note["r"], p["amt"]["r"])  # right-aligned with the figure
            assert note["lines"] == 1
    finally:
        ctx.close()
    ctx, pg, fr = open_card(harness, browser, size, shape("mushroom note"), later=True)
    try:
        match = panels(fr)["panels"][0]
        assert match["fig"]["text"] == "~20 g"
        note = match["note"]
        assert note["text"].startswith("(only UV-treated mushrooms") and inside(note, match)  # a long note is a block under the sentence, from its left edge
        assert note["y"] >= match["sent"]["b"] - 1 and note["x"] <= match["sent"]["x"] + 1.5 and match["sw"] <= match["cw"] + 1
    finally:
        ctx.close()


def test_a_short_bracket_shares_the_row_of_the_sentence_where_there_is_room_for_it(harness, browser):
    ctx, pg, fr = open_card(harness, browser, (412, 680), shape("units in brackets"), later=True)
    try:
        match, guide = panels(fr)["panels"]
        assert match["note"]["y"] < match["sent"]["b"] and match["note"]["x"] >= match["sent"]["r"] - 1, (match["note"], match["sent"])  # the pill panel has room
        assert guide["note"]["y"] >= guide["sent"]["b"] - 1  # the athlete panel has the hint too: its note has a row of its own
    finally:
        ctx.close()
    ctx, pg, fr = open_card(harness, browser, (320, 568), shape("units in brackets"), later=True)
    try:
        for p in panels(fr)["panels"]:
            assert p["note"]["y"] >= p["sent"]["b"] - 1  # no room beside a sentence on a 320 px phone
    finally:
        ctx.close()


# ------------------------------------------------------------------ verdicts
@pytest.mark.parametrize("zoom,size", [(100, (320, 568)), (100, (390, 844)), (150, (320, 640)), (200, (320, 568)), (200, (390, 844))])
@pytest.mark.parametrize("name", ["athlete verdict", "two verdicts", "a lot of food", "long label", "mushroom note"])
def test_a_long_verdict_wraps_under_the_sentence_and_nothing_overflows(harness, browser, zoom, size, name):
    args = shape(name)
    ctx, pg, fr = open_card(harness, browser, size, args, zoom=zoom, later=True)
    try:
        m = panels(fr)
        assert len(m["panels"]) == 2 and m["body"]["sw"] <= m["body"]["cw"] + 1, (name, m["body"])  # the card does not scroll sideways
        for p, which in zip(m["panels"], ("matchDose", "rdaAmount")):
            assert p["sw"] <= p["cw"] + 1, (name, which, p["sw"], p["cw"])  # nothing runs out of the panel
            assert inside(p, m["foodwrap"], 1.0)
            for box in p["kids"]:
                assert box["x"] >= p["x"] - 0.5 and box["r"] <= p["r"] + 0.5, (name, which, box, p["x"], p["r"])
            text = SHAPES[name][which]
            if re.match(r"(not practical|a lot of)", text):
                v = p["verdict"]
                assert v and v["text"] == text and p["amt"] is None and p["fig"] is None, (name, which, p["text"])  # verbatim; "eat" and a figure only before an amount
                assert v["y"] >= p["sent"]["b"] - 1 and v["x"] <= p["sent"]["x"] + 1.5, (v, p["sent"])  # under the sentence, from its left edge
                assert v["r"] <= p["r"] - p["borderR"] - p["padR"] + 1 and v["fs"] >= 11.5, (v, p)
        assert pg.errors == []
    finally:
        ctx.close()


# ------------------------------------------------------------------ the athlete panel is a real button
def centre_of(m: dict, i: int) -> tuple:
    p = m["panels"][i]
    return p["x"] + p["w"] / 2, p["y"] + p["h"] / 2


def page_point(pg, fx: float, fy: float) -> tuple:
    box = pg.locator("iframe").bounding_box()
    return box["x"] + fx, box["y"] + fy


def events(pg) -> list:
    return pg.evaluate("window.__events")


@pytest.mark.parametrize("size", [(390, 844), (320, 568), (360, 640)])
def test_the_athlete_panel_is_at_least_44px_high_and_a_tap_opens_the_guide_without_deciding_anything(harness, browser, size):
    ctx, pg, fr = open_card(harness, browser, size, shape("plain"), later=True)
    try:
        m = panels(fr)
        guide = m["panels"][1]
        assert guide["h"] >= 44 - 0.01 and guide["w"] >= 44, guide  # a thumb-sized target
        # a tap on the heading, on the sentence and on the chevron all open the guide (the whole panel is the button)
        for point in ((guide["head"]["x"] + 6, guide["head"]["y"] + 4), (guide["sent"]["x"] + 20, guide["sent"]["y"] + 6), (guide["chev"]["x"] + 2, guide["chev"]["y"] + 6)):
            pg.mouse.click(*page_point(pg, *point))
            pg.wait_for_timeout(50)
        guides = [e for e in events(pg) if e.get("kind") == "guide"]
        assert len(guides) == 3 and all(e["nutrient"] == "vitamin c" and "dir" not in e for e in guides), events(pg)
        assert not [e for e in events(pg) if "dir" in e]  # nothing was decided
        before = len(events(pg))
        pg.mouse.click(*page_point(pg, *centre_of(m, 0)))  # the pill panel is not a button
        pg.wait_for_timeout(50)
        assert len(events(pg)) == before
        fr.locator("button.pl-guide").focus()  # the keyboard
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(50)
        assert len([e for e in events(pg) if e.get("kind") == "guide"]) == 4
        assert fr.evaluate("document.activeElement.className") == "pl pl-guide"
        assert pg.errors == []
    finally:
        ctx.close()


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("direction", [-1, 1])
def test_a_mouse_drag_that_starts_on_either_panel_still_swipes_the_card(harness, browser, which, direction):
    ctx, pg, fr = open_card(harness, browser, (390, 844), shape("plain"), later=True, touch=False)
    try:
        m = panels(fr)
        x, y = page_point(pg, m["panels"][which]["x"] + 60, m["panels"][which]["y"] + m["panels"][which]["h"] / 2)
        pg.mouse.move(x, y)
        pg.mouse.down()
        for step in range(1, 13):
            pg.mouse.move(x + direction * 22 * step, y + step)
        pg.mouse.up()
        pg.wait_for_timeout(100)
        swipes = [e for e in events(pg) if "dir" in e]
        assert [e["dir"] for e in swipes] == ["right" if direction > 0 else "left"], events(pg)
        assert not [e for e in events(pg) if e.get("kind") == "guide"]  # a drag is no tap on the button
        assert pg.errors == []
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(390, 844), (320, 568)])
def test_a_finger_drag_that_starts_on_the_athlete_panel_swipes_it_too(harness, browser, size):
    ctx, pg, fr = open_card(harness, browser, size, shape("plain"), later=True)
    try:
        m = panels(fr)
        guide = m["panels"][1]
        x, y = page_point(pg, guide["x"] + 40, guide["y"] + guide["h"] / 2)
        cdp = ctx.new_cdp_session(pg)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        for i in range(1, 9):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x - 24 * i, "y": y + i}]})
            pg.wait_for_timeout(16)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        pg.wait_for_timeout(150)
        assert [e["dir"] for e in events(pg) if "dir" in e] == ["left"], events(pg)
        assert not [e for e in events(pg) if e.get("kind") == "guide"]
    finally:
        ctx.close()


# ------------------------------------------------------------------ one amount or none
@pytest.mark.parametrize("size", [(390, 844), (320, 568)])
def test_a_card_with_one_amount_has_one_panel_and_no_empty_one(harness, browser, size):
    for name, only in (("only the pill match", "pl pl-match"), ("only the athlete target", "pl pl-guide")):
        ctx, pg, fr = open_card(harness, browser, size, shape(name), later=True)
        try:
            m = panels(fr)
            assert [p["cls"] for p in m["panels"]] == [only], (name, m["panels"])
            p = m["panels"][0]
            assert p["head"]["text"] and p["sent"]["text"] and p["amt"]["text"].startswith("eat ~") and p["h"] >= 30, p  # all of it, nothing blank
            assert abs(m["portion"]["h"] - p["h"]) < 0.5, (m["portion"], p)  # the wrapper is the one panel and nothing else
            assert abs(m["portion"]["y"] - m["prevB"] - m["portionMargin"]) < 0.6 and m["portionMargin"] in (6, 10), m  # the usual distance under the food, no stray gap
            if only == "pl pl-guide":
                assert p["h"] >= 44 - 0.01
        finally:
            ctx.close()


@pytest.mark.parametrize("size", [(390, 844), (320, 568)])
def test_a_card_with_no_amount_draws_no_panel_and_leaves_no_gap(harness, browser, size):
    ctx, pg, fr = open_card(harness, browser, size, shape("no amounts"), later=True)
    try:
        m = panels(fr)
        assert m["panels"] == [] and m["portion"] is None and fr.locator(".portion").count() == 0
        assert abs((m["foodwrap"]["b"] - m["fwPad"]) - m["lastKid"]["b"]) < 0.5, (m["foodwrap"], m["lastKid"], m["fwPad"])  # the box ends where its last row does
        assert fr.evaluate("document.getElementById('card').getAttribute('aria-label')").count("Athlete target") == 0
        assert pg.errors == []
    finally:
        ctx.close()


def test_the_next_card_replaces_the_panels_and_leaves_none_behind(harness, browser):
    ctx, pg, fr = open_card(harness, browser, (390, 844), shape("plain"))
    try:
        for name, count in (("only the pill match", 1), ("no amounts", 0), ("two verdicts", 2), ("only the athlete target", 1), ("plain", 2)):
            Harness.redraw(pg, {**shape(name), "index": 1}, 250)
            assert len(panels(fr)["panels"]) == count and fr.locator(".portion").count() == (1 if count else 0), name
            assert fr.locator("button.pl-guide").count() == (1 if name in ("only the athlete target", "two verdicts", "plain") else 0), name
        assert pg.errors == []
    finally:
        ctx.close()


# ------------------------------------------------------------------ compact
# The component's old one-box lines, as they were before the panels (kept here so the growth is measured in THIS browser, with THESE fonts):
# one green box of two lines, the athlete line a button. Class names differ (`o-`) so the new rules do not touch them.
LEGACY_CSS = """
  .o-portion { margin-top: 10px; padding: 8px 10px; border-radius: 12px; background: #ecfdf5; border: 1px solid #a7f3d0; color: #14532d; font-size: .84rem; line-height: 1.5; }
  .o-portion .o-pl { display: block; }
  .o-portion button.o-pl { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; text-align: left; font: inherit; color: inherit; background: none; border: 0; padding: 4px 0; margin: 0; cursor: pointer; min-height: 32px; }
  .o-portion button.o-pl .o-text { flex: 1 1 auto; min-width: 0; }
  .o-portion button.o-pl .chev { flex: 0 0 auto; font-weight: 800; color: #047857; }
  .o-portion b { color: #0f172a; }
  #wrap.tight .o-portion { margin-top: 6px; padding: 4px 8px; }
"""


def legacy_html(args: dict) -> str:
    def amount(txt: str) -> str:
        return ("eat " if re.match(r"[~<\d]", txt) else "") + "<b>" + txt + "</b>"

    rows = ""
    if args.get("matchDose"):
        rows += '<span class="o-pl">\U0001f48a Match this dose &rarr; ' + amount(args["matchDose"]) + "</span>"
    if args.get("rdaAmount"):
        rows += ('<button type="button" class="o-pl"><span class="o-text">\U0001f3c3 Target for very active people'
                 + (" (" + args["rdaLabel"] + ")" if args.get("rdaLabel") else "") + " &rarr; " + amount(args["rdaAmount"])
                 + '</span><span class="chev">&rsaquo;</span></button>')
    return '<div class="o-portion">' + rows + "</div>" if rows else ""


MEASURE_JS = """([css, html]) => {
  const body = document.getElementById('cbody'), box = document.querySelector('.portion');
  const now = { box: box.getBoundingClientRect().height, hidden: body.scrollHeight - body.clientHeight };
  const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  box.outerHTML = html;
  const old = document.querySelector('.o-portion');
  return { now, before: { box: old.getBoundingClientRect().height, hidden: body.scrollHeight - body.clientHeight }, tight: document.getElementById('wrap').classList.contains('tight') };
}"""

# How much taller than the old box the two panels may be (px). Measured in Chromium here: +28 on a normal card in the roomy density and +20 in the
# tight one (one line of text per panel), +30 at the most with a note or a verdict in the tight density, +40 in the roomy one (the old box wrapped
# those lines too, only later). Three more lines than a plain card are the price of two panels with a heading each.
GROWTH = {"plain": 30, "three digits": 30, "IU dose": 30, "athlete amount smaller": 30, "units in brackets": 44, "athlete verdict": 44,
          "a lot of food": 44, "long label": 44, "mushroom note": 44, "two verdicts": 44,
          "no athlete target label": 46}  # (the old line was one line shorter when it carried no target: "Target for very active people -> eat ~88 g")


@pytest.mark.parametrize("size", [(390, 844), (412, 915), (375, 667), (360, 640), (320, 640), (320, 568)])
@pytest.mark.parametrize("name", list(GROWTH))
def test_the_two_panels_are_not_much_taller_than_the_one_box_they_replace(harness, browser, size, name):
    args = shape(name, bioNote=LONG_NOTE)
    ctx, pg, fr = open_card(harness, browser, size, args, later=True)
    try:
        got = fr.evaluate(MEASURE_JS, [LEGACY_CSS, legacy_html(args)])
        grew = got["now"]["box"] - got["before"]["box"]
        assert grew <= GROWTH[name], (name, size, got)
        # a card that scrolls scrolls by no more than it did before the panels (+ the same allowance)
        assert got["now"]["hidden"] - got["before"]["hidden"] <= GROWTH[name], (name, size, got)
    finally:
        ctx.close()


@pytest.mark.parametrize("size", [(320, 568), (320, 640)])
def test_the_card_that_scrolls_on_a_small_phone_scrolls_no_more_than_30px_further_than_before(harness, browser, size):
    """Vitamin D3 (the long note) is the card that scrolls on 320x568 / 320x640. Same card, same frame, old box against new panels."""
    args = shape("three digits", bioNote=LONG_NOTE)
    ctx, pg, fr = open_card(harness, browser, size, args, later=True)
    try:
        got = fr.evaluate(MEASURE_JS, [LEGACY_CSS, legacy_html(args)])
        assert got["tight"] and got["before"]["hidden"] > 0, got  # it does scroll, in the tight density
        assert got["now"]["hidden"] - got["before"]["hidden"] <= 30, got
    finally:
        ctx.close()


# ------------------------------------------------------------------ colours
def _unique_card_themes(sw) -> list:
    seen, themes = set(), []
    for needle, colour in sw._NUTRIENT_BASE_COLORS + [("", "#64748b")]:
        if colour not in seen:
            seen.add(colour)
            themes.append((needle or "default", sw._component_card_theme(needle or "unknown nutrient")))
    return themes


COLOURS_JS = """() => {
  const rgb = s => (s.match(/[\\d.]+/g) || []).map(Number);
  const out = { panels: [], others: {} };
  for (const p of document.querySelectorAll('.portion .pl')) {
    const bg = getComputedStyle(p).backgroundColor, parts = [];
    for (const sel of ['.pl-head', '.pl-sent', '.pl-amt', '.pl-amt b', '.pl-note', '.pl-verdict', '.pl-why', '.chev']) {
      const e = p.querySelector(sel); if (e) { parts.push([sel, getComputedStyle(e).color]); }
    }
    out.panels.push({ cls: p.className, bg, border: getComputedStyle(p).borderTopColor, bar: getComputedStyle(p).borderLeftColor, parts });
  }
  for (const [name, sel] of [['warn', '.warn'], ['note', '.bio']]) {
    const e = document.querySelector(sel); if (e) { const cs = getComputedStyle(e); out.others[name] = [cs.backgroundColor, cs.borderTopColor]; }
  }
  for (const c of document.querySelectorAll('#card .chip')) { const cs = getComputedStyle(c); out.others[c.id] = [cs.backgroundImage, cs.borderTopColor]; }
  for (const id of ['btnKeep', 'btnRepl', 'btnBack']) { const cs = getComputedStyle(document.getElementById(id)); out.others[id] = [cs.backgroundColor, cs.borderTopColor, cs.color]; }
  return out;
}"""


def _rgb(css: str) -> tuple:
    nums = [float(v) for v in re.findall(r"[\d.]+", css)]
    return tuple(nums[:3]) + ((nums[3],) if len(nums) > 3 else ())


def _lum(rgb) -> float:
    lin = [(c / 255) / 12.92 if c / 255 <= 0.03928 else (((c / 255) + 0.055) / 1.055) ** 2.4 for c in rgb[:3]]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a, b) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_text_of_both_panels_reads_on_its_tint_on_every_card_colour_the_page_draws(harness, browser, sw):
    args = card_args(**{**SHAPES["units in brackets"], "rdaAmount": "not practical from food alone (~71 g/day; realistic max ~70 g/day)"}, canAsk=True)
    ctx, pg, fr = harness.open(browser, (390, 844), args)
    try:
        themes = _unique_card_themes(sw)
        assert len(themes) >= 15, len(themes)
        checked = set()
        for name, theme in themes:
            Harness.redraw(pg, {**args, "accent": theme["accent"], "ink": theme["accent2"], "bg": theme["bg"]}, 120)
            seen = fr.evaluate(COLOURS_JS)
            assert [p["cls"] for p in seen["panels"]] == ["pl pl-match", "pl pl-guide"], name
            for p in seen["panels"]:
                bg = _rgb(p["bg"])
                assert len(bg) == 3 or bg[3] == 1, (name, p["bg"])  # opaque: what is behind the panel (the card's colour) cannot change its contrast
                for sel, colour in p["parts"]:
                    checked.add(sel)
                    assert _ratio(_rgb(colour), bg) >= 4.5, (name, p["cls"], sel, colour, p["bg"], _ratio(_rgb(colour), bg))
        assert checked >= {".pl-head", ".pl-sent", ".pl-amt", ".pl-amt b", ".pl-note", ".pl-verdict", ".pl-why", ".chev"}, checked  # every kind of text was on screen
    finally:
        ctx.close()


def test_the_athlete_panel_is_none_of_the_colours_the_rest_of_the_card_uses(harness, browser):
    args = card_args(**{**SHAPES["plain"], "warn": "Heads-up: a high dose.", "canAsk": True, "canSwap": True})
    ctx, pg, fr = harness.open(browser, (390, 844), args)
    try:
        seen = fr.evaluate(COLOURS_JS)
        match, guide = seen["panels"]
        assert match["bg"] != guide["bg"] and match["bar"] != guide["bar"] and match["border"] != guide["border"]
        assert set(seen["others"]) >= {"warn", "note", "btnAsk", "btnSwap", "btnKeep", "btnRepl", "btnBack"}, seen["others"].keys()
        own = {_rgb(guide[k]) for k in ("bg", "border", "bar")}
        for name, found in seen["others"].items():
            for value in found:
                for rgb in re.findall(r"rgba?\([^)]*\)", value):
                    assert _rgb(rgb)[:3] not in {c[:3] for c in own}, (name, rgb, own)  # not one of their colours, tint or line
                    if _lum(_rgb(rgb)) > 0.65 and (len(_rgb(rgb)) == 3 or _rgb(rgb)[3] > 0.5):
                        assert sum(abs(a - b) for a, b in zip(_rgb(rgb)[:3], _rgb(guide["bg"])[:3])) >= 15, (name, rgb, guide["bg"])
    finally:
        ctx.close()


@pytest.mark.parametrize("zoom,size", [(100, (320, 568)), (150, (390, 844)), (200, (320, 640))])
def test_an_amount_with_its_unit_never_breaks_between_the_number_and_the_unit(harness, browser, zoom, size):
    """"For very active people: 200 mg" may wrap, but never between "200" and "mg" (a no-break space), whatever the text size."""
    probe = """() => { const out = [];
      for (const s of document.querySelectorAll('.pl-sent')) {
        const node = s.firstChild, at = node.textContent.search(/\\d[\\d.,]*\u00a0\\S+$|\\d[\\d.,]*\u00a0\\S+ in your pill$/);
        if (at < 0) { out.push(['no nbsp', node.textContent]); continue; }
        const m = node.textContent.slice(at).match(/^\\d[\\d.,]*\u00a0\\S+/)[0], r = document.createRange();
        r.setStart(node, at); r.setEnd(node, at + m.length);
        out.push([m, new Set([...r.getClientRects()].map(b => Math.round(b.top))).size]);
      } return out; }"""
    for name in ("plain", "three digits", "IU dose"):
        ctx, pg, fr = open_card(harness, browser, size, shape(name), zoom=zoom, later=True)
        try:
            got = fr.evaluate(probe)
            assert len(got) == 2 and all(isinstance(n, int) and n == 1 for _, n in got), (name, zoom, got)  # each amount is on one line
            assert "\u00a0" in fr.evaluate("document.querySelector('.pl-guide .pl-sent').textContent")
        finally:
            ctx.close()


# ------------------------------------------------------------------ the hint and the screen reader
@pytest.mark.parametrize("size,hint", [((390, 844), True), ((412, 915), True), ((375, 667), True), ((360, 640), True), ((360, 800), True),
                                       ((344, 640), False), ((320, 568), False), ((320, 640), False)])
def test_the_hint_word_shows_where_there_is_room_and_the_chevron_always_does(harness, browser, size, hint):
    for label in ("200 mg", "1300 mg", "3500 mg"):  # the longest targets: the sentence stays on one line beside the hint
        ctx, pg, fr = open_card(harness, browser, size, shape("plain", rdaLabel=label), later=True)
        try:
            guide = panels(fr)["panels"][1]
            assert guide["chev"]["w"] > 4 and guide["chev"]["display"] != "none" and inside(guide["chev"], guide)
            assert (guide["why"]["display"] != "none") is hint, (size, guide["why"])
            assert guide["sent"]["tl"] == 1 and guide["sent"]["text"] == "For very active people: " + label, (size, label, guide["sent"])
            if hint:
                assert guide["why"]["text"] == "Why?" and inside(guide["why"], guide) and guide["why"]["x"] >= guide["sent"]["r"] - 1
        finally:
            ctx.close()


@pytest.mark.parametrize("size", [(360, 640), (360, 800)])
@pytest.mark.parametrize("name", ["plain", "three digits", "units in brackets", "athlete verdict", "a lot of food", "long label",
                                  "no athlete target label", "only the athlete target"])
def test_a_360_px_phone_keeps_the_hint_word_and_it_costs_no_height(harness, browser, size, name):
    """360 px is the most common Android width. The panel there is 280 px wide: the word fits beside the sentence for every target label
    (the container query's limit is below 280 px: it is inclusive), so the athlete panel is not a grey box with a lone chevron that does not
    look tappable, and the word makes the panel no taller than without it."""
    ctx, pg, fr = open_card(harness, browser, size, shape(name), later=True)
    try:
        guide = panels(fr)["panels"][-1]
        assert guide["cls"] == "pl pl-guide" and guide["w"] >= 279.5, guide["w"]  # the container is exactly the limit's neighbourhood: 280 px
        assert guide["why"]["display"] != "none" and guide["why"]["text"] == "Why?", guide["why"]
        assert inside(guide["why"], guide) and guide["why"]["x"] >= guide["sent"]["r"] - 1 and guide["chev"]["x"] >= guide["why"]["r"] - 1
        assert guide["sent"]["tl"] == 1, guide["sent"]  # the sentence stays on one line beside the word
        assert guide["sw"] <= guide["cw"] + 1, guide  # and nothing leaves the panel
        with_word = guide["h"]
        fr.add_style_tag(content=".pl-why { display: none !important; }")
        pg.wait_for_timeout(150)
        assert with_word <= panels(fr)["panels"][-1]["h"] + 0.5, (with_word, panels(fr)["panels"][-1]["h"])
        assert pg.errors == []
    finally:
        ctx.close()


def test_the_card_label_and_the_button_name_carry_the_athlete_target(harness, browser):
    cases = [("plain", ". To match the dose: ~35 g. Athlete target for very active people: 200 mg, about 88 g. Notes:"),
             ("athlete verdict", ". To match the dose: ~8 g. Athlete target for very active people: 500 mg, not practical from food alone (~71 g/day; realistic max ~70 g/day). Notes:"),
             ("units in brackets", ". To match the dose: ~2.9 g (~0.6 Brazil nuts). Athlete target for very active people: 70 mcg, about 3.7 g (~0.7 Brazil nuts). Notes:"),
             ("no athlete target label", ". To match the dose: ~35 g. Athlete target for very active people, about 88 g. Notes:"),
             ("only the pill match", ". To match the dose: ~35 g. Notes:")]
    for name, tail in cases:
        ctx, pg, fr = open_card(harness, browser, (390, 844), shape(name))
        try:
            label = fr.evaluate("document.getElementById('card').getAttribute('aria-label')")
            assert tail in label, (name, label)
            if name == "only the pill match":
                assert "Athlete target" not in label
            else:
                button = fr.get_by_role("button", name=re.compile(r"^Athlete target .*Opens the athlete guide\.?$"))
                assert button.count() == 1, name  # one button, named for what it is and what it does
        finally:
            ctx.close()


# ------------------------------------------------------------------ large text
@pytest.mark.parametrize("zoom", [150, 200])
@pytest.mark.parametrize("size", [(320, 640), (390, 844)])
@pytest.mark.parametrize("name", ["plain", "three digits", "units in brackets", "athlete verdict", "no athlete target label"])
def test_at_large_text_every_part_stays_inside_its_panel_and_the_heading_does_not_run_into_the_figure(harness, browser, zoom, size, name):
    ctx, pg, fr = open_card(harness, browser, size, shape(name), zoom=zoom, later=True)
    try:
        m = panels(fr)
        assert m["body"]["sw"] <= m["body"]["cw"] + 1, m["body"]
        for p in m["panels"]:
            assert p["sw"] <= p["cw"] + 1 and inside(p, m["foodwrap"], 1.0), (name, zoom, p)
            for box in p["kids"]:
                assert box["x"] >= p["x"] - 0.5 and box["r"] <= p["r"] + 0.5, (name, zoom, box, p["x"], p["r"])
            if p["amt"]:
                h, a = p["head"], p["amt"]
                apart = h["r"] <= a["x"] + 0.5 or a["r"] <= h["x"] + 0.5 or h["b"] <= a["y"] + 0.5 or a["b"] <= h["y"] + 0.5
                assert apart, (name, zoom, h, a)  # the heading wraps or the figure drops to its own row: never one over the other
                assert a["lines"] == 1, a
            if p["why"]:
                assert p["why"]["display"] == "none"  # no room for the hint's word: only the chevron
            assert p["h"] >= 30
        guide = m["panels"][1]
        assert guide["h"] >= 44 - 0.01 and guide["chev"]["w"] > 4, guide
        assert pg.errors == []
    finally:
        ctx.close()


def test_a_card_fits_its_frame_the_same_way_with_the_panels(harness, browser):
    """The frame is sized to the card's content (measureContent clones the body): the panels, with their container query, measure the same
    in the clone, so a card that fits has no scroll and the bottom hint stays pinned to the foot."""
    ctx, pg, fr = open_card(harness, browser, (390, 844), shape("plain"))
    try:
        m = fr.evaluate("""() => { const b = document.getElementById('cbody'), h = document.querySelector('.hint').getBoundingClientRect(), f = document.querySelector('.foodwrap').getBoundingClientRect();
          return { scrolls: b.scrollHeight > b.clientHeight, hintBelow: h.top >= f.bottom, cue: document.getElementById('stage').classList.contains('cue') }; }""")
        assert m == {"scrolls": False, "hintBelow": True, "cue": False}, m
    finally:
        ctx.close()


# ------------------------------------------------------------------ forced-colors mode (Windows high contrast)
def _forced_colors_damage(harness, browser, size, args, zoom=100) -> dict:
    """Forced-colors mode puts a backplate behind every glyph. What it must not do, as the number of pixels that differ (summed over both panels):
    "figure": the figure's glyphs painted over by the rows after it (the tail of the "g" of "~88 g" was cut: it read as "q"); compared with the
    same card with the figure forced above everything (what a figure that nothing paints over looks like).
    "border": the panel's border painted over by its content (a gap under the chevron where the panel's padding is small); compared with the
    border the same panel draws with its content hidden (visibility keeps the layout). Each panel is its own screenshot: a card that scrolls
    inside itself (large text) would cut a taller element at its window."""
    from PIL import Image, ImageChops
    ctx, pg, fr = open_card(harness, browser, size, args, zoom=zoom, later=True, forced_colors="active")

    def shot(selector, css=None):
        style = fr.add_style_tag(content=css) if css else None
        pg.wait_for_timeout(120)
        image = Image.open(io.BytesIO(fr.query_selector(selector).screenshot())).convert("RGB")
        if style:
            style.evaluate("e => e.remove()")
        return image

    def count(image):
        return image.convert("L").point(lambda v: 255 if v else 0).histogram()[255]  # the pixels that are not black in a difference image: those that differ

    damage = {"figure": 0, "border": 0}
    try:
        assert fr.evaluate("matchMedia('(forced-colors: active)').matches") is True  # the emulation is on
        for panel in (".pl-match", ".pl-guide"):
            shipped = shot(panel)
            above = shot(panel, ".pl-amt { position: relative !important; z-index: 1 !important; }")
            bare = shot(panel, ".pl > * { visibility: hidden !important; }")
            assert shipped.size == above.size == bare.size, (panel, shipped.size, above.size, bare.size)
            border_only = bare.convert("L").point(lambda v: 255 if v < 250 else 0).convert("RGB")  # whatever the empty panel paints: its border and its bar
            damage["figure"] += count(ImageChops.difference(shipped, above))
            damage["border"] += count(ImageChops.multiply(border_only, ImageChops.difference(shipped, bare)))
    finally:
        ctx.close()
    return damage


@pytest.mark.parametrize("size", [(390, 844), (375, 667), (320, 568)])
@pytest.mark.parametrize("name", ["plain", "three digits", "units in brackets", "athlete verdict"])
def test_in_forced_colors_mode_the_figure_and_the_border_are_not_overpainted(harness, browser, size, name):
    assert _forced_colors_damage(harness, browser, size, shape(name)) == {"figure": 0, "border": 0}, (size, name)


@pytest.mark.parametrize("zoom,size", [(150, (375, 667)), (200, (320, 568)), (150, (390, 844))])
def test_in_forced_colors_mode_large_text_does_not_overpaint_either(harness, browser, zoom, size):
    assert _forced_colors_damage(harness, browser, size, shape("plain"), zoom=zoom) == {"figure": 0, "border": 0}, (zoom, size)


@pytest.mark.parametrize("defect,replacement,found", [
    (".pl-amt { position: relative; z-index: 1;", ".pl-amt {", "figure"),  # (z-index alone lifts a flex item too: both go)
    ("@media (forced-colors: active) { .pl-more { clip-path: inset(0 -4px); } }", "", "border"),
])
def test_the_forced_colors_checks_see_the_defects_they_are_for(browser, tmp_path, defect, replacement, found):
    """The same checks run on a copy of the card without the fix find the damage, so the test above fails without it."""
    shipped = COMPONENT.read_text(encoding="utf-8")
    assert shipped.count(defect) == 1, defect
    broken = tmp_path / "index.html"
    broken.write_text(shipped.replace(defect, replacement, 1), encoding="utf-8")
    with Harness(component=broken) as h:
        for size in [(375, 667), (320, 568)]:  # the tight density: the panels' padding is 3 px there
            assert _forced_colors_damage(h, browser, size, shape("plain"))[found] > 0, (found, size)

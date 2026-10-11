"""The swipe card is ONE rounded box and everything the visitor taps lives inside it: offline pins of the component's structure.

The card (swipe_component/index.html) is a scrolling body (#cbody: the header with the Ask AI chip, the "Eat instead" box with the Swap
food chip, the notes) above a footer that is pinned to its foot (#actions: Back / Keep pill / Replace). Nothing of the frame sits below
the box: the page's fixed bottom bar is the only thing under it. The browser half, with real geometry, touch and focus:
tests/test_ux_card_inside_browser.py. The older pins of the tools' events are in tests/test_ux_fixed_screen.py, those of the frame maths
in tests/test_ux_row_clear_browser.py.

Tab order (the DOM order, which is also the visual one): the card, Ask AI (header), Swap food (the "Eat instead" box), the
athlete-guide button, then Back, Keep pill, Replace in the footer. A chip is present only when the card offers it."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "swipe_mobile_app" / "swipe_component" / "index.html").read_text(encoding="utf-8")
SCRIPT = SRC[SRC.index("<script>"):]
STYLE = SRC[SRC.index("<style>"):SRC.index("</style>")]
BODY = SRC[SRC.index("<body>"):SRC.index("<script>")]


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent: "Node | None"):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, attrs, parent, [], ""

    @property
    def id(self) -> str:
        return self.attrs.get("id") or ""

    def find(self, ident: str) -> "Node":
        for node in self.walk():
            if node.id == ident:
                return node
        raise AssertionError(ident)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def kids(self) -> list[str]:
        return [c.id or (c.attrs.get("class") or c.tag) for c in self.children]


class _Tree(HTMLParser):
    VOID = {"meta", "br", "img", "input", "link"}

    def __init__(self) -> None:
        super().__init__()
        self.root = Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in self.VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent  # type: ignore[assignment]
        if node is not self.root:
            self.cur = node.parent  # type: ignore[assignment]

    def handle_data(self, data):
        self.cur.text += data


def _dom() -> Node:
    parser = _Tree()
    parser.feed(BODY)
    return parser.root


def _rule(selector: str) -> str:
    match = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", STYLE)
    assert match, selector
    return match.group(1)


def _px(declarations: str, prop: str) -> list[float]:
    match = re.search(r"(?<![-\w])" + re.escape(prop) + r":\s*([^;]+);?", declarations)
    assert match, (prop, declarations)
    return [float(v) for v in re.findall(r"(-?[\d.]+)px", match.group(1))]


# ------------------------------------------------------------------ the markup: one box, nothing below it
def test_the_frame_is_the_card_and_nothing_sits_below_it():
    dom = _dom()
    wrap = dom.find("wrap")
    assert wrap.kids() == ["stage", "live"], wrap.kids()  # the stage, then the screen-reader live region: no row of tools, no row of buttons
    assert dom.find("stage").kids() == ["railKeep", "railRepl", "card"]  # the stamps, and THE card (the scroll cue lives inside it)
    for gone in ('id="extras"', "btnMore", "More options", "extras"):
        assert gone not in SRC, gone
    assert "actionsHeight" not in SRC and "var extras" not in SCRIPT


def test_the_card_holds_a_scrolling_body_and_a_pinned_footer_in_one_box():
    dom = _dom()
    card = dom.find("card")
    assert card.kids() == ["cmain", "actions"], card.kids()  # the window of the text, then the footer: the footer is the card's last child
    assert card.attrs.get("tabindex") == "0" and card.attrs.get("role") == "group"  # the card is still the one stop of its text
    main = next(n for n in card.children if n.attrs.get("class") == "cmain")
    assert main.kids() == ["cbody", "cue"]  # the body scrolls; the "scroll for more" pill is drawn over its foot, above the footer
    css = _rule(".tcard")
    assert "overflow: hidden" in css and "flex-direction: column" in css and "border-radius: 20px" in css and "box-shadow" in css  # ONE rounded, shadowed box
    assert "inset: 0 0 6px 0" in css  # the frame's last 6 px are the strip for its shadow: the card ends there
    assert re.search(r"var CARD_EDGE = 8;", SCRIPT) and re.search(r"var ROW_GAP = 6;", SCRIPT)  # the card's two 1 px borders + that strip
    body = _rule(".cbody")
    assert "overflow-y: auto" in body and "min-height: 0" in body and "flex: 1 1 auto" in body  # the one scroller, above the footer
    assert "overflow-x: hidden" in body  # a tap area that pokes out of the body's padding never makes a sideways scroll
    assert "flex: 1 1 auto" in _rule(".cmain") and "position: relative" in _rule(".cmain")


def test_the_footer_keeps_its_buttons_ids_labels_and_texts():
    dom = _dom()
    footer = dom.find("actions")
    assert footer.kids() == ["btnBack", "btnKeep", "btnRepl"]  # the direct parent of exactly these three: the keyboard's place is kept by it
    assert [b.attrs.get("aria-label") for b in footer.children] == ["Back to the previous card", "Keep pill", "Replace"]
    assert re.search(r'<button type="button" id="btnBack" aria-label="Back to the previous card" title="Back">&#8617;</button>', SRC)
    assert re.search(r'<button type="button" id="btnKeep" aria-label="Keep pill"><span class="arr" aria-hidden="true">&#9664; </span>Keep pill</button>', SRC)
    assert re.search(r'<button type="button" id="btnRepl" aria-label="Replace">Replace<span class="arr" aria-hidden="true"> &#9654;</span></button>', SRC)
    css = _rule("#actions")
    assert "flex: 0 0 auto" in css and "border-top: 1px solid" in css  # pinned, under a hairline
    assert min(_px(css, "padding")) >= 6  # room for the 3 px focus ring + its 2 px offset inside the card's overflow
    button = _rule("#actions button")
    assert "min-height: 48px" in button and "touch-action: manipulation" in button  # finger-sized, no double-tap delay
    assert "font-size: .95rem" in button
    assert "#fca5a5" in _rule("#actions #btnKeep") and "background: #047857" in _rule("#actions #btnRepl")  # Keep outlined red, Replace filled green
    assert "flex: 0 0 52px" in _rule("#actions #btnBack")
    assert "#wrap.big .arr { display: none; }" in STYLE  # from about 140 % text the arrows go, the labels say it
    # the footer is built once and never rebuilt: the body is what render() writes
    assert "cbody.innerHTML =" in SCRIPT and "card.innerHTML" not in SCRIPT and "actions.innerHTML" not in SCRIPT


# ------------------------------------------------------------------ the chips
def test_the_chips_are_drawn_only_when_the_card_offers_them_and_sit_beside_what_they_act_on():
    ask = "args.canAsk ? '<button type=\"button\" class=\"chip\" id=\"btnAsk\" data-tool=\"ask\" aria-label=\"Ask AI about this nutrient\">Ask AI</button>' : ''"
    swap = "args.canSwap ? '<button type=\"button\" class=\"chip\" id=\"btnSwap\" data-tool=\"swap\" aria-label=\"Swap food: choose another whole food\">Swap food</button>' : ''"
    assert ask in SCRIPT and swap in SCRIPT  # absent (no hidden button, no gap) when the AI is off / there is no second food
    # Ask AI: last in the header row (the badge and the nutrient's name come first), so it is right-aligned there ...
    assert re.search(r"<div class=\"head\">.*?In your pill: .*?</div></div>' \+ askHtml \+ '</div>'", SCRIPT, re.S)
    # ... Swap food: in the header of the "Eat instead" box, next to its label
    assert "<div class=\"foodwrap\"><div class=\"foodhead\"><div class=\"foodlbl\">Eat instead</div>' + swapHtml + '</div>" in SCRIPT
    assert "btnSwap.hidden" not in SCRIPT and "btnAsk.hidden" not in SCRIPT and "var btnSwap" not in SCRIPT and "var btnAsk" not in SCRIPT  # rebuilt with the card: never held
    # the chips are not part of the texts the card is known by
    assert "'<div class=\"name\">' + esc(args.name" in SCRIPT and "foodTxt" in SCRIPT


def test_a_chip_is_compact_to_look_at_and_a_finger_to_tap():
    css = _rule(".chip")
    assert "min-height: 34px" in css and "white-space: nowrap" in css  # one line, 34 px to look at
    assert "touch-action: manipulation" in css and "cursor: pointer" in css
    size = float(re.search(r"font-size:\s*([\d.]+)rem", css).group(1))
    assert size >= 0.72
    match = re.search(r"\.chip::after \{ content: \"\"; position: absolute; inset: (-?\d+)px (-?\d+)px; \}", STYLE)
    assert match, "the tap area is a pseudo element: it adds nothing to the layout"
    assert 34 + 2 * -int(match.group(1)) >= 44  # 46 px
    tight = re.search(r"#wrap\.tight \.chip \{ min-height: (\d+)px; padding: 0 9px; \}", STYLE)
    tight_hit = re.search(r"#wrap\.tight \.chip::after \{ inset: (-?\d+)px -4px; \}", STYLE)
    assert tight and tight_hit and int(tight.group(1)) + 2 * -int(tight_hit.group(1)) >= 44  # a tight card's chip is 30 px, its tap area still 44
    assert "outline: 3px solid #1d4ed8" in _rule(".chip:focus-visible") and "outline-offset: 2px" in _rule(".chip:focus-visible")
    assert "margin-left: auto" in STYLE[STYLE.index(".head .chip"):STYLE.index(".head .chip") + 120]  # right-aligned, also when it wraps onto its own row
    # the ring is 5 px: the body's padding is wider, so the card's overflow never clips it
    assert min(_px(_rule(".cbody"), "padding")) >= 12 and min(_px(_rule("#wrap.tight .cbody"), "padding")) >= 6


def test_the_tight_card_changes_only_spacing_for_the_chips_and_the_footer():
    for selector in ("#wrap.tight .chip", "#wrap.tight .chip::after", "#wrap.tight .foodhead .chip", "#wrap.tight #actions"):
        match = re.search(re.escape(selector) + r" \{([^}]*)\}", STYLE)
        assert match, selector
        props = {p.split(":")[0].strip() for p in match.group(1).split(";") if ":" in p}
        assert props <= {"min-height", "inset", "margin-top", "margin-bottom", "padding"}, (selector, props)  # never a font size, a content or a display
    assert "min-height: 48px" in _rule("#actions button") and "#wrap.tight #actions button" not in STYLE  # the footer's buttons are 48 px on every card


# ------------------------------------------------------------------ taps and drags
def test_a_press_on_a_button_of_the_card_is_a_tap_and_never_a_drag():
    helper = SCRIPT[SCRIPT.index("function onPlainButton(e)"):SCRIPT.index("if (window.PointerEvent) {")]
    assert 'e.target.closest("button")' in helper and 'hasAttribute("data-guide")' in helper  # every button but the athlete-guide line
    handler = SCRIPT[SCRIPT.index('card.addEventListener("pointerdown"'):SCRIPT.index('card.addEventListener("pointermove"')]
    assert handler.index("if (onPlainButton(e)) return;") < handler.index("down(e.clientX, e.clientY);") < handler.index("card.setPointerCapture")
    # without a capture on the card the press stays with the button, whose own click then fires (a capture would make the card the target)
    assert "if (onPlainButton(e)) return; var t = e.touches[0]; down(t.clientX, t.clientY);" in SCRIPT  # the browsers without Pointer Events
    assert "if (onPlainButton(e)) return; down(e.clientX, e.clientY);" in SCRIPT
    # the athlete-guide line keeps today's rule: a tap opens the guide, a drag that starts on it still swipes (the capture waits for a move)
    assert "if (!downOnGuide) { try { card.setPointerCapture(e.pointerId); } catch (err) {} }" in SCRIPT
    assert "e.detail > 0 && movedBeyondTap" in SCRIPT


def test_the_buttons_and_the_keys_still_decide_as_before():
    for line in ('btnKeep.addEventListener("click", function () { commit("left"); });',
                 'btnRepl.addEventListener("click", function () { commit("right"); });',
                 'btnBack.addEventListener("click", function () { commit("back"); });'):
        assert line in SCRIPT, line
    assert 'if (e.key === "ArrowLeft") { commit("left"); e.preventDefault(); }' in SCRIPT
    assert 'lastFocusId = (active && active.parentNode && active.parentNode.id === "actions") ? active.id : "";' in SCRIPT  # the footer is the buttons' parent
    # a pending swipe dims the chips as well (aria-disabled, so the focus stays where it is)
    assert 'cbody.querySelectorAll("[data-tool]")' in SCRIPT and 'setAttribute("aria-disabled", "true")' in SCRIPT
    # the keys that scroll a page scroll the body when the card itself has the focus
    keys = SCRIPT[SCRIPT.index('card.addEventListener("keydown"'):SCRIPT.index("// aria-disabled buttons are still clickable")]
    assert "e.target !== card" in keys and all(k in keys for k in ("ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End"))


def test_the_focus_goes_back_to_the_chip_that_opened_a_sheet_even_when_the_card_was_rebuilt():
    assert 'returnTo = kind === "swap" ? "btnSwap" : "btnAsk";' in SCRIPT and 'var button = document.getElementById(returnTo);' in SCRIPT  # by id, not by element
    assert "var chipFocus = (document.activeElement && cbody.contains(document.activeElement)) ? document.activeElement.id : \"\";" in SCRIPT
    assert "if (chipFocus) { var chip = document.getElementById(chipFocus); if (chip) { focusChip(chip); } }" in SCRIPT
    assert "if (button) { focusChip(button); }" in SCRIPT


def test_a_chip_that_gets_the_focus_back_is_scrolled_into_the_bodys_window_not_left_outside_it():
    """At 200 % text on a small phone the Swap food chip is below the body's first window. render() used to focus the new chip with
    preventScroll and then put the body back to its top (and the page's focus handler did the same), so the focused chip was out of view
    or under the fade. Now: the card is in its place and the body at its top first, then the chip is focused and scrolled into the window."""
    render = SCRIPT[SCRIPT.index("function render(args)"):SCRIPT.index("function focusChip(chip)")]
    assert render.count("cbody.scrollTop = 0;") == 1  # one reset of the body ...
    assert render.index('card.style.transform = "";') < render.index("cbody.scrollTop = 0;") < render.index("focusChip(chip)")  # ... before the chip is focused
    assert "scrollTop" not in render[render.index("focusChip(chip)"):]  # and none after the focus (it would undo the scroll to the chip)
    helper = SCRIPT[SCRIPT.index("function focusChip(chip)"):SCRIPT.index("function rails(dx)")]
    assert "chip.focus({ preventScroll: true })" in helper  # the page (Streamlit's main area) is never moved by it ...
    assert "cbody.scrollTop += " in helper and "cbody.scrollTop -= " in helper and "scrollPaddingBottom" in helper  # ... only the body scrolls, clear of the fade
    assert "scrollIntoView" not in helper
    handler = SCRIPT[SCRIPT.index('window.addEventListener("focus"'):SCRIPT.index("// The frame is announced by its title")]
    assert "focusChip(button)" in handler and "button.focus(" not in handler
    assert "chip.focus(" not in SCRIPT.replace(helper, "")  # no other way of focusing a chip


# ------------------------------------------------------------------ the scroll cue and the frame maths
def test_the_fade_and_the_pill_sit_above_the_footer_and_travel_with_the_card():
    fade = _rule("#stage.cue .cmain::after")
    assert "bottom: 0" in fade and "height: 30px" in fade and "pointer-events: none" in fade  # at the foot of the window, which ends where the footer begins
    assert "bottom: 0" not in _rule("#cue") and "pointer-events: none" in _rule("#cue")
    assert "scroll-padding-bottom: 44px" in _rule(".cbody")  # a focused control is scrolled clear of the fade and the pill
    assert "cbody.scrollHeight - cbody.clientHeight - cbody.scrollTop - pad" in SCRIPT  # the cut is the body's, never the footer's


def test_the_frame_is_sized_from_the_body_and_the_footer():
    assert "function chromeHeight() { return actions.offsetHeight + CARD_EDGE; }" in SCRIPT
    assert "var grow = measured && avail - chrome < MIN_WINDOW;" in SCRIPT  # the body's window is what MIN_WINDOW protects
    assert "avail = Math.max(chrome + MIN_WINDOW, growMax);" in SCRIPT
    assert "var bodyRoom = h - (spacer ? spacerPx : 0) - chromeHeight();" in SCRIPT
    probe = SCRIPT[SCRIPT.index("function measureContent()"):SCRIPT.index("function chromeHeight()")]
    assert "cbody.cloneNode(true)" in probe and "removeAttribute(\"id\")" in probe  # the probe is the body alone, without a second #btnAsk / #btnSwap


def test_the_fonts_stay_readable():
    sizes = [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)rem", SRC)]
    assert min(sizes) >= 0.72
    assert "overflow-wrap" in SRC and "flex-wrap: wrap" in SRC and "min-width: min-content" in SRC  # a large text wraps, never cuts a word

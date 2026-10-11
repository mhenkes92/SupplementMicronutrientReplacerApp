"""The swipe card is ONE rounded box and everything the visitor taps lives inside it: offline pins of the component's structure.

The card (swipe_component/index.html) is a scrolling body (#cbody: the header with the Ask AI chip, the "Eat instead" box with the Swap
food chip, the notes) above a footer that is pinned to its foot (#actions: Back / Keep pill / Replace). Nothing of the frame sits below
the box: the page's fixed bottom bar is the only thing under it. The browser half, with real geometry, touch and focus:
tests/test_ux_card_inside_browser.py. The older pins of the tools' events are in tests/test_ux_fixed_screen.py, those of the frame maths
in tests/test_ux_row_clear_browser.py.

Tab order (the DOM order, which is also the visual one): the card, Ask AI (header), Swap food (the "Eat instead" box), the
athlete-guide button, then Back, Keep pill, Replace in the footer. A chip is present only when the card offers it.

The chips look like buttons (a tinted, raised pill with an icon: Ask AI indigo with a sparkle, Swap food teal with two arrows) and
point at themselves once with a short ring (three slow cycles on the first card of a scan, only until the visitor has tapped a chip):
the pins of the colours' contrast, of the finite, paint-only animation and of when it may run are after the chip tests; the browser
half (computed styles, rectangles while the ring runs, the localStorage flag) is in tests/test_ux_card_inside_browser.py."""
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
    ask = "args.canAsk ? '<button type=\"button\" class=\"chip\" id=\"btnAsk\" data-tool=\"ask\" aria-label=\"Ask AI about this nutrient\">' + ICON_ASK + 'Ask AI</button>' : ''"
    swap = "args.canSwap ? '<button type=\"button\" class=\"chip\" id=\"btnSwap\" data-tool=\"swap\" aria-label=\"Swap food: choose another whole food\">' + ICON_SWAP + 'Swap food</button>' : ''"
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
    tight = re.search(r"#wrap\.tight \.chip \{ min-height: (\d+)px; padding: 0 10px 0 8px; \}", STYLE)
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


def _hexrgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _luminance(rgb: tuple[int, int, int]) -> float:
    lin = [(c / 255) / 12.92 if c / 255 <= 0.03928 else (((c / 255) + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def _family(rule: str) -> dict[str, str]:
    """The custom properties (--c-ink, --c-top, ...) a chip family declares."""
    return {k: v.strip() for k, v in re.findall(r"--c-([a-z]+):\s*([^;]+);", rule)}


def _card_colours() -> list[tuple[int, int, int]]:
    """White and the darkest stop (18 % of the nutrient's colour over white) of every card colour the page can give a card."""
    app = (ROOT / "swipe_mobile_app" / "app.py").read_text(encoding="utf-8")
    table = app[app.index("_NUTRIENT_BASE_COLORS: list"):app.index("def _component_card_theme")]
    bases = set(re.findall(r'"(#[0-9a-f]{6})"\)', table)) | {"#64748b"}  # the default card is slate
    assert len(bases) >= 15, bases
    return [(255, 255, 255)] + [_mix(_hexrgb(b), (255, 255, 255), 0.82) for b in bases]


# ------------------------------------------------------------------ the chips look like buttons
def test_the_chips_are_raised_tinted_buttons_with_a_border_and_a_bold_label():
    css = _rule(".chip")
    assert "display: inline-flex" in css and "align-items: center" in css  # the icon and the label on one line
    assert re.search(r"border:\s*1\.5px solid var\(--c-line\)", css)  # a clear accent border
    assert "linear-gradient(180deg, var(--c-top), var(--c-bot))" in css  # a soft tinted fill, not the old white
    assert re.search(r"box-shadow:\s*0 2px 0 rgba\(var\(--c-edge\)", css)  # a bottom edge: it stands off the card
    assert "font-weight: 800" in css and "white-space: nowrap" in css
    assert "#94a3b8" not in css and "rgba(255, 255, 255, 0.92)" not in css  # the old grey outlined pill is gone
    pressed = _rule('.chip:not([aria-disabled="true"]):active')
    assert "var(--c-press)" in pressed and "translateY(1px)" in pressed  # a pressed state that shows
    assert "outline: 3px solid #1d4ed8" in _rule(".chip:focus-visible")  # and the focus ring is kept
    assert "opacity: .45" in _rule('.chip[aria-disabled="true"]')


def test_each_chip_has_its_own_colour_family_and_both_read_clearly():
    """Ask AI indigo, Swap food teal: told apart from each other and from the footer (Keep: red outline, Replace: filled green).
    Text >= 4.5:1 on the whole tint (and the pressed tint), the border >= 3:1 against every card colour the page can draw (the Swap
    chip sits in the "Eat instead" box: 82 % white over the card), measured from the declared colours."""
    ask, swap = _family(_rule(".chip")), _family(_rule('.chip[data-tool="swap"]'))
    required = {"ink", "top", "bot", "line", "press", "edge", "glow"}
    assert set(ask) >= required and set(swap) >= required, (ask, swap)
    for name, fam, boxed in (("ask", ask, False), ("swap", swap, True)):
        ink = _hexrgb(fam["ink"])
        for tint in ("top", "bot", "press"):
            assert _contrast(ink, _hexrgb(fam[tint])) >= 4.5, (name, tint)  # 6.0 to 8.9 in fact
        for ground in _card_colours():
            ground = _mix(ground, (255, 255, 255), 0.82) if boxed else ground
            assert _contrast(_hexrgb(fam["line"]), ground) >= 3.0, (name, ground)  # 4.7 or more in fact
        assert _luminance(_hexrgb(fam["top"])) > 0.8 and _luminance(_hexrgb(fam["line"])) < 0.2  # a light tint, a dark line: a button, not a blob
    assert ask["line"] != swap["line"] and ask["top"] != swap["top"] and ask["ink"] != swap["ink"]  # two families
    # the hues are apart: indigo is blue-violet (blue far above green), teal blue-green (green far above red, blue near green)
    r, g, b = _hexrgb(ask["line"])
    assert b > g + 60 and b > r
    r, g, b = _hexrgb(swap["line"])
    assert g > r + 60 and abs(b - g) < 40
    footer = _rule("#actions #btnKeep") + _rule("#actions #btnRepl")
    for fam in (ask, swap):
        assert not any(fam[k] in footer for k in ("line", "ink", "top")), fam  # nothing of the footer's palette


def test_the_chip_icons_are_decorative_inline_svg_in_the_text_colour():
    for name in ("ICON_ASK", "ICON_SWAP"):
        match = re.search(r"var %s = (.*?</svg>)';" % name, SCRIPT, re.S)
        assert match, name
        svg = re.sub(r"'\s*\+\s*'", "", match.group(1)).lstrip("'") + ""
        assert svg.startswith('<svg class="ico" viewBox="0 0 16 16" aria-hidden="true" focusable="false">'), name  # the button's aria-label is its name
        assert svg.count("<svg") == 1 and svg.endswith("</svg>")
        assert "currentColor" in svg and "<text" not in svg and "<title" not in svg and "<image" not in svg and "url(" not in svg  # no font, no file
        assert svg.isascii(), name  # no emoji
    ico = _rule(".chip .ico")
    assert "width: 1.1em" in ico and "height: 1.1em" in ico and "flex: 0 0 auto" in ico  # it grows with the text size
    # the visible words and the accessible names are the ones the tests, the screen readers and the page already know
    for label, icon, words in (("Ask AI about this nutrient", "ICON_ASK", "Ask AI"), ("Swap food: choose another whole food", "ICON_SWAP", "Swap food")):
        assert f"aria-label=\"{label}\">' + {icon} + '{words}</button>'" in SCRIPT, label


def test_the_icon_goes_on_a_320_px_phone_so_the_header_chip_stays_beside_the_name():
    """The card is 288 px wide on a 320 px phone: with the icon the chip is 20 px wider than the sample's names leave room for, so it would
    take a row of its own on most cards (+40 px, measured). Without it the chip is as narrow as before the icon. (A 340 px phone: 308 px, room
    enough.)"""
    media = STYLE[STYLE.index("@media (max-width: 300px)"):]
    media = media[:media.index("\n  }") + 4]
    assert ".chip .ico { display: none; }" in media
    assert "#wrap .chip { padding: 0 11px; }" in media and "#wrap.tight .chip { padding: 0 9px; }" in media  # even paddings again, in both densities
    assert STYLE.index("@media (max-width: 300px)") > STYLE.index("#wrap.tight .chip { min-height")  # later, so the tight rule's padding does not win


# ------------------------------------------------------------------ the attention pulse
def test_the_ring_is_a_finite_slow_paint_only_animation_and_reduced_motion_removes_it():
    assert "infinite" not in STYLE  # never an endless loop
    assert STYLE.count("@keyframes") == 1  # the one ring
    use = _rule(".chip.pulse::before")
    match = re.fullmatch(r"\s*animation:\s*chip-ring ([\d.]+)s ease-out (\d+) both;\s*", use)
    assert match, use
    seconds, cycles = float(match.group(1)), int(match.group(2))
    assert cycles == 3 and seconds >= 1.0  # three cycles; one ring per 1.4 s is far below the 3 flashes a second of WCAG 2.3.1
    # the script's numbers are the stylesheet's (it removes the class when the third ring has faded)
    assert "var PULSE_DELAY = 700, PULSE_MS = 1400, PULSE_CYCLES = 3;" in SCRIPT and (seconds, cycles) == (1.4, 3)
    frames = STYLE[STYLE.index("@keyframes chip-ring"):]
    frames = frames[:frames.index("\n  }") + 4]
    props = set(re.findall(r"([a-z-]+):", re.sub(r"rgba\([^)]*\)", "", frames.split("{", 1)[1])))
    assert props == {"box-shadow"}, props  # paint only: no transform, size, margin or opacity (nothing that changes a rect or the scroll area)
    assert "rgba(var(--c-glow), 0)" in frames  # it fades out completely
    ring = _rule(".chip::before")
    assert "position: absolute" in ring and "pointer-events: none" in ring and "border-radius: inherit" in ring and "inset: -1.5px" in ring
    reduced = STYLE[STYLE.index("@media (prefers-reduced-motion: reduce)"):]
    reduced = reduced[:reduced.index("\n  }") + 4]
    assert ".chip.pulse::before { animation: none !important; }" in reduced  # no animation at all: the static look is the cue
    # the ring's spread is less than the body's side padding (the chip at the right edge): the card's clipping never cuts a visible ring
    last = re.search(r"70%, 100% \{ box-shadow: 0 0 (\d+)px (\d+)px", STYLE)
    assert last and int(last.group(2)) < min(_px(_rule(".cbody"), "padding")[1], _px(_rule("#wrap.tight .cbody"), "padding")[1])


def test_the_pulse_runs_on_the_first_card_of_a_scan_for_a_visitor_who_has_not_used_a_chip_and_stops_at_the_first_interaction():
    assert 'var TOOLS_KEY = "suppswipe_tools_used";' in SCRIPT
    # the flag lives in localStorage and in memory: every access is inside a try / catch (a private window or blocked site data throws)
    assert SCRIPT.count("window.localStorage.") == 2
    read = SCRIPT[SCRIPT.index("function toolsWereUsed()"):SCRIPT.index("function markToolsUsed()")]
    write = SCRIPT[SCRIPT.index("function markToolsUsed()"):SCRIPT.index("function stopPulse()")]
    for body, call in ((read, "window.localStorage.getItem(TOOLS_KEY)"), (write, "window.localStorage.setItem(TOOLS_KEY")):
        assert body.index("try {") < body.index(call) < body.index("catch (e)"), body
    assert "return toolsUsed;" in read and write.index("toolsUsed = true;") < write.index("try {")  # the memory fallback is written first
    # when it is offered: the first card of a scan (not one opened from the results), with a chip, once per card, never after a chip was used
    assert "if (cardIndex === 0 && !editing && (args.canAsk || args.canSwap) && turn !== pulseKey && !toolsWereUsed()) {" in SCRIPT
    assert 'var turn = scanId + "|" + cardId;' in SCRIPT and "pulseKey = turn;" in SCRIPT  # a redraw of the same card does not start it again
    assert "pulseTimer = setTimeout(startPulse, PULSE_DELAY);" in SCRIPT
    start = SCRIPT[SCRIPT.index("function startPulse()"):SCRIPT.index("// The visitor is interacting")]
    for guard in ("!chips.length", "committed", "document.hidden", "hostBusy()", "toolsWereUsed()"):
        assert guard in start, guard  # not on a card without chips, not during a swipe, in a hidden tab, while the page is busy
    assert 'classList.add("pulse")' in start and "setTimeout(stopPulse, PULSE_MS * PULSE_CYCLES + 200)" in start
    # stopped at once by any press or key in the card (a drag starts with a press), by a press or key in the page around it, and by a swipe
    assert '["pointerdown", "touchstart", "mousedown", "keydown"].forEach(function (type) { document.addEventListener(type, stopPulse, { capture: true, passive: true }); });' in SCRIPT
    assert 'window.parent.addEventListener("pointerdown", stopPulse, true);' in SCRIPT
    assert "committed = true;\n    stopPulse();" in SCRIPT
    # tapping a chip is what sets the flag, before the sheet's event is sent
    click = SCRIPT[SCRIPT.index('card.addEventListener("click"'):SCRIPT.index("btnKeep.addEventListener")]
    assert click.index("markToolsUsed();") < click.index("toolEvent(") and "stopPulse();" in click
    assert "stopPulse();\n    var turn" in SCRIPT  # and a new card clears the old card's timer before it decides again


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

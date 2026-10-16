"""The swipe card is ONE rounded box and everything the visitor taps lives inside it: offline pins of the component's structure.

The card (swipe_component/index.html) is a scrolling body (#cbody: the header with the Ask AI chip, the "Eat instead" box with the Swap
food chip, the notes) above a footer that is pinned to its foot (#actions: Back / Keep pill / Replace). Nothing of the frame sits below
the box: the page's fixed bottom bar is the only thing under it. The browser half, with real geometry, touch and focus:
tests/test_ux_card_inside_browser.py. The older pins of the tools' events are in tests/test_ux_fixed_screen.py, those of the frame maths
in tests/test_ux_row_clear_browser.py.

Tab order (the DOM order, which is also the visual one): the card, Ask AI (header), Swap food (the "Eat instead" box), the
athlete-guide button, then Back, Keep pill, Replace in the footer. A chip is present only when the card offers it.

The chips look like buttons (a tinted, raised pill with an icon: Ask AI indigo with a sparkle, Swap food teal with two arrows) and
breathe: a soft halo whose opacity rises and falls once every 3 s, on every card that has them, for everyone, always (no flag, no timer,
nothing remembered): the pins of the colours' contrast, of the slow, opacity-only animation, of the halo's reach and of where it holds still
are after the chip tests; the browser half (computed styles, the opacity over time, rectangles while it breathes, a drag, reduced motion)
is in tests/test_ux_card_inside_browser.py."""
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
    # the glow reaches 9.5 px (pinned below): the body's padding is wider at the sides, so the card's overflow never clips it
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


# ------------------------------------------------------------------ the glow
GLOW_REACH = 10  # px: the halo may reach this far past a chip at the most (the body's side padding is 12-14 px)


def _block(start: str) -> str:
    """From `start` (an at-rule or a @keyframes) to the end of its block: the first "\n  }" on a line of its own."""
    text = STYLE[STYLE.index(start):]
    return text[:text.index("\n  }") + 4]


def _glow() -> dict:
    """The numbers of the breathing, read from the stylesheet: the period, the keyframes' opacities, the halo's blur, spread and alpha."""
    use = _rule(".chip::before")
    anim = re.search(r"animation:\s*chip-glow ([\d.]+)s ease-in-out infinite;", use)
    assert anim, use
    frames = _block("@keyframes chip-glow")
    steps = re.findall(r"([\d%,\s]+?)\s*\{\s*([^}]*)\}", frames.split("{", 1)[1])
    shadow = re.search(r"box-shadow:\s*0 0 ([\d.]+)px ([\d.]+)px rgba\(var\(--c-glow\), ([\d.]+)\);", use)
    assert shadow, use
    return {"use": use, "seconds": float(anim.group(1)), "frames": frames, "steps": [(k.strip(), v.strip()) for k, v in steps],
            "blur": float(shadow.group(1)), "spread": float(shadow.group(2)), "alpha": float(shadow.group(3)),
            "inset": abs(_px(use, "inset")[0]), "base": float(re.search(r"(?<![-\w])opacity:\s*([\d.]+);", use).group(1))}


def test_the_chips_breathe_slowly_and_only_their_halos_opacity_moves():
    g = _glow()
    assert STYLE.count("@keyframes") == 1 and STYLE.count("infinite") == 1  # the one breathing is the only animation, and the only endless one
    assert g["seconds"] >= 2.5  # a breath is slower than 0.4 Hz (3 s): far below the 3 flashes a second of WCAG 2.3.1, calm, not a blink
    props = {name for _, body in g["steps"] for name in re.findall(r"([a-z-]+):", body)}
    assert props == {"opacity"}, props  # compositor-only: no paint, no transform, size, margin or shadow that moves or reflows anything
    keys = [k for k, _ in g["steps"]]
    assert keys == ["0%, 100%", "50%"], keys  # low at both ends, high in the middle: the loop has no seam
    low, high = (float(re.search(r"opacity:\s*([\d.]+)", body).group(1)) for _, body in g["steps"])
    assert 0.2 <= low <= 0.4 and 0.8 <= high <= 1.0 and high - low >= 0.5, (low, high)  # slight, yet a plain difference between the ends
    assert g["base"] == low  # without the animation (a held card) the halo sits at the low end
    # it starts from the low end, both chips together (the same rule, the same moment): they breathe in phase
    assert "animation-delay" not in g["use"] and "animation-delay" not in STYLE
    assert re.search(r"ease-in-out infinite;", g["use"])  # eased at both ends of each half


def test_the_halo_is_one_still_soft_shadow_that_never_reaches_past_the_cards_padding_or_adds_a_box():
    g = _glow()
    use = g["use"]
    for need in ('content: ""', "position: absolute", "pointer-events: none", "border-radius: inherit"):
        assert need in use, need  # a pseudo element: it adds no box, no scroll area and no tap target, and it follows the pill's shape
    assert "inset: -1px" in use  # on the chip's border box (the 1 px Chromium border, 1.5 px elsewhere): no seam between border and halo
    reach = g["inset"] + g["spread"] + g["blur"]
    assert reach <= GLOW_REACH, reach  # 9.5 px
    sides = (_px(_rule(".cbody"), "padding")[1], _px(_rule("#wrap.tight .cbody"), "padding")[1])  # the right padding: the chip at the edge
    assert reach < min(sides), (reach, sides)  # so the body's overflow clip never cuts it
    assert 0.5 <= g["alpha"] <= 0.95  # slight: a soft, translucent halo, not a solid ring
    assert "box-shadow" not in g["frames"]  # the shadow is drawn once; the keyframes only fade it
    assert re.search(r"transition:\s*box-shadow [\d.]+s ease;", use)  # only the shadow's colour fades (the hold below); the opacity is never transitioned
    # the glow colour is the chip family's own (indigo / teal): --c-glow is declared with the family, nothing else sets a colour for it
    assert "--c-glow: 99, 102, 241" in _rule(".chip") and "--c-glow: 20, 184, 166" in _rule('.chip[data-tool="swap"]')
    # the pseudo element sits under no text: an outer shadow is drawn outside its own box only, so the chip's text and tint are untouched
    assert "background" not in use and "color" not in use.replace("--c-glow", "")


def test_the_glow_holds_still_while_the_card_is_held_and_while_the_page_is_busy_and_is_gone_on_a_chip_that_is_not_live():
    g = _glow()
    hold = _rule("#wrap.dragging .chip::before, #wrap.busy .chip::before")
    assert "animation-play-state: paused" in hold  # it stops where it is (no snap), and goes on from there
    dim = re.search(r"box-shadow:\s*0 0 ([\d.]+)px ([\d.]+)px rgba\(var\(--c-glow\), ([\d.]+)\);", hold)
    assert dim and (float(dim.group(1)), float(dim.group(2))) == (g["blur"], g["spread"]) and float(dim.group(3)) <= 0.35 < g["alpha"], hold  # a fainter halo, never a wider one
    off = _rule('.chip[aria-disabled="true"]::before')
    assert "animation: none" in off and "opacity: 0" in off  # a swipe is on its way: its chips are dimmed as a whole, and carry no glow
    # the wrapper's class follows the script's own state: paint() writes every class of #wrap at once, so a repaint cannot drop it
    paint = SCRIPT[SCRIPT.index("function paint()"):SCRIPT.index("function setTight(")]
    assert '(dragging ? " dragging" : "")' in paint and '(busy ? " busy" : "")' in paint
    down = SCRIPT[SCRIPT.index("function down(x, y)"):SCRIPT.index("function move(x, y, ev)")]
    assert down.index("dragging = true;") < down.index("paint();")  # a press on the card (a swipe starts with one) holds the glow ...
    up = SCRIPT[SCRIPT.index("function up()"):SCRIPT.index("// A press on a button of the card")]
    assert up.index("dragging = false;") < up.index("paint();") < up.index('commit("right")')  # ... and the release (also a pointercancel) lets it go, whatever follows
    poll = SCRIPT[SCRIPT.index("setInterval(function () {"):SCRIPT.index("function setValue(v)")]
    assert "var wasBusy = busy;" in poll and "busy = hostBusy() || hostSheet();" in poll and "if (busy !== wasBusy) { paint(); }" in poll  # the page's busy state and an open sheet, on the existing beat
    assert poll.index("busy = hostBusy() || hostSheet();") < poll.index("if (committed || document.hidden || lastRoom === null) { return; }")  # also while a swipe is on its way
    assert SCRIPT.count("setInterval(") == 1  # no second timer for it
    sheet = SCRIPT[SCRIPT.index("function hostSheet()"):SCRIPT.index("// Every event carries the card")]
    assert 'querySelector(\'[data-testid="stDialog"]\')' in sheet and "try {" in sheet and "catch (e) { return false; }" in sheet  # an unreadable page is not a sheet


def test_reduced_motion_keeps_a_still_soft_halo_and_forced_colors_drops_the_animation():
    g = _glow()
    reduced = _block("@media (prefers-reduced-motion: reduce)")
    rule = re.search(r"\.chip::before \{([^}]*)\}", reduced)
    assert rule, reduced
    declarations = rule.group(1)
    assert "animation: none !important" in declarations and "transition: none" in declarations  # nothing moves at all ...
    still = float(re.search(r"opacity:\s*([\d.]+);", declarations).group(1))
    assert 0.4 <= still <= 0.7 and g["base"] < still, still  # ... but the halo stays, constant and soft: these visitors get the cue too
    assert "infinite" not in reduced and "@keyframes" not in reduced
    assert ".tcard, .rail { transition: none !important; }" in reduced  # (as before)
    forced = "@media (forced-colors: active) { .chip::before { animation: none; } }"
    assert forced in STYLE  # the browser drops box-shadow there: nothing to animate; the chips keep their 1.5 px borders (pinned above)
    assert "border: 1.5px solid var(--c-line)" in _rule(".chip")


def test_nothing_of_the_old_attention_pulse_is_left():
    """The pulse (a ring three times on the first card, until a chip was tapped once, remembered in localStorage) is gone: the visitor who
    tapped a chip once never saw a cue again and cards 2+ never had one. The glow needs no flag, no timer, no listener on the page."""
    for gone in ("TOOLS_KEY", "suppswipe_tools_used", "toolsWereUsed", "markToolsUsed", "stopPulse", "startPulse", "pulseKey", "pulseTimer",
                 "PULSE_DELAY", "PULSE_MS", "PULSE_CYCLES", "toolsUsed", "localStorage", "pagehide", "removeEventListener"):
        assert gone not in SCRIPT, gone
    for gone in (".pulse", "chip-ring", "@keyframes chip-ring"):
        assert gone not in STYLE, gone
    assert "pulse" not in SRC.lower().replace("pulses", "")  # no word of it in the component at all
    assert "window.parent.addEventListener" in SCRIPT  # the host's resize (the frame maths) is the one listener left on the page
    assert len(re.findall(r"window\.parent\.addEventListener\(", SCRIPT)) == 1
    click = SCRIPT[SCRIPT.index('card.addEventListener("click"'):SCRIPT.index("btnKeep.addEventListener")]
    assert 'if (hit.hasAttribute("data-tool")) { toolEvent(hit.getAttribute("data-tool")); return; }' in click  # a tap on a chip only opens its sheet


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

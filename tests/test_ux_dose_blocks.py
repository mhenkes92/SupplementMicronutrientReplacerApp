"""The two food amounts of the swipe card are two separate panels: offline pins of the markup, the colours and the shapes of the texts.

Owner's request (2026-10-11): "Mb there should be a clearer separation between match this pill dose and athlete target dose information so it's
easier to understand what that means?" The "Eat instead" box used to end in ONE green box with two lines ("Match this dose -> eat ~35 g",
"Target for very active people (200 mg) -> eat ~88 g"). It is now two panels (`.pl-match`, green, and `button.pl-guide`, slate), each with a
heading, the food amount as a big figure at the end of the heading row and one plain sentence that says what the amount is a match for:

    MATCH YOUR PILL                        eat ~35 g        ATHLETE TARGET                          eat ~88 g
    Equivalent to your pill's 80 mg                         For very active people: 200 mg           Why? >

What only a browser can measure (the gap, the right-aligned figures, the 44 px tap target, a drag from the athlete panel, the height against
the old box, the contrast on every card colour, 150 / 200 % text) is in tests/test_ux_dose_blocks_browser.py; the older pins of the
athlete-guide event and of the pointer code are in tests/test_ux_app_bar.py and tests/test_ux_card_inside.py."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from card_harness import SHAPES
from card_tools import card_args, swipe

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "swipe_mobile_app" / "app.py")
SRC = (ROOT / "swipe_mobile_app" / "swipe_component" / "index.html").read_text(encoding="utf-8")
STYLE = SRC[SRC.index("<style>"):SRC.index("</style>")]
SCRIPT = SRC[SRC.index("<script>"):]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


def _rule(selector: str) -> str:
    """The declarations of the first CSS rule whose selector is exactly `selector`."""
    match = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", STYLE)
    assert match, selector
    return match.group(1)


def _props(rule: str, prefix: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"--" + prefix + r"-([a-z]+):\s*([^;]+);", rule)}


def _hexrgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _luminance(rgb: tuple[int, int, int]) -> float:
    lin = [(c / 255) / 12.92 if c / 255 <= 0.03928 else (((c / 255) + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(_hexrgb(a)), _luminance(_hexrgb(b))), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _apart(a: str, b: str) -> int:
    """How far two colours are apart (sum of the channel differences, 0-765)."""
    return sum(abs(x - y) for x, y in zip(_hexrgb(a), _hexrgb(b)))


def _px(declarations: str, prop: str) -> list[float]:
    match = re.search(r"(?<![-\w])" + re.escape(prop) + r":\s*([^;]+);?", declarations)
    assert match, (prop, declarations)
    return [float(v) for v in re.findall(r"(-?[\d.]+)px", match.group(1))]


# ------------------------------------------------------------------ the markup: two panels, not two lines of one box
def test_the_two_amounts_are_two_separate_panels_with_a_heading_a_sentence_and_the_figure():
    assert "'<div class=\"pl pl-match\">' + panel(\"Match your pill\"" in SCRIPT  # panel one: the pill's dose, a plain box
    assert "'<button type=\"button\" class=\"pl pl-guide\" data-guide=\"1\">' + panel(\"Athlete target\"" in SCRIPT  # panel two: the athlete target
    panel = SCRIPT[SCRIPT.index("function panel("):SCRIPT.index('var portionRows = "";')]
    for part in ('class="pl-top"><span class="pl-head">', 'class="pl-amt">eat <b>', 'class="pl-line"><span class="pl-sent">'):
        assert part in panel, part  # the heading and the figure share the first row, the sentence is the second
    assert "'<div class=\"portion\">' + portionRows + '</div>'" in SCRIPT  # the wrapper holds the panels and is drawn only when there is one
    assert "var portionHtml = portionRows ?" in SCRIPT
    # the old one-box wording is gone from the card
    for gone in ("Match this dose", "Target for very active people", "💊 Match", "🏃 Target", "pl-text"):
        assert gone not in SRC, gone
    # the wrapper is no box of its own: the panels are the boxes
    wrapper = _rule(".portion")
    assert "background" not in wrapper and "border" not in wrapper and "padding" not in wrapper
    assert "flex-direction: column" in wrapper and _px(wrapper, "gap")[0] >= 8  # a clear gap: two things, not one
    box = _rule(".pl")
    assert "var(--p-bg)" in box and "var(--p-line)" in box and "border-radius: 12px" in box  # each is its own rounded, tinted panel


def test_the_sentences_say_what_each_amount_is_a_match_for():
    # "Equivalent to", not "the same as": the amount is sized to the pill's dose, which is not always the amount printed on the label (folic
    # acid is sized in DFE, so 54 g of beans hold ~340 mcg of food folate for a 200 mcg pill; a weekly pill is matched by one day's share;
    # a range by its low end). A sentence that claimed an equal quantity contradicted the card's own blue note.
    sentence = ('"Equivalent to " +\n        (weekly ? "one day of your pill" : range ? "the low end (" + glue(esc(pill.replace(/\\s*–\\s*[\\d.,]+/, ""))) + ")"'
                ' : plain ? "your pill\'s " + glue(esc(pill)) : "the dose in your pill")')
    assert sentence in SCRIPT
    # the low end of a range is the label's number before the dash: "100–200 mg" -> "100 mg" (what the portion is sized to), "0,5–1 mg" -> "0,5 mg"
    low = re.search(r"pill\.replace\(/(.*?)/, \"\"\)", SCRIPT)
    assert low
    for label, want in (("100–200 mg", "100 mg"), ("0.5–1 mg", "0.5 mg"), ("100 – 200 mcg", "100 mcg"), ("1,000–2,000 IU", "1,000 IU"), ("0,5–1 mg", "0,5 mg")):
        assert re.sub(low.group(1), "", label, count=1) == want, label
    assert "Same as the" not in SRC  # the old wording asserted equal amounts
    assert '"For very active people" + (args.rdaLabel ? ": " + glue(esc(args.rdaLabel)) : "")' in SCRIPT  # "For very active people: 200 mg" / without the target
    assert 'function glue(s) { return String(s).replace(/ /g, "\\u00a0"); }' in SCRIPT  # "200 mg" has a no-break space: a unit never starts a line
    # the pill's dose is named only when it is a plain amount: a range, a weekly dose and "Dose not found" are not
    plain = re.search(r"var plain = /(.*)/\.test\(pill\);", SCRIPT)
    assert plain
    rx = re.compile(plain.group(1))
    for ok in ("80 mg", "2.5 mcg", "1000 IU", "0.025 mg", "25 mcg"):
        assert rx.search(ok), ok
    for bad in ("100–200 mg", "5000 IU once a week (~714 IU/day)", "Dose not found", "", "n/a"):
        assert not rx.search(bad), bad
    # a weekly dose (the card is matched on ONE DAY of it) and a range (matched on its low end) have a wording of their own, and never "the dose"
    weekly = re.search(r"var weekly = /(.*)/i\.test\(pill\);", SCRIPT)
    ranged = re.search(r"var range = /(.*)/\.test\(pill\);", SCRIPT)
    assert weekly and ranged
    wk, rg = re.compile(weekly.group(1), re.I), re.compile(ranged.group(1))
    for hit in ("5000 IU once a week (~714 IU/day)", "10 mg every 3 days (~3.3 mg/day)", "1 mg once a week (~0.14 mg/day)"):
        assert wk.search(hit), hit
    for miss in ("80 mg", "100–200 mg", "Dose not found", "1000 IU"):
        assert not wk.search(miss), miss
    for hit in ("100–200 mg", "0.5–1 mg", "100 – 200 mcg"):
        assert rg.search(hit), hit
    for miss in ("80 mg", "5000 IU once a week (~714 IU/day)", "Dose not found", "25 mcg", ""):
        assert not rg.search(miss), miss
    assert "very active people" in SRC  # the safety posture of the old line is kept: the target is named for who it is for


def test_a_figure_is_the_amount_itself_and_a_verdict_is_set_under_the_sentence():
    split = SCRIPT[SCRIPT.index("function amountParts("):SCRIPT.index("function spoken(")]
    assert 'if (!/^[~<\\d]/.test(t)) { return { figure: "", rest: t, verdict: true }; }' in split  # "eat " (and a figure) only before an actual amount
    assert 't.indexOf(" (")' in split  # "~2.9 g (~0.6 Brazil nuts)": the figure is what comes before the first bracket
    panel = SCRIPT[SCRIPT.index("function panel("):SCRIPT.index('var portionRows = "";')]
    assert "a.figure ?" in panel and "a.rest && !near" in panel  # no figure for a verdict, no note line without a note
    assert "a.verdict ? 'pl-verdict' : 'pl-note'" in panel
    assert "var near = !a.verdict && a.rest.length > 0 && a.rest.length <= 24;" in panel  # a short bracket may share the sentence's row ...
    assert 'class="pl-note pl-near"' in panel and "flex-wrap: wrap" in _rule(".pl-row") and "margin-left: auto" in _rule(".pl-near")  # ... and wraps under it when it does not fit
    assert "esc(a.figure)" in panel and "esc(a.rest)" in panel and "esc(head)" not in panel  # the texts of the app are escaped; the headings are literals
    rule = _rule(".pl-verdict")
    assert "overflow-wrap: anywhere" in rule and "display: block" in rule  # a long verdict wraps under the sentence, never out of the panel
    assert "white-space: nowrap" in _rule(".pl-amt")  # and a figure is one line


def test_the_figure_and_the_chevron_cannot_paint_over_what_is_next_to_them_in_forced_colors_mode():
    """Windows high contrast (forced-colors) puts a backplate behind every glyph. The next row's backplate used to be painted over the tail of the
    figure's "g" (it read as "q"), and the chevron's over the panel's bottom border. A positioned figure is painted above the rows after it;
    the chevron is clipped to its row (the Chromium tests test_in_forced_colors_mode_* draw both, and a copy of the card without either rule)."""
    assert "position: relative; z-index: 1;" in _rule(".pl-amt")  # above the later rows and above the chevron, whose clip-path is a stacking context
    # the chevron's backplate is taller than its line (line-height .8): where the panel's padding is small (tight, 3 px) it reached the panel's
    # bottom border and cut a gap in it; clipped to its own row (and not at its sides: the word's "W" overhangs) it cannot
    assert "@media (forced-colors: active) { .pl-more { clip-path: inset(0 -4px); } }" in STYLE


def test_the_athlete_panel_is_still_the_one_button_that_opens_the_guide():
    assert '<button type="button" class="pl pl-guide" data-guide="1">' in SCRIPT
    assert "'<span class=\"sr-only\">. Opens the athlete guide.</span></button>'" in SCRIPT  # what a screen reader hears it does
    assert "'<span class=\"pl-more\" aria-hidden=\"true\"><span class=\"pl-why\">Why?</span><span class=\"chev\">&rsaquo;</span></span>'" in SCRIPT  # the hint, for the eye
    box = _rule(".pl-guide")
    assert _px(box, "min-height") == [44.0]  # the tap target is at least 44 px high (it was 32)
    assert "cursor: pointer" in box
    assert "outline: 3px solid #1d4ed8" in _rule(".pl-guide:focus-visible")
    # the pointer code is the one that keeps a drag that starts on the panel a swipe (its pins: tests/test_ux_card_inside.py)
    assert 'e.target.closest("[data-guide]")' in SCRIPT and "if (!downOnGuide)" in SCRIPT
    assert 'e.target.closest("[data-tool], [data-guide]")' in SCRIPT


def test_the_card_label_names_the_athlete_target_too():
    label = SCRIPT[SCRIPT.index('card.setAttribute("aria-label"'):SCRIPT.index("btnRepl.disabled")]
    assert '". Athlete target for very active people" + (args.rdaLabel ? ": " + args.rdaLabel : "") + ", " + spoken(args.rdaAmount)' in label
    assert label.index("To match the dose") < label.index("Athlete target for very active people") < label.index("Warning: ")
    spoken = SCRIPT[SCRIPT.index("function spoken("):SCRIPT.index("function panel(")]
    assert 'charAt(0) === "~" ? "about " + t.slice(1).trim() : t' in spoken  # "~88 g" is said "about 88 g"; a verdict as it is


# ------------------------------------------------------------------ the colours: one family per panel
def test_each_panel_has_its_own_colour_family_and_all_its_text_reads_on_it():
    match, guide = _props(_rule(".pl"), "p"), _props(_rule(".pl-guide"), "p")
    required = {"bg", "line", "bar", "head", "ink", "eat", "fig"}
    assert set(match) == required and set(guide) <= required, (match, guide)  # the athlete panel overrides what differs
    guide = {**match, **guide}
    for name, fam in (("match", match), ("guide", guide)):
        assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in fam.values()), name  # opaque: the text sits on the panel's own tint, whatever card colour is behind it
        for text in ("head", "ink", "eat", "fig"):
            assert _contrast(fam[text], fam["bg"]) >= 4.5, (name, text, _contrast(fam[text], fam["bg"]))  # 5.2 to 17 in fact
        assert _luminance(_hexrgb(fam["bg"])) > 0.65 and _luminance(_hexrgb(fam["head"])) < 0.2  # a light tint, a dark heading: not a blob
    assert _rule(".pl-more").count("var(--p-head)") == 1 and "var(--p-eat)" in _rule(".pl-amt")  # the family's colours are what the parts use
    assert match["bg"] != guide["bg"] and _apart(match["bg"], guide["bg"]) >= 25 and _apart(match["bar"], guide["bar"]) >= 150
    assert _contrast(guide["line"], "#ffffff") >= 3.0  # the athlete panel's outline is a button's edge: 3:1 against the white of the "Eat instead" box
    # green for the pill's match (green ahead of red and blue), a cool neutral for the athlete target (blue ahead of red, green near blue)
    r, g, b = _hexrgb(match["bg"])
    assert g > r and g > b
    r, g, b = _hexrgb(guide["bg"])
    assert b > r and abs(b - g) < 20 and g > r


def test_the_athlete_panel_is_none_of_the_other_colours_of_the_card():
    """The warning's orange, the note's blue, the chips' indigo and teal, the footer's red and green: the slate is not one of them."""
    guide = {**_props(_rule(".pl"), "p"), **_props(_rule(".pl-guide"), "p")}
    match = _props(_rule(".pl"), "p")

    def colours(rule: str) -> list[str]:
        return re.findall(r"#[0-9a-f]{6}", rule)

    others = {
        "warning": colours(_rule(".warn")),
        "note": colours(_rule(".bio")),
        "ask chip": colours(_rule(".chip")),
        "swap chip": colours(_rule('.chip[data-tool="swap"]')),
        "footer": colours(_rule("#actions #btnKeep")) + colours(_rule("#actions #btnRepl")) + colours(_rule("#actions #btnBack")),
    }
    assert all(others.values()), others
    for name, found in others.items():
        for colour in found:
            assert colour != guide["bg"] and colour != guide["line"] and colour != guide["bar"], (name, colour)
        tints = [c for c in found if _luminance(_hexrgb(c)) > 0.65]
        for tint in tints:
            assert _apart(tint, guide["bg"]) >= 15, (name, tint)  # a different tint, not the same one with a little noise
    # and the panel for the pill's match keeps to the green family it always had (the "Eat instead" label and Replace are green too)
    assert match["bg"] == "#ecfdf5" and match["line"] == "#a7f3d0" and match["head"] == "#047857"
    assert guide["bg"] == "#d9e0ea"  # (the footer's Back button has a slate-200 border: the panel is the next step down, not the same colour)


# ------------------------------------------------------------------ compact: the tight card, a narrow panel
def test_the_tight_card_makes_the_panels_smaller_without_changing_the_words():
    normal, tight = _rule(".pl"), _rule("#wrap.tight .pl")
    n_pad, t_pad = _px(normal, "padding"), _px(tight, "padding")
    assert n_pad[0] > t_pad[0] and n_pad[0] + n_pad[0] >= t_pad[0] + t_pad[0] + 4  # less air above and below the two rows
    assert "font-size" not in tight and "display" not in tight and "content" not in tight  # same words, same sizes: only the spacing
    assert _px(_rule("#wrap.tight .portion"), "margin-top")[0] < _px(_rule(".portion"), "margin-top")[0]
    assert _px(_rule("#wrap.tight .portion"), "margin-top") and "gap" not in _rule("#wrap.tight .portion")  # the gap between the panels stays 8 px


def test_the_hint_word_makes_way_for_the_sentence_in_a_narrow_panel_and_the_chevron_stays():
    assert "container-type: inline-size" in _rule(".portion")
    query = re.search(r"@container \(max-width: ([0-9.]+)em\) \{ \.pl-why \{ display: none; \} \}", STYLE)  # in em: a larger text size narrows it too
    assert query
    # The panel is the card's width minus 80 px (measured: 320 -> 240, 344 -> 264, 360 -> 280, 375 -> 295 px). A 360 px phone, the most common
    # Android width, keeps the word (the sentence stays on one line beside it there, at every target label); a 344 px phone, where the longest
    # label ("3500 mg") would wrap, drops it. The query is inclusive, so a panel of exactly the limit has NO word: the limit must be below 280.
    assert 264 < float(query.group(1)) * 16 < 280, query.group(1)
    assert "display: none" not in _rule(".pl-more .chev") and "display: none" not in _rule(".pl-more")  # the chevron is always visible
    assert "flex: 1 1 auto" in _rule(".pl-sent") and "min-width: 0" in _rule(".pl-sent")  # the sentence takes what the hint leaves, and may wrap
    sizes = [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)rem", STYLE)]
    assert min(sizes) >= 0.72  # the audit floor holds for the new text too


# ------------------------------------------------------------------ the shapes of the texts the app really builds
AMOUNT = re.compile(r"^[~<]?\d[\d.,]*\s?(?:g|kg)(?: \(.+\))?$")  # "~35 g", "<1 g", "~2.9 g (~0.6 Brazil nuts)", "~20 g (only UV-treated mushrooms ...)"
VERDICT = re.compile(r"^(?:not practical from food alone|a lot of food) \(.+\)$")  # "a lot of food (~470 g/day)", "not practical from food alone (~1.3 kg/day)"
TARGET = re.compile(r"^\d[\d.,]*\s(?:mg|mcg|g)$")  # "200 mg", "25 mcg", "2 g"
LABELS = [
    "Vitamin C 80 mg 100%\nVitamin D3 20 µg 400%\nVitamin B12 2.5 µg 100%\nFolic acid 200 µg 100%\nMagnesium 56 mg 15%\nZinc 10 mg 100%\nSelenium 55 µg 100%",
    "Vitamin A 800 µg 100%\nVitamin E 12 mg 100%\nVitamin K2 75 µg\nThiamin 1.1 mg 100%\nRiboflavin 1.4 mg 100%\nNiacin 16 mg 100%\nVitamin B6 1.4 mg 100%\nBiotin 50 µg 100%\nPantothenic acid 6 mg 100%",
    "Calcium 800 mg 100%\nIron 14 mg 100%\nIodine 150 µg 100%\nCopper 1 mg 100%\nManganese 2 mg 100%\nChromium 40 µg 100%\nMolybdenum 50 µg 100%\nPotassium 200 mg 10%\nPhosphorus 700 mg",
    "Omega-3 (EPA+DHA) 500 mg\nCholine 100 mg\nFluoride 3 mg\nSodium 100 mg\nChloride 200 mg\nFolate (as 5-MTHF) 400 µg DFE\nVitamin D3 1000 IU\nVitamin C 1000 mg\nMagnesium 400 mg\nZinc 25 mg\nIron 28 mg",
]


def _kind(text: str) -> str:
    if not text:
        return "none"
    return "amount" if AMOUNT.match(text) else "verdict" if VERDICT.match(text) else "other"


def test_the_table_of_shapes_the_browser_tests_use_is_made_of_the_kinds_the_app_builds():
    kinds = set()
    for name, shape in SHAPES.items():
        for key in ("matchDose", "rdaAmount"):
            kind = _kind(shape.get(key, ""))
            assert kind != "other", (name, key, shape.get(key))
            kinds.add(kind)
        assert not shape.get("rdaLabel") or TARGET.match(shape["rdaLabel"]), name
        assert bool(shape.get("rdaLabel")) <= bool(shape.get("rdaAmount")), name  # the app sets the target's label only together with its amount
    assert kinds == {"none", "amount", "verdict"}


@pytest.fixture(scope="module")
def built_cards():
    """The props of every card of four labels that together name every nutrient of the RDA table (the real builder, offline)."""
    AppTest.from_file(APP, default_timeout=60).run()  # the first run of a process reloads blockbrain.app (the stale-module guard): do it once, first
    cards = []
    for label in LABELS:
        at = AppTest.from_file(APP, default_timeout=120)
        at.run()
        at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "camera_barcode": "", "manual": label}
        at.session_state["swipe_is_analyzing"] = True
        at.session_state["swipe_analysis_kicked"] = True
        at.session_state["swipe_last_auto_signature"] = "own-label"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        for i in range(len(at.session_state["swipe_cards"])):
            args = card_args(at)
            assert args, (label, i)
            cards.append(args)
            swipe(at, "left", f"k{i}")
    return cards


def test_every_amount_the_app_builds_is_a_kind_the_panels_can_split(built_cards):
    """The component splits "~2.9 g (~0.6 Brazil nuts)" at its first bracket and treats everything that does not start with ~, < or a digit as
    a verdict: so the app must keep building amounts of the first kind and verdicts of the second, and a target label of the plain kind."""
    assert len(built_cards) >= 30, len(built_cards)  # one card per nutrient of the table, and then some
    seen = {"amount": 0, "verdict": 0, "none": 0}
    for args in built_cards:
        for key in ("matchDose", "rdaAmount"):
            text = args.get(key) or ""
            kind = _kind(text)
            assert kind != "other", (args["name"], key, text)
            seen[kind] += 1
            if kind == "verdict":
                assert not re.match(r"[~<\d]", text), text  # no "eat" and no figure before a verdict
            if kind == "amount":
                assert not text.startswith(("not ", "a lot")), text
        label = args.get("rdaLabel") or ""
        assert (label == "") == (not args.get("rdaAmount")), (args["name"], label, args.get("rdaAmount"))  # no amount, no target; an amount, its target
        assert label == "" or TARGET.match(label), (args["name"], label)
        if args.get("matchDose"):
            assert re.fullmatch(r"\d[^()–]{0,13}", args["dose"]), args["dose"]  # a pill dose that is a plain amount: the sentence can name it
    assert seen["amount"] > 40 and seen["verdict"] >= 5 and seen["none"] >= 1, seen  # all three kinds are really built

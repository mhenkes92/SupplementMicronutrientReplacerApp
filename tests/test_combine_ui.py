"""The "How to combine" parts that the visitor sees (offline): the "Pairs well with" prop of a swipe card, the "Your day" block at the top of
the Meals tab (HTML builders and every state of it), "See all combinations", the part of a plan row's window, the bridge caption under the
meals radio and the share text. The rules and the engine are in test_combine_rules.py / test_combine_engine.py; what only a browser can
prove (geometry, tap targets, keyboard, contrast, no horizontal scroll) is in test_combine_browser.py."""
from __future__ import annotations

import html as htmllib
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import combine
import llm_cache
from test_combine_rules import FORBIDDEN, HOURS

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "swipe_mobile_app" / "app.py")
APP_SRC = (ROOT / "swipe_mobile_app" / "app.py").read_text(encoding="utf-8")
SAMPLE = ["vitamin c", "vitamin d3", "vitamin b12", "folic acid", "magnesium", "zinc", "selenium"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")  # no background meal plan: it would use quota and the fake
    monkeypatch.delenv("SUPPSWIPE_COMBINE", raising=False)
    llm_cache.clear()
    yield
    llm_cache.clear()


# ------------------------------------------------------------------------------------------------------------------------ helpers
@pytest.fixture(scope="module")
def lab(sw):
    class Lab:
        pass

    lab = Lab()
    lab.sw = sw
    lab.pools = {}

    def pool(nutrient, contains):
        if nutrient not in lab.pools:
            lab.pools[nutrient] = [dict(r) for r in sw.bb._build_local_food_rows_for_component(nutrient, limit=150)]
        return dict(next(r for r in lab.pools[nutrient] if contains.lower() in r["food_description"].lower()))

    def keep(component, value, unit, form="", index=0):
        return {
            "component_key": sw.bb.normalize_lookup_key(component), "component": component,
            "dose_label": sw._dose_label({"dose_value": value, "dose_unit": unit}), "dose_value": value, "dose_max": None,
            "dose_unit": unit, "form": form, "decision": "keep", "selected_food": None, "card_index": index,
        }

    def replace(component, value, unit, food, form="", index=0):
        return {**keep(component, value, unit, form, index), "decision": "replace", "selected_food": food}

    def html_of(keep_items, replace_items=()):
        """The block as the Meals tab draws it for these decisions."""
        day = sw._combine_day(list(keep_items), list(replace_items), False)
        rows = combine.table_rows(list(keep_items), list(replace_items), sw._combine_hooks(False))
        return day, sw._combine_day_html(day, rows)

    lab.pool, lab.keep, lab.replace, lab.html_of = pool, keep, replace, html_of
    return lab


def _classes(markup: str) -> set[str]:
    return {c for value in re.findall(r"class='([^']*)'", markup) for c in value.split()}


def _text_of(markup: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _results(decide: dict[str, str] | None = None, default: str = "left") -> AppTest:
    """The sample label decided card by card ("right" = replace with the card's food, "left" = keep the pill)."""
    decide = decide or {}
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_try_sample").click().run()
    cards = at.session_state["swipe_cards"]
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    for i, card in enumerate(cards):
        direction = decide.get(card["component_key"], default)
        if direction == "right" and not card.get("foods"):
            direction = "left"
        at.session_state[key] = {"dir": direction, "id": f"t{i}", "card": card["component_key"], "index": i}
        at.run()
    assert at.session_state["swipe_index"] == len(cards)
    assert not at.exception, [e.value for e in at.exception]
    return at


def _flat(node) -> list:
    """The node and everything under it (AppTest's blocks hold their children in `children`; an element has none)."""
    out = [node]
    for child in getattr(node, "children", {}).values():
        out += _flat(child)
    return out


def _block(at: AppTest) -> str:
    """The "Your day" markdown of the page ("" when there is none)."""
    return next((m.value for m in at.markdown if "class='cb-card'" in m.value), "")


# ------------------------------------------------------------------------------------------------------------ the HTML builders
def test_the_block_is_one_line_without_ids_and_every_class_is_a_single_literal(lab):
    day, markup = lab.html_of(
        [lab.keep("Iron", 14, "mg", index=0), lab.keep("Vitamin D3", 20, "mcg", index=1), lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=2),
         lab.keep("Magnesium", 56, "mg", index=3)]
    )
    assert markup and "\n" not in markup and "  " not in markup  # Markdown would break an HTML block (a newline) or make code of it (4 spaces)
    assert not re.search(r"\bid=|aria-labelledby", markup)  # the sanitizer may rewrite ids
    assert markup.startswith("<div class='plan-h' role='heading' aria-level='3'>")
    for cls in _classes(markup):
        assert re.search(rf"class='{re.escape(cls)}'", APP_SRC), cls  # tests/test_ux_css.py reads exactly this
    # tags are balanced (the details, summaries and lists are what the page's DOM must be able to nest)
    for tag in ("details", "summary", "ul", "li", "div", "span", "a"):
        assert len(re.findall(rf"<{tag}[ >]", markup)) == len(re.findall(rf"</{tag}>", markup)), tag


def test_headings_do_not_skip_a_level_and_lists_are_named(lab):
    _day, markup = lab.html_of([lab.keep("Iron", 14, "mg"), lab.keep("Vitamin D3", 20, "mcg"), lab.keep("Magnesium", 56, "mg", index=2)])
    levels = [int(n) for n in re.findall(r"role='heading' aria-level='(\d)'", markup)]
    assert levels[0] == 3 and set(levels[1:]) == {4}  # "Your day" is level 3 under the page title's level 2; meals and "Any meal" are level 4
    for label in re.findall(r"<ul class='cb-items' aria-label='([^']*)'>", markup):
        assert label  # every list has a name
    assert markup.count("<ul ") == markup.count("<ul class='cb-items' aria-label='")
    # a chip says what it is for a screen reader before its decorative icon
    assert "<li class='cb-chip' data-kind='pill'><span class='cb-sr'>Pill: </span><span aria-hidden='true'>" in markup


def test_every_engine_string_is_escaped(lab):
    evil = "<img src=x onerror=alert(1)>"
    day = {
        "has_items": True, "needs_layout": False, "empty": False,
        "slots": [
            {"slot": 1, "key": "breakfast", "label": evil, "items": [{"kind": "pill", "text": evil, "icon": evil, "label": "x", "uid": "p"}],
             "tips": [{"rule_id": "x", "also": [], "rel": "with<", "rank": 1, "text": evil, "why": evil, "caveats": [evil], "extra": evil,
                       "sources": [{"name": evil, "url": "https://example.org/?a=1&b='2'"}], "items": []}]},
        ],
        "anytime": [], "held": [], "free_foods": [], "directives": [], "medicine": None,
    }
    rows = [{"id": "x", "rel": "apart", "icon": "", "title": evil, "text": evil, "why": evil, "caveats": [evil], "in_plan": True,
             "sources": [{"name": evil, "url": "javascript:alert(1)"}, {"name": evil, "url": ""}], "confidence": "high"}]
    markup = lab.sw._combine_day_html(day, rows)
    assert "<img" not in markup and "onerror=alert" not in markup.replace("onerror=alert(1)&gt;", "")
    assert "&lt;img src=x onerror=alert(1)&gt;" in markup
    assert "href='javascript" not in markup and "href='https://example.org/?a=1&amp;b=&#x27;2&#x27;'" in markup  # only https links, quoted safely
    assert "<details class='cb-tip' data-rel='note'>" in markup  # an unknown rel is a note, never injected into the attribute


def test_a_source_is_a_whole_line_link_that_opens_in_a_new_tab_and_says_so(lab):
    _day, markup = lab.html_of([lab.keep("Vitamin D3", 20, "mcg")])
    links = re.findall(r"<a class='cb-src' href='(https://[^']+)' target='_blank' rel='noopener noreferrer'>(Source: [^<]+)<span class='cb-sr'> \(opens in a new tab\)</span></a>", markup)
    assert links and all("ods.od.nih.gov" in href or "pubmed" in href or "efsa" in href for href, _ in links)
    assert "target='_blank' rel='noopener" in markup and "target='_blank'>" not in markup  # never without rel
    # a source without a usable address is only a name
    assert lab.sw._combine_source_html({"name": "A book", "url": ""}) == "<div class='cb-src'>Source: A book</div>"
    assert lab.sw._combine_source_html({"name": "A book", "url": "http://x.org"}) == "<div class='cb-src'>Source: A book</div>"


def test_a_tip_is_a_native_disclosure_whose_words_are_the_rules(lab):
    day, markup = lab.html_of([lab.keep("Vitamin D3", 20, "mcg")])
    tips = [t for s in day["slots"] for t in s["tips"]]
    assert [t["rule_id"] for t in tips] == ["fat-vitamins-de"]
    rule = combine.rule("fat-vitamins-de")
    block = re.search(r"<details class='cb-tip' data-rel='with'>.*?</details>", markup).group(0)
    assert f"<span class='cb-t'><span class='cb-sr'>Pairs well: </span>{htmllib.escape(rule['takeaway'])}</span>" in block
    assert htmllib.escape(rule["why"]) in block and f"Note: {htmllib.escape(rule['caveats'])}" in block
    assert htmllib.escape(combine._HINTS["add_fat"]) in block  # the hint only shows INSIDE the opened tip, once
    assert markup.count(htmllib.escape(combine._HINTS["add_fat"])) == 1
    assert "<details class='cb-tip' data-rel='with' open" not in markup  # closed at first: one line, the why one tap away
    # the three kinds are told apart by a word as well as a glyph
    _d, mixed = lab.html_of([lab.keep("Iron", 14, "mg"), lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=1)])
    for rel, word in (("apart", "Better apart: "), ("with", "Pairs well: "), ("note", "Good to know: ")):
        assert f"<details class='cb-tip' data-rel='{rel}'><summary><span class='cb-g' aria-hidden='true'>" in mixed
        assert f"<span class='cb-sr'>{word}</span>" in mixed


def test_a_meal_shows_at_most_three_tips_and_the_table_has_them_all(lab):
    plan = [lab.keep("Iron", 14, "mg"), lab.keep("Vitamin D3", 20, "mcg", index=1), lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=2),
            lab.keep("Zinc", 10, "mg", index=3)]
    day, markup = lab.html_of(plan, [lab.replace("Iron", 14, "mg", lab.pool("iron", "lentils"), index=4)])
    biggest = max(len(s["tips"]) for s in day["slots"])
    assert biggest >= 3
    slots = re.findall(r"<div class='cb-slot'>.*?(?=<div class='cb-slot'>|<div class='cb-any'>|<div class='cb-med'>|<details class='cb-all'>)", markup)
    assert slots
    for slot in slots:
        assert slot.count("<details class='cb-tip'") <= lab.sw._COMBINE_TIPS_PER_SLOT
    table = markup[markup.index("<details class='cb-all'>"):]
    assert table.count("<details class='cb-tip'") == len(combine.RULES) == 11  # nothing is hidden: every rule is in the table
    assert all(htmllib.escape(r["takeaway"]) in table for r in combine.RULES)


def test_the_table_puts_the_visitors_own_combinations_first_and_says_so(lab):
    _d, markup = lab.html_of([lab.keep("Vitamin D3", 20, "mcg"), lab.keep("Magnesium", 56, "mg", index=1)])
    table = markup[markup.index("<details class='cb-all'>"):]
    assert table.startswith("<details class='cb-all'><summary>See all combinations</summary><div class='cb-rows'>")
    first, rest = table.split("<div class='cb-label'>Also good to know</div>")
    assert first.count("<span class='cb-tag'>In your plan</span>") == 2 and "<span class='cb-tag'>" not in rest
    assert "Vitamins D and E + fat" in first and "Pills + medicines" in first
    assert "How foods and pills combine" not in table
    # a plan none of the rules is about: one neutral label, no tag
    _d, plain = lab.html_of([lab.keep("Selenium", 55, "mcg")])
    table = plain[plain.index("<details class='cb-all'>"):]
    assert "<div class='cb-label'>How foods and pills combine</div>" in table and "cb-tag" not in table and "Also good to know" not in table
    # more than one source: a heading and one line each; one source: "Source: name"
    assert "<div class='cb-srch'>Sources:</div>" in table and "<a class='cb-src' href='https://" in table


def test_the_words_the_visitor_reads_pass_the_safety_lint(lab):
    _d, markup = lab.html_of(
        [lab.keep("Iron", 14, "mg"), lab.keep("Vitamin D3", 20, "mcg", index=1), lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=2),
         lab.keep("Magnesium", 56, "mg", index=3)], [lab.replace("Iron", 14, "mg", lab.pool("iron", "lentils"), index=4)],
    )
    words = _text_of(markup) + " " + " ".join(
        [lab.sw._COMBINE_CAPTION, lab.sw._COMBINE_EMPTY_TITLE, lab.sw._COMBINE_EMPTY_BODY, lab.sw._COMBINE_HELD_TITLE, lab.sw._COMBINE_HELD_NOTE,
         combine.ONE_TABLET_NOTE, *lab.sw._COMBINE_BRIDGE.values()]
    )
    assert not FORBIDDEN.search(words), FORBIDDEN.search(words)
    assert not HOURS.search(words)
    # the tips and their why / notes carry no number but the sourced 500 mg and 25 mg (the doses in the chips and the sources' years are not tips)
    inside = markup.split("<div class='cb-dbody'>", 1)[1]  # the closed strip above it only counts the tips ("6 pairing tips")
    prose = " ".join(_text_of(m) for pair in re.findall(r"<span class='cb-t'>(.*?)</span></summary>|<div class='cb-(?:why|note|extra)'>(.*?)</div>", inside) for m in pair if m)
    assert prose and set(re.findall(r"\d+", prose)) <= {"500", "25"}, set(re.findall(r"\d+", prose))
    assert lab.sw._COMBINE_CAPTION.endswith("General guidance, not medical advice.")


# ------------------------------------------------------------------------------------------------------------ the states of the block
def test_nothing_chosen_draws_no_block(lab):
    assert lab.sw._combine_day_html(lab.sw._combine_day([], [], False), []) == ""
    assert lab.sw._combine_day_html({}, []) == "" and lab.sw._combine_day_html(None, []) == ""  # type: ignore[arg-type]


def test_items_but_no_tip_is_a_calm_empty_card_never_a_blank_or_empty_meals(lab):
    # selenium: no rule about it, so it is "Any meal" at most; the card says so instead of drawing three empty meals
    day, markup = lab.html_of([lab.keep("Selenium", 55, "mcg")])
    assert day["has_items"] and day["empty"]
    assert "<details class='cb-day' data-state='empty'><summary><span class='cb-g' aria-hidden='true'>✓</span>" in markup  # the closed strip says it
    assert "No special pairing needed" in markup and "<div class='cb-empty'><div class='cb-eb'>Nothing in your plan needs a particular order." in markup
    assert "cb-slot" not in markup and "Breakfast" not in markup and "cb-med" not in markup  # no empty meals, no medicine strip
    assert "See all combinations" in markup  # the way on: never a dead end
    # a mineral pill brings the pharmacist strip into the empty card
    _d, with_med = lab.html_of([lab.keep("Magnesium", 56, "mg")])
    assert "No special pairing needed" in with_med and "<div class='cb-med'><details class='cb-tip' data-rel='note'>" in with_med


def test_the_day_is_one_closed_strip_that_says_what_is_inside(lab):
    """The Meals tab opens on meals, not on a schedule: the day is a 44 px strip ("4 pairing tips", the meals it names) until a tap."""
    day, markup = lab.html_of([lab.keep("Iron", 14, "mg"), lab.keep("Vitamin D3", 20, "mcg", index=1),
                               lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=2), lab.keep("Magnesium", 56, "mg", index=3)])
    assert "<details class='cb-day' data-state='tips'><summary>" in markup and "<details class='cb-day' data-state='tips' open" not in markup
    strip = re.search(r"<details class='cb-day'[^>]*><summary>(.*?)</summary>", markup).group(1)
    body = markup.split("<div class='cb-dbody'>", 1)[1]
    drawn = body[:body.index("<details class='cb-all'>")].count("<details class='cb-tip'")  # the tips of the meals + the pharmacist strip
    assert drawn >= 4 and _text_of(strip).startswith(f"{drawn} pairing tips")
    assert _text_of(strip).endswith("Breakfast · Lunch · Dinner") and "<ul" not in strip  # it names the meals, it draws none of them
    # everything else (the caption, the meals, the table) is inside, after the strip: closed, none of it is on screen
    head = markup[:markup.index("<details class='cb-day'")]
    assert "cb-cap" not in head and "cb-slot" not in head and head.count("<div") == 2  # the heading and the card, nothing else
    assert body.startswith("<div class='cb-cap'>") and "cb-slot" in body and "<details class='cb-all'>" in body
    # one tip is "1 pairing tip"; the strip is not a tip itself (no rel, no source)
    _d, one = lab.html_of([lab.keep("Vitamin D3", 20, "mcg")])
    assert _text_of(re.search(r"<details class='cb-day'[^>]*><summary>(.*?)</summary>", one).group(1)) == "1 pairing tip . Lunch"
    assert lab.sw._combine_summary({"medicine": None, "held": []}, [{"label": "Lunch", "items": [{}], "tips": []}]) == ("tips", "", "Meal by meal", "Lunch")


def test_the_day_tells_a_visitor_whose_nutrients_are_in_one_tablet_what_to_do(lab):
    tablet = [lab.keep("Vitamin D3", 20, "mcg"), lab.keep("Iron", 14, "mg", index=1), lab.keep("Calcium", 120, "mg", "calcium carbonate", index=2),
              lab.keep("Zinc", 10, "mg", index=3), lab.keep("Magnesium", 56, "mg", index=4), lab.keep("Vitamin C", 80, "mg", index=5)]
    day, markup = lab.html_of(tablet)
    assert day["split_pills"]
    cap = re.search(r"<div class='cb-cap'>(.*?)</div>", markup).group(1)
    assert cap == htmllib.escape(lab.sw._COMBINE_CAPTION + " " + combine.ONE_TABLET_NOTE)
    assert cap.startswith("A simple order for your pills and foods") and "General guidance, not medical advice." in cap
    apart = re.search(r"<details class='cb-tip' data-rel='apart'>(?:(?!</details>).)*Take iron and calcium pills at different meals.*?</details>", markup).group(0)
    assert f"Note: {htmllib.escape(combine._ONE_TABLET)}" in apart  # in the opened tip too
    # nothing kept apart: the caption is the caption
    _d, calm = lab.html_of([lab.keep("Vitamin D3", 20, "mcg"), lab.keep("Magnesium", 56, "mg", index=1)])
    assert re.search(r"<div class='cb-cap'>(.*?)</div>", calm).group(1) == htmllib.escape(lab.sw._COMBINE_CAPTION)


def test_swapped_foods_alone_have_no_meals_unless_a_layout_is_needed(lab):
    day, markup = lab.html_of([], [lab.replace("Vitamin C", 80, "mg", lab.pool("vitamin c", "guava"))])
    assert day["has_items"] and not day["needs_layout"]
    assert "No special pairing needed" in markup and "Guavas" not in markup
    # a plant-iron food alone needs a vitamin C partner: it is placed, with its two tips
    lentils = lab.pool("iron", "lentils")
    day, markup = lab.html_of([], [lab.replace("Iron", 14, "mg", lentils)])
    assert day["needs_layout"]
    lunch = re.search(r"<div class='cb-slot'>.*?(?=<div class='cb-slot'>|<details class='cb-all'>)", markup).group(0)
    assert "Lunch" in lunch and "<li class='cb-chip' data-kind='food'><span class='cb-sr'>Food: </span>" in lunch
    for rule_id in ("iron-vitc", "iron-tea-coffee"):
        assert htmllib.escape(combine.rule(rule_id)["takeaway"]) in lunch


def test_split_calcium_is_two_chips_and_the_tip_is_said_once(lab):
    day, markup = lab.html_of([lab.keep("Calcium", 1000, "mg", "calcium carbonate")])
    assert markup.count("Calcium, half of 1000 mg") == 2  # two meals, two chips
    split = htmllib.escape(combine.rule("calcium-split-500")["takeaway"])
    assert markup[:markup.index("<details class='cb-all'>")].count(split) == 1


def test_a_pill_over_its_upper_level_is_not_placed_and_gets_no_advice(lab):
    day, markup = lab.html_of([lab.keep("Iron", 60, "mg"), lab.keep("Vitamin D3", 20, "mcg", index=1)])
    assert [i["label"] for i in day["held"]] == ["Iron"]
    held = re.search(r"<div class='cb-held'>.*?</div></div>", markup).group(0)
    assert "See the Heads-up" in held and "Iron 60 mg" in held and lab.sw._COMBINE_HELD_NOTE in held
    assert "Not scheduled" not in markup and "cb-tip" not in held  # no timing advice, nothing that nudges absorption
    meals = markup[:markup.index("<div class='cb-held'>")]
    assert "Iron" not in meals and "Vitamin D3 20 mcg" in meals  # the other pills never pair with it
    # only a held pill: NO reassurance ("No special pairing needed", a green tick, "Enjoy your pills") above pills the Heads-up box warns
    # about; just the row that points to the box (and the pharmacist strip: a medicine binds whatever the dose)
    day, only = lab.html_of([lab.keep("Iron", 60, "mg")])
    assert "<details class='cb-day' data-state='held'><summary><span class='cb-g' aria-hidden='true'>i</span>" in only
    for reassurance in ("No special pairing needed", "✓", "Enjoy", "cb-empty", "Nothing in your plan needs"):
        assert reassurance not in only, reassurance
    assert "See the Heads-up" in only and "<div class='cb-held'>" in only and "<div class='cb-med'>" in only
    # several held pills and a pill that needs no order: still no reassurance (the pill with no order is not drawn without a meal)
    _d, mixed = lab.html_of([lab.keep("Iron", 60, "mg"), lab.keep("Zinc", 60, "mg", index=1), lab.keep("Selenium", 55, "mcg", index=2)])
    assert "data-state='held'" in mixed and "Enjoy" not in mixed and "No special pairing needed" not in mixed
    assert "Iron 60 mg" in mixed and "Zinc 60 mg" in mixed


def test_the_beta_carotene_advice_for_smokers_is_not_called_an_upper_level(lab):
    """The Heads-up box holds two kinds of warning. EFSA's advice to smokers about beta-carotene (15 mg or more) is not an upper intake
    level, so the day says only "see the Heads-up", never "above the usual upper level" or "not scheduled" about a non-smoker's pill."""
    sw = lab.sw
    pill = lab.keep("Beta carotene", 15, "mg")
    warning = sw._final_upper_limit_warnings([pill])
    assert warning and "smokers" in warning[0] and "upper intake level" not in warning[0]
    day, markup = lab.html_of([pill])
    assert [i["label"] for i in day["held"]] == ["Beta-carotene"]  # the Heads-up warns about it, so it is not placed
    text = _text_of(markup)
    assert "Beta-carotene 15 mg" in text and "See the Heads-up" in text
    for claim in ("upper level", "upper intake", "Above the usual", "Not scheduled", "Enjoy", "No special pairing needed"):
        assert claim not in text, claim
    for words in (sw._COMBINE_HELD_TITLE, sw._COMBINE_HELD_NOTE):
        assert "upper" not in words.lower() and "scheduled" not in words.lower() and "dose" not in words.lower()


def test_a_plan_of_held_pills_alone_has_no_reassurance_on_the_results_page(lab):
    at = _results({})
    at.session_state["swipe_decisions"] = {"iron": lab.keep("Iron", 60, "mg"), "zinc": lab.keep("Zinc", 60, "mg", index=1)}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    box = next(m.value for m in at.markdown if "class='plan-warn'" in m.value)
    assert "above the upper intake level for iron" in box  # the orange box is the message
    block = _block(at)
    assert block and "data-state='held'" in block and "See the Heads-up" in block
    for reassurance in ("Enjoy", "No special pairing needed", "✓", "Nothing in your plan needs"):
        assert reassurance not in block, reassurance


def test_the_pharmacist_strip_is_the_rule_and_only_for_calcium_iron_magnesium_zinc(lab):
    _d, with_zinc = lab.html_of([lab.keep("Zinc", 10, "mg")])
    medicine = combine.rule("medicine-spacing")
    assert htmllib.escape(medicine["takeaway"]) in with_zinc and htmllib.escape(medicine["why"]) in with_zinc
    assert not re.search(r"\d", medicine["takeaway"])  # no hours, no numbers
    _d, without = lab.html_of([lab.keep("Vitamin D3", 20, "mcg"), lab.keep("Selenium", 55, "mcg", index=1)])
    assert "<div class='cb-med'>" not in without  # the table still has the row, closed


def test_a_plant_based_or_pregnancy_plan_gets_only_the_foods_the_app_already_allows(lab):
    liver = lab.pool("iron", "liver")
    assert lab.sw._not_advised_in_pregnancy(liver)
    kept = [lab.keep("Iron", 14, "mg")]
    swapped = [lab.replace("Iron", 14, "mg", liver, index=1)]
    day = lab.sw._combine_day(kept, swapped, True)  # pregnant: the liver is not placed anywhere
    assert [i["label"] for s in day["slots"] for i in s["items"] if i["kind"] == "food"] == []
    assert "Liver" not in lab.sw._combine_day_html(day, [])


# ------------------------------------------------------------------------------------------------------------ the swipe card's tags
def test_card_pairs_only_where_a_sourced_rule_has_a_tag(sw):
    def pairs(key, value, unit, form="", **extra):
        card = {"component_key": key, "component": key.title(), "dose_value": value, "dose_unit": unit, "form": form, "dose_max": None, **extra}
        return sw._card_pairs(card, key, form)

    assert pairs("vitamin d3", 20, "mcg") == [{"icon": "\U0001F951", "text": "some fat"}]
    assert pairs("vitamin e", 15, "mg") == [{"icon": "\U0001F951", "text": "some fat"}]
    assert pairs("fish oil", 1000, "mg") == [{"icon": combine.rule("fat-omega3")["icon"], "text": "some fat"}]  # the rule's own icon (a fish)
    assert pairs("calcium", 400, "mg", "calcium carbonate") == [{"icon": "\U0001F37D️", "text": "a meal"}]
    for key, value, unit in (("magnesium", 56, "mg"), ("zinc", 10, "mg"), ("vitamin c", 80, "mg"), ("selenium", 55, "mcg"), ("iron", 14, "mg"),
                             ("vitamin b12", 2.5, "mcg"), ("folic acid", 200, "mcg"), ("unknown thing", 1, "mg")):
        assert pairs(key, value, unit) == [], key
    assert pairs("calcium", 400, "mg") == []  # an unknown form shows no carbonate tag


def test_no_tag_on_a_card_with_an_upper_limit_warning_and_none_when_off(sw, monkeypatch):
    card = {"component_key": "vitamin d3", "component": "Vitamin D3", "dose_value": 5000, "dose_unit": "mcg", "form": "", "dose_max": None}
    assert sw._final_upper_limit_warnings([card])  # 5000 mcg is far over the upper level: the card shows the orange warning
    assert sw._card_pairs(card, "vitamin d3", "") == []
    ok = {**card, "dose_value": 20}
    assert sw._card_pairs(ok, "vitamin d3", "")
    monkeypatch.setenv("SUPPSWIPE_COMBINE", "0")
    assert sw._card_pairs(ok, "vitamin d3", "") == []


def _card_props(at: AppTest) -> dict:
    import json

    for element in at.get("component_instance"):
        args = json.loads(element.proto.json_args or "{}")
        if "cardId" in args:
            return args
    return {}


def test_in_the_sample_label_only_vitamin_d3_gets_the_row():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_try_sample").click().run()
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    seen = {}
    for i, card in enumerate(at.session_state["swipe_cards"]):
        props = _card_props(at)
        seen[card["component_key"]] = props.get("pairs")
        at.session_state[key] = {"dir": "left", "id": f"t{i}", "card": card["component_key"], "index": i}
        at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert seen == {k: ([{"icon": "\U0001F951", "text": "some fat"}] if k == "vitamin d3" else []) for k in SAMPLE}


def test_the_card_row_is_off_with_the_switch():
    import os

    os.environ["SUPPSWIPE_COMBINE"] = "0"
    try:
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        at.button(key="appbar_scan").click().run()
        at.button(key="swipe_try_sample").click().run()
        key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
        at.session_state[key] = {"dir": "left", "id": "t0", "card": "vitamin c", "index": 0}
        at.run()
        assert _card_props(at)["pairs"] == [] and _card_props(at)["cardId"] == "vitamin d3"
    finally:
        os.environ.pop("SUPPSWIPE_COMBINE", None)


# ------------------------------------------------------------------------------------------------------------ the results page
def test_kept_pills_alone_have_a_day_drawn_after_the_swipe_right_hint():
    at = _results({}, default="left")  # every pill kept: nothing swapped
    block = _block(at)
    assert block and "Lunch" in block and "Vitamin D3 20 mcg" in block and "Any meal" in block
    children = list(at.tabs[1].children.values())
    kinds = [c.type for c in children]
    info_at = next(i for i, c in enumerate(children) if c.type == "info" and "Swipe right on at least one nutrient" in c.value)
    block_at = next(i for i, c in enumerate(children) if c.type == "markdown" and "class='cb-card'" in c.value)
    assert info_at < block_at, kinds  # the early return does not take the block with it
    assert not [b for b in at.button if "combine" in str(b.key) or str(b.key).startswith("cb_")]  # no widget, no key, no rerun


def test_the_block_comes_after_the_plan_controls_and_adds_no_widgets():
    """The Meals tab opens on meals and its main button: the day is below them (above them it pushed "Generate my meals" a screen down)."""
    with_swaps = _results({"vitamin c": "right", "vitamin b12": "right"})
    block = _block(with_swaps)
    assert block
    tab = with_swaps.tabs[1]
    children = list(tab.children.values())
    day_at = next(i for i, c in enumerate(children) if c.type == "markdown" and "class='cb-card'" in c.value)
    quick = next(i for i, c in enumerate(children) if c.type == "markdown" and "Quick ideas" in c.value)
    radio = next(i for i, c in enumerate(children) if c.type == "radio")
    controls = next(i for i, c in enumerate(children) if any(b.type == "button" and "Generate my meals" in b.label for b in _flat(c)))
    assert quick < radio < controls < day_at  # Quick ideas, the meals radio, the plan controls, then the day (the last thing of the tab)
    assert day_at == len(children) - 1
    # widgets of the page: the same as without the feature (a day is markdown only)
    import os

    os.environ["SUPPSWIPE_COMBINE"] = "0"
    try:
        off = _results({"vitamin c": "right", "vitamin b12": "right"})
    finally:
        os.environ.pop("SUPPSWIPE_COMBINE", None)
    assert [b.key for b in with_swaps.button] == [b.key for b in off.button]
    assert [r.key for r in with_swaps.radio] == [r.key for r in off.radio]
    assert _block(off) == "" and not any("\U0001F550 Your day</div>" in m.value for m in off.markdown)


def test_the_day_keeps_its_place_in_the_page_when_the_meal_plan_is_done(monkeypatch):
    """The plan's controls (the button, the live plan, the finished plan with "Different meals") are ONE element of the tab, so the day
    below them keeps the same index when the plan is written: Streamlit remounts an element whose index moved, and a strip the visitor
    had opened while the plan was being written would snap shut (tests/test_combine_browser.py proves it in a real browser)."""
    from concurrent.futures import Future

    def day_at(at: AppTest) -> tuple[int, int]:
        children = list(at.tabs[1].children.values())
        return next(i for i, c in enumerate(children) if c.type == "markdown" and "class='cb-card'" in c.value), len(children)

    at = _results({"vitamin c": "right", "vitamin b12": "right"})
    idle = day_at(at)  # "Generate my meals" is there
    assert [b for b in at.button if "Generate my meals" in b.label]
    monkeypatch.setattr(llm_cache, "inflight", lambda key: Future())  # the plan is being written in the background
    at.run()
    assert not at.exception and not [b for b in at.button if "Generate my meals" in b.label]
    writing = day_at(at)
    monkeypatch.setattr(llm_cache, "inflight", lambda key: None)
    monkeypatch.setattr(llm_cache, "get", lambda key: "**Meal 1** Red lentils with guava.")  # ... and is done
    at.run()
    assert [b for b in at.button if "Different meals" in b.label]
    done = day_at(at)
    assert idle == writing == done and done[0] == done[1] - 1, (idle, writing, done)


def test_the_five_tabs_and_no_exception_with_the_block():
    at = _results({"vitamin c": "right", "zinc": "right"})
    assert [t.label for t in at.tabs] == ["🥗 Plan", "🍽️ Meals", "🛒 Shopping", "💬 Ask AI", "📤 Share"]
    assert not at.exception, [e.value for e in at.exception]


def test_the_share_text_carries_the_day_as_plain_lines_and_nothing_else():
    at = _results({"vitamin c": "right", "vitamin b12": "right"})
    shared = next(c.value for c in at.code if "SuppSwipe — my results" in c.value)
    assert "How to combine (general guidance, not medical advice):" in shared
    assert "Lunch: Vitamin D3 20 mcg" in shared and "Any meal: " in shared
    after = shared[shared.index("How to combine"):]
    assert "Source" not in after.split("Made with SuppSwipe")[0] and "http" not in after.split("Made with SuppSwipe")[0]  # lines only: no tips, no sources
    import os

    os.environ["SUPPSWIPE_COMBINE"] = "0"
    try:
        off = _results({"vitamin c": "right", "vitamin b12": "right"})
    finally:
        os.environ.pop("SUPPSWIPE_COMBINE", None)
    assert "How to combine" not in next(c.value for c in off.code if "SuppSwipe — my results" in c.value)


def test_the_bridge_caption_follows_the_meals_radio_only_when_the_plan_has_a_layout(lab, sw, monkeypatch):
    # The sample's foods need no layout: no caption. A lone plant-iron food does.
    at = _results({"vitamin c": "right"})
    assert not [c for c in at.caption if "Written around your day" in c.value]
    captions = sw._COMBINE_BRIDGE
    assert captions[3].startswith("Written around your day:") and "2 meals" in captions[2] and "1 meal," in captions[1]
    assert all(not FORBIDDEN.search(v) and not HOURS.search(v) for v in captions.values())


# ------------------------------------------------------------------------------------------------------------ the plan row's window
def _with_decisions(at: AppTest, extra: dict) -> AppTest:
    decisions = dict(at.session_state["swipe_decisions"])
    decisions.update(extra)
    at.session_state["swipe_decisions"] = decisions
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _open_row(at: AppTest, kind: str, key: str) -> AppTest:
    at.session_state["swipe_plan_item"] = {"kind": kind, "key": key}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _plan_text(at: AppTest) -> str:
    """Everything the open options window says (the page behind it carries the whole table of combinations, so it is no place to look)."""
    out: list[str] = []

    def walk(node) -> None:
        for child in getattr(node, "children", {}).values():
            if child.type in ("markdown", "caption"):
                out.append(str(child.value))
            walk(child)

    for dialog in at.get("dialog"):
        walk(dialog)
    return " ".join(out)


def test_a_kept_calcium_pill_window_has_when_and_how_to_take_it(lab):
    at = _results({})
    extra = {
        "calcium": lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=8),
        "iron": lab.keep("Iron", 14, "mg", index=9),
    }
    _with_decisions(at, extra)
    _open_row(at, "keep", "calcium")
    labels = [e.label for e in at.expander]
    assert "\U0001F550 When and how to take it" in labels
    assert labels.index("\U0001F550 When and how to take it") < labels.index("\U0001F3C3 Athlete targets")  # between what it adds and the athlete targets
    text = _plan_text(at)
    for rule_id in ("calcium-split-500", "calcium-carbonate-meal", "iron-calcium-pill", "calcium-vitamin-d"):
        assert combine.rule(rule_id)["takeaway"] in text, rule_id  # all that apply to this pill: the window is not the day, nothing is merged away
    assert "Source: [NIH ODS" in text  # the primary source, named
    assert "https://ods.od.nih.gov/" in text  # the source is a link in plain Streamlit (it opens in a new tab by itself)


def test_a_kept_magnesium_pill_window_has_only_the_pharmacist_line(lab):
    at = _results({})
    _with_decisions(at, {"magnesium": lab.keep("Magnesium", 56, "mg", index=4)})
    _open_row(at, "keep", "magnesium")
    assert "\U0001F550 When and how to take it" in [e.label for e in at.expander]
    text = _plan_text(at)
    assert combine.rule("medicine-spacing")["takeaway"] in text
    assert not any(combine.rule(r)["takeaway"] in text for r in ("iron-vitc", "fat-vitamins-de", "calcium-split-500", "iron-calcium-pill", "iron-tea-coffee"))


def test_a_window_with_nothing_to_pair_has_no_expander_and_no_empty_message(lab):
    at = _results({})
    _with_decisions(at, {"selenium": lab.keep("Selenium", 55, "mcg", index=6)})
    _open_row(at, "keep", "selenium")
    labels = [e.label for e in at.expander]
    assert not any("When and how to take it" in x or "What to pair it with" in x for x in labels)
    assert not any("pair" in c.value.lower() and "nothing" in c.value.lower() for c in at.caption)


def test_a_plant_iron_food_window_says_what_to_pair_it_with(lab):
    lentils = lab.pool("iron", "lentils")
    at = _results({})
    _with_decisions(at, {"iron": lab.replace("Iron", 14, "mg", lentils, index=8)})
    row = next(b for b in at.button if str(b.key).startswith("planbtn_food_") and "entil" in b.label)
    row.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "\U0001F9E9 What to pair it with" in [e.label for e in at.expander]
    text = _plan_text(at)
    assert combine.rule("iron-vitc")["takeaway"] in text and combine.rule("iron-tea-coffee")["takeaway"] in text
    assert not combine.rule("fat-carotenoids")["takeaway"] in text


def test_the_dialog_part_is_off_with_the_switch(lab):
    import os

    os.environ["SUPPSWIPE_COMBINE"] = "0"
    try:
        at = _results({})
        _with_decisions(at, {"calcium": lab.keep("Calcium", 1000, "mg", "calcium carbonate", index=8)})
        _open_row(at, "keep", "calcium")
        assert not any("When and how to take it" in e.label for e in at.expander)
    finally:
        os.environ.pop("SUPPSWIPE_COMBINE", None)


# ------------------------------------------------------------------------------------------------------------ source hygiene
def test_the_new_literals_of_the_app_are_free_of_the_banned_words_and_the_module_name():
    start = APP_SRC.index("# --- \"How to combine\" on the results page")
    end = APP_SRC.index("def _render_meals_tab(")
    literals = " ".join(re.findall(r"\"([^\"\n]{12,})\"", APP_SRC[start:end]))
    assert not FORBIDDEN.search(literals) and not HOURS.search(literals), FORBIDDEN.search(literals)
    assert "combine.py" not in APP_SRC  # tests/test_deploy_reload.py reads "combine.<name>" as an attribute of the module

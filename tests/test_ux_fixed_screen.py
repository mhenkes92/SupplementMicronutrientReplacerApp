"""The fixed, Tinder-like screens: the cards screen is the swipe card and nothing else (no page scroll), the welcome card carries its own
Scan button.

Owner's request (2026-10-09): "I don't like that u can scroll down ... place ask ai and the food drop down menu inside the card to swipe
... Since the first card doesn't have much content ... put the scan button inside the card". What was drawn under the card (the food
list with its USDA line, the Ask AI popover, the report button, the "Filter: X" caption, the footer) is gone; the card's own tools
(Swap food / Ask AI / More, three buttons inside the component, above Keep / Replace) open three sheets instead. AppTest has no
iframe: a test plays the component by writing its value under the component's key (tests/card_tools.py), which is exactly what the
browser does. What only a browser can measure (no page scroll, the buttons' size, the sheets over the real card) is in
tests/test_ux_fixed_screen_browser.py."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from card_tools import card_args, component_key, dismiss_sheet, send, sheet_titles, swap_options, swipe, tap_tool, tool_value

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "swipe_mobile_app" / "app.py")
SRC = Path(APP).read_text(encoding="utf-8")
COMPONENT = (ROOT / "swipe_mobile_app" / "swipe_component" / "index.html").read_text(encoding="utf-8")
SHEET = "swipe_sheet"
TITLES = {"swap": "Swap the food", "ask": "Ask AI", "report": "Report a problem"}
OWN_LABEL = "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%"
ONE_FOOD = {"food_description": "Beef, ground, 85% lean meat, raw", "food_category": "Beef Products", "nutrient_amount": 12.0}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


def _analyse(at: AppTest, manual: str = OWN_LABEL) -> None:
    at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "camera_barcode": "", "manual": manual}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.session_state["swipe_last_auto_signature"] = "own-label"  # what a real input records: the sample label is not saved
    at.run()


def _screen(name: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if name == "cards":
        at.button(key="appbar_scan").click().run()
        at.button(key="swipe_try_sample").click().run()
    elif name == "results":
        _analyse(at)
        for i in range(len(at.session_state["swipe_cards"])):
            swipe(at, "left", f"k{i}")
    assert not at.exception, [e.value for e in at.exception]
    return at


def _below_the_card(at: AppTest) -> list[str]:
    """What the page draws after the swipe card, as "type:key". The two helper frames (the history store of this browser and the
    scroll-to-top frame) are 0 px high and taken out of the flow by the CSS: they are not "below the card"."""
    kids = list(at.main.children.values())
    index = next(i for i, k in enumerate(kids) if getattr(k, "key", None) == "swipe_card")
    return [f"{k.type}:{getattr(k, 'key', None)}" for k in kids[index + 1:]
            if getattr(k, "key", None) != "suppswipe_history_store" and k.type != "iframe"]


def _state(**extra):
    state = {"swipe_cards": [{"component_key": "zinc"}, {"component_key": "iron"}], "swipe_index": 0}
    state.update(extra)
    return state


# ------------------------------------------------------------------ the cards screen: the card and nothing below it
def test_the_cards_screen_draws_nothing_below_the_card():
    at = _screen("cards")
    assert _below_the_card(at) == []
    assert not at.selectbox and not at.get("popover") and not at.chat_input  # no food list, no Ask AI, no chat on the page
    assert [b.key for b in at.button if not str(b.key).startswith("appbar")] == []  # not even the report button
    texts = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    assert "Prefer another food" not in texts and "Report a problem" not in texts and "USDA:" not in texts
    assert "Filter:" not in texts and "brand-foot" not in " ".join(m.value for m in at.markdown if "<div class='brand-foot'>" in m.value)
    assert [c.value for c in at.caption] == ["🧪 Sample label, not your product."]  # the one line ABOVE the card (a sample is no real scan)
    assert at.session_state[SHEET] is None and not at.get("dialog")  # and nothing is open


def test_the_results_screen_is_not_changed_by_any_of_it():
    at = _screen("results")
    assert any("<div class='brand-foot'>" in m.value for m in at.markdown)  # the © line closes the long page, as before
    assert "swipe_analyze_btn" in [b.key for b in at.button]  # and the primary Scan button
    assert not [k for k in at.main.children.values() if getattr(k, "key", None) == "swipe_card"]  # no card container (so no fixed-screen rules)
    assert not at.get("component_instance") or card_args(at) == {}
    tabs = [t.label for t in at.tabs]
    assert tabs == ["🥗 Plan", "🍽️ Meals", "🛒 Shopping", "💬 Ask AI", "📤 Share"]  # the long dashboard, with its own Ask AI tab


def test_the_card_carries_the_tools_the_sheets_need_as_props():
    at = _screen("cards")
    args = card_args(at)
    assert args["canSwap"] is True and args["canAsk"] is True  # sample card: many foods, the AI is configured (the tests' fake key)
    assert args["foodNote"] == "" and args["source"].startswith("USDA: ")  # the USDA line moved into the card (and the Swap sheet)
    assert args["food"] == "Guavas" and args["source"] == "USDA: Guavas, common, raw"


def test_a_card_without_a_second_food_offers_no_swap_and_a_forged_tap_opens_nothing():
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["swipe_cards"] = [{"component": "Zinc", "component_key": "zinc", "nutrient_key": "zinc", "dose_label": "10 mg",
                                        "dose_value": 10, "dose_unit": "mg", "form": "", "foods": [ONE_FOOD]}]
    at.session_state["swipe_index"] = 0
    at.run()
    assert not at.exception and card_args(at)["canSwap"] is False and card_args(at)["canAsk"] is True
    tap_tool(at, "swap")
    assert not at.exception and sheet_titles(at) == [] and at.session_state[SHEET] is None  # nothing to swap to: nothing opens
    tap_tool(at, "ask")
    assert sheet_titles(at) == [TITLES["ask"]]  # the other tools are unaffected


def test_no_food_at_all_says_why_inside_the_card_and_offers_neither_replace_nor_swap():
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["swipe_cards"] = [{"component": "Zzz", "component_key": "zzz unknown nutrient", "dose_label": "10 mg",
                                        "dose_value": 10, "dose_unit": "mg", "form": "", "foods": []}]
    at.session_state["swipe_index"] = 0
    at.run()
    args = card_args(at)
    assert args["food"] == "" and "No whole-food alternatives" in args["foodNote"]
    assert args["canReplace"] is False and args["canSwap"] is False
    assert not [c for c in at.caption if "No whole-food alternatives" in c.value]  # said in the card, not under it


# ------------------------------------------------------------------ the three events
@pytest.mark.parametrize("kind", ["swap", "ask", "report"])
def test_each_tool_opens_its_sheet_and_changes_nothing_about_the_card_or_a_decision(kind):
    at = _screen("cards")
    card = at.session_state["swipe_cards"][0]
    food_before = card_args(at)["food"]
    tap_tool(at, kind)
    assert not at.exception, [e.value for e in at.exception]
    assert sheet_titles(at) == [TITLES[kind]]  # exactly one dialog, the right one
    assert at.session_state[SHEET] == kind and at.session_state["swipe_sheet_card"] == [card["component_key"], 0]
    assert at.session_state["swipe_index"] == 0 and at.session_state["swipe_decisions"] == {}
    assert at.session_state.get("swipe_last_swipe_id", "") == ""  # never mistaken for a swipe
    dismiss_sheet(at)
    assert sheet_titles(at) == [] and at.session_state[SHEET] is None
    assert at.session_state["swipe_index"] == 0 and card_args(at)["food"] == food_before  # the same card, the same food
    # The same value arriving again (every rerun re-reads it) does not reopen a sheet that was dismissed.
    at.run()
    assert sheet_titles(at) == []


def test_a_sheet_of_the_card_returns_to_the_same_card_after_a_swipe_and_a_back():
    at = _screen("cards")
    tap_tool(at, "report")
    dismiss_sheet(at)
    swipe(at, "left", "s1")
    assert at.session_state["swipe_index"] == 1
    tap_tool(at, "ask")
    assert at.session_state["swipe_sheet_card"] == [at.session_state["swipe_cards"][1]["component_key"], 1]  # the card on screen now
    dismiss_sheet(at)
    swipe(at, "back", "b1")
    assert at.session_state["swipe_index"] == 0


@pytest.mark.parametrize("kind", ["swap", "ask", "report"])
@pytest.mark.parametrize("override", [
    {"card": "some other card"},        # made on another card (stale)
    {"index": 5},                        # stale index
    {"card": None, "index": 1},          # damaged
    {"id": ""},                          # no id
])
def test_a_stale_or_damaged_tool_event_does_nothing(kind, override):
    at = _screen("cards")
    send(at, tool_value(at, kind, **override))
    assert not at.exception and sheet_titles(at) == [] and at.session_state.get(SHEET) is None
    assert at.session_state["swipe_index"] == 0 and at.session_state["swipe_decisions"] == {}


def test_a_duplicate_tool_event_opens_once_and_another_card_ignores_an_event_of_the_first():
    at = _screen("cards")
    value = tool_value(at, "swap")
    send(at, value)
    assert sheet_titles(at) == [TITLES["swap"]]
    dismiss_sheet(at)
    send(at, value)  # the very same event again (a rerun re-reads the component value)
    assert sheet_titles(at) == [] and at.session_state[SHEET] is None
    swipe(at, "left", "s1")
    assert at.session_state["swipe_index"] == 1
    send(at, dict(value, id="swap-late"))  # an event of card 1 that arrives while card 2 is on screen
    assert sheet_titles(at) == [] and at.session_state[SHEET] is None


def test_a_tool_event_on_the_results_screen_is_ignored():
    at = _screen("results")
    at.session_state[component_key(at)] = {"kind": "swap", "id": "late", "card": "zinc", "index": 2}
    at.run()
    assert not at.exception and sheet_titles(at) == [] and at.session_state.get(SHEET) is None


def test_a_tool_value_never_reaches_the_swipe_handler_and_is_ignored_while_an_analysis_runs(sw, monkeypatch):
    seen = []
    monkeypatch.setattr(sw, "_apply_card_swipe", lambda state, value: seen.append(value))
    state = _state()
    for kind in ("swap", "ask", "report"):
        assert sw._apply_card_tool_tap(state, {"kind": kind, "id": f"{kind}1", "card": "zinc", "index": 0}) is True
        assert state[SHEET] == kind and state["swipe_sheet_card"] == ["zinc", 0]
    assert sw._apply_card_tool_tap(state, {"dir": "left", "id": "s1", "card": "zinc", "index": 0}) is False  # a swipe is the swipe handler's
    assert sw._apply_card_tool_tap(state, {"kind": "guide", "id": "g1", "card": "zinc", "index": 0}) is False  # and a guide tap the guide's
    assert sw._apply_card_tool_tap(state, None) is False and sw._apply_card_tool_tap(state, "swap") is False and seen == []
    assert "swipe_decisions" not in state and "swipe_last_swipe_id" not in state and state["swipe_index"] == 0
    # In flight: consumed (it cannot come back later) but it opens nothing.
    busy = _state(swipe_is_analyzing=True, swipe_pending_request={"manual": "x"}, swipe_sheet=None)
    assert sw._apply_card_tool_tap(busy, {"kind": "swap", "id": "b1", "card": "zinc", "index": 0}) is True
    assert busy[SHEET] is None and "swipe_sheet_card" not in busy and busy["swipe_last_tool_id"] == "b1"
    # The tool event is also the older dialogs' loser: it clears their requests, like a bar tap does.
    over = _state(swipe_confirm_restart=True, swipe_open_analyze=True, swipe_plan_item={"kind": "keep"}, swipe_guide_focus="zinc")
    assert sw._apply_card_tool_tap(over, {"kind": "ask", "id": "o1", "card": "zinc", "index": 0}) is True
    assert over["swipe_confirm_restart"] is False and over["swipe_open_analyze"] is False and over["swipe_plan_item"] is None
    assert "swipe_guide_focus" not in over and over[SHEET] == "ask"


def test_closing_a_sheet_forgets_which_card_it_was_for(sw, monkeypatch):
    state = {SHEET: "swap", "swipe_sheet_card": ["zinc", 0], "swipe_guide_focus": "zinc", "swipe_index": 0}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._close_sheet()  # the on_dismiss of every sheet (X, Esc, a tap outside)
    assert state[SHEET] is None and "swipe_sheet_card" not in state and "swipe_guide_focus" not in state and state["swipe_index"] == 0


def test_a_swipe_still_works_after_a_tool_event():
    at = _screen("cards")
    first = at.session_state["swipe_cards"][0]["component_key"]
    tap_tool(at, "swap")
    dismiss_sheet(at)
    swipe(at, "right", "s1")
    assert at.session_state["swipe_index"] == 1 and at.session_state["swipe_decisions"][first]["decision"] == "replace"


def test_a_sheet_flag_for_another_card_or_a_missing_tool_opens_nothing(sw):
    """A flag left behind (or forged) must not open a sheet for a card that is not on screen."""
    at = _screen("cards")
    at.session_state[SHEET] = "swap"
    at.session_state["swipe_sheet_card"] = ["not this card", 0]
    at.run()
    assert not at.exception and sheet_titles(at) == [] and at.session_state[SHEET] is None
    at.session_state[SHEET] = "report"  # no card recorded at all
    at.session_state["swipe_sheet_card"] = None
    at.run()
    assert sheet_titles(at) == [] and at.session_state[SHEET] is None


# ------------------------------------------------------------------ the Swap food sheet is the old dropdown
def test_the_swap_sheet_holds_the_food_list_with_the_same_options_and_the_source_line():
    at = _screen("cards")
    labels = list(at.session_state["swipe_card_view"]["labels"])
    tap_tool(at, "swap")
    [select] = at.selectbox
    assert select.label == "Prefer another food?" and list(select.options) == labels and len(labels) > 1
    assert select.value == at.session_state["swipe_card_view"]["shown"] == "Guavas (228.3 mg/100g)"  # the everyday default, not the richest food (acerola)
    assert select.key == f"swipe_food_select_{at.session_state['swipe_cards'][0]['component_key']}_0"
    assert "USDA: Guavas, common, raw" in [c.value for c in at.caption]  # the source line, as it was under the card
    assert [b.label for b in at.button if b.key == "swipe_swap_done"] == ["Done"]


def test_a_food_picked_in_the_sheet_reaches_the_card_the_decision_the_plan_and_the_saved_scan(sw):
    at = _screen("cards")
    key0 = at.session_state["swipe_cards"][0]["component_key"]
    tap_tool(at, "swap")
    second = at.selectbox[0].options[1]
    at.selectbox[0].set_value(second).run()
    assert not at.exception and at.session_state[f"swipe_food_pick_{key0}_0"] == second
    assert sheet_titles(at) == [TITLES["swap"]]  # the sheet stays open while the visitor looks at the food
    assert "USDA: Peppers, hot chili, green, raw" in [c.value for c in at.caption]  # the caption follows the pick
    at.button(key="swipe_swap_done").click().run()  # Done: the sheet closes and the page behind follows
    assert sheet_titles(at) == [] and at.session_state[SHEET] is None
    args = card_args(at)
    assert args["food"] == "Green chili pepper" and args["source"] == "USDA: Peppers, hot chili, green, raw"
    assert args["matchDose"] != "~35 g"  # the portion was worked out for the picked food
    picked = dict(at.session_state["swipe_card_view"]["options"])[second]
    swipe(at, "right", "r1")  # replace: the decision records the picked food, exactly as the dropdown's value did
    decision = at.session_state["swipe_decisions"][key0]
    assert decision["decision"] == "replace" and decision["selected_food"]["food_description"] == picked["food_description"]
    names = ("swipe_cards", "swipe_analysis_text", "swipe_decisions", "swipe_diet_profile_id", "swipe_pregnant", "swipe_index", "swipe_label_source")
    state = {k: at.session_state[k] for k in names}
    state["swipe_label_source"] = {"kind": "text", "url": ""}  # the sample label is never saved: the visitor's own label is
    snapshot = sw._scan_snapshot(state, now=1.0)  # and the saved scan (Resume) remembers it
    assert snapshot["decisions"][key0] == {"decision": "replace", "food_description": picked["food_description"]}
    for i in range(1, len(at.session_state["swipe_cards"])):
        swipe(at, "left", f"k{i}")
    rows = at.session_state["swipe_decisions"]
    assert at.session_state["swipe_index"] == len(at.session_state["swipe_cards"])
    assert "Green chili pepper" in " ".join(m.value for m in at.markdown)  # the plan on the results shows the picked food


def test_dismissing_the_swap_sheet_without_done_applies_the_pick_too():
    """X / Esc / a tap outside rerun the app (on_dismiss): the card follows, like the Diet sheet."""
    at = _screen("cards")
    tap_tool(at, "swap")
    second = next(o for o in at.selectbox[0].options if o != at.selectbox[0].value)
    at.selectbox[0].set_value(second).run()
    dismiss_sheet(at)
    assert card_args(at)["food"] != "Guavas" and at.session_state["swipe_card_view"]["shown"] == second


def test_the_default_food_is_unchanged_until_the_visitor_picks_one(sw):
    at = _screen("cards")
    card = at.session_state["swipe_cards"][0]
    foods = sw._with_fortified_options(sw._card_food_options(card["foods"], None), card, None)
    labels = [sw._food_label(f) for f in foods]
    assert at.session_state["swipe_card_view"]["shown"] == labels[sw._default_food_index(foods, card, None)]
    assert f"swipe_food_pick_{card['component_key']}_0" not in at.session_state  # nothing is stored for a default


def test_a_pick_that_the_filter_removed_falls_back_to_the_default_food():
    at = _screen("cards")
    key0 = at.session_state["swipe_cards"][0]["component_key"]
    at.session_state[f"swipe_food_pick_{key0}_0"] = "A food that is not on this card (1 mg/100g)"
    at.run()
    assert not at.exception and card_args(at)["food"] == "Guavas"


def test_swap_options_helper_matches_the_card():
    at = _screen("cards")
    assert swap_options(at) == list(at.session_state["swipe_card_view"]["labels"])


# ------------------------------------------------------------------ the Ask AI sheet is the old popover's chat
def test_the_ask_sheet_has_the_chat_the_suggestions_and_the_sources_label(monkeypatch):
    import blockbrain.app as bb

    seen = []

    def fake_text(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        seen.append({"user": user, "history": history})
        return "Vitamin C is water-soluble. Sheet answer 1"

    monkeypatch.setattr(bb, "call_blockbrain_text", fake_text)
    at = _screen("cards")
    key0 = at.session_state["swipe_cards"][0]["component_key"]
    tap_tool(at, "ask")
    assert sheet_titles(at) == [TITLES["ask"]] and len(at.chat_input) == 1
    assert at.chat_input[0].max_chars == 500 and at.chat_input[0].key == f"swipe_rag_chat_input_{key0}_0"
    pills = [g for g in at.get("button_group") if g.key == f"swipe_rag_suggest_{key0}_0"]
    assert len(pills) == 1 and list(pills[0].options)[0] == "Is 80 mg a safe daily dose?"  # the card's own suggestions
    at.chat_input[0].set_value("Sheet question one?").run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(seen) == 1 and "Question: Sheet question one?" in seen[0]["user"] and "Dose in the user's supplement: 80 mg" in seen[0]["user"]
    chat = at.session_state["swipe_rag_chats"][key0]
    assert [m["role"] for m in chat] == ["user", "assistant"] and chat[0]["content"] == "Sheet question one?"
    assert chat[1]["content"].endswith("_🤖 General AI answer (not medical advice)_")  # the sources label, as before
    assert sheet_titles(at) == [TITLES["ask"]]  # the sheet is still open, showing the answer
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    # A follow-up carries the history (the model's memory) and is a second question of the session's allowance.
    at.chat_input[0].set_value("And a second one?").run()
    assert len(seen) == 2 and [t["role"] for t in seen[1]["history"]] == ["user", "assistant"]
    # The chat is the card's: it is still there when the sheet is opened again, and Clear chat empties it.
    dismiss_sheet(at)
    tap_tool(at, "ask")
    assert len([m for m in at.chat_message if m.name == "user"]) == 2
    next(b for b in at.button if b.label == "Clear chat").click().run()
    assert at.session_state["swipe_rag_chats"][key0] == [] and sheet_titles(at) == [TITLES["ask"]]


def test_the_ask_sheet_keeps_the_sessions_quota_and_never_asks_the_model_past_it(monkeypatch):
    import time

    import blockbrain.app as bb

    monkeypatch.setattr(bb, "call_blockbrain_text", lambda *a, **k: pytest.fail("the allowance is used up: no model call"))
    monkeypatch.setattr(bb, "build_rag_index", lambda: ([], "none"))  # and no local index to fall back on
    at = _screen("cards")
    now = time.time()
    at.session_state["_suppswipe_llm_usage"] = {"generate": [now - 10 + i * 0.001 for i in range(100)]}  # far more than the hourly allowance
    tap_tool(at, "ask")
    at.chat_input[0].set_value("A question after the allowance?").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("unavailable" in e.value for e in at.error)  # the same message the popover gave
    assert not at.session_state["swipe_rag_chats"].get(at.session_state["swipe_cards"][0]["component_key"])  # nothing stored as an answer
    assert not [m for m in at.chat_message if m.name == "assistant"]  # and no empty avatar


def test_the_ask_sheet_stays_a_sheet_with_the_ai_switched_off_between_the_tap_and_the_run(monkeypatch):
    at = _screen("cards")
    tap_tool(at, "ask")
    assert sheet_titles(at) == [TITLES["ask"]]
    monkeypatch.setenv("BLOCKBRAIN_ORG_ID", "")
    monkeypatch.setenv("BLOCKBRAIN_MODEL", "")
    monkeypatch.setenv("BLOCKBRAIN_BOT_ID", "")
    at.run()  # a full run with the AI gone: the flag is not honoured, no chat box that can only end in an error
    assert not at.exception and sheet_titles(at) == [] and not at.chat_input and at.session_state[SHEET] is None


# ------------------------------------------------------------------ the More sheet: the report
def test_the_report_sheet_logs_one_line_for_the_card_with_the_food_on_it_and_closes(caplog):
    import logging

    at = _screen("cards")
    key0 = at.session_state["swipe_cards"][0]["component_key"]
    tap_tool(at, "swap")
    at.selectbox[0].set_value(at.selectbox[0].options[1]).run()
    dismiss_sheet(at)  # the card now shows another food: the report is about THAT food
    tap_tool(at, "report")
    assert sheet_titles(at) == [TITLES["report"]]
    button = at.button(key=f"swipe_report_{key0}_0_{at.session_state['swipe_reset_nonce']}")
    assert button.label == "🚩 Report a problem with this card"
    assert any("No text from you, nothing personal" in c.value for c in at.caption)  # what is logged, said before the tap
    with caplog.at_level(logging.WARNING, logger="blockbrain.app"):
        button.click().run()
    lines = [r.getMessage() for r in caplog.records if "SuppSwipe card report" in r.getMessage()]
    assert len(lines) == 1 and '"nutrient": "Vitamin C"' in lines[0] and "Peppers, hot chili, green, raw" in lines[0]
    assert [t.value for t in at.toast] == ["Thanks — logged for review"]
    assert sheet_titles(at) == [] and at.session_state[SHEET] is None and at.session_state["swipe_index"] == 0


# ------------------------------------------------------------------ the welcome card has its own Scan button
def test_the_welcome_card_scan_button_opens_the_same_sheet_as_the_bars_scan_item():
    at = AppTest.from_file(APP, default_timeout=60).run()
    button = at.button(key="hero_scan")
    assert button.label == "Scan a supplement" and button.proto.type == "primary"
    button.click().run()
    assert not at.exception and sheet_titles(at) == ["Scan a supplement"]
    assert [b.key for b in at.button if b.key in ("swipe_scan_analyze", "swipe_resume_scan", "swipe_try_sample")] == ["swipe_scan_analyze", "swipe_try_sample"]
    hero = AppTest.from_file(APP, default_timeout=60).run()
    hero.button(key="appbar_scan").click().run()
    assert sheet_titles(hero) == sheet_titles(at)  # and the same three options, whichever way in
    assert at.session_state[SHEET] == hero.session_state[SHEET] == "scan"


def test_the_hero_button_does_nothing_while_an_analysis_runs(sw, monkeypatch):
    state = {"swipe_is_analyzing": True, "swipe_pending_request": {"manual": "x"}}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._tap_scan()
    assert SHEET not in state  # the same callback as the bar's: dead while the OCR runs


def test_the_welcome_card_keeps_its_text_and_loses_only_the_hint_line():
    at = AppTest.from_file(APP, default_timeout=60).run()
    text = " ".join(m.value for m in at.markdown)
    for needle in ("Ditch the pill.", "Eat the real thing.", "Scan your supplement and see which nutrients", "your label", "keep or replace", "your food plan"):
        assert needle in text
    assert "hero-hint" not in text and "Tap <b>Scan</b>" not in text
    assert any("not medical advice" in c.value for c in at.caption)  # the disclaimer stays below the card


# ------------------------------------------------------------------ the component and the page (static)
def test_the_component_has_the_three_tools_above_keep_and_replace_and_posts_one_kind_each():
    assert re.search(r'<div id="extras">\s*<button type="button" id="btnSwap"[^>]*hidden>Swap food</button>\s*'
                     r'<button type="button" id="btnAsk"[^>]*hidden>Ask AI</button>\s*'
                     r'<button type="button" id="btnMore"[^>]*>More</button>\s*</div>\s*<div id="actions">', COMPONENT)
    for kind, button in (("swap", "btnSwap"), ("ask", "btnAsk"), ("report", "btnMore")):
        assert f'{button}.addEventListener("click", function () {{ toolEvent("{kind}"); }});' in COMPONENT
    assert 'setValue({ kind: kind, id: kind + "-" + now + "-" + seq, card: cardId, index: cardIndex });' in COMPONENT
    body = COMPONENT[COMPONENT.index("function toolEvent(kind)"):COMPONENT.index('btnSwap.addEventListener("click"')]
    assert "if (committed) return;" in body and "toolUntil" in body  # nothing while a swipe is on its way; a double tap sends one event
    assert "btnSwap.hidden = !args.canSwap;" in COMPONENT and "btnAsk.hidden = !args.canAsk;" in COMPONENT
    css = COMPONENT[COMPONENT.index("#extras button {"):COMPONENT.index("#extras button[hidden]")]
    assert "min-height: 44px" in css and "white-space: nowrap" in css  # finger-sized, the label on one line
    assert 'aria-label="Swap food: choose another whole food"' in COMPONENT and 'aria-label="More options: report a problem with this card"' in COMPONENT


def test_the_frame_looks_at_its_room_again_when_nothing_tells_it_that_it_changed():
    """A filter chip appears above the card when the Diet sheet closes; Streamlit sends no new props for an unchanged card, and the
    window did not resize: without this look the row would end under the bar (measured: 28 px at 320x640 with the longest chip)."""
    poll = COMPONENT[COMPONENT.index("setInterval(function () {"):COMPONENT.index("function setValue(v)")]
    assert "var room = hostRoom();" in poll and "if (room !== lastRoom) { fitHeight(); }" in poll
    assert "if (committed || document.hidden || lastRoom === null) { return; }" in poll  # not during a swipe, not in a hidden tab
    assert "lastRoom = room;" in COMPONENT[COMPONENT.index("function fitHeight()"):COMPONENT.index("var measured = isFinite(room);")]
    assert COMPONENT.count("setInterval(") == 1


def test_a_resumed_scan_starts_from_the_density_a_new_scan_has_on_this_phone():
    """The frame now fills the room, so a later card would fit at normal density and a resumed scan would look different from the
    one it was started as (and keep its swipe hint on every card): its first draw takes the same starting density as card 1."""
    assert "var firstDraw = !drawnBefore;" in COMPONENT
    assert "else if (firstDraw) { tight = hostRoom() < TIGHT_BELOW; }" in COMPONENT


def test_the_component_says_why_there_is_no_food_and_keeps_the_source_line_only_where_it_fits():
    assert "esc(args.foodNote || \"No whole-food match for this one.\")" in COMPONENT and "Pick a food below" not in COMPONENT
    assert "#wrap.nosrc .src { display: none; }" in COMPONENT and "showSrc = false; paint();" in COMPONENT
    assert "args.foodNote" in COMPONENT[COMPONENT.index("card.setAttribute(\"aria-label\""):COMPONENT.index("btnRepl.disabled")]  # a screen reader hears it too


def test_the_page_has_no_bottom_padding_on_the_cards_screen_and_the_frame_fills_the_room(sw, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(sw.st, "markdown", lambda body, **_kw: seen.append(str(body)))
    sw._render_header()
    css = seen[0]
    assert re.search(r'\.block-container:has\(\[class~="st-key-swipe_card"\]\) \{\s*padding-bottom: 0;\s*\}', css)
    assert 'iframe[src*="tinder_swipe"] { display: block; }' in css  # an inline frame leaves a strip under itself
    assert 'st-key-suppswipe_history_store' in css and '[height="0px"]:has(> iframe[data-testid="stIFrame"])' in css  # the helper frames take no room
    assert "border: 1px solid #e2e8f0" in css[css.index('[class~="st-key-hero_card"] {'):css.index('[class~="st-key-hero_card"] [data-testid')]  # the card is its container
    assert ".hero-hint" not in css
    assert 'if (!lastHeight) {\n      fitHeight();' in COMPONENT  # the first draw of a frame asks for its final height at once
    assert re.search(r"var FILL_MAX = 900;", COMPONENT) and "Math.min(Math.max(room, ROOM_FLOOR), FILL_MAX)" in COMPONENT
    assert "#wrap.spacer { padding-bottom: 76px; }" in COMPONENT and "var SPACER = 76;" in COMPONENT  # the page's padding became the frame's strip
    # A bigger bar (large text: 108 px at 200 %) needs a longer strip, or the page cannot scroll the row clear of it: measured, never shorter.
    assert "footprint = Math.max(0, Math.ceil(pw.innerHeight - barTop));" in COMPONENT[COMPONENT.index("function hostRoom()"):COMPONENT.index("function paint()")]
    assert "spacerPx = spacer ? Math.max(SPACER, footprint + ROW_GAP + 4) : SPACER;" in COMPONENT
    assert 'wrap.style.paddingBottom = (spacer && spacerPx > SPACER) ? spacerPx + "px" : "";' in COMPONENT


def test_the_removed_controls_are_gone_from_the_card_path_of_the_source():
    body = SRC[SRC.index("def _render_card() -> None:"):SRC.index("# --- Results dashboard ----")]
    for gone in ("st.selectbox", "st.popover", "st.caption(f\"Filter", "st.caption(f\"USDA", "Report a problem with this card", "_render_rag_chat_popup"):
        assert gone not in body, gone
    assert "_render_rag_chat_popup" not in SRC
    assert SRC.count("st.dialog(") >= 8 and all(f"def _{name}_sheet()" in SRC for name in TITLES if name != "ask") and "def _ask_sheet()" in SRC

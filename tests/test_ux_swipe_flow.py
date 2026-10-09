"""Swipe handling: one script run per swipe, stale-value dedupe, edit mode from
the results screen, and the earlier food staying selected on a reopened card."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")

SALMON = {"food_description": "Fish, salmon, Atlantic, wild, raw", "amount_per_100g": 3.2, "unit": "UG"}
CLAMS = {"food_description": "Mollusks, clam, mixed species, raw", "amount_per_100g": 11.3, "unit": "UG"}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # The results screen prefetches a meal plan in the background; keep it off.
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


def _cards():
    return [
        {"component": "Vitamin C", "component_key": "vitamin c", "dose_label": "80 mg", "dose_value": 80, "dose_unit": "mg", "foods": []},
        {"component": "Vitamin B12", "component_key": "vitamin b12", "dose_label": "2.5 mcg", "dose_value": 2.5, "dose_unit": "mcg", "foods": [CLAMS, SALMON]},
        {"component": "Zinc", "component_key": "zinc", "dose_label": "10 mg", "dose_value": 10, "dose_unit": "mg", "foods": []},
    ]


def _state(index=0, **extra):
    state = {"swipe_cards": _cards(), "swipe_index": index, "swipe_decisions": {}}
    state.update(extra)
    return state


def _view(index, key, options, selected, select_key="sel"):
    return {"index": index, "component_key": key, "select_key": select_key, "options": options, "selected": selected}


def test_component_key_is_stable_per_scan(sw):
    # One key per reset nonce: the same iframe is reused for every card.
    assert sw._swipe_component_key(0) == sw._swipe_component_key("0") == "tinder_0"
    assert sw._swipe_component_key(3) == "tinder_3"


def test_keep_advances_and_records_once(sw):
    state = _state(0)
    value = {"dir": "left", "id": "1-1", "card": "vitamin c", "index": 0}
    assert sw._apply_card_swipe(state, value) is True
    assert state["swipe_index"] == 1
    assert state["swipe_decisions"]["vitamin c"]["decision"] == "keep"
    # The value stays in session state on later runs: it must not apply again.
    assert sw._apply_card_swipe(state, value) is False
    assert state["swipe_index"] == 1


def test_replace_records_the_food_selected_in_the_dropdown(sw):
    state = _state(1)
    state["swipe_card_view"] = _view(1, "vitamin b12", {"Clams": CLAMS, "Salmon": SALMON}, CLAMS)
    state["sel"] = "Salmon"  # the user changed the dropdown before swiping
    assert sw._apply_card_swipe(state, {"dir": "right", "id": "2", "card": "vitamin b12", "index": 1})
    assert state["swipe_decisions"]["vitamin b12"]["selected_food"] is SALMON
    assert state["swipe_decisions"]["vitamin b12"]["card_index"] == 1
    assert state["swipe_index"] == 2


def test_replace_falls_back_to_the_food_the_card_showed(sw):
    state = _state(1)
    state["swipe_card_view"] = _view(1, "vitamin b12", {"Clams": CLAMS}, CLAMS)
    assert sw._apply_card_swipe(state, {"dir": "right", "id": "3", "card": "vitamin b12", "index": 1})
    assert state["swipe_decisions"]["vitamin b12"]["selected_food"] is CLAMS


def test_replace_without_a_food_is_ignored(sw):
    state = _state(0)
    state["swipe_card_view"] = _view(0, "vitamin c", {}, None)
    assert sw._apply_card_swipe(state, {"dir": "right", "id": "4", "card": "vitamin c", "index": 0}) is False
    assert state["swipe_index"] == 0 and not state["swipe_decisions"]
    # ... but it is marked handled, so the card is re-sent (ack) and resets.
    assert state["swipe_last_swipe_id"] == "4"


def test_value_from_another_card_is_ignored(sw):
    state = _state(2)
    assert sw._apply_card_swipe(state, {"dir": "left", "id": "5", "card": "vitamin c", "index": 0}) is False
    assert state["swipe_index"] == 2 and not state["swipe_decisions"]


@pytest.mark.parametrize("value", [None, "left", {}, {"dir": "left"}, {"dir": "up", "id": "x"}])
def test_junk_values_do_nothing(sw, value):
    state = _state(0)
    assert sw._apply_card_swipe(state, value) is False
    assert state["swipe_index"] == 0


def test_back_goes_to_previous_card_but_not_before_the_first(sw):
    state = _state(2)
    assert sw._apply_card_swipe(state, {"dir": "back", "id": "6", "card": "zinc", "index": 2})
    assert state["swipe_index"] == 1
    state = _state(0)
    assert sw._apply_card_swipe(state, {"dir": "back", "id": "7", "card": "vitamin c", "index": 0}) is False
    assert state["swipe_index"] == 0


def test_edit_mode_returns_straight_to_results(sw):
    earlier = {"zinc": {"decision": "keep", "component_key": "zinc"}}
    state = _state(0, swipe_edit_return=True, swipe_decisions=dict(earlier))
    assert sw._apply_card_swipe(state, {"dir": "left", "id": "8", "card": "vitamin c", "index": 0})
    assert state["swipe_index"] == len(state["swipe_cards"])  # the results screen
    assert state["swipe_edit_return"] is False
    assert state["swipe_decisions"]["zinc"] == earlier["zinc"]  # later cards untouched


def test_edit_mode_back_returns_to_results_unchanged(sw):
    state = _state(0, swipe_edit_return=True)
    assert sw._apply_card_swipe(state, {"dir": "back", "id": "9", "card": "vitamin c", "index": 0})
    assert state["swipe_index"] == 3 and not state["swipe_decisions"]
    assert state["swipe_edit_return"] is False


def test_restore_previous_food_matches_by_description(sw):
    state: dict = {}
    decision = {"decision": "replace", "selected_food": dict(SALMON)}
    sw._restore_previous_food(state, "sel", ["Clams (11 µg/100g)", "Salmon (3 µg/100g)"], [CLAMS, SALMON], decision)
    assert state["sel"] == "Salmon (3 µg/100g)"


def test_restore_previous_food_never_overrides_a_live_dropdown(sw):
    state = {"sel": "Clams (11 µg/100g)"}
    decision = {"decision": "replace", "selected_food": SALMON}
    sw._restore_previous_food(state, "sel", ["Clams (11 µg/100g)", "Salmon (3 µg/100g)"], [CLAMS, SALMON], decision)
    assert state["sel"] == "Clams (11 µg/100g)"


def test_restore_previous_food_ignores_unknown_or_missing(sw):
    state: dict = {}
    sw._restore_previous_food(state, "sel", ["Clams"], [CLAMS], {"decision": "replace", "selected_food": SALMON})
    sw._restore_previous_food(state, "sel", ["Clams"], [CLAMS], None)
    assert "sel" not in state


# --- End to end through the real script (AppTest) ------------------------------


def _sample_app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="appbar_scan").click().run()  # the sample button lives in the Scan sheet
    at.button(key="swipe_try_sample").click().run()
    assert not at.exception
    assert at.session_state["swipe_cards"]
    return at


def _swipe(at: AppTest, direction: str, swipe_id: str) -> AppTest:
    cards = at.session_state["swipe_cards"]
    index = at.session_state["swipe_index"]
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    at.session_state[key] = {"dir": direction, "id": swipe_id, "card": cards[index]["component_key"], "index": index}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_one_run_per_swipe_shows_the_next_card():
    at = _sample_app()
    _swipe(at, "left", "s1")
    assert at.session_state["swipe_index"] == 1
    # A later run with the same (stale) value does not swipe again.
    at.run()
    assert at.session_state["swipe_index"] == 1
    _swipe(at, "right", "s2")
    assert at.session_state["swipe_index"] == 2
    assert at.session_state["swipe_decisions"][at.session_state["swipe_cards"][1]["component_key"]]["decision"] == "replace"


def test_no_progress_dots_on_the_card():
    at = _sample_app()
    html = " ".join(m.value for m in at.markdown)
    assert "class='swipe-progress'" not in html and "class='swipe-dot" not in html


def test_results_tap_opens_card_in_edit_mode_and_returns():
    at = _sample_app()
    n = len(at.session_state["swipe_cards"])
    for i in range(n):
        _swipe(at, "left", f"k{i}")
    assert at.session_state["swipe_index"] == n
    first_key = at.session_state["swipe_cards"][0]["component_key"]
    at.button(key="planbtn_keep_0").click().run()  # the first kept pill's row opens its options window ...
    at.button(key="plandlg_change_0").click().run()  # ... and "Change a choice" reopens its card
    assert at.session_state["swipe_index"] == 0
    assert at.session_state["swipe_edit_return"] is True
    _swipe(at, "right", "edit1")
    assert at.session_state["swipe_index"] == n  # straight back to the results
    assert at.session_state["swipe_decisions"][first_key]["decision"] == "replace"
    assert all(at.session_state["swipe_decisions"][c["component_key"]]["decision"] == "keep"
               for c in at.session_state["swipe_cards"][1:])


def test_reopened_card_keeps_the_earlier_food():
    at = _sample_app()
    cards = at.session_state["swipe_cards"]
    key0 = cards[0]["component_key"]
    select = at.selectbox(key=f"swipe_food_select_{key0}_0")
    second = select.options[1]
    select.set_value(second).run()
    _swipe(at, "right", "r1")
    assert at.session_state["swipe_index"] == 1
    _swipe(at, "back", "b1")
    assert at.session_state["swipe_index"] == 0
    assert at.selectbox(key=f"swipe_food_select_{key0}_0").value == second

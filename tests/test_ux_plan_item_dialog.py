"""The plan's food and kept-pill rows open an options window (dialog): change the choice, what the
whole food adds (AI), the Athlete RDA guide. AppTest reruns the whole script on a click inside a dialog,
so what only a browser can prove (the page behind is not rerun, X / Esc / outside tap dismiss, focus
returns to the row) is in tests/test_ux_browser.py."""
from __future__ import annotations

import html
import threading
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import blockbrain.app as bb
import llm_cache

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")  # no background meal plan: it would use quota and the fake
    llm_cache.clear()
    yield
    llm_cache.clear()


def _results_app(replace_every: int = 2) -> AppTest:
    """The sample label, decided card by card: every `replace_every`-th card replaced, the others kept."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="swipe_try_sample").click().run()
    cards = at.session_state["swipe_cards"]
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    for i, card in enumerate(cards):
        direction = "right" if i % replace_every == 0 and card.get("foods") else "left"
        at.session_state[key] = {"dir": direction, "id": f"t{i}", "card": card["component_key"], "index": i}
        at.run()
    assert at.session_state["swipe_index"] == len(cards)
    assert not at.exception, [e.value for e in at.exception]
    return at


def _dialogs(at: AppTest) -> list[str]:
    return [d.proto.dialog.title for d in at.get("dialog")]


def _row_buttons(at: AppTest, kind: str):
    return [b for b in at.button if str(b.key).startswith(f"planbtn_{kind}_")]


def _text(at: AppTest) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info])


def _await_jobs(timeout: float = 10.0) -> None:
    """Wait for the background generations to land. The dialog never waits for them (a click is answered at once): the
    browser's polling fragment draws the result, AppTest needs one more run."""
    for future in list(llm_cache._inflight.values()):
        future.result(timeout=timeout)


def _fake_text(calls: list[str], answer: str = "**Zinc → Oysters**\n- 💊 Pill alone: one line\n- 🥗 Whole food also gives: more"):
    def fake(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        calls.append(user)
        return answer

    return fake


# ---------------------------------------------------------------- the rows
def test_every_food_and_kept_pill_is_a_tappable_row_and_nothing_is_open():
    at = _results_app()
    decisions = at.session_state["swipe_decisions"].values()
    foods = {bb.normalize_lookup_key(bb.food_display_name(d["selected_food"]["food_description"])) for d in decisions if d["decision"] == "replace"}
    assert len(_row_buttons(at, "food")) == len(foods) > 0
    assert len(_row_buttons(at, "keep")) == sum(1 for d in decisions if d["decision"] == "keep") > 0
    assert _dialogs(at) == []
    # The three old bottom controls are gone from the page: the options live in the dialog.
    keys = {b.key for b in at.button}
    assert not any(str(k).startswith(("final_repl_", "final_keep_")) for k in keys) and "swipe_gen_benefits" not in keys
    # One hint, once.
    assert [c.value for c in at.caption].count("Tap a food or a supplement for options.") == 1


def test_row_buttons_have_a_name_a_screen_reader_can_use():
    at = _results_app()
    assert any("class='plan-row' aria-hidden='true'" in m.value for m in at.markdown)  # the button carries the name
    for b in _row_buttons(at, "food") + _row_buttons(at, "keep"):
        assert b.label.endswith("Opens options.")
    assert any("kept as a supplement" in b.label for b in _row_buttons(at, "keep"))


def test_only_the_athlete_guide_is_left_at_the_bottom():
    at = _results_app()
    popovers = [p.proto.popover.label for p in at.get("popover")]
    assert "\U0001F3C3 Athlete RDA guide" in popovers and "✎ Change a choice" not in popovers


# ---------------------------------------------------------------- the food dialog
def test_a_food_row_opens_its_dialog_with_the_three_options():
    at = _results_app()
    row = _row_buttons(at, "food")[0]
    row.click().run()
    assert not at.exception, [e.value for e in at.exception]
    request = at.session_state["swipe_plan_item"]
    assert request["kind"] == "food"
    food = at.session_state["swipe_decisions"]
    titles = _dialogs(at)
    assert len(titles) == 1 and titles[0]
    assert "✎ Change a choice" in _text(at)  # a heading; the other two options are expanders, as the owner drew them
    labels = [e.label for e in at.expander]
    assert "🌱 What the whole food adds (AI)" in labels and "🏃 Athlete RDA guide" in labels
    keys = [b.key for b in at.button]
    assert "plandlg_change_0" in keys and "plandlg_benefits" in keys and "plandlg_close" in keys
    assert any("athlete target" in m.value and "adult RDA" in m.value for m in at.markdown)


def test_the_full_table_is_in_the_dialog_and_has_every_nutrient(sw):
    at = _results_app()
    _row_buttons(at, "food")[0].click().run()
    assert len(at.table) >= 1
    assert max(len(t.value) for t in at.table) == len(sw._MICRONUTRIENT_RDA)


def test_a_kept_pill_dialog_has_no_comparison():
    at = _results_app()
    _row_buttons(at, "keep")[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(_dialogs(at)) == 1
    keys = [b.key for b in at.button]
    assert "plandlg_change_0" in keys and "plandlg_benefits" not in keys
    text = _text(at)
    assert "kept as a supplement" in text and "✎ Change a choice" in text
    labels = [e.label for e in at.expander]
    assert labels == ["🏃 Athlete RDA guide"]  # no food was chosen, so no comparison


def test_one_food_for_two_nutrients_is_one_row_with_a_change_button_per_nutrient():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="swipe_try_sample").click().run()
    cards = at.session_state["swipe_cards"]
    food = next(c["foods"][0] for c in cards if c.get("foods"))
    decisions = {}
    for i, card in enumerate(cards[:2]):
        decisions[card["component_key"]] = {
            "component_key": card["component_key"], "component": card["component"], "dose_label": card["dose_label"],
            "dose_value": card["dose_value"], "dose_unit": card["dose_unit"], "form": card["form"],
            "decision": "replace", "selected_food": food, "card_index": i,
        }
    at.session_state["swipe_decisions"] = decisions
    at.session_state["swipe_index"] = len(cards)
    at.run()
    rows = _row_buttons(at, "food")
    assert len(rows) == 1
    rows[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    keys = [b.key for b in at.button if str(b.key).startswith("plandlg_change_")]
    assert keys == ["plandlg_change_0", "plandlg_change_1"]
    at.button(key="plandlg_change_1").click().run()
    assert at.session_state["swipe_index"] == 1 and at.session_state["swipe_edit_return"] is True


# ---------------------------------------------------------------- change a choice
@pytest.mark.parametrize("kind", ["food", "keep"])
def test_change_a_choice_reopens_the_card_in_edit_mode_and_closes_the_dialog(kind):
    at = _results_app()
    _row_buttons(at, kind)[0].click().run()
    at.button(key="plandlg_change_0").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert _dialogs(at) == [] and not at.session_state["swipe_plan_item"]
    assert at.session_state["swipe_edit_return"] is True
    index = at.session_state["swipe_index"]
    cards = at.session_state["swipe_cards"]
    assert index < len(cards)
    # Deciding the card goes straight back to the results, and the dialog does not come back.
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    at.session_state[key] = {"dir": "left", "id": "again", "card": cards[index]["component_key"], "index": index}
    at.run()
    assert at.session_state["swipe_index"] == len(cards)
    assert _dialogs(at) == [] and not at.session_state["swipe_plan_item"]


# ---------------------------------------------------------------- what the whole food adds (AI)
# The fake is installed AFTER the first run of the app: that run reloads blockbrain.app once (see _load_current),
# which would put the real function back.
def test_the_comparison_is_asked_once_per_food_and_then_served_from_the_cache(monkeypatch):
    at = _results_app()
    calls: list[str] = []
    monkeypatch.setattr(bb, "call_blockbrain_text", _fake_text(calls))
    _row_buttons(at, "food")[0].click().run()
    at.button(key="plandlg_benefits").click().run()
    assert not at.exception, [e.value for e in at.exception]
    _await_jobs()
    at.run()
    assert len(calls) == 1 and calls[0].count("Whole food chosen instead") == 1  # only this food's pairings
    assert any("Pill alone" in m.value for m in at.markdown)
    assert len(_dialogs(at)) == 1  # still open
    # The button is not taken away from under a keyboard user (nor disabled, which also drops the focus): it stays, and
    # the status line says it is ready.
    assert not at.button(key="plandlg_benefits").disabled
    assert any("role='status'" in m.value and "Comparison ready" in m.value for m in at.markdown)
    at.run()
    assert len(calls) == 1
    # Another food is another question.
    at.button(key="plandlg_close").click().run()
    assert _dialogs(at) == []
    _row_buttons(at, "food")[1].click().run()
    at.button(key="plandlg_benefits").click().run()
    _await_jobs()
    assert len(calls) == 2 and calls[0] != calls[1]


def test_asking_for_the_comparison_returns_at_once_and_draws_a_live_status(monkeypatch):
    """The click that starts the job must not wait for the model: Done / Change a choice queue behind a running script."""
    at = _results_app()
    gate, calls = threading.Event(), []

    def slow(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        calls.append(user)
        if on_text:
            on_text("**Zinc → Oysters**\n- 💊 Pill alone: so far")
        gate.wait(15)
        return "**Zinc → Oysters**\n- 💊 Pill alone: one line"

    monkeypatch.setattr(bb, "call_blockbrain_text", slow)
    _row_buttons(at, "food")[0].click().run()
    started = time.monotonic()
    at.button(key="plandlg_benefits").click().run()
    try:
        assert time.monotonic() - started < 5, "the run waited for the model"
        assert not at.exception, [e.value for e in at.exception]
        assert any("role='status'" in m.value and "Gathering whole-food benefits" in m.value for m in at.markdown)
        for _ in range(100):
            if calls:
                break
            time.sleep(0.05)
        at.button(key="plandlg_benefits").click().run()  # a second tap starts no second job (and spends no second allowance)
        assert len(calls) == 1 and len(at.session_state["_suppswipe_llm_usage"]["generate"]) == 1
        # Closing and reopening while it is still being written does not wait for it either.
        at.button(key="plandlg_close").click().run()
        started = time.monotonic()
        _row_buttons(at, "food")[0].click().run()
        assert time.monotonic() - started < 5, "reopening an in-flight food waited for the model"
        assert len(_dialogs(at)) == 1 and not at.exception, [e.value for e in at.exception]
    finally:
        gate.set()
    _await_jobs()


def test_a_comparison_that_fails_says_so_uses_one_allowance_and_can_be_retried(monkeypatch):
    at = _results_app()
    calls: list[str] = []
    monkeypatch.setattr(bb, "call_blockbrain_text", _fake_text(calls, answer=""))
    _row_buttons(at, "food")[0].click().run()
    at.button(key="plandlg_benefits").click().run()
    assert not at.exception, [e.value for e in at.exception]
    _await_jobs()
    at.run()
    assert any("fetch the comparison" in w.value for w in at.warning)
    assert not any("unavailable" in w.value for w in at.warning)  # a transient failure: trying again is the advice
    assert not at.button(key="plandlg_benefits").disabled  # retry
    assert len(at.session_state["_suppswipe_llm_usage"]["generate"]) == 1
    assert not any("limit for AI answers" in i.value for i in at.info)
    # The retry asks again, and a good answer clears the failure.
    monkeypatch.setattr(bb, "call_blockbrain_text", _fake_text(calls))
    at.button(key="plandlg_benefits").click().run()
    _await_jobs()
    at.run()
    assert not at.warning and any("Pill alone" in m.value for m in at.markdown)


def test_a_hard_failure_on_the_worker_thread_reaches_the_user_as_unavailable(monkeypatch):
    """last_call_error() is per thread and the job runs on a pool thread: the failure is carried over by llm_cache."""
    at = _results_app()

    def revoked(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        bb._note_failure("text", "HTTP 401 unauthorized", time.monotonic())  # what the real adapter does, on ITS thread
        return ""

    monkeypatch.setattr(bb, "call_blockbrain_text", revoked)
    _row_buttons(at, "food")[0].click().run()
    at.button(key="plandlg_benefits").click().run()
    _await_jobs()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("The AI helper is unavailable right now" in w.value for w in at.warning)
    assert not any("fetch the comparison" in w.value for w in at.warning)


def test_a_reached_limit_shows_only_the_limit_message(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_MAX_GENERATIONS_PER_HOUR", "1")
    at = _results_app()
    calls: list[str] = []
    monkeypatch.setattr(bb, "call_blockbrain_text", _fake_text(calls))
    _row_buttons(at, "food")[0].click().run()
    at.button(key="plandlg_benefits").click().run()
    assert len(calls) == 1
    at.button(key="plandlg_close").click().run()
    _row_buttons(at, "food")[1].click().run()
    at.button(key="plandlg_benefits").click().run()
    assert len(calls) == 1  # no second call
    assert any("limit for AI answers" in i.value for i in at.info)
    assert not at.warning


def test_the_comparison_is_explained_when_the_ai_is_off_and_the_local_facts_stay(sw, monkeypatch):
    at = _results_app()
    for name in ("BLOCKBRAIN_ORG_ID", "BLOCKBRAIN_MODEL", "BLOCKBRAIN_BOT_ID"):
        monkeypatch.setenv(name, "")
    replaced = [d for d in at.session_state["swipe_decisions"].values() if d["decision"] == "replace"]
    with_bonus = next(i for i, row in enumerate(sw._plan_rows(replaced)) if row["bonus"])  # e.g. oysters bring zinc and omega-3 too
    _row_buttons(at, "food")[with_bonus].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "plandlg_benefits" not in [b.key for b in at.button]
    assert any("AI comparison is switched off" in c.value for c in at.caption)
    assert any(c.value.startswith("Also in this portion: ") for c in at.caption)  # from the USDA data, no AI needed


def test_closing_the_window_mid_generation_loses_nothing(sw, monkeypatch):
    """The call runs in the background: closing the window leaves the generation going, and its answer is there the next
    time the window opens."""
    gate = threading.Event()

    def slow(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        gate.wait(10)
        return "**Zinc → Oysters**\n- 💊 Pill alone: one line"

    monkeypatch.setattr(bb, "call_blockbrain_text", slow)
    items = [{"component": "Zinc", "selected_food": {"food_description": "Oysters, eastern, farmed, raw"}}]
    key = sw._benefits_prompts(items)[2]
    future = sw._start_whole_food_benefits(items)
    assert sw._start_whole_food_benefits(items) is future  # asking again joins the same job
    assert llm_cache.get(key) is None and llm_cache.inflight(key) is not None
    gate.set()
    future.result(timeout=10)
    assert llm_cache.get(key).startswith("**Zinc")
    assert sw._start_whole_food_benefits(items) is None  # cached: nothing to start


def test_a_window_opened_while_the_comparison_is_being_written_shows_it_when_done(sw, monkeypatch):
    at = _results_app()
    gate, calls = threading.Event(), []

    def slow(system, user, model=None, on_text=None, history=None, budget_s=None, allow_tools=False):
        calls.append(user)
        gate.wait(10)
        return "**Zinc → Oysters**\n- 🥗 Whole food also gives: more"

    monkeypatch.setattr(bb, "call_blockbrain_text", slow)
    replaced = [d for d in at.session_state["swipe_decisions"].values() if d["decision"] == "replace"]
    sw._start_whole_food_benefits(sw._plan_rows(replaced)[0]["items"])  # started earlier, e.g. before the window was closed
    started = time.monotonic()
    _row_buttons(at, "food")[0].click().run()
    assert time.monotonic() - started < 5, "the run waited for the model"
    assert not at.exception, [e.value for e in at.exception]
    assert "plandlg_benefits" not in [b.key for b in at.button]  # nobody asked in this session: no button, just the progress
    assert any("Gathering whole-food benefits" in m.value for m in at.markdown)
    gate.set()
    _await_jobs()
    at.run()
    assert any("whole food also gives" in m.value.lower() for m in at.markdown) and len(calls) == 1
    assert not at.session_state["_suppswipe_llm_usage"].get("generate") if "_suppswipe_llm_usage" in at.session_state else True


def test_the_change_copy_says_where_the_user_lands():
    at = _results_app()
    _row_buttons(at, "food")[0].click().run()
    captions = [c.value for c in at.caption]
    assert any("you come straight back to your plan" in c for c in captions)
    assert not any("straight back here" in c for c in captions)


def test_llm_cache_failures_are_per_key_expire_and_clear_on_a_new_job(monkeypatch):
    llm_cache.set_failure("k", "HTTP 401")
    assert llm_cache.failure("k") == "HTTP 401" and llm_cache.failure("other") is None
    future = llm_cache.submit("k", lambda: "an answer")  # a new job forgets the old failure
    assert llm_cache.failure("k") is None
    future.result(timeout=10)
    llm_cache.set_failure("old", "x")
    now = time.monotonic()
    monkeypatch.setattr(llm_cache.time, "monotonic", lambda: now + llm_cache._FAILURE_TTL_S + 1)
    assert llm_cache.failure("old") is None
    llm_cache.set_failure("gone", "x")
    llm_cache.clear()
    assert llm_cache.failure("gone") is None


# ---------------------------------------------------------------- the request's life
def test_a_stale_request_shows_nothing_and_is_cleared():
    at = _results_app()
    for request in ({"kind": "keep", "key": "no such nutrient"}, {"kind": "food", "key": "no such food"}, {"kind": "?", "key": ""}, "junk"):
        at.session_state["swipe_plan_item"] = request
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert _dialogs(at) == [] and not at.session_state["swipe_plan_item"]


def test_a_request_is_not_shown_over_the_cards_and_not_later_either():
    at = _results_app()
    _row_buttons(at, "food")[0].click().run()
    assert len(_dialogs(at)) == 1
    at.session_state["swipe_index"] = 0  # e.g. "Back to the cards" with a stale request
    at.run()
    assert _dialogs(at) == [] and not at.session_state["swipe_plan_item"]
    at.session_state["swipe_index"] = len(at.session_state["swipe_cards"])
    at.run()
    assert _dialogs(at) == []


def test_the_analyze_dialog_wins_and_nothing_breaks_with_two_flags():
    at = _results_app()
    _row_buttons(at, "food")[0].click().run()
    at.session_state["swipe_open_analyze"] = True
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _dialogs(at) == ["Analyze my supplement"]


def test_scan_another_supplement_forgets_the_request():
    at = _results_app()
    _row_buttons(at, "keep")[0].click().run()
    at.button(key="swipe_analyze_btn").click().run()
    assert not at.session_state["swipe_plan_item"] if "swipe_plan_item" in at.session_state else True
    assert _dialogs(at) == ["Analyze my supplement"]


def test_the_close_handler_clears_the_request(sw, monkeypatch):
    state = {"swipe_plan_item": {"kind": "food", "key": "x"}}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._close_plan_item_dialog()
    assert state["swipe_plan_item"] is None
    state["swipe_plan_item"] = {"kind": "food", "key": "x"}
    sw._open_plan_item("keep", "zinc")
    assert state["swipe_plan_item"] == {"kind": "keep", "key": "zinc"}


def test_the_snapshot_never_holds_the_request(sw):
    state = {"swipe_cards": [{"component_key": "zinc"}], "swipe_analysis_text": "Zinc 10 mg", "swipe_index": 1,
             "swipe_decisions": {}, "swipe_plan_item": {"kind": "food", "key": "x"}}
    assert "swipe_plan_item" not in str(sw._scan_snapshot(state))


# ---------------------------------------------------------------- small pieces
def test_titles_are_plain_text_and_escaped(sw):
    item = {"kind": "food", "items": [], "row": {"food": {"food_description": "*Sesame* [raw]: a_very_long_name"}}}
    assert sw._plan_item_title(item) == r"\*sesame\* \[raw\]\: a\_very\_long\_name"
    assert sw._plan_item_title({"kind": "keep", "items": [{"component": "Vitamin B12"}], "row": None}) == "Vitamin B12"


def test_markdown_escape_keeps_labels_literal(sw):
    assert sw._md_escape("Oil [x] ~y~ $5 :red[a] `b` *c* _d_") == r"Oil \[x\] \~y\~ \$5 \:red\[a\] \`b\` \*c\* \_d\_"


def test_a_nutrient_without_a_guide_row_says_so_and_does_not_break():
    at = _results_app()
    key = next(iter(at.session_state["swipe_decisions"]))
    d = dict(at.session_state["swipe_decisions"][key], decision="keep", component="Lutein", component_key="lutein")
    decisions = dict(at.session_state["swipe_decisions"])
    decisions["lutein"] = d
    at.session_state["swipe_decisions"] = decisions
    at.run()
    at.session_state["swipe_plan_item"] = {"kind": "keep", "key": "lutein"}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("no athlete target is tracked" in c.value for c in at.caption)
    assert len(at.table) == 2  # the page's Athlete RDA popover and the window's copy of the table


def test_pregnancy_mode_offers_no_comparison_for_a_food_it_advises_against(monkeypatch):
    at = _results_app()
    decisions = dict(at.session_state["swipe_decisions"])
    key = next(k for k, d in decisions.items() if d["decision"] == "replace")
    liver = {"food_description": "Beef, liver, raw", "amount_per_100g": 100.0, "unit": "mcg"}
    decisions[key] = dict(decisions[key], selected_food=liver)
    at.session_state["swipe_decisions"] = decisions
    at.session_state["swipe_pregnant"] = True
    at.run()
    row = next(b for b in _row_buttons(at, "food") if "iver" in b.label)
    row.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "plandlg_benefits" not in [b.key for b in at.button]
    assert any("isn't advised in pregnancy" in c.value for c in at.caption)


def test_the_window_repeats_the_heads_up_of_its_own_item_only():
    at = _results_app()
    decisions = dict(at.session_state["swipe_decisions"])
    key = next(k for k, d in decisions.items() if d["decision"] == "replace")
    liver = {"food_description": "Beef, liver, raw", "amount_per_100g": 100.0, "unit": "mcg"}
    decisions[key] = dict(decisions[key], selected_food=liver)
    at.session_state["swipe_decisions"] = decisions
    at.session_state["swipe_pregnant"] = True
    at.run()

    def boxes():
        return [html.unescape(m.value) for m in at.markdown if "class='plan-warn'" in m.value]

    page_only = len(boxes())  # the page's Heads-up box, when it has anything to say
    next(b for b in _row_buttons(at, "food") if "iver" in b.label).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(boxes()) == page_only + 1 and "isn't advised in pregnancy" in boxes()[-1]  # the liver's own copy, in its window
    at.button(key="plandlg_close").click().run()
    assert not at.get("dialog")
    others = [b for b in _row_buttons(at, "food") if "iver" not in b.label]
    if others:  # another food's window does not repeat the liver warning
        others[0].click().run()
        assert not any("liver" in box.lower() for box in boxes()[page_only:])


def test_the_meal_plan_poller_does_not_redraw_the_page_behind_an_open_window(sw, monkeypatch):
    """A full rerun makes an open window blink; the finished plan is drawn when the window is closed (that reruns too)."""
    reruns: list[int] = []
    monkeypatch.setattr(sw.st, "rerun", lambda *a, **k: reruns.append(1))
    monkeypatch.setattr(sw.st, "markdown", lambda *a, **k: None)
    poll = getattr(sw._live_meal_plan, "__wrapped__", sw._live_meal_plan)
    state = {"swipe_plan_item": {"kind": "keep", "key": "x"}}
    monkeypatch.setattr(sw.st, "session_state", state)
    poll("a plan that is not being written")
    assert not reruns
    state["swipe_plan_item"] = None
    poll("a plan that is not being written")
    assert reruns

"""The More tab: the bar's right-most item (it took About's place; a sixth tab does not fit) and its sheet. The sheet is titled "More" and
has two entries, each a full-width button with a one-line description: "About & privacy" (always) and "Report a problem with this card"
(only while a card is on screen and no analysis runs). An entry REPLACES the sheet (one dialog at a time: the flags of the next sheet
are set and the whole app reruns), and what is opened that way gives the keyboard focus back to the More tab when it is closed.

AppTest reruns the whole script on every click and has no on_dismiss (X, Esc, a tap outside), so the dismissal is played by calling the
callback on a plain state; what only a browser can prove (geometry, the real focus, Esc, a double tap, the dead tab while a photo is
read) is in tests/test_ux_more_tab_browser.py. The card's report sheet itself, and the "report" event of a card page that was loaded
before the tab existed, are in tests/test_ux_fixed_screen.py."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from card_tools import open_about, open_more, open_report, sheet_titles, tap_more_entry, tap_tool

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")
OWN_LABEL = "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%"
ABOUT, REPORT, MORE = "About & privacy", "Report a problem with this card", "More"
SCREENS = ["welcome", "cards", "results", "error"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


def _swipe(at: AppTest, index: int, direction: str = "left") -> None:
    card = at.session_state["swipe_cards"][index]
    at.session_state["tinder_" + str(at.session_state["swipe_reset_nonce"])] = {
        "dir": direction, "id": f"s{index}", "card": card["component_key"], "index": index}
    at.run()


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
            _swipe(at, i)
    elif name == "error":  # the welcome screen again, with the message of a failed analysis on it
        _analyse(at, "hello world asdf qwerty")
        assert at.error
    assert not at.exception, [e.value for e in at.exception]
    return at


def _entries(at: AppTest) -> list[str]:
    """The keys of the More sheet's entries that are on screen."""
    return [str(b.key) for b in at.button if str(b.key).startswith("more_")]


def _focus_scripts(at: AppTest) -> list[str]:
    return [str(e.proto.srcdoc) for e in at.get("iframe") if "/* focus " in str(e.proto.srcdoc)]


def _page_text(at: AppTest) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])


# ------------------------------------------------------------------ the tab
@pytest.mark.parametrize("screen", SCREENS)
def test_more_is_the_fifth_and_last_item_of_the_bar_on_every_screen(screen):
    at = _screen(screen)
    bar = [b for b in at.button if str(b.key).startswith("appbar")]
    assert [b.key for b in bar] == ["appbar_guide", "appbar_diet", "appbar_scan", "appbar_scans", "appbar_more"]
    assert bar[-1].label == MORE and not any(b.disabled for b in bar)
    assert not [b for b in at.button if b.label == "About"]  # the About tab is gone: the More sheet opens it
    assert at.get("dialog") == [] and _entries(at) == []  # nothing is open until the tab is tapped


def test_the_tab_has_an_ellipsis_icon_for_the_live_and_the_dead_bar_and_nothing_is_left_of_the_about_tab(sw):
    src = Path(APP).read_text(encoding="utf-8")
    assert '[class~="st-key-appbar_more"], [class~="st-key-appbar_busy_more"] { --ss-ico: var(--ss-ico-more); }' in src
    icon = next(ln for ln in src.splitlines() if ln.strip().startswith("--ss-ico-more:"))
    assert icon.count("%3Ccircle") == 3 and "viewBox='0 0 24 24'" in icon  # three dots on one line
    assert "ss-ico-about" not in src and "appbar_about" not in src
    assert sw._BAR_ITEMS[-1] == ("more", "More") and len(sw._BAR_ITEMS) == 5  # a sixth tab does not fit (five are 44.8 px at 360 px)


def test_a_phone_that_has_a_card_open_gets_the_dead_more_tab_while_a_photo_is_read(sw, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "suppswipe_app", sw)

    def busy_script():
        import sys

        sys.modules["suppswipe_app"]._render_app_bar(None, True)

    dead = AppTest.from_function(busy_script).run()
    assert not dead.exception, [e.value for e in dead.exception]
    assert [b.key for b in dead.button][-1] == "appbar_busy_more" and dead.button[-1].label == MORE and dead.button[-1].disabled


def test_the_more_tab_does_nothing_while_an_analysis_runs(sw, monkeypatch):
    """The dead twin's tab is disabled; the callback also refuses (a forged click must not leave a flag that survives the run)."""
    state = {"swipe_is_analyzing": True, "swipe_pending_request": {"manual": "x"}}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._open_sheet("more")
    assert "swipe_sheet" not in state
    state["swipe_is_analyzing"] = False
    sw._open_sheet("more")
    assert state["swipe_sheet"] == "more"


# ------------------------------------------------------------------ the sheet
@pytest.mark.parametrize("screen", SCREENS)
def test_the_sheet_offers_about_always_and_the_report_only_while_a_card_is_on_screen(screen):
    at = _screen(screen)
    before = (at.session_state["swipe_index"], len(at.session_state["swipe_decisions"]))
    open_more(at)
    assert not at.exception, [e.value for e in at.exception]
    assert sheet_titles(at) == [MORE]  # one dialog
    assert _entries(at) == (["more_about", "more_report"] if screen == "cards" else ["more_about"])
    labels = {b.key: b.label for b in at.button if str(b.key).startswith("more_")}
    assert labels["more_about"] == ABOUT and labels.get("more_report", REPORT) == REPORT
    assert all(b.proto.icon == "" for b in at.button if str(b.key).startswith("more_"))  # a plain label: no Material ligature in the name
    captions = [c.value for c in at.caption]
    assert ("What is sent, what stays on your device." in captions) and (("Logged for review, nothing personal." in captions) == (screen == "cards"))
    assert (at.session_state["swipe_index"], len(at.session_state["swipe_decisions"])) == before  # opening it changes nothing


def test_the_first_block_of_the_sheet_is_its_one_keyboard_stop():
    for screen in ("welcome", "cards"):
        at = open_more(_screen(screen))
        blocks = [m.value for m in at.markdown if "tabindex='0'" in m.value]
        assert len(blocks) == 1 and "role='region' aria-label='More content'" in blocks[0], blocks
        first = next(m.value for m in at.markdown if m.value.startswith("<div class='sc-sub'"))
        assert first == blocks[0]  # and it is the first block of the sheet, above the two entries


def test_the_sheet_stays_open_over_stray_reruns_and_the_report_entry_is_for_the_card_on_screen():
    at = open_more(_screen("cards"))
    at.run()
    at.run()  # e.g. the history iframe re-sending its value
    assert sheet_titles(at) == [MORE] and _entries(at) == ["more_about", "more_report"]
    _swipe(at, 0)  # a swipe of a stale page arrives while the sheet is open: the card on screen is the next one
    assert at.session_state["swipe_index"] == 1 and sheet_titles(at) == [MORE]
    tap_more_entry(at, "report")
    second = at.session_state["swipe_cards"][1]["component_key"]
    assert sheet_titles(at) == ["Report a problem"] and at.session_state["swipe_sheet_card"] == [second, 1]


def test_an_older_unanswered_dialog_wins_over_the_sheet_and_two_dialogs_never_stack():
    at = open_more(_screen("cards"))
    at.session_state["swipe_confirm_restart"] = True
    at.run()
    assert not at.exception, [e.value for e in at.exception]  # a second st.dialog in one run raises
    assert sheet_titles(at) == ["Start over?"]
    at.session_state["swipe_confirm_restart"] = False
    at.run()
    assert sheet_titles(at) == [MORE]  # nothing else requested: the sheet comes back


def test_a_bar_tap_drops_the_pending_focus_request_and_the_way_in_of_an_earlier_sheet(sw, monkeypatch):
    state = {"_suppswipe_focus_bar": "more", "swipe_sheet_from": "more", "swipe_sheet": "about"}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._open_sheet("guide")
    assert "_suppswipe_focus_bar" not in state and "swipe_sheet_from" not in state and state["swipe_sheet"] == "guide"


# ------------------------------------------------------------------ About & privacy through More
@pytest.mark.parametrize("screen", SCREENS)
def test_the_about_entry_replaces_the_sheet_with_the_about_sheet(sw, screen):
    at = _screen(screen)
    open_about(at)
    assert not at.exception, [e.value for e in at.exception]
    assert sheet_titles(at) == [ABOUT]  # exactly one dialog: the More sheet is gone
    assert at.session_state["swipe_sheet"] == "about" and at.session_state["swipe_sheet_from"] == "more"
    text = _page_text(at)
    assert f"Build {sw.BUILD_TAG}" in text and "not medical advice" in text
    assert _entries(at) == []  # none of the More sheet's buttons is drawn any more


def test_the_about_sheet_keeps_its_content_and_changes_nothing_underneath():
    at = _screen("cards")
    before = (at.session_state["swipe_index"], len(at.session_state["swipe_decisions"]), at.session_state["swipe_diet_profile_id"])
    open_about(at)
    for heading in ("Not medical advice", "What is sent to services", "What stays on your device", "On the server", "Sources"):
        assert any(heading in m.value for m in at.markdown), heading
    assert (at.session_state["swipe_index"], len(at.session_state["swipe_decisions"]), at.session_state["swipe_diet_profile_id"]) == before


# ------------------------------------------------------------------ the report through More
def test_the_report_entry_replaces_the_sheet_with_the_report_sheet_for_that_card_and_the_report_logs_that_card(sw, caplog):
    at = _screen("cards")
    _swipe(at, 0)  # on the second card, not the first
    card = at.session_state["swipe_cards"][1]
    open_report(at)
    assert not at.exception, [e.value for e in at.exception]
    assert sheet_titles(at) == ["Report a problem"]  # one dialog
    assert at.session_state["swipe_sheet"] == "report" and at.session_state["swipe_sheet_card"] == [card["component_key"], 1]
    assert at.session_state["swipe_sheet_from"] == "more" and _entries(at) == []
    report = next(b for b in at.button if b.label == "🚩 Report a problem with this card")
    with caplog.at_level(logging.WARNING, logger="blockbrain.app"):
        report.click()
        at.run()
    lines = [r.getMessage() for r in caplog.records if "SuppSwipe card report" in r.getMessage()]
    assert len(lines) == 1 and f'"nutrient": "{sw._nutrient_title(card["component"])}"' in lines[0], lines
    assert [t.value for t in at.toast] == ["Thanks — logged for review"]
    assert at.session_state["swipe_sheet"] is None and sheet_titles(at) == [] and "swipe_sheet_card" not in at.session_state
    assert at.session_state["swipe_index"] == 1  # the card is back, undecided


def test_the_report_sheet_through_more_changes_no_decision_and_no_food():
    at = _screen("cards")
    _swipe(at, 0)
    view = dict(at.session_state["swipe_card_view"])
    decisions = dict(at.session_state["swipe_decisions"])
    open_report(at)
    assert at.session_state["swipe_decisions"] == decisions and at.session_state["swipe_index"] == 1
    assert at.session_state["swipe_card_view"]["shown"] == view["shown"] and at.session_state["swipe_card_view"]["labels"] == view["labels"]


def test_a_stale_event_from_a_card_page_loaded_before_the_tab_still_opens_the_report_and_it_is_no_step_on_from_more():
    at = _screen("cards")
    tap_tool(at, "report")  # the old More button of the card: {"kind": "report"}
    assert sheet_titles(at) == ["Report a problem"] and "swipe_sheet_from" not in at.session_state  # not a step on from the More sheet
    assert at.session_state["swipe_sheet_card"] == [at.session_state["swipe_cards"][0]["component_key"], 0]


def test_a_stale_report_event_does_not_inherit_the_way_in_of_an_earlier_more_sheet(sw):
    state = {"swipe_cards": [{"component_key": "zinc"}], "swipe_index": 0, "swipe_sheet_from": "more", "swipe_sheet": "about"}
    assert sw._apply_card_tool_tap(state, {"kind": "report", "id": "r1", "card": "zinc", "index": 0}) is True
    assert state["swipe_sheet"] == "report" and "swipe_sheet_from" not in state
    state = {"swipe_cards": [{"component_key": "zinc"}], "swipe_index": 0, "swipe_sheet_from": "more", "swipe_sheet": "about"}
    assert sw._apply_card_guide_tap(state, {"kind": "guide", "id": "g1", "card": "zinc", "index": 0}) is True
    assert state["swipe_sheet"] == "guide" and "swipe_sheet_from" not in state


# ------------------------------------------------------------------ stale and forged states open nothing
def test_the_card_on_screen_is_the_one_the_last_full_run_drew(sw, monkeypatch):
    cards = [{"component_key": "zinc"}, {"component_key": "iron"}]
    state = {"swipe_cards": cards, "swipe_index": 1, "swipe_card_view": {"index": 1, "component_key": "iron"}}
    monkeypatch.setattr(sw.st, "session_state", state)
    assert sw._card_on_screen() == ("iron", 1)
    state["swipe_index"] = 2  # the results
    assert sw._card_on_screen() is None
    state["swipe_index"] = -1
    assert sw._card_on_screen() is None
    state["swipe_index"] = 1
    state["swipe_card_view"] = {"index": 0, "component_key": "zinc"}  # a view of another card
    assert sw._card_on_screen() is None
    state["swipe_card_view"] = {"index": 1, "component_key": "zinc"}  # a view of another nutrient
    assert sw._card_on_screen() is None
    state["swipe_card_view"] = None
    assert sw._card_on_screen() is None
    state.update(swipe_cards=[], swipe_card_view={"index": 0, "component_key": "zinc"}, swipe_index=0)
    assert sw._card_on_screen() is None  # no cards at all


@pytest.mark.parametrize("what", ["index_past_the_end", "cards_gone", "view_of_another_card", "analysis_running"])
def test_a_report_tap_that_finds_no_card_to_report_closes_the_sheet_and_opens_nothing(sw, monkeypatch, what):
    state = {"swipe_cards": [{"component_key": "zinc"}], "swipe_index": 0, "swipe_card_view": {"index": 0, "component_key": "zinc"},
             "swipe_sheet": "more", "swipe_confirm_restart": False}
    if what == "index_past_the_end":
        state["swipe_index"] = 1
    elif what == "cards_gone":
        state["swipe_cards"] = []
    elif what == "view_of_another_card":
        state["swipe_card_view"] = {"index": 0, "component_key": "iron"}
    else:
        state.update(swipe_is_analyzing=True, swipe_pending_request={"manual": "x"})
    reruns = []
    monkeypatch.setattr(sw.st, "session_state", state)
    monkeypatch.setattr(sw.st, "rerun", lambda **kw: reruns.append(kw))
    sw._more_to_report()
    assert state["swipe_sheet"] is None and "swipe_sheet_card" not in state and "swipe_sheet_from" not in state  # nothing opens
    assert state["_suppswipe_focus_bar"] == "more"  # the More tab is where the visitor was
    assert reruns == [{"scope": "app"}]


def test_a_report_tap_on_a_card_sets_exactly_the_flags_the_card_tool_sets(sw, monkeypatch):
    state = {"swipe_cards": [{"component_key": "zinc"}], "swipe_index": 0, "swipe_card_view": {"index": 0, "component_key": "zinc"},
             "swipe_sheet": "more", "swipe_open_analyze": True, "swipe_confirm_restart": True, "swipe_plan_item": {"kind": "keep"}}
    reruns = []
    monkeypatch.setattr(sw.st, "session_state", state)
    monkeypatch.setattr(sw.st, "rerun", lambda **kw: reruns.append(kw))
    sw._more_to_report()
    assert state["swipe_sheet"] == "report" and state["swipe_sheet_card"] == ["zinc", 0] and state["swipe_sheet_from"] == "more"
    assert state["swipe_open_analyze"] is False and state["swipe_confirm_restart"] is False and state["swipe_plan_item"] is None
    assert reruns == [{"scope": "app"}]
    # ... which is what makes the Report sheet's own guard (_card_sheet_view) accept it
    assert sw._card_sheet_view() == state["swipe_card_view"]


@pytest.mark.parametrize("flag", ["none", "another_card", "another_index", "a_string"])
def test_a_report_flag_without_its_card_opens_nothing_whatever_the_way_in(flag):
    at = _screen("cards")
    key = at.session_state["swipe_cards"][0]["component_key"]
    at.session_state["swipe_sheet"] = "report"
    at.session_state["swipe_sheet_from"] = "more"
    at.session_state["swipe_sheet_card"] = {"none": None, "another_card": ["not this card", 0], "another_index": [key, 7], "a_string": key}[flag]
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert sheet_titles(at) == [] and at.session_state["swipe_sheet"] is None


def test_the_more_sheet_while_the_results_are_on_screen_has_no_report_even_for_a_forged_flag():
    at = _screen("results")
    open_more(at)
    assert _entries(at) == ["more_about"]
    at.session_state["swipe_sheet"] = "report"  # a forged or stale flag, with the card of an earlier screen
    at.session_state["swipe_sheet_card"] = [at.session_state["swipe_cards"][0]["component_key"], 0]
    at.run()
    assert not at.exception and sheet_titles(at) == [] and at.session_state["swipe_sheet"] is None


# ------------------------------------------------------------------ the focus goes back to the More tab
def test_what_the_more_sheet_opened_gives_the_focus_back_to_the_more_tab_when_it_is_dismissed(sw, monkeypatch):
    for name in ("about", "report"):
        state = {"swipe_sheet": name, "swipe_sheet_from": "more", "swipe_sheet_card": ["zinc", 0], "swipe_guide_focus": "x"}
        monkeypatch.setattr(sw.st, "session_state", state)
        sw._close_sheet()  # the on_dismiss of the sheet: X, Esc, a tap outside
        assert state == {"swipe_sheet": None, "_suppswipe_focus_bar": "more"}, (name, state)


def test_a_sheet_that_something_else_opened_asks_for_no_focus(sw, monkeypatch):
    for state in (
        {"swipe_sheet": "report", "swipe_sheet_card": ["zinc", 0]},  # the card's own tool (a stale page)
        {"swipe_sheet": "more"},  # the More sheet itself: its opener is the tab, which Streamlit hands the focus back to
        {"swipe_sheet": "guide", "swipe_sheet_from": "more"},  # a flag left behind: only the sheets More opens count
        {"swipe_sheet": "about"},
        {"swipe_sheet": None, "swipe_sheet_from": "more"},
    ):
        monkeypatch.setattr(sw.st, "session_state", state)
        sw._close_sheet()
        assert "_suppswipe_focus_bar" not in state and "swipe_sheet_from" not in state and state["swipe_sheet"] is None, state


def test_the_focus_script_for_the_more_tab_is_drawn_once_after_the_report_is_sent_and_names_only_that_tab():
    at = _screen("cards")
    open_report(at)
    assert not _focus_scripts(at)  # nothing asked for it yet: the sheet is open
    next(b for b in at.button if b.label == "🚩 Report a problem with this card").click()
    at.run()
    scripts = _focus_scripts(at)
    assert len(scripts) == 1 and "st-key-appbar_more" in scripts[0] and sheet_titles(at) == []
    at.run()  # one run only: the request was used up
    assert not _focus_scripts(at) and "_suppswipe_focus_bar" not in at.session_state


# ------------------------------------------------------------------ one source of truth for the sheets
def test_the_sheets_the_more_sheet_opens_are_known_and_have_titles(sw):
    assert sw._SHEET_TITLES["more"] == MORE and sw._SHEET_TITLES["about"] == ABOUT and sw._SHEET_TITLES["report"] == "Report a problem"
    assert "more" not in sw._CARD_SHEETS and "about" not in sw._CARD_SHEETS  # only the card sheets need a card
    assert "report" in sw._CARD_SHEETS and "report" in sw._CARD_TOOLS  # a stale card page may still send it
    assert callable(sw._more_sheet) and callable(sw._about_sheet) and callable(sw._report_sheet)


def test_the_entries_have_their_own_keys_and_the_report_sheet_keeps_its_button():
    src = Path(APP).read_text(encoding="utf-8")
    assert 'key="more_about"' in src and 'key="more_report"' in src and 'with st.container(key="sheet_more")' in src
    assert 'key=f"swipe_report_{component_key}_{index}_' in src  # never the same key as the Report sheet's own button
    assert '"🚩 Report a problem with this card"' in src and '"Report a problem with this card"' in src

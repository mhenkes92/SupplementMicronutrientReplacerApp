"""The fixed bottom bar (Guide, Scans, About) and its three sheets. AppTest reruns the whole script on every click, so a
sheet is requested by a session flag (`swipe_sheet`) like the other dialogs; what only a browser can prove (docking, the
Cloud badge, the sheets' size, Esc and focus) is in tests/test_ux_app_bar_browser.py."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "swipe_mobile_app" / "app.py")
BAR = {"guide": "appbar_guide", "scans": "appbar_scans", "about": "appbar_about"}
LABELS = ["Guide", "Scans", "About"]
TITLES = {"guide": "Athlete RDA guide", "scans": "Recent scans", "about": "About & privacy"}
SHEET = "swipe_sheet"
REMOVED_POPOVERS = ("🏃 Athlete RDA guide", "🕘 Recent scans", "🔒 About & privacy")
OWN_LABEL = "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%"
ENTRY = {"ts": "2026-10-01 10:00", "diet": "Vegan", "kept": [{"component": "Zinc", "dose": "10 mg"}],
         "replaced": [{"component": "Vitamin C", "food": "Guava", "amount": "90 g"}]}
ENTRY_NEWER = dict(ENTRY, ts="2026-10-02 18:30")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


def _analyse(at: AppTest, manual: str = OWN_LABEL) -> None:
    at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "camera_barcode": "", "manual": manual}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.session_state["swipe_last_auto_signature"] = "own-label"  # what a real input records: the sample label is not saved
    at.run()


def _swipe_all(at: AppTest, direction: str = "left") -> None:
    cards = at.session_state["swipe_cards"]
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    for i, card in enumerate(cards):
        at.session_state[key] = {"dir": direction, "id": f"c{i}", "card": card["component_key"], "index": i}
        at.run()
    assert at.session_state["swipe_index"] == len(cards)


def _screen(name: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if name == "welcome":
        pass
    elif name == "cards":
        at.button(key="swipe_try_sample").click().run()
    elif name == "results":
        _analyse(at)
        _swipe_all(at, "left")
    elif name == "error":  # the welcome screen again, with the message of a failed analysis on it
        _analyse(at, "hello world asdf qwerty")
        assert at.error
    assert not at.exception, [e.value for e in at.exception]
    return at


SCREENS = ["welcome", "cards", "results", "error"]


def _bar(at: AppTest):
    return [b for b in at.button if str(b.key).startswith("appbar")]


def _titles(at: AppTest) -> list[str]:
    return [d.proto.dialog.title for d in at.get("dialog")]


def _text(at: AppTest) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])


# ------------------------------------------------------------------ the bar is on every screen
@pytest.mark.parametrize("screen", SCREENS)
def test_every_screen_has_the_bar_with_all_three_buttons_and_no_popover_left(screen):
    at = _screen(screen)
    assert [b.key for b in _bar(at)] == list(BAR.values())  # always all three, in this order, whatever the history holds
    assert [b.label for b in _bar(at)] == LABELS
    assert not any(b.disabled for b in _bar(at))
    assert at.get("dialog") == []  # nothing is open until a button is tapped
    popovers = [p.proto.popover.label for p in at.get("popover")]
    assert not [p for p in popovers if p.startswith(REMOVED_POPOVERS)], popovers  # the card's "💬 Ask AI" popover may stay


def test_the_primary_scan_button_stays_where_it_is():
    for screen, label in (("welcome", "📸 Analyze my supplement"), ("results", "📸 Scan another supplement")):
        at = _screen(screen)
        assert [b.label for b in at.button if b.key == "swipe_analyze_btn"] == [label]


def test_no_bar_label_collides_with_the_labels_other_tests_and_users_look_for():
    for label in LABELS:
        assert label not in ("Analyze", "Cancel", "Done", "Close", "Start over")


# ------------------------------------------------------------------ each sheet, on each screen
@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("sheet", list(BAR))
def test_each_button_opens_its_sheet_on_each_screen(sw, screen, sheet):
    at = _screen(screen)
    before = (at.session_state["swipe_index"], len(at.session_state["swipe_decisions"]))
    at.button(key=BAR[sheet]).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert _titles(at) == [TITLES[sheet]]  # exactly one dialog
    text = _text(at)
    if sheet == "guide":
        for name in ("Vitamin B12", "Magnesium", "Omega-3 ALA", "Selenium"):  # every tracked nutrient is in the guide
            assert name in text
        assert ("In your scan" in text) and (("Scan a supplement and its nutrients appear here" in text) == (screen in ("welcome", "error")))
    elif sheet == "scans" and screen == "results":
        assert "No scans yet" not in text and "kept" in text  # the finished own scan was recorded: a readable card
    elif sheet == "scans":
        assert "No scans yet" in text  # the empty state (this fresh session has no history)
    else:
        assert f"Build {sw.BUILD_TAG}" in text and "not medical advice" in text
    # Opening a sheet changes nothing about the scan underneath.
    assert (at.session_state["swipe_index"], len(at.session_state["swipe_decisions"])) == before
    assert [b.key for b in _bar(at)] == list(BAR.values())  # the bar is still there behind the sheet


@pytest.mark.parametrize("sheet", list(BAR))
def test_a_sheet_stays_open_over_stray_reruns_until_it_is_dismissed(sw, monkeypatch, sheet):
    at = _screen("welcome")
    at.button(key=BAR[sheet]).click().run()
    at.run()
    at.run()  # e.g. the history iframe re-sending its value
    assert _titles(at) == [TITLES[sheet]]
    state = {SHEET: sheet, "swipe_guide_focus": "x", "swipe_scans_confirm": True, "swipe_scans_more": True}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._close_sheet()  # the dialog's on_dismiss (X, Esc, tap outside): the flag is the only thing that keeps it open
    assert state == {SHEET: None}
    at.session_state[SHEET] = None
    at.run()
    assert _titles(at) == []


# ------------------------------------------------------------------ the guide
def test_the_guide_lists_the_scan_first_with_the_pill_and_the_food_and_every_other_nutrient_once():
    at = _screen("cards")
    at.button(key=BAR["guide"]).click().run()
    text = _text(at)
    assert "In your scan" in text and "All other nutrients" in text
    scan_part, other_part = text.split("All other nutrients", 1)
    for name in ("Vitamin C", "Vitamin D", "Zinc", "Selenium"):  # the sample label's nutrients lead ...
        assert name in scan_part and f"<span class='gd-name'>{name}" not in other_part  # ... and are not repeated below
    assert "In your pill:" in scan_part and "of the athlete target" in scan_part
    assert "Vitamin A" in other_part and "Omega-3 (EPA+DHA)" in other_part


def test_the_guide_marks_swapped_and_kept_and_opens_at_a_requested_nutrient():
    at = _screen("results")
    focus = at.session_state["swipe_cards"][-1]["component_key"]
    at.session_state["swipe_guide_focus"] = focus
    at.button(key=BAR["guide"]).click().run()  # a bar tap shows the guide as is: no focus
    assert "data-focus='1'" not in " ".join(m.value for m in at.markdown)
    at.session_state[SHEET] = "guide"
    at.session_state["swipe_guide_focus"] = focus
    at.run()
    html = [m.value for m in at.markdown if "data-focus='1'" in m.value]
    assert len(html) == 1
    first_row = re.search(r"<ul class='gd-list'>(.*?)</li>", html[0].split('In your scan', 1)[1]).group(1)
    assert "data-focus='1'" in first_row  # the requested nutrient is the first row of its list and is marked


def test_a_card_nutrient_without_a_guide_row_is_named_not_dropped(sw):
    at = _screen("cards")
    cards = [dict(c) for c in at.session_state["swipe_cards"]]
    cards[0].update(component="Lutein", component_key="lutein")
    at.session_state["swipe_cards"] = cards
    at.button(key=BAR["guide"]).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "No athlete target is tracked for Lutein." in _text(at)


def test_rows_that_share_a_guide_entry_appear_once(sw):
    at = _screen("cards")
    cards = [dict(c) for c in at.session_state["swipe_cards"]]
    cards[0].update(component="Vitamin K", component_key="vitamin k")
    cards[1].update(component="Vitamin K2 (MK-7)", component_key="vitamin k2 (mk-7)")
    at.session_state["swipe_cards"] = cards
    at.button(key=BAR["guide"]).click().run()
    assert not at.exception, [e.value for e in at.exception]
    body = " ".join(m.value for m in at.markdown)
    assert body.count("<span class='gd-name'>Vitamin K</span>") == 1


def test_dynamic_text_in_the_guide_is_escaped():
    """The rows are raw HTML: a dose label read from a photo (or any card text) must never become markup."""
    at = _screen("results")
    cards = [dict(c) for c in at.session_state["swipe_cards"]]
    cards[0].update(dose_label="<img src=x onerror=1>")
    decisions = {k: dict(v) for k, v in at.session_state["swipe_decisions"].items()}
    for d in decisions.values():
        d["decision"] = "replace"
        d["selected_food"] = {"name": "Guava <script>alert(1)</script>", "fdc_id": 1}
    at.session_state["swipe_cards"] = cards
    at.session_state["swipe_decisions"] = decisions
    at.button(key=BAR["guide"]).click().run()
    assert not at.exception, [e.value for e in at.exception]
    body = " ".join(m.value for m in at.markdown)
    assert "<img src=x" not in body and "<script>" not in body
    assert "&lt;img src=x onerror=1&gt;" in body


def test_every_nutrient_is_in_the_guide_exactly_once(sw):
    at = _screen("welcome")
    at.button(key=BAR["guide"]).click().run()
    body = " ".join(m.value for m in at.markdown)
    for entry in sw._MICRONUTRIENT_RDA:
        assert body.count(f"<span class='gd-name'>{entry['display'].replace('&', '&amp;')}</span>") == 1, entry["display"]


def test_the_guide_row_text_is_a_sentence_for_screen_readers(sw):
    row = sw._guide_row_html(sw._rda_for_component("vitamin d"))
    assert "Vitamin D: athlete target 25 mcg, adult RDA 15 mcg, EU label 5 mcg, often low in athletes." in row
    assert "aria-hidden='true'" in row and "<i data-part='adult' style='width:60.0%'></i><i data-part='extra'></i>" in row
    folate = sw._guide_row_html(sw._rda_for_component("folate"))  # DFE targets next to a folic acid label value: say so
    assert "600 mcg DFE" in folate and "Adult RDA 400 mcg DFE" in folate and "EU label 200 mcg folic acid" in folate
    same = sw._guide_row_html(sw._rda_for_component("biotin"))
    assert "data-part='extra'" not in same and "Same as the adult RDA (30 mcg)" in same  # athlete == adult: one light bar


def test_the_pill_ring_never_leaves_the_bar(sw):
    row = sw._guide_row_html(sw._rda_for_component("zinc"), ratio=3.4)
    assert "left:100.0%;transform:translateX(-100.0%)" in row
    assert sw._athlete_share_phrase(3.4) == "3.4× the athlete target" and sw._athlete_share_phrase(0.4) == "40% of the athlete target"


# ------------------------------------------------------------------ Recent scans
def test_recent_scans_is_newest_first_and_clear_history_asks_first_then_ends_in_the_empty_state():
    at = _screen("welcome")
    at.session_state["suppswipe_scan_history"] = [ENTRY, ENTRY_NEWER]
    at.run()
    at.button(key=BAR["scans"]).click().run()
    text = " ".join(m.value for m in at.markdown)
    assert text.index("2 Oct 2026") < text.index("1 Oct 2026")  # the date only: the stamp is the server's clock
    assert "2026-10-02" not in text and "18:30" not in text
    assert "Guava" in text and "No scans yet" not in text and "2 saved in this browser, newest first." in text
    assert "Vegan" in text  # a diet other than "no restriction" is shown
    at.button(key="swipe_clear_history").click().run()  # asks first: nothing is deleted yet
    assert at.session_state["suppswipe_scan_history"] == [ENTRY, ENTRY_NEWER]
    assert "Delete all 2 saved scans?" in " ".join(m.value for m in at.markdown)
    at.button(key="swipe_clear_history_cancel").click().run()
    assert at.session_state["suppswipe_scan_history"] == [ENTRY, ENTRY_NEWER] and "swipe_clear_history" in [b.key for b in at.button]
    at.button(key="swipe_clear_history").click().run()
    at.button(key="swipe_clear_history_confirm").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["suppswipe_scan_history"] == []
    assert _titles(at) == [TITLES["scans"]] and "No scans yet" in _text(at) + " ".join(m.value for m in at.markdown)  # stays open, now empty
    assert "swipe_clear_history" not in [b.key for b in at.button]  # nothing left to clear


def test_more_than_ten_scans_show_the_newest_ten_and_a_button_for_the_rest():
    at = _screen("welcome")
    at.session_state["suppswipe_scan_history"] = [dict(ENTRY, ts=f"2026-09-{d:02d} 10:00") for d in range(1, 13)]
    at.run()
    at.button(key=BAR["scans"]).click().run()
    body = " ".join(m.value for m in at.markdown)
    assert body.count("class='sc-card'") == 10 and "12 Sep 2026" in body and not re.search(r"(?<!\d)[12] Sep 2026", body)
    older = at.button(key="swipe_scans_older")
    assert older.label == "Show 2 older scans"
    older.click().run()
    assert " ".join(m.value for m in at.markdown).count("class='sc-card'") == 12 and "swipe_scans_older" not in [b.key for b in at.button]


def test_a_damaged_history_entry_does_not_break_the_sheet(sw):
    at = _screen("welcome")
    at.session_state["suppswipe_scan_history"] = [{"ts": "garbage", "kept": "x", "replaced": [{"component": "<b>x</b>"}]}, {}]
    at.run()
    at.button(key=BAR["scans"]).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "&lt;b&gt;x&lt;/b&gt;" in " ".join(m.value for m in at.markdown)  # escaped, never markup


def test_the_empty_state_button_starts_a_scan_like_the_page_button():
    at = _screen("welcome")
    at.button(key=BAR["scans"]).click().run()
    at.button(key="swipe_scans_cta").click().run()
    assert _titles(at) == ["Analyze my supplement"] and not at.session_state.get(SHEET)
    at = _screen("cards")
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    card = at.session_state["swipe_cards"][0]
    at.session_state[key] = {"dir": "left", "id": "s1", "card": card["component_key"], "index": 0}
    at.run()
    at.button(key=BAR["scans"]).click().run()
    at.button(key="swipe_scans_cta").click().run()
    assert _titles(at) == ["Start over?"]  # mid-scan: the same question as the page button


def test_history_key_exists_without_opening_the_sheet():
    """The popover drew its body (and so created the key) on every run; test_ux_audit_fixes reads the key straight off
    session state after a sample scan."""
    at = _screen("cards")
    assert at.session_state["suppswipe_scan_history"] == []


# ------------------------------------------------------------------ one dialog at a time
def test_one_dialog_at_a_time_and_the_older_dialogs_win():
    at = _screen("results")
    at.session_state[SHEET] = "guide"
    at.session_state["swipe_open_analyze"] = True
    at.run()
    assert not at.exception, [e.value for e in at.exception]  # a second st.dialog in one run raises
    assert _titles(at) == ["Analyze my supplement"]
    at.session_state["swipe_open_analyze"] = False
    at.session_state["swipe_confirm_restart"] = True
    at.run()
    assert _titles(at) == ["Start over?"]
    at.session_state["swipe_confirm_restart"] = False
    at.session_state["swipe_plan_item"] = {"kind": "keep", "key": at.session_state["swipe_cards"][0]["component_key"]}
    at.run()
    assert not at.exception and len(_titles(at)) == 1 and _titles(at) != [TITLES["guide"]]
    at.session_state["swipe_plan_item"] = None
    at.run()
    assert _titles(at) == [TITLES["guide"]]  # nothing else requested: the sheet comes back


def test_a_bar_tap_drops_an_older_unanswered_dialog_request():
    at = _screen("results")
    at.session_state["swipe_open_analyze"] = True  # stale: the dialog was never answered
    at.session_state["swipe_plan_item"] = None
    at.button(key=BAR["about"]).click().run()
    assert _titles(at) == [TITLES["about"]] and at.session_state["swipe_open_analyze"] is False


def test_an_unknown_sheet_flag_opens_nothing_and_does_not_break():
    at = _screen("welcome")
    at.session_state[SHEET] = "no such sheet"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _titles(at) == [] and at.session_state[SHEET] is None


# ------------------------------------------------------------------ state survives Start over
def test_history_and_the_bar_survive_scan_another_and_start_over():
    at = _screen("results")
    assert len(at.session_state["suppswipe_scan_history"]) == 1  # the finished own scan was recorded
    at.button(key="swipe_analyze_btn").click().run()  # "Scan another supplement": no confirmation after a finished plan
    assert _titles(at) == ["Analyze my supplement"] and not at.session_state.get(SHEET)
    assert [b.key for b in _bar(at)] == list(BAR.values())
    at.session_state["swipe_open_analyze"] = False
    at.run()
    assert len(at.session_state["suppswipe_scan_history"]) == 1
    at.button(key=BAR["scans"]).click().run()
    assert _titles(at) == [TITLES["scans"]] and "Zinc" in " ".join(m.value for m in at.markdown)  # the earlier scan is listed


def test_start_over_in_the_middle_of_a_scan_keeps_history_and_closes_no_bar():
    at = _screen("cards")
    at.session_state["suppswipe_scan_history"] = [ENTRY]
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    card = at.session_state["swipe_cards"][0]
    at.session_state[key] = {"dir": "left", "id": "s1", "card": card["component_key"], "index": 0}
    at.run()
    at.button(key="swipe_analyze_btn").click().run()  # mid-scan: asks first
    assert _titles(at) == ["Start over?"]
    at.button(key="swipe_restart_confirm").click().run()
    assert _titles(at) == ["Analyze my supplement"]
    assert at.session_state["suppswipe_scan_history"] == [ENTRY]
    assert [b.key for b in _bar(at)] == list(BAR.values())


def test_start_over_forgets_the_sheet_state_but_not_history_or_diet(sw, monkeypatch):
    state = {SHEET: "about", "swipe_guide_focus": "zinc", "swipe_scans_confirm": True, "swipe_last_guide_id": "g1", "swipe_cards": [1],
             "suppswipe_scan_history": [ENTRY], "swipe_diet_profile_id": "vegan"}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._reset_swipe_state()
    assert not state.get(SHEET) and "swipe_guide_focus" not in state and "swipe_scans_confirm" not in state
    assert state["suppswipe_scan_history"] == [ENTRY] and state["swipe_diet_profile_id"] == "vegan"


# ------------------------------------------------------------------ the item dialog no longer carries the table
def test_the_item_dialog_keeps_its_own_targets_and_points_to_the_guide():
    at = _screen("results")
    key = next(b.key for b in at.button if str(b.key).startswith("planbtn_keep_"))
    at.button(key=key).click().run()
    assert len(at.get("dialog")) == 1
    assert len(at.table) == 0  # no 31-row table inside a window that is about ONE item
    assert any("athlete target" in m.value and "adult RDA" in m.value for m in at.markdown)
    assert any("Guide" in c.value for c in at.caption)  # the pointer
    assert [e.label for e in at.expander] == ["🏃 Athlete targets"]
    assert "plandlg_guide" in [b.key for b in at.button]


def test_open_sheet_does_nothing_while_an_analysis_is_in_flight(sw, monkeypatch):
    """The busy buttons are disabled; the callback also refuses (a forged click must not set a flag that survives the run)."""
    state = {"swipe_is_analyzing": True, "swipe_pending_request": {"manual": "x"}, "swipe_open_analyze": True}
    monkeypatch.setattr(sw.st, "session_state", state)
    assert sw._analysis_in_flight()
    sw._open_sheet("guide")
    assert SHEET not in state and state["swipe_open_analyze"] is True
    state["swipe_is_analyzing"] = False  # not analysing any more: the same call opens the sheet and drops the older request
    assert not sw._analysis_in_flight()
    sw._open_sheet("guide")
    assert state[SHEET] == "guide" and state["swipe_open_analyze"] is False


# ------------------------------------------------------------------ analysing
def test_the_bar_is_enabled_again_after_a_failed_analysis():
    """While an analysis runs the bar is drawn dead (a tap would interrupt the run and start the OCR again). When the run ends
    in an error nothing reruns the page, so the live bar has to take its place."""
    at = _screen("error")
    assert at.error and not any(b.disabled for b in _bar(at))
    assert [b.key for b in _bar(at)] == list(BAR.values())  # the live keys, not the busy twins


def test_the_busy_bar_is_dead_and_uses_its_own_keys(sw):
    src = Path(APP).read_text(encoding="utf-8")
    assert 'prefix = "appbar_busy" if busy else "appbar"' in src
    assert re.search(r"if busy:\s+st\.button\(label, key=f\"\{prefix\}_\{name\}\", width=\"stretch\", disabled=True\)", src)


# ------------------------------------------------------------------ a tap on the athlete line of a card
def test_a_tap_on_the_athlete_line_opens_the_guide_at_that_nutrient_and_changes_nothing_else():
    at = _screen("cards")
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    card = at.session_state["swipe_cards"][0]
    at.session_state[key] = {"kind": "guide", "id": "g1-1", "card": card["component_key"], "index": 0, "nutrient": card["component_key"]}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _titles(at) == [TITLES["guide"]] and at.session_state["swipe_guide_focus"] == card["component_key"]
    assert at.session_state["swipe_index"] == 0 and at.session_state["swipe_decisions"] == {}
    assert at.session_state.get("swipe_last_swipe_id", "") == ""  # never mistaken for a swipe
    # The same value arriving again (every rerun re-reads it) does not reopen a dismissed sheet.
    at.session_state[SHEET] = None
    at.run()
    assert _titles(at) == []


@pytest.mark.parametrize("value", [
    {"kind": "guide", "id": "g2", "card": "some other card", "index": 0},      # made on another card
    {"kind": "guide", "id": "g3", "card": None, "index": 7},                   # stale index
    {"kind": "guide", "id": "", "card": "x", "index": 0},                      # no id
    {"kind": "guide"},
])
def test_a_stale_or_damaged_guide_tap_does_nothing(value):
    at = _screen("cards")
    at.session_state["tinder_" + str(at.session_state["swipe_reset_nonce"])] = value
    at.run()
    assert not at.exception and _titles(at) == [] and at.session_state["swipe_index"] == 0


def test_a_swipe_still_works_after_a_guide_tap():
    at = _screen("cards")
    key = "tinder_" + str(at.session_state["swipe_reset_nonce"])
    card = at.session_state["swipe_cards"][0]
    at.session_state[key] = {"kind": "guide", "id": "g1-1", "card": card["component_key"], "index": 0}
    at.run()
    at.session_state[SHEET] = None
    at.session_state[key] = {"dir": "left", "id": "s1", "card": card["component_key"], "index": 0}
    at.run()
    assert at.session_state["swipe_index"] == 1 and card["component_key"] in at.session_state["swipe_decisions"]


def test_the_component_sends_the_guide_event_as_its_own_kind_and_keeps_drags_apart_from_taps():
    src = (Path(APP).parent / "swipe_component" / "index.html").read_text(encoding="utf-8")
    assert '<button type="button" class="pl pl-guide" data-guide="1">' in src and "Opens the athlete guide." in src
    assert 'kind: "guide"' in src and "if (committed) return;" in src  # nothing while a swipe is on its way
    assert "e.detail > 0 && movedBeyondTap" in src  # a pointer click after a drag is no tap; a keyboard Enter (detail 0) still works
    assert "if (!downOnGuide)" in src  # the capture waits on the line, so a tap reaches the button and a drag still swipes
    assert "outline: 3px solid #1d4ed8" in src and "min-height: 32px" in src  # a visible focus ring and a target of 32 px
    assert "Math.max(HEIGHT, Math.min(780, contentHeight + actionsHeight + 10), maxHeight)" in src  # the frame rule is untouched


def test_a_guide_value_never_reaches_the_swipe_handler(sw, monkeypatch):
    seen = []
    monkeypatch.setattr(sw, "_apply_card_swipe", lambda state, value: seen.append(value))
    state = {"swipe_cards": [{"component_key": "zinc"}], "swipe_index": 0}
    for value in ({"kind": "guide", "id": "g1", "card": "zinc", "index": 0}, {"kind": "guide", "id": ""}, {"kind": "guide"}):
        assert sw._apply_card_guide_tap(state, value) is True
    assert sw._apply_card_guide_tap(state, {"dir": "left", "id": "s1", "card": "zinc", "index": 0}) is False  # a swipe is the swipe handler's
    assert sw._apply_card_guide_tap(state, None) is False and seen == []
    assert state["swipe_sheet"] == "guide" and state["swipe_guide_focus"] == "zinc" and state["swipe_index"] == 0
    assert "swipe_decisions" not in state and "swipe_last_swipe_id" not in state


# ------------------------------------------------------------------ keyboard and screen readers
@pytest.mark.parametrize("sheet", list(BAR))
def test_each_sheet_has_a_keyboard_stop_inside_its_scroller(sheet):
    """The dialog itself holds the focus and the body scrolls inside it: without a focusable block in the body, arrow keys and
    PageDown scroll nothing (the browser check presses the keys)."""
    at = _screen("welcome")
    at.session_state["suppswipe_scan_history"] = [ENTRY]
    at.run()
    at.button(key=BAR[sheet]).click().run()
    assert not at.exception, [e.value for e in at.exception]
    blocks = [m.value for m in at.markdown if "tabindex='0'" in m.value]
    assert len(blocks) == 1, blocks  # one stop, not one per row
    if sheet != "about":
        assert f"role='region' aria-label='{TITLES[sheet]} content'" in blocks[0]
    else:
        assert "role='heading'" in blocks[0] and "Not medical advice" in blocks[0]


def test_the_clear_history_question_is_an_alert_that_names_what_is_deleted():
    at = _screen("welcome")
    at.session_state["suppswipe_scan_history"] = [ENTRY, ENTRY_NEWER]
    at.run()
    at.button(key=BAR["scans"]).click().run()
    at.button(key="swipe_clear_history").click().run()
    asks = [m.value for m in at.markdown if "<div class='sc-ask'" in m.value]
    assert len(asks) == 1 and "role='alert'" in asks[0] and "Delete all 2 saved scans?" in asks[0]
    assert "aria-label='Confirm'" not in asks[0]  # a group called "Confirm" said nothing


def test_the_live_bar_is_a_plain_container_and_only_the_busy_twin_uses_a_slot(sw):
    """An st.empty() slot sends an empty element first on every run: the browser sometimes paints it and the bar remounts."""
    import inspect

    src = inspect.getsource(sw._build_mobile_ui)
    assert src.count("st.empty()") == 1 and src.index("st.empty()") > src.index("if _analysis_in_flight():")
    assert "    _render_app_bar()\n" in src  # outside the if: every run
    assert "slot if slot is not None else st" in inspect.getsource(sw._render_app_bar)


def test_the_bar_rules_for_typing_and_for_a_running_answer_start_at_the_body():
    """A popover's body is mounted outside the app root and outside the main block: its field and its spinner are invisible to a
    selector scoped to either. And only controls that open a keyboard hide the bar (a toggle, a radio, a dropdown keep focus)."""
    css = Path(APP).read_text(encoding="utf-8")
    rules = [ln.strip() for ln in css.splitlines() if 'st-key-appbar"]' in ln and ("input" in ln or "stSpinner" in ln) and ln.strip().startswith(("body", "[data-testid"))]
    assert rules and all(r.startswith("body:has(") for r in rules), rules
    typing = next(r for r in rules if "focus" in r)
    assert "textarea:focus" in typing and ':not([role="combobox"])' in typing and "checkbox" not in typing and "radio" not in typing
    assert "input:focus" not in typing

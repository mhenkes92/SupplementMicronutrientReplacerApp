"""Bar v2: the bottom bar is Guide | Diet | Scan | Recent | About. Scan opens the way to start (Analyze / Resume / Sample, or a question
first when a scan or a plan is on screen), Diet opens the dietary filter and the pregnancy toggle (they are on no page any more), and
the welcome screen is the hero card, one hint and the bar.

AppTest reruns the whole script on every click (no fragment reruns, no widget remounts): what only a browser can prove (the geometry,
Resume + the Diet sheet in one real session, the sheets' size, focus) is in tests/test_ux_app_bar_browser.py and test_ux_bar_v2_browser.py."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "swipe_mobile_app" / "app.py")
OWN_LABEL = "Vitamin C 80 mg 100%\nZinc 10 mg 100%\nSelenium 55 µg 100%"
BAR_KEYS = ["appbar_guide", "appbar_diet", "appbar_scan", "appbar_scans", "appbar_about"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("SUPPSWIPE_PREFETCH_MEALS", "0")


@pytest.fixture(scope="module")
def sample_cards(sw):
    import blockbrain.app as bb

    return sw._build_swipe_cards(sw._filter_to_micronutrients(bb.parse_components(sw._SAMPLE_LABEL_TEXT)), [])


def _titles(at: AppTest) -> list[str]:
    return [d.proto.dialog.title for d in at.get("dialog")]


def _page_buttons(at: AppTest) -> list[str]:
    """Every button that is not a bar item: with no dialog open that is the page itself."""
    return [str(b.key) for b in at.button if not str(b.key).startswith("appbar")]


def _header(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if "class=\"brand\"" in m.value)


def _chip(at: AppTest) -> str:
    """The text of the one-line diet chip under the brand ('' when there is none)."""
    import re

    match = re.search(r"<div class='diet-note'><span aria-hidden='true'>🥗</span> (.*?)</div>", _header(at))
    return match.group(1) if match else ""


def _has_footer(at: AppTest) -> bool:
    return any("<div class='brand-foot'>" in m.value for m in at.markdown)


def _diet_chips(at: AppTest) -> list:
    return [g for g in at.get("button_group") if g.key == "swipe_diet_pills"]


def _analyse(at: AppTest, manual: str = OWN_LABEL) -> None:
    at.session_state["swipe_pending_request"] = {"upload_bytes": b"", "camera_bytes": b"", "camera_barcode": "", "manual": manual}
    at.session_state["swipe_is_analyzing"] = True
    at.session_state["swipe_analysis_kicked"] = True
    at.session_state["swipe_last_auto_signature"] = "own-label"  # what a real input records: the sample label is not saved
    at.run()


def _swipe(at: AppTest, index: int, direction: str = "left") -> None:
    card = at.session_state["swipe_cards"][index]
    at.session_state["tinder_" + str(at.session_state["swipe_reset_nonce"])] = {
        "dir": direction, "id": f"s{index}", "card": card["component_key"], "index": index}
    at.run()


def _screen(name: str, **state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    if name == "cards":
        at.button(key="appbar_scan").click().run()
        at.button(key="swipe_try_sample").click().run()
    elif name == "midscan":
        at = _screen("cards", **state)
        _swipe(at, 0)
    elif name == "results":
        _analyse(at)
        for i in range(len(at.session_state["swipe_cards"])):
            _swipe(at, i)
        assert at.session_state["swipe_index"] == len(at.session_state["swipe_cards"])
    elif name == "sample_results":
        at = _screen("cards", **state)
        for i in range(len(at.session_state["swipe_cards"])):
            _swipe(at, i)
    assert not at.exception, [e.value for e in at.exception]
    return at


def _saved(sw, cards, **extra):
    """A saved scan as the browser would hand it back (two decisions, on the third card)."""
    food = cards[0]["foods"][1]
    state = {
        "swipe_cards": cards, "swipe_analysis_text": sw._SAMPLE_LABEL_TEXT, "swipe_index": 2,
        "swipe_label_source": {"kind": "input", "url": ""},
        "swipe_decisions": {
            cards[0]["component_key"]: sw._decision_record(cards[0], 0, "replace", food),
            cards[1]["component_key"]: sw._decision_record(cards[1], 1, "keep", None),
        },
    }
    state.update(extra)
    return sw._scan_snapshot(state, now=time.time())


# ------------------------------------------------------------------ the bar: five items, one order
def test_the_bar_has_five_items_in_this_order_and_the_history_item_is_called_recent(sw):
    assert [(name, label) for name, label in sw._BAR_ITEMS] == [
        ("guide", "Guide"), ("diet", "Diet"), ("scan", "Scan"), ("scans", "Recent"), ("about", "About")]
    assert set(sw._SHEET_TITLES) == {name for name, _ in sw._BAR_ITEMS}
    assert sw._SHEET_TITLES["scans"] == "Recent scans"  # the item is "Recent", its window keeps the title
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert [b.key for b in at.button] == BAR_KEYS and [b.label for b in at.button] == ["Guide", "Diet", "Scan", "Recent", "About"]


def test_scan_is_the_primary_item_in_the_css_and_every_item_has_an_icon_for_the_live_and_the_dead_bar(sw):
    src = Path(APP).read_text(encoding="utf-8")
    for name, _label in sw._BAR_ITEMS:
        assert f'st-key-appbar_{name}"], [class~="st-key-appbar_busy_{name}"] {{ --ss-ico: var(--ss-ico-{name}); }}' in src
        assert f"--ss-ico-{name}:" in src
    assert 'st-key-appbar_scan"] button' in src and "background: #047857" in src  # filled, white on green
    assert 'st-key-appbar_busy_scan"] button' in src  # and the dead twin looks the same


def test_the_dead_twin_has_five_disabled_buttons_with_their_own_keys_and_the_live_bar_five_live_ones(sw, monkeypatch):
    monkeypatch.setitem(sys.modules, "suppswipe_app", sw)  # the scripts below draw the bar through the module the tests already hold

    def busy_script():
        import sys

        sys.modules["suppswipe_app"]._render_app_bar(None, True)

    def live_script():
        import sys

        sys.modules["suppswipe_app"]._render_app_bar()

    dead = AppTest.from_function(busy_script).run()
    assert not dead.exception, [e.value for e in dead.exception]
    assert [b.key for b in dead.button] == [k.replace("appbar_", "appbar_busy_") for k in BAR_KEYS]
    assert all(b.disabled for b in dead.button) and len(dead.button) == 5
    live = AppTest.from_function(live_script).run()
    assert [b.key for b in live.button] == BAR_KEYS and not any(b.disabled for b in live.button)


# ------------------------------------------------------------------ the welcome screen: hero, one hint, the disclaimer, the bar
def test_the_welcome_screen_has_no_button_chip_toggle_or_footer_of_its_own():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert _page_buttons(at) == []
    assert not at.get("button_group") and not at.toggle and not at.expander
    text = " ".join(m.value for m in at.markdown)
    assert "class='hero'" in text and "Tap <b>Scan</b> below to start" in text  # the one hint, inside the card
    assert any("not medical advice" in c.value for c in at.caption)  # and the disclaimer
    assert not _has_footer(at)  # the © line is in About


@pytest.mark.parametrize("screen", ["welcome", "midscan", "results"])
def test_no_filter_chips_toggle_settings_or_old_buttons_on_any_page(screen):
    at = AppTest.from_file(APP, default_timeout=60).run() if screen == "welcome" else _screen(screen)
    assert not _diet_chips(at)
    assert not [t for t in at.toggle if "Pregnant" in t.label]
    assert not [e for e in at.expander if "Diet" in e.label]  # the results' settings expander is gone
    assert not {"swipe_try_sample", "swipe_resume_scan", "swipe_scan_analyze"} & set(_page_buttons(at))
    assert ("swipe_analyze_btn" in _page_buttons(at)) == (screen == "results")  # the primary Scan button ends a finished plan, only there


def test_the_footer_stays_on_the_card_and_results_screens_and_the_about_sheet_carries_it_too(sw):
    for screen in ("midscan", "results"):
        at = _screen(screen)
        assert _has_footer(at), screen
    at.button(key="appbar_about").click().run()
    assert f"© mfitness92 · Build {sw.BUILD_TAG}" in " ".join(m.value for m in at.markdown)


# ------------------------------------------------------------------ the Scan item: a sheet on the welcome screen
def test_scan_sheet_has_analyze_and_sample_and_resume_only_with_a_saved_scan_that_is_less_than_a_week_old(sw, sample_cards):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key="appbar_scan").click().run()
    assert _titles(at) == ["Scan a supplement"]
    assert _page_buttons(at) == ["swipe_scan_analyze", "swipe_try_sample"]
    assert [b.label for b in at.button if b.key in ("swipe_scan_analyze", "swipe_try_sample")] == ["Analyze my supplement", "Try with a sample label"]
    assert at.button(key="swipe_scan_analyze").proto.type == "primary"  # the main way in
    # A saved scan adds the Resume row between the two, and says what it brings back.
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = _saved(sw, sample_cards, swipe_diet_profile_id="vegan", swipe_pregnant=True)
    at.run()
    at.button(key="appbar_scan").click().run()
    assert _page_buttons(at) == ["swipe_scan_analyze", "swipe_resume_scan", "swipe_try_sample"]
    assert at.button(key="swipe_resume_scan").label == "Resume last scan"
    captions = [c.value for c in at.caption]
    assert f"2 of {len(sample_cards)} cards done · saved today" in captions
    assert "That scan used: Vegan · Pregnancy" in captions  # what it will run with is said before the tap
    assert "Starting a new scan replaces the one you can resume." in captions
    # Too old: not offered.
    old = _saved(sw, sample_cards)
    old["ts"] = time.time() - 8 * 24 * 3600
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = old
    at.run()
    at.button(key="appbar_scan").click().run()
    assert _page_buttons(at) == ["swipe_scan_analyze", "swipe_try_sample"]


def test_a_saved_scan_without_a_filter_says_nothing_about_one(sw, sample_cards):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = _saved(sw, sample_cards)
    at.run()
    at.button(key="appbar_scan").click().run()
    assert not [c for c in at.caption if c.value.startswith("That scan used")]


def test_a_saved_sample_scan_says_so(sw, sample_cards):
    saved = _saved(sw, sample_cards)
    saved["label_source"] = {"kind": "sample", "url": ""}
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = saved
    at.run()
    at.button(key="appbar_scan").click().run()
    assert f"Sample label · 2 of {len(sample_cards)} cards done · saved today" in [c.value for c in at.caption]


def test_analyze_closes_the_sheet_and_opens_the_analyze_window():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_scan_analyze").click().run()
    assert _titles(at) == ["Analyze my supplement"] and at.session_state["swipe_sheet"] is None


def test_the_sample_starts_the_sample_and_closes_the_sheet():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_try_sample").click().run()
    assert not at.exception and _titles(at) == [] and at.session_state["swipe_label_source"]["kind"] == "sample"
    assert at.session_state["swipe_cards"] and at.session_state["swipe_sheet"] is None and at.session_state["swipe_index"] == 0


def test_the_sample_on_cards_nobody_decided_on_replaces_them_instead_of_doing_nothing():
    at = _screen("cards")  # the sample is on screen, no decision yet
    first = [c["component_key"] for c in at.session_state["swipe_cards"]]
    at.button(key="appbar_scan").click().run()
    assert _titles(at) == ["Scan a supplement"]  # nothing to lose: the sheet, not a question
    assert "swipe_resume_scan" not in _page_buttons(at)  # the cards on screen are the saved scan
    assert any("It replaces the cards on screen." in c.value for c in at.caption)
    at.button(key="swipe_try_sample").click().run()
    assert not at.exception and _titles(at) == [] and at.session_state["swipe_sheet"] is None
    assert [c["component_key"] for c in at.session_state["swipe_cards"]] == first and at.session_state["swipe_index"] == 0


# ------------------------------------------------------------------ Resume
def test_resume_from_the_sheet_restores_the_scan_closes_the_sheet_and_the_page_says_the_filter(sw, sample_cards):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = _saved(sw, sample_cards, swipe_diet_profile_id="vegetarian", swipe_pregnant=True)
    at.run()
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_resume_scan").click().run()
    assert not at.exception and _titles(at) == [] and at.session_state["swipe_sheet"] is None
    assert at.session_state["swipe_index"] == 2 and len(at.session_state["swipe_decisions"]) == 2
    assert at.session_state["swipe_diet_profile_id"] == "vegetarian" and at.session_state["swipe_pregnant"] is True
    assert _chip(at) == "Diet: Vegetarian · Pregnancy"  # said in words on the page at once
    assert ["Resumed with the filter from that scan: Vegetarian · Pregnancy."] == [t.value for t in at.toast]


def test_resume_never_switches_a_filter_off_and_says_when_the_scan_was_saved_with_another(sw, sample_cards):
    """The visitor set Nut-free, then tapped Resume on a scan saved with no filter: the filter stays, and the page says what the scan
    was saved with."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = _saved(sw, sample_cards)  # saved with no filter
    at.session_state["swipe_diet_profile_id"] = "nut free"
    at.run()
    assert _chip(at) == "Diet: Nut-free"
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_resume_scan").click().run()
    assert at.session_state["swipe_diet_profile_id"] == "nut free" and _chip(at) == "Diet: Nut-free"
    assert ["Resumed with your filter: Nut-free. That scan was saved with: no filter."] == [t.value for t in at.toast]


def test_restore_scan_merges_the_filter_instead_of_overwriting_it(sw, sample_cards):
    saved = _saved(sw, sample_cards, swipe_diet_profile_id="vegan", swipe_pregnant=False)
    # no filter now: the scan's own applies
    state: dict = {}
    assert sw._restore_scan(state, saved) and state["swipe_diet_profile_id"] == "vegan" and state["swipe_pregnant"] is False
    # a filter now: it stays, whatever the scan was saved with
    state = {"swipe_diet_profile_id": "gluten free", "swipe_pregnant": True}
    assert sw._restore_scan(state, saved)
    assert state["swipe_diet_profile_id"] == "gluten free" and state["swipe_pregnant"] is True  # pregnancy is on if either side has it
    # pregnancy saved, not now: on
    state = {"swipe_diet_profile_id": "none", "swipe_pregnant": False}
    assert sw._restore_scan(state, dict(saved, pregnant=True)) and state["swipe_pregnant"] is True
    # the widgets of the Diet sheet are left to the sheet: a stale value would be ignored there, or worse, shown instead of the filter
    state = {"swipe_diet_pills": "none", "swipe_pregnant_toggle": False}
    assert sw._restore_scan(state, saved) and "swipe_diet_pills" not in state and "swipe_pregnant_toggle" not in state
    # a damaged pregnancy value is not "true"
    state = {}
    assert sw._restore_scan(state, dict(saved, pregnant="false")) and state["swipe_pregnant"] is False


def test_the_resume_note_names_both_filters_only_when_they_differ(sw, sample_cards):
    saved = _saved(sw, sample_cards, swipe_diet_profile_id="vegan")
    assert sw._resume_filter_note(saved, {"swipe_diet_profile_id": "vegan", "swipe_pregnant": False}) == "Resumed with the filter from that scan: Vegan."
    assert sw._resume_filter_note(saved, {"swipe_diet_profile_id": "nut free", "swipe_pregnant": True}) == (
        "Resumed with your filter: Nut-free · Pregnancy. That scan was saved with: Vegan.")
    plain = _saved(sw, sample_cards)
    assert sw._resume_filter_note(plain, {"swipe_diet_profile_id": "none", "swipe_pregnant": False}) == ""  # nothing to say
    assert sw._resume_filter_note(plain, {"swipe_diet_profile_id": "none", "swipe_pregnant": True}).endswith("saved with: no filter.")


def test_a_failed_resume_says_so_inside_the_sheet_keeps_it_open_and_does_not_offer_the_scan_again(sw, sample_cards):
    bad = _saved(sw, sample_cards)
    bad["text"] = "hello world no nutrients here"
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["_suppswipe_saved_scan"] = bad
    at.run()
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_resume_scan").click().run()
    assert _titles(at) == ["Scan a supplement"] and not at.session_state["swipe_cards"]
    alerts = [m.value for m in at.markdown if "role='alert'" in m.value]
    assert len(alerts) == 1 and "Couldn't restore your last scan." in alerts[0] and "scan the label again" in alerts[0]
    assert at.session_state["_suppswipe_saved_scan"] is None  # forgotten: not offered again
    assert not at.toast


# ------------------------------------------------------------------ Scan while a scan is going, and on the results
def test_scan_mid_scan_asks_first_with_the_start_over_question():
    at = _screen("midscan")
    cards = at.session_state["swipe_cards"]
    at.button(key="appbar_scan").click().run()
    assert _titles(at) == ["Start over?"] and at.session_state["swipe_sheet"] is None  # no sheet: Resume and Sample make no sense now
    assert at.session_state["swipe_cards"] == cards and at.session_state["swipe_index"] == 1  # nothing touched yet
    at.button(key="swipe_restart_cancel").click().run()
    assert _titles(at) == [] and at.session_state["swipe_index"] == 1 and len(at.session_state["swipe_decisions"]) == 1
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_restart_confirm").click().run()
    assert _titles(at) == ["Analyze my supplement"] and not at.session_state["swipe_cards"]  # the same as the page button always did


def test_scan_on_the_results_asks_first_and_names_what_stays(sw):
    at = _screen("results")
    decisions = dict(at.session_state["swipe_decisions"])
    at.button(key="appbar_scan").click().run()
    assert _titles(at) == ["Scan another supplement?"] and at.session_state["swipe_sheet"] is None
    body = " ".join(m.value for m in at.markdown)
    assert "This clears the plan on screen. Your choices stay in Recent scans." in body
    assert at.session_state["swipe_decisions"] == decisions  # asking changes nothing
    at.button(key="swipe_scan_another_cancel").click().run()
    assert _titles(at) == [] and at.session_state["swipe_decisions"] == decisions and at.session_state["swipe_index"] == len(decisions)
    at.button(key="appbar_scan").click().run()
    at.button(key="swipe_scan_another_confirm").click().run()
    assert not at.exception and _titles(at) == ["Analyze my supplement"]
    assert not at.session_state["swipe_cards"] and at.session_state["_suppswipe_saved_scan"] is None  # what the page's own button does
    assert len(at.session_state["suppswipe_scan_history"]) == 1  # the finished scan is in Recent scans


def test_scan_on_a_finished_sample_says_the_sample_is_not_saved():
    at = _screen("sample_results")
    assert at.session_state["swipe_index"] == len(at.session_state["swipe_cards"])
    at.button(key="appbar_scan").click().run()
    assert _titles(at) == ["Scan another supplement?"]
    assert "This clears the sample plan on screen. The sample is not saved." in " ".join(m.value for m in at.markdown)
    assert at.session_state["suppswipe_scan_history"] == []


def test_the_pages_own_scan_button_on_the_results_still_starts_at_once():
    at = _screen("results")
    at.button(key="swipe_analyze_btn").click().run()
    assert _titles(at) == ["Analyze my supplement"] and not at.session_state["swipe_cards"]  # no question: unchanged


def test_scan_does_nothing_while_an_analysis_runs(sw, monkeypatch):
    state = {"swipe_is_analyzing": True, "swipe_pending_request": {"manual": "x"}}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._tap_scan()
    assert "swipe_sheet" not in state and "swipe_confirm_restart" not in state


def test_a_scan_tap_drops_an_older_unanswered_request_and_sets_exactly_one(sw, monkeypatch):
    state = {"swipe_open_analyze": True, "swipe_confirm_restart": False, "swipe_plan_item": {"kind": "keep", "key": "x"}, "swipe_cards": []}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._tap_scan()
    assert state["swipe_sheet"] == "scan" and state["swipe_open_analyze"] is False and state["swipe_plan_item"] is None
    assert not state["swipe_confirm_restart"]


# ------------------------------------------------------------------ the Diet item and its sheet
@pytest.mark.parametrize("screen", ["welcome", "midscan", "results"])
def test_the_diet_sheet_draws_the_chips_and_the_toggle_exactly_once_on_every_screen(screen):
    at = AppTest.from_file(APP, default_timeout=60).run() if screen == "welcome" else _screen(screen)
    at.button(key="appbar_diet").click().run()
    assert not at.exception, [e.value for e in at.exception]  # a second widget with the same key would raise DuplicateWidgetID here
    assert _titles(at) == ["Diet & pregnancy"]
    assert [g.key for g in _diet_chips(at)] == ["swipe_diet_pills"]
    assert [t.label for t in at.toggle] == ["🤰 Pregnant or breastfeeding"]
    captions = " ".join(c.value for c in at.caption)
    assert "Hides liver and other organ meats" in captions  # the toggle's explanation is text, not a tooltip
    assert at.toggle[0].proto.help == ""
    assert ("Your choices so far are kept." in captions) == (screen in ("midscan", "results"))  # only when there is something decided


def test_the_diet_sheet_says_what_the_chosen_filter_does_in_the_profiles_own_words(sw):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key="appbar_diet").click().run()
    effect = [m.value for m in at.markdown if "class='diet-effect'" in m.value]
    assert len(effect) == 1 and "No restriction." in effect[0] and "No foods are screened out." in effect[0]
    _diet_chips(at)[0].set_value("nut free")
    at.run()
    effect = [m.value for m in at.markdown if "class='diet-effect'" in m.value]
    assert "<b>Nut-free.</b> Screens out tree nuts" in effect[0] and "Coconut" in effect[0]
    fine = " ".join(m.value for m in at.markdown if "diet-fine" in m.value)
    assert "not a certification or an allergy safeguard" in fine


def test_a_chip_and_the_toggle_in_the_sheet_reach_the_cards_and_the_mirrors_like_the_page_chips_did():
    at = _screen("midscan")
    at.button(key="appbar_diet").click().run()
    _diet_chips(at)[0].set_value("vegan")
    at.run()
    assert at.session_state["swipe_diet_profile_id"] == "vegan"
    next(t for t in at.toggle if "Pregnant" in t.label).set_value(True)
    at.run()
    assert at.session_state["swipe_pregnant"] is True
    at.button(key="swipe_diet_done").click().run()  # Done closes the sheet and the page follows
    assert _titles(at) == [] and at.session_state["swipe_sheet"] is None
    assert "Filter: Vegan" in [c.value for c in at.caption]  # the card behind (its food list is filtered)
    assert _chip(at) == "Diet: Vegan · Pregnancy"


def test_the_sheet_is_seeded_from_the_mirrors_each_time_it_opens():
    """Round trip: set a filter in the sheet, close it, open it again: the chip is still on it and the toggle too."""
    at = _screen("midscan")
    at.button(key="appbar_diet").click().run()
    _diet_chips(at)[0].set_value("vegetarian")
    next(t for t in at.toggle if "Pregnant" in t.label).set_value(True)
    at.run()
    at.button(key="swipe_diet_done").click().run()
    at.button(key="appbar_diet").click().run()
    assert _diet_chips(at)[0].value == "vegetarian"
    assert next(t for t in at.toggle if "Pregnant" in t.label).value is True
    # and a filter that arrived by another way (Resume) is what the sheet opens on, whatever the widget keys held
    at.session_state["swipe_sheet"] = None
    at.session_state["swipe_diet_profile_id"] = "gluten free"
    at.session_state["swipe_pregnant"] = False
    at.session_state["swipe_diet_pills"] = "vegetarian"  # stale
    at.session_state["swipe_pregnant_toggle"] = True  # stale
    at.run()
    at.button(key="appbar_diet").click().run()
    assert _diet_chips(at)[0].value == "gluten free"
    assert next(t for t in at.toggle if "Pregnant" in t.label).value is False


def test_opening_the_diet_sheet_drops_stale_widget_values_so_it_is_seeded_from_the_mirrors(sw, monkeypatch):
    """Streamlit applies a value written into a widget's key only in the run right after the write. Resume used to write both keys
    while the widgets were not on screen: the sheet then opened on "No restriction" / off with the filter on (seen in the browser,
    never in AppTest, which reruns everything)."""
    state = {"swipe_diet_pills": "vegan", "swipe_pregnant_toggle": True, "swipe_diet_profile_id": "vegetarian", "swipe_pregnant": True}
    monkeypatch.setattr(sw.st, "session_state", state)
    sw._open_sheet("diet")
    assert "swipe_diet_pills" not in state and "swipe_pregnant_toggle" not in state and state["swipe_sheet"] == "diet"
    assert state["swipe_diet_profile_id"] == "vegetarian" and state["swipe_pregnant"] is True  # the mirrors are what counts
    state.update(swipe_diet_pills="x", swipe_pregnant_toggle=True)
    sw._open_sheet("guide")
    assert state["swipe_diet_pills"] == "x"  # other sheets leave the widget state alone


@pytest.mark.parametrize("screen", ["welcome", "midscan", "results"])
def test_an_active_filter_or_pregnancy_is_always_said_in_words_on_the_page_and_the_diet_item_has_its_dot(screen):
    at = AppTest.from_file(APP, default_timeout=60).run() if screen == "welcome" else _screen(screen)
    assert _chip(at) == ""  # nothing to say: absence means "no restriction"
    at.session_state["swipe_diet_profile_id"] = "vegan"
    at.run()
    assert _chip(at) == "Diet: Vegan"
    at.session_state["swipe_diet_profile_id"] = "nut free"
    at.session_state["swipe_pregnant"] = True
    at.run()
    assert _chip(at) == "Diet: Nut-free · Pregnancy"
    at.session_state["swipe_diet_profile_id"] = "none"
    at.run()
    assert _chip(at) == "Pregnancy mode"
    at.session_state["swipe_pregnant"] = False
    at.run()
    assert _chip(at) == ""
    css = Path(APP).read_text(encoding="utf-8")
    assert 'body:has(.diet-note) [class~="st-key-appbar_diet"] button::after' in css  # the dot follows the chip, nothing else
    assert 'body:has(.diet-note) [class~="st-key-appbar_busy_diet"] button::after' in css  # also on the dead twin


def test_the_chip_is_drawn_before_an_analysis_so_it_shows_on_the_analysing_screen_too(sw):
    import inspect

    src = inspect.getsource(sw._build_mobile_ui)
    assert src.index("_render_header()") < src.index("if _analysis_in_flight():")  # the header (brand + chip) comes first
    assert "_diet_chip_html()" in inspect.getsource(sw._render_header)


def test_the_chip_text_is_escaped(sw, monkeypatch):
    monkeypatch.setattr(sw, "_diet_summary", lambda *_a: "<b>x</b>")
    assert "<b>x</b>" not in sw._diet_chip_html() and "&lt;b&gt;x&lt;/b&gt;" in sw._diet_chip_html()


def test_the_no_alternatives_note_points_to_the_diet_item():
    at = AppTest.from_file(APP, default_timeout=60)
    beef = {"food_description": "Beef, ground, 85% lean meat, raw", "food_category": "Beef Products", "nutrient_amount": 12.0}
    at.session_state["swipe_cards"] = [{"component": "Zzz", "component_key": "zzz unknown nutrient", "dose_label": "10 mg",
                                        "dose_value": 10, "dose_unit": "mg", "form": "", "foods": [beef]}]
    at.session_state["swipe_index"] = 0
    at.session_state["swipe_diet_profile_id"] = "vegan"
    at.run()
    captions = [c.value for c in at.caption]
    assert any("No whole-food alternatives fit the “Vegan” filter. Tap Diet in the bottom bar to change the filter." in c for c in captions), captions
    assert not any("Switch the dietary filter below" in c for c in captions)


# ------------------------------------------------------------------ one dialog at a time
def test_the_new_sheets_join_the_one_dialog_chain_and_the_older_requests_win():
    at = _screen("midscan")
    at.session_state["swipe_sheet"] = "diet"
    at.session_state["swipe_confirm_restart"] = True
    at.run()
    assert not at.exception, [e.value for e in at.exception]  # a second st.dialog in one run raises
    assert _titles(at) == ["Start over?"]
    at.session_state["swipe_confirm_restart"] = False
    at.session_state["swipe_open_analyze"] = True
    at.run()
    assert _titles(at) == ["Analyze my supplement"]
    at.session_state["swipe_open_analyze"] = False
    at.run()
    assert _titles(at) == ["Diet & pregnancy"]  # nothing else requested: the sheet comes back
    at.session_state["swipe_sheet"] = "scan"
    at.run()
    assert _titles(at) == ["Scan a supplement"]


@pytest.mark.parametrize("first", ["appbar_guide", "appbar_diet", "appbar_scans", "appbar_about"])
def test_a_second_bar_tap_swaps_the_sheet_never_stacks_two(first):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key=first).click().run()
    at.session_state["swipe_sheet"] = None  # the way out of a modal is the X: the bar behind it cannot be tapped
    at.button(key="appbar_scan").click().run()
    assert len(_titles(at)) == 1 and _titles(at) == ["Scan a supplement"]


def test_a_bar_tap_drops_the_scan_question_that_was_never_answered():
    at = _screen("results")
    at.button(key="appbar_scan").click().run()
    assert _titles(at) == ["Scan another supplement?"]
    at.button(key="appbar_diet").click().run()
    assert _titles(at) == ["Diet & pregnancy"] and not at.session_state["swipe_confirm_restart"]


def test_every_sheet_on_every_screen_leaves_the_scan_underneath_alone(sw):
    for screen in ("welcome", "midscan", "results"):
        at = AppTest.from_file(APP, default_timeout=60).run() if screen == "welcome" else _screen(screen)
        before = (at.session_state["swipe_index"], len(at.session_state["swipe_decisions"]), at.session_state["swipe_diet_profile_id"])
        for key in ("appbar_diet", "appbar_guide", "appbar_scans", "appbar_about", "appbar_scan"):
            at.session_state["swipe_sheet"] = None
            at.session_state["swipe_confirm_restart"] = False
            at.button(key=key).click().run()
            assert not at.exception, (screen, key, [e.value for e in at.exception])
            assert len(_titles(at)) == 1, (screen, key, _titles(at))
            assert (at.session_state["swipe_index"], len(at.session_state["swipe_decisions"]), at.session_state["swipe_diet_profile_id"]) == before

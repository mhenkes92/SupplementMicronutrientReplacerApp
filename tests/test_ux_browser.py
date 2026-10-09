"""Browser checks of the swipe / results / resume flow (Playwright + Chromium).

Opt-in, because they start a real Streamlit server: run with
SUPPSWIPE_BROWSER_TESTS=1 (screenshots go to SUPPSWIPE_SCREENSHOT_DIR when set).
Offline: the sample label needs no LLM and the API key is a dummy.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

if os.getenv("SUPPSWIPE_BROWSER_TESTS", "") != "1":
    pytest.skip("browser tests are opt-in (SUPPSWIPE_BROWSER_TESTS=1)", allow_module_level=True)
sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
SHOTS = os.getenv("SUPPSWIPE_SCREENSHOT_DIR", "")
CARD = 'iframe[src*="tinder_swipe"]'


def _free_port() -> int:
    """An unused local port: the first free one of SUPPSWIPE_TEST_PORT_RANGE ("9001-9009", for a shared machine), else any."""
    spec = os.getenv("SUPPSWIPE_TEST_PORT_RANGE", "")
    if spec:
        low, _, high = spec.partition("-")
        for port in range(int(low), int(high or low) + 1):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    # Unconfigured on purpose (no org, no model, no bot): this server has no network guard, so no AI call may ever leave it.
    env = dict(
        os.environ,
        BLOCKBRAIN_API_KEY="dummy-offline-key",
        BLOCKBRAIN_ORG_ID="",
        BLOCKBRAIN_MODEL="",
        BLOCKBRAIN_BOT_ID="",
        SUPPSWIPE_PREFETCH_MEALS="0",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "swipe_mobile_app" / "app.py"),
         "--server.port", str(port), "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{port}/"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "_stcore/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(server, wait_until="networkidle")
    pg.base_url = server  # type: ignore[attr-defined]
    yield pg
    ctx.close()


def shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def card(page):
    return page.frame_locator(CARD)


def card_name(page) -> str:
    return card(page).locator("#card .name").inner_text(timeout=20000)


def wait_name_change(page, old: str, timeout: float = 15.0) -> str:
    end = time.time() + timeout
    while time.time() < end:
        try:
            new = card(page).locator("#card .name").inner_text(timeout=1000)
        except Exception:
            new = ""
        if new and new != old:
            return new
        time.sleep(0.05)
    raise AssertionError(f"card stayed on {old!r}")


def settle(page, seconds: float = 0.8) -> None:
    """Let Streamlit finish the run (the status widget disappears)."""
    page.wait_for_timeout(int(seconds * 1000))
    try:
        page.locator('[data-testid="stStatusWidget"]').wait_for(state="hidden", timeout=10000)
    except Exception:
        pass


def open_scan_sheet(page) -> None:
    """The bar's Scan item: the three ways to start (Analyze / Resume / Sample) are in its sheet."""
    page.locator('[class~="st-key-appbar_scan"] button').click()
    page.get_by_role("dialog").wait_for(timeout=10000)
    settle(page, 0.5)


def open_diet_sheet(page) -> None:
    page.locator('[class~="st-key-appbar_diet"] button').click()
    page.get_by_role("dialog").wait_for(timeout=10000)
    settle(page, 0.5)


def close_sheet(page) -> None:
    page.keyboard.press("Escape")
    page.get_by_role("dialog").wait_for(state="detached", timeout=10000)
    settle(page)


def choose_diet(page, label: str) -> None:
    """A chip of the Diet sheet; closing the sheet is what applies it to the page behind (a dismiss reruns the app)."""
    open_diet_sheet(page)
    page.locator('[data-testid="stDialog"] [data-testid="stButtonGroup"] button', has_text=label).click()
    settle(page)
    close_sheet(page)


def start_sample(page) -> None:
    open_scan_sheet(page)
    page.get_by_role("button", name="Try with a sample label").click()
    card(page).locator("#card .name").wait_for(timeout=60000)
    settle(page)


def start_own_label(page) -> None:
    """A visitor's own pasted label (the sample label is a demo: it is not saved as a scan)."""
    open_scan_sheet(page)
    page.get_by_role("button", name="Analyze my supplement").click()
    dialog = page.get_by_role("dialog")
    dialog.locator("button", has_text="Paste").click()
    settle(page)
    dialog.locator("textarea").fill("Vitamin C 80 mg 100%\nVitamin D3 20 µg 400%\nZinc 10 mg 100%\nSelenium 55 µg 100%")
    dialog.get_by_role("button", name="Analyze").click()
    card(page).locator("#card .name").wait_for(timeout=60000)
    settle(page)


def mark_iframe(page) -> None:
    page.frame(url=lambda u: "tinder_swipe" in u).evaluate("window.__kept = true")


def iframe_kept(page) -> bool:
    return bool(page.frame(url=lambda u: "tinder_swipe" in u).evaluate("window.__kept === true"))


def results_heading(page):
    return page.locator(".plan-kicker", has_text="Your plan")


DIALOG = '[data-testid="stDialog"]'


def open_plan_item(page, kind: str, text: str):
    """Tap the food ("food") or kept-pill ("keep") row of the Plan tab that mentions `text`; returns its options window."""
    page.locator(f'[class*="st-key-planbtn_{kind}_"] button', has_text=text).first.click()
    dialog = page.locator(DIALOG)
    dialog.wait_for(timeout=10000)
    settle(page)
    return dialog


def change_choice(page, kind: str, text: str) -> None:
    """Reopen a decided card: tap its row, then "Change a choice" in the options window."""
    dialog = open_plan_item(page, kind, text)
    dialog.locator('[class*="st-key-plandlg_change_"] button').first.click()


def finish_all_cards(page, replace: bool = False) -> None:
    for _ in range(20):
        if results_heading(page).count():
            return
        name = card_name(page)
        repl = card(page).locator("#btnRepl")
        (repl if replace and not repl.is_disabled() else card(page).locator("#btnKeep")).click()
        for _ in range(100):
            if results_heading(page).count():
                break
            try:
                if card(page).locator("#card .name").inner_text(timeout=300) != name:
                    break
            except Exception:
                pass
            time.sleep(0.05)
    settle(page)
    assert results_heading(page).count()


def open_swap_sheet(page) -> None:
    """The card's Swap food button: the food list is in its sheet (nothing is drawn below the card any more)."""
    card(page).locator("#btnSwap").click(timeout=10000)
    page.get_by_role("dialog").wait_for(timeout=10000)
    settle(page, 0.7)  # a sheet ignores taps for its first 450 ms


def choose_option(page, index: int, close: bool = True) -> str:
    """Pick the `index`-th food in the Swap food sheet and close it (the card behind then shows it); `close=False` leaves it open."""
    open_swap_sheet(page)
    page.locator('[data-testid="stDialog"] [data-testid="stSelectbox"]').first.click()
    options = page.locator('[role="option"]')
    options.first.wait_for(timeout=5000)
    label = options.nth(index).inner_text()
    options.nth(index).click()
    settle(page)
    if close:
        close_sheet(page)
    return label


def food_options(page) -> list[str]:
    """Every food the Swap food sheet offers for the card on screen (the sheet is closed again)."""
    open_swap_sheet(page)
    page.locator('[data-testid="stDialog"] [data-testid="stSelectbox"]').first.click()
    page.locator('[role="option"]').first.wait_for(timeout=5000)
    labels = [o.inner_text() for o in page.locator('[role="option"]').all()]
    page.keyboard.press("Escape")  # the list first ...
    close_sheet(page)  # ... then the sheet
    return labels


def selected_option(page) -> str:
    """The food the Swap food sheet opens on (it is closed again with Esc)."""
    open_swap_sheet(page)
    value = page.locator('[data-testid="stDialog"] [data-testid="stSelectbox"] input').first.input_value()
    close_sheet(page)
    return value


def wait_filter_chip(page, text: str) -> None:
    """The page says an active filter with the chip under the brand ("Diet: Vegan"): the cards screen has no line of its own."""
    page.locator(".diet-note", has_text=text).wait_for(timeout=10000)


def test_swipes_buttons_keyboard_drag_back_reuse_one_iframe(page):
    start_sample(page)
    mark_iframe(page)
    first = card_name(page)
    card(page).locator("#btnKeep").click()
    second = wait_name_change(page, first)
    card(page).locator("#card").focus()
    page.keyboard.press("ArrowLeft")
    third = wait_name_change(page, second)
    box = card(page).locator("#card").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 3
    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 11):
        page.mouse.move(x - 18 * step, y + step)
    page.mouse.up()
    fourth = wait_name_change(page, third)
    card(page).locator("#btnBack").click()
    assert wait_name_change(page, fourth) == third
    assert iframe_kept(page), "the card iframe was reloaded"
    assert "Card 3 of" in card(page).locator("#card .count").inner_text()
    # No colour-only progress dots on the page.
    assert page.locator(".swipe-progress, .swipe-dot").count() == 0
    shot(page, "ux_card_after_back")


def test_reopened_card_keeps_the_chosen_food(page):
    start_sample(page)
    first = card_name(page)
    label = choose_option(page, 2)
    card(page).locator("#btnRepl").click()
    second = wait_name_change(page, first)
    settle(page)
    card(page).locator("#btnBack").click()
    assert wait_name_change(page, second) == first
    settle(page)
    assert selected_option(page).strip() == label.strip()
    assert "Your choice: replaced with" in card(page).locator("#card").inner_text()
    shot(page, "ux_reopened_card_food_kept")


def test_edit_from_results_returns_to_results(page):
    start_sample(page)
    finish_all_cards(page)
    shot(page, "ux_results_tabs")
    change_choice(page, "keep", "Vitamin B12")
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    assert page.locator(DIALOG).count() == 0  # the window closed with the tap
    assert "Editing from your results" in card(page).locator("#card").inner_text()
    assert card(page).locator("#btnBack").get_attribute("aria-label") == "Back to your results"
    shot(page, "ux_edit_mode_card")
    card(page).locator("#btnRepl").click()
    results_heading(page).wait_for(timeout=20000)
    settle(page)
    assert page.locator(DIALOG).count() == 0  # and it does not come back after the decision
    dialog = open_plan_item(page, "food", "Vitamin B12")  # B12 is a swapped food now
    assert dialog.locator('[class*="st-key-plandlg_change_"] button').first.inner_text().count("→") == 1
    page.keyboard.press("Escape")
    settle(page)
    assert page.locator(DIALOG).count() == 0
    # Back in edit mode also returns to the results, unchanged.
    change_choice(page, "keep", "Zinc")
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    card(page).locator("#btnBack").click()
    results_heading(page).wait_for(timeout=20000)


def test_results_tabs_show_their_content(page):
    start_sample(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()  # one kept pill, the rest swapped
    wait_name_change(page, name)
    settle(page)
    finish_all_cards(page, replace=True)
    page.get_by_role("tab", name="📤 Share").wait_for(timeout=10000)  # all tabs drawn
    tabs = page.get_by_role("tab")
    assert [t.strip() for t in tabs.all_inner_texts()] == ["🥗 Plan", "🍽️ Meals", "🛒 Shopping", "💬 Ask AI", "📤 Share"]
    page.locator(".plan-hero").wait_for(timeout=5000)
    # The options live in each row's window now, the guide in the bottom bar: no popover is left on the page.
    page.locator('[class~="st-key-appbar_guide"] button').wait_for(timeout=5000)
    assert page.locator('[class~="st-key-appbar_guide"] button').count() == 1
    assert page.get_by_role("button", name="Athlete RDA guide").count() == 0
    assert page.get_by_role("button", name="✎ Change a choice").count() == 0
    page.locator('[class*="st-key-planbtn_food_"] button').first.wait_for(state="attached", timeout=5000)
    page.locator('[class*="st-key-planbtn_keep_"] button').first.wait_for(state="attached", timeout=5000)
    assert page.locator('[class*="st-key-planbtn_keep_"] button').count() == 1
    page.get_by_role("tab", name="🍽️ Meals").click()
    page.get_by_text("Quick ideas").wait_for(timeout=5000)
    page.get_by_role("tab", name="🛒 Shopping").click()
    page.get_by_text("Total per week").wait_for(timeout=5000)
    page.get_by_role("link", name="idealo.de").wait_for(timeout=5000)
    page.get_by_role("tab", name="💬 Ask AI").click()
    # This server has no Blockbrain settings: the tab explains that instead of offering a chat that can only fail.
    page.get_by_text("AI answers are switched off right now").first.wait_for(timeout=5000)
    assert page.get_by_test_id("stChatInput").count() == 0
    page.get_by_role("tab", name="📤 Share").click()
    page.get_by_text("SuppSwipe — my results").first.wait_for(timeout=5000)
    # The page scrolls again (no "page lock"): long tab content is reachable.
    page.locator('[data-testid="stCode"]').first.scroll_into_view_if_needed()
    assert page.evaluate("document.querySelector('[data-testid=stMain]').scrollTop") > 0
    assert page.evaluate("document.scrollingElement.scrollWidth") <= 390  # no sideways scroll
    shot(page, "ux_results_share_tab")
    assert page.get_by_role("button", name="↩ Back to the cards").count() == 1
    # Settings fold away under the plan; scanning again needs no confirmation.
    assert page.locator('[data-testid="stExpander"]', has_text="Diet:").count() == 0  # the settings moved into the Diet sheet
    page.get_by_role("button", name="📸 Scan another supplement").click()
    page.get_by_role("dialog").wait_for(timeout=10000)


def test_filter_line_and_misfit_flag(page):
    start_sample(page)
    # Card 3 is vitamin B12: replace it with the top (animal) food, no filter yet.
    for _ in range(2):
        name = card_name(page)
        card(page).locator("#btnKeep").click()
        wait_name_change(page, name)
    settle(page)
    assert "b12" in card_name(page).lower()
    assert page.get_by_text("Filter:", exact=False).count() == 0 and page.locator(".diet-note").count() == 0
    name = card_name(page)
    card(page).locator("#btnRepl").click()
    wait_name_change(page, name)
    settle(page)
    choose_diet(page, "Vegan")
    wait_filter_chip(page, "Diet: Vegan")
    shot(page, "ux_card_filter_line")
    finish_all_cards(page)
    flag = page.locator('[data-testid="stButton"] button', has_text="doesn't fit Vegan — tap to choose another")
    flag.first.wait_for(timeout=5000)
    page.get_by_role("tab", name="📤 Share").click()
    share = page.locator('[data-testid="stCode"]').first.inner_text()
    assert "Vitamin B12" not in share.split("Kept as a supplement")[0]
    shot(page, "ux_results_misfit_flag")
    page.get_by_role("tab", name="🥗 Plan").click()
    flag.first.click()
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    card(page).locator("#btnRepl").click()  # re-chosen from the vegan list
    results_heading(page).wait_for(timeout=20000)
    settle(page)
    assert page.locator('[data-testid="stButton"] button', has_text="doesn't fit Vegan").count() == 0


def test_resume_after_refresh_and_start_over_clears_it(page):
    start_own_label(page)  # a real scan: the sample label is never saved, so it cannot be resumed
    for _ in range(2):
        name = card_name(page)
        card(page).locator("#btnKeep").click()
        wait_name_change(page, name)
    settle(page, 1.2)
    total = card(page).locator("#card .count").inner_text().split(" of ")[1].strip()
    resume_at = card_name(page)
    page.reload(wait_until="networkidle")
    open_scan_sheet(page)
    resume = page.get_by_role("button", name="Resume last scan")
    resume.wait_for(timeout=20000)
    page.get_by_text(f"2 of {total} cards done · saved today").wait_for(timeout=5000)  # what it brings back, and when
    shot(page, "ux_welcome_resume")
    resume.click()
    card(page).locator("#card .name").wait_for(timeout=20000)
    assert card_name(page) == resume_at
    settle(page)
    page.locator('[class~="st-key-appbar_scan"] button').click()  # half-way through a scan the Scan item asks first
    page.get_by_role("button", name="Start over").click()
    settle(page, 1.5)
    page.keyboard.press("Escape")
    page.reload(wait_until="networkidle")
    settle(page, 2.0)
    open_scan_sheet(page)  # the offer lives in the sheet: look there, or "no Resume button" proves nothing
    assert page.get_by_role("button", name="Try with a sample label").count() == 1
    assert page.get_by_role("button", name="Resume last scan").count() == 0


def test_clear_history_on_the_results_forgets_the_saved_scan(page):
    start_own_label(page)
    finish_all_cards(page)
    settle(page, 1.2)
    assert page.evaluate("localStorage.getItem('suppswipe_current_scan_v1')")
    page.locator('[class~="st-key-appbar_scans"] button').click()
    page.get_by_role("button", name="Clear history").click()
    page.get_by_role("button", name="Delete", exact=True).click()  # asks first
    settle(page, 1.5)
    assert page.evaluate("localStorage.getItem('suppswipe_scan_history_v1')") is None
    assert page.evaluate("localStorage.getItem('suppswipe_current_scan_v1')") is None
    page.reload(wait_until="networkidle")
    settle(page, 2.0)
    open_scan_sheet(page)
    assert page.get_by_role("button", name="Try with a sample label").count() == 1
    assert page.get_by_role("button", name="Resume last scan").count() == 0


def test_filter_change_keeps_the_chosen_food_while_offered(page):
    # Card 2 is vitamin D: switching to Vegan drops the fish but keeps the
    # UV mushrooms, so a chosen mushroom stays selected.
    start_sample(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    assert "vitamin d" in wait_name_change(page, name).lower()
    settle(page)
    chosen = choose_option(page, 2)
    assert "mushroom" in chosen.lower()
    choose_diet(page, "Vegan")
    wait_filter_chip(page, "Diet: Vegan")
    assert selected_option(page) == chosen


def _active_chips(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('[data-testid="stButtonGroup"] button')]
            .filter(b => b.getAttribute('aria-checked') === 'true').map(b => b.innerText)"""
    )


def test_resume_keeps_the_diet_filter_chip(page):
    start_own_label(page)  # a real scan: the sample label is never saved, so it cannot be resumed
    choose_diet(page, "Vegan")
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    settle(page, 1.2)
    page.reload(wait_until="networkidle")
    open_scan_sheet(page)
    page.get_by_role("button", name="Resume last scan").click(timeout=20000)
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    wait_filter_chip(page, "Diet: Vegan")  # said on the page at once (the chip under the brand) ...
    open_diet_sheet(page)
    assert _active_chips(page) == ["Vegan"]  # ... and the sheet opens on it (a value written while the chips were not on screen is not applied by Streamlit)
    close_sheet(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()  # the next run keeps the filter
    wait_name_change(page, name)
    settle(page)
    open_diet_sheet(page)
    assert _active_chips(page) == ["Vegan"]
    close_sheet(page)
    wait_filter_chip(page, "Diet: Vegan")


def test_build_tag_in_the_about_sheet(page):
    page.locator('[class~="st-key-appbar_about"] button').click()
    page.get_by_text("Build ", exact=False).first.wait_for(timeout=5000)


# --- Final review (UX journey) ----------------------------------------------------------


def _small_page(browser, server, width: int = 320, height: int = 640):
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(server, wait_until="networkidle")
    return ctx, pg


def test_start_over_always_opens_the_analyze_dialog(page):
    # UXJ-F1: a stray rerun (the history iframe re-sending its value) used to
    # close the one-shot dialog in about 1 of 6 tries.
    dialog = page.get_by_role("dialog")
    for attempt in range(10):
        if dialog.count():
            dialog.get_by_role("button", name="Cancel").click()
            settle(page)
        start_sample(page)
        name = card_name(page)
        card(page).locator("#btnKeep").click()
        wait_name_change(page, name)
        settle(page)
        page.locator('[class~="st-key-appbar_scan"] button').click()  # half-way through a scan: the question, not the sheet
        page.get_by_role("button", name="Start over").click()
        page.wait_for_timeout(2000)
        settle(page)
        assert dialog.count() == 1, f"attempt {attempt}: no dialog after Start over"
        assert dialog.get_by_text("Analyze my supplement").count() == 1
    # Cancel closes it, and it stays closed on the next run.
    dialog.get_by_role("button", name="Cancel").click()
    settle(page, 1.5)
    assert dialog.count() == 0
    page.locator('[class~="st-key-appbar_scan"] button').wait_for(timeout=10000)


def test_small_phone_sees_the_first_card_after_the_sample_button(browser, server):
    # UXJ-F2: the page kept the scroll position of the (far down) button.
    ctx, pg = _small_page(browser, server)
    try:
        # No page below the hero to scroll to any more: the welcome page is the hero and the bar.
        assert pg.evaluate("document.querySelector('[data-testid=stMain]').scrollTop") == 0
        open_scan_sheet(pg)
        button = pg.get_by_role("button", name="Try with a sample label")
        button.click()
        card(pg).locator("#card .name").wait_for(timeout=60000)
        settle(pg, 1.5)
        box = pg.locator(CARD).bounding_box()
        assert box is not None and box["y"] >= -1, box
        shot(pg, "ux_small_phone_first_card")
    finally:
        ctx.close()


def test_small_phone_sees_an_analysis_error(browser, server):
    ctx, pg = _small_page(browser, server)
    try:
        open_scan_sheet(pg)
        pg.get_by_role("button", name="Analyze my supplement").click()
        dialog = pg.get_by_role("dialog")
        dialog.locator("button", has_text="Paste").click()
        settle(pg)
        dialog.locator("textarea").fill("Hello, this text names no nutrient at all")
        dialog.get_by_role("button", name="Analyze").click()
        alert = pg.locator('[data-testid="stAlert"]', has_text="vitamins or minerals")
        alert.first.wait_for(timeout=30000)
        settle(pg, 1.5)
        box = alert.first.bounding_box()
        assert box is not None and 0 <= box["y"] < 640, box
    finally:
        ctx.close()


def test_resume_keeps_the_pregnancy_toggle(page):
    # UXJ-F4
    start_own_label(page)  # a real scan: the sample label is never saved, so it cannot be resumed
    open_diet_sheet(page)
    page.get_by_text("Pregnant or breastfeeding").click()
    settle(page)
    close_sheet(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    settle(page, 1.2)
    page.reload(wait_until="networkidle")
    open_scan_sheet(page)
    page.get_by_role("button", name="Resume last scan").click(timeout=20000)
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    open_diet_sheet(page)
    toggle = page.locator("label", has_text="Pregnant or breastfeeding").locator("input")
    assert toggle.is_checked()


def test_low_dose_note_is_info_not_a_warning(page):
    # UXJ-F3: card 5 of the sample label is magnesium 56 mg (15% NRV).
    start_sample(page)
    for _ in range(4):
        name = card_name(page)
        card(page).locator("#btnKeep").click()
        wait_name_change(page, name)
    settle(page)
    assert "magnesium" in card_name(page).lower()
    assert card(page).locator("#card .bio").inner_text().count("Low dose: about 15%") == 1
    assert card(page).locator("#card .warn").count() == 0 or "Low dose" not in card(page).locator("#card .warn").inner_text()


def test_meal_count_survives_editing_a_card(page):
    # UXJ-F6: replace the first card so the results offer meals.
    start_sample(page)
    name = card_name(page)
    card(page).locator("#btnRepl").click()
    wait_name_change(page, name)
    finish_all_cards(page)
    page.get_by_role("tab", name="🍽️ Meals").click()
    page.get_by_text("1 meal", exact=True).click()
    settle(page)
    page.get_by_role("tab", name="🥗 Plan").click()
    change_choice(page, "keep", "Selenium")
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    card(page).locator("#btnBack").click()
    results_heading(page).wait_for(timeout=20000)
    settle(page)
    page.get_by_role("tab", name="🍽️ Meals").click()
    assert page.get_by_role("radio", name="1 meal").is_checked()


def test_keep_and_replace_stay_where_the_thumb_is(page):
    """UX audit: the card frame fitted each card, so the buttons moved ~100 px between taps (and fell below the fold on
    short phones). The frame now only grows during a scan, so the buttons never move UP from card to card."""
    start_sample(page)
    heights, button_tops = [], []
    name = card_name(page)
    for _ in range(5):
        frame = page.locator(CARD).bounding_box()
        keep = card(page).locator("#btnKeep").bounding_box()
        assert frame is not None and keep is not None
        heights.append(round(frame["height"]))
        button_tops.append(round(keep["y"] - frame["y"]))
        card(page).locator("#btnKeep").click()
        try:
            name = wait_name_change(page, name)
        except AssertionError:
            break
        settle(page, 0.4)
    assert len(heights) >= 3
    assert heights == sorted(heights), heights  # never shrinks
    assert button_tops == sorted(button_tops), button_tops  # so the buttons never jump up between cards


def _mixed_results(page) -> None:
    """The sample label with the first card replaced and the rest kept: one food row, several kept pills."""
    start_sample(page)
    name = card_name(page)
    card(page).locator("#btnRepl").click()
    wait_name_change(page, name)
    finish_all_cards(page)
    page.locator('[class*="st-key-planbtn_food_"] button').first.wait_for(state="attached", timeout=5000)


def _window_box(page) -> dict:
    return page.evaluate(
        "() => { const r = document.querySelector('[data-testid=stDialog] [role=dialog]').getBoundingClientRect();"
        " return {x: r.x, right: r.right, bottom: r.bottom, vw: innerWidth, vh: innerHeight, page: document.scrollingElement.scrollWidth}; }"
    )


@pytest.mark.parametrize("size", [(390, 844), (320, 640)])
def test_tapping_a_row_opens_its_options_window(browser, server, size):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(server, wait_until="networkidle")
    _mixed_results(page)
    row = page.locator('[class*="st-key-planbtn_food_"] button').first
    dialog = open_plan_item(page, "food", "")
    box = _window_box(page)
    assert box["x"] >= 0 and box["right"] <= box["vw"] and box["page"] <= box["vw"]  # inside the phone, no sideways scroll
    text = dialog.inner_text()
    for heading in ("CHANGE A CHOICE", "What the whole food adds (AI)", "Athlete targets"):  # the last two are expanders
        assert heading in text
    assert not dialog.locator("table").first.is_visible()  # both expanders start closed: a short window
    dialog.get_by_text("What the whole food adds (AI)").click()
    dialog.get_by_text("The AI comparison is switched off right now.").wait_for(timeout=5000)  # this server has no Blockbrain settings
    assert dialog.get_by_role("button", name="Show the comparison").count() == 0
    dialog.get_by_text("Athlete targets").click()
    dialog.locator(".gd-list").wait_for(timeout=5000)
    assert dialog.locator("table").count() == 0  # the item's own targets; all nutrients are in the guide
    assert dialog.get_by_role("button", name="Open the Athlete guide").count() == 1
    assert _window_box(page)["page"] <= size[0]  # still no sideways page scroll with both expanders open
    shot(page, f"ux_plan_item_window_{size[0]}")
    # X, Esc and the Close button all leave the window, and the page behind keeps its tab.
    assert dialog.get_by_role("button", name="Done").count() == 1
    dialog.get_by_role("button", name="Close", exact=True).click()  # the X, 44 px wide
    settle(page)
    assert page.locator(DIALOG).count() == 0
    assert page.get_by_role("tab", name="🥗 Plan").get_attribute("aria-selected") == "true"
    row.click()
    page.locator(DIALOG).wait_for(timeout=10000)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    assert page.locator(DIALOG).count() == 0
    row.click()
    page.locator(DIALOG).wait_for(timeout=10000)
    settle(page)
    page.locator(".st-key-plandlg_close button").click()
    settle(page)
    assert page.locator(DIALOG).count() == 0
    ctx.close()


def test_a_kept_pill_window_has_no_comparison_and_changes_the_choice(page):
    _mixed_results(page)
    dialog = open_plan_item(page, "keep", "")
    text = dialog.inner_text()
    assert "CHANGE A CHOICE" in text and "Athlete targets" in text and "What the whole food adds" not in text
    assert "kept as a supplement" in text
    dialog.locator(".st-key-plandlg_change_0 button").click()
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    assert page.locator(DIALOG).count() == 0 and "Editing from your results" in card(page).locator("#card").inner_text()
    card(page).locator("#btnBack").click()
    results_heading(page).wait_for(timeout=20000)
    settle(page)
    assert page.locator(DIALOG).count() == 0


def test_scanning_another_supplement_after_a_row_window_opens_the_analyze_dialog(page):
    _mixed_results(page)
    open_plan_item(page, "keep", "")
    page.keyboard.press("Escape")
    settle(page)
    page.get_by_role("button", name="📸 Scan another supplement").click()
    page.get_by_role("dialog").wait_for(timeout=10000)
    assert page.get_by_role("dialog").get_by_text("Analyze my supplement").count() >= 1
    assert page.locator(DIALOG).count() == 1  # only the Analyze dialog


def test_each_row_button_covers_its_whole_row(page):
    """The tap target is a transparent button over the .plan-row. If a Streamlit change breaks the overlay CSS the buttons
    would show as ordinary ones under their rows: this fails first."""
    _mixed_results(page)
    boxes = page.evaluate(
        "() => [...document.querySelectorAll('[class*=\"st-key-planrow_\"]')].map(row => {"
        " const r = row.getBoundingClientRect(), b = row.querySelector('button').getBoundingClientRect();"
        " return {row: [r.x, r.y, r.width, r.height], button: [b.x, b.y, b.width, b.height]}; })"
    )
    assert len(boxes) >= 2
    for box in boxes:
        assert all(abs(a - b) <= 1 for a, b in zip(box["row"], box["button"])), box
        assert box["button"][3] >= 44  # a finger-sized target


# ---------------------------------------------------------------- the AI comparison in the options window
# The default server above has no Blockbrain settings. This one talks to tests/fake_blockbrain.py only: a sitecustomize in
# its PYTHONPATH sends every request for a *.theblockbrain.ai host to the fake and refuses any other non-local host.
_FAKE_ROUTING = '''
import os
_fake = os.environ.get("SUPPSWIPE_TEST_FAKE_BB", "").rstrip("/")
if _fake:
    import requests.adapters
    from urllib.parse import urlparse
    _send = requests.adapters.HTTPAdapter.send
    def _to_fake(self, request, *args, **kwargs):
        host = urlparse(request.url).hostname or ""
        if host.endswith("theblockbrain.ai"):
            request.url = _fake + request.url.split(urlparse(request.url).netloc, 1)[1]
        elif host not in ("127.0.0.1", "localhost"):
            raise RuntimeError("only the local fake may be contacted: " + host)
        return _send(self, request, *args, **kwargs)
    requests.adapters.HTTPAdapter.send = _to_fake
'''


@pytest.fixture
def ai_server(tmp_path_factory):
    """One server per test: a finished or running comparison is cached in the server process, per food."""
    import fake_blockbrain as fb

    fake = fb.FakeBlockbrain().start()
    routing = tmp_path_factory.mktemp("fake_routing")
    (routing / "sitecustomize.py").write_text(_FAKE_ROUTING)
    port = _free_port()
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(routing), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
        SUPPSWIPE_TEST_FAKE_BB=fake.url,
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        BLOCKBRAIN_API_KEY=fb.API_KEY,
        BLOCKBRAIN_ORG_ID=fb.ORG_ID,
        BLOCKBRAIN_BOT_ID=fb.BOT_ID,
        SUPPSWIPE_PREFETCH_MEALS="0",
    )
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        env.pop(name, None)
    env.pop("BLOCKBRAIN_MODEL", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "swipe_mobile_app" / "app.py"),
         "--server.port", str(port), "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{port}/"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "_stcore/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    yield url, fake
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    fake.stop()


_ANSWER = "**Vitamin C → Guavas**\n\n- 💊 Pill alone: one nutrient\n- 🥗 Whole food also gives: fibre and more"


def _slow_comparison(ai_server, page, seconds: float):
    """A results page whose first food window has its comparison being written (the fake model needs `seconds`)."""
    url, fake = ai_server
    fake.stream_script = [{"delay": seconds, "text": _ANSWER}]
    page.goto(url, wait_until="networkidle")
    _mixed_results(page)
    dialog = open_plan_item(page, "food", "")
    dialog.get_by_text("What the whole food adds (AI)").click()
    dialog.get_by_role("button", name="Show the comparison").click()
    dialog.locator(".plan-writing").wait_for(timeout=10000)
    return dialog


def _closed_within(page, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if page.locator(DIALOG).count() == 0:
            return True
        page.wait_for_timeout(50)
    return False


def test_done_answers_at_once_while_the_comparison_is_being_written(ai_server, page):
    """A tap inside the window is queued behind a running script: no run may wait for the model (it takes 15-40 s live)."""
    dialog = _slow_comparison(ai_server, page, 12)
    page.wait_for_timeout(1000)
    started = time.time()
    dialog.locator(".st-key-plandlg_close button").click()
    assert _closed_within(page, 3), "Done waited for the AI comparison"
    assert time.time() - started < 3


def test_change_a_choice_answers_at_once_while_the_comparison_is_being_written(ai_server, page):
    dialog = _slow_comparison(ai_server, page, 12)
    page.wait_for_timeout(1000)
    dialog.locator(".st-key-plandlg_change_0 button").click()
    assert _closed_within(page, 3), "Change a choice waited for the AI comparison"
    card(page).locator("#card .name").wait_for(timeout=10000)
    assert "Editing from your results" in card(page).locator("#card").inner_text()


def test_reopening_a_food_that_is_still_being_written_does_not_block_the_page(ai_server, page):
    dialog = _slow_comparison(ai_server, page, 12)
    page.keyboard.press("Escape")
    assert _closed_within(page, 3)
    page.wait_for_timeout(500)
    dialog = open_plan_item(page, "food", "")  # waits for the dialog only: the page run must not sit in a wait loop
    dialog.locator(".st-key-plandlg_change_0 button").click()
    assert _closed_within(page, 3), "reopening an in-flight food held back the window's buttons"


def test_the_comparison_appears_by_itself_and_keeps_the_focus_and_announces_itself(ai_server, page):
    dialog = _slow_comparison(ai_server, page, 2)
    button = dialog.get_by_role("button", name="Show the comparison")
    assert dialog.locator("[role=status]").count() == 1
    dialog.get_by_text("fibre and more").wait_for(timeout=15000)  # drawn by the polling fragment: nothing was tapped
    assert dialog.locator("[role=status]").inner_text() == "Comparison ready"
    assert button.count() == 1 and button.is_enabled()  # not removed (focus would jump to the window's X)
    assert page.evaluate("document.activeElement && document.activeElement.getAttribute('aria-label')") != "Close"
    shot(page, "ux_plan_item_comparison")

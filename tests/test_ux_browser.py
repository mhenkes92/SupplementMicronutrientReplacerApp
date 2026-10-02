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


def start_sample(page) -> None:
    page.get_by_role("button", name="Try it with a sample label").click()
    card(page).locator("#card .name").wait_for(timeout=60000)
    settle(page)


def mark_iframe(page) -> None:
    page.frame(url=lambda u: "tinder_swipe" in u).evaluate("window.__kept = true")


def iframe_kept(page) -> bool:
    return bool(page.frame(url=lambda u: "tinder_swipe" in u).evaluate("window.__kept === true"))


def results_heading(page):
    return page.locator(".plan-kicker", has_text="Your plan")


def change_choice(page, text: str) -> None:
    """Reopen a decided card from the results' "Change a choice" menu."""
    page.get_by_role("button", name="✎ Change a choice").click()
    page.locator('[data-testid="stPopoverBody"] button', has_text=text).first.click()


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


def choose_option(page, index: int) -> str:
    page.locator('[data-testid="stSelectbox"]').first.click()
    options = page.locator('[role="option"]')
    options.first.wait_for(timeout=5000)
    label = options.nth(index).inner_text()
    options.nth(index).click()
    settle(page)
    return label


def selected_option(page) -> str:
    return page.locator('[data-testid="stSelectbox"] input').first.input_value()


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
    change_choice(page, "Vitamin B12")
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    assert "Editing from your results" in card(page).locator("#card").inner_text()
    assert card(page).locator("#btnBack").get_attribute("aria-label") == "Back to your results"
    shot(page, "ux_edit_mode_card")
    card(page).locator("#btnRepl").click()
    results_heading(page).wait_for(timeout=20000)
    settle(page)
    page.get_by_role("button", name="✎ Change a choice").click()
    b12 = page.locator('[data-testid="stPopoverBody"] button', has_text="Vitamin B12").first
    assert b12.inner_text().count("→") == 1
    page.keyboard.press("Escape")
    settle(page)
    # Back in edit mode also returns to the results, unchanged.
    change_choice(page, "Zinc")
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
    for name in ("Athlete RDA guide", "✎ Change a choice"):
        page.get_by_role("button", name=name).wait_for(timeout=5000)
        assert page.get_by_role("button", name=name).count() == 1
    page.get_by_role("tab", name="🍽️ Meals").click()
    page.get_by_text("Quick ideas").wait_for(timeout=5000)
    page.get_by_role("tab", name="🛒 Shopping").click()
    page.get_by_text("Total per week").wait_for(timeout=5000)
    page.get_by_role("link", name="idealo.de").wait_for(timeout=5000)
    page.get_by_role("tab", name="💬 Ask AI").click()
    page.locator('[data-testid="stButtonGroup"] button', has_text="Is my plan balanced?").wait_for(timeout=5000)
    page.get_by_role("tab", name="📤 Share").click()
    page.get_by_text("SuppSwipe — my results").first.wait_for(timeout=5000)
    # The page scrolls again (no "page lock"): long tab content is reachable.
    page.locator('[data-testid="stCode"]').first.scroll_into_view_if_needed()
    assert page.evaluate("document.querySelector('[data-testid=stMain]').scrollTop") > 0
    assert page.evaluate("document.scrollingElement.scrollWidth") <= 390  # no sideways scroll
    shot(page, "ux_results_share_tab")
    assert page.get_by_role("button", name="↩ Back to the cards").count() == 1
    # Settings fold away under the plan; scanning again needs no confirmation.
    assert page.locator('[data-testid="stExpander"]', has_text="Diet: no restriction").count() == 1
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
    assert page.get_by_text("Filter:", exact=False).count() == 0
    name = card_name(page)
    card(page).locator("#btnRepl").click()
    wait_name_change(page, name)
    settle(page)
    page.locator('[data-testid="stButtonGroup"] button', has_text="Vegan").click()
    settle(page)
    page.get_by_text("Filter: Vegan").wait_for(timeout=5000)
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
    start_sample(page)
    for _ in range(2):
        name = card_name(page)
        card(page).locator("#btnKeep").click()
        wait_name_change(page, name)
    settle(page, 1.2)
    total = card(page).locator("#card .count").inner_text().split(" of ")[1].strip()
    resume_at = card_name(page)
    page.reload(wait_until="networkidle")
    resume = page.get_by_role("button", name=f"↩ Resume your last scan (2 of {total} cards done)")
    resume.wait_for(timeout=20000)
    shot(page, "ux_welcome_resume")
    resume.click()
    card(page).locator("#card .name").wait_for(timeout=20000)
    assert card_name(page) == resume_at
    settle(page)
    page.get_by_role("button", name="Analyze my Supplement").click()
    page.get_by_role("button", name="Start over").click()
    settle(page, 1.5)
    page.keyboard.press("Escape")
    page.reload(wait_until="networkidle")
    settle(page, 2.0)
    assert page.get_by_role("button", name="Resume your last scan").count() == 0


def test_clear_history_on_the_results_forgets_the_saved_scan(page):
    start_sample(page)
    finish_all_cards(page)
    settle(page, 1.2)
    assert page.evaluate("localStorage.getItem('suppswipe_current_scan_v1')")
    page.get_by_role("button", name="🕘 Recent scans").click()
    page.get_by_role("button", name="Clear history").click()
    settle(page, 1.5)
    assert page.evaluate("localStorage.getItem('suppswipe_scan_history_v1')") is None
    assert page.evaluate("localStorage.getItem('suppswipe_current_scan_v1')") is None
    page.reload(wait_until="networkidle")
    settle(page, 2.0)
    assert page.get_by_role("button", name="Resume your last scan").count() == 0


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
    page.locator('[data-testid="stButtonGroup"] button', has_text="Vegan").click()
    settle(page)
    page.get_by_text("Filter: Vegan").wait_for(timeout=5000)
    assert selected_option(page) == chosen


def _active_chips(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('[data-testid="stButtonGroup"] button')]
            .filter(b => b.getAttribute('aria-checked') === 'true').map(b => b.innerText)"""
    )


def test_resume_keeps_the_diet_filter_chip(page):
    start_sample(page)
    page.locator('[data-testid="stButtonGroup"] button', has_text="Vegan").click()
    settle(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    settle(page, 1.2)
    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Resume your last scan").click(timeout=20000)
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    assert _active_chips(page) == ["Vegan"]
    name = card_name(page)
    card(page).locator("#btnKeep").click()  # the next run keeps the filter
    wait_name_change(page, name)
    settle(page)
    assert _active_chips(page) == ["Vegan"]
    page.get_by_text("Filter: Vegan").wait_for(timeout=5000)


def test_build_tag_in_about_popover(page):
    page.get_by_role("button", name="🔒 About & privacy").click()
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
        page.get_by_role("button", name="Analyze my Supplement").click()
        page.get_by_role("button", name="Start over").click()
        page.wait_for_timeout(2000)
        settle(page)
        assert dialog.count() == 1, f"attempt {attempt}: no dialog after Start over"
        assert dialog.get_by_text("Analyze my supplement").count() == 1
    # Cancel closes it, and it stays closed on the next run.
    dialog.get_by_role("button", name="Cancel").click()
    settle(page, 1.5)
    assert dialog.count() == 0
    page.get_by_role("button", name="Try it with a sample label").wait_for(timeout=10000)


def test_small_phone_sees_the_first_card_after_the_sample_button(browser, server):
    # UXJ-F2: the page kept the scroll position of the (far down) button.
    ctx, pg = _small_page(browser, server)
    try:
        # Scrolled down to the dietary filter, then back up to the sample button.
        pg.locator('[data-testid="stButtonGroup"]').first.scroll_into_view_if_needed()
        assert pg.evaluate("document.querySelector('[data-testid=stMain]').scrollTop") > 0
        button = pg.get_by_role("button", name="Try it with a sample label")
        button.scroll_into_view_if_needed()
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
        button = pg.get_by_role("button", name="Analyze my Supplement")
        button.scroll_into_view_if_needed()
        button.click()
        dialog = pg.get_by_role("dialog")
        dialog.locator("button", has_text="Paste").click()
        settle(pg)
        dialog.locator("textarea").fill("Hello, this text names no nutrient at all")
        dialog.get_by_role("button", name="Analyze").click()
        alert = pg.locator('[data-testid="stAlert"]', has_text="No micronutrients")
        alert.first.wait_for(timeout=30000)
        settle(pg, 1.5)
        box = alert.first.bounding_box()
        assert box is not None and 0 <= box["y"] < 640, box
    finally:
        ctx.close()


def test_resume_keeps_the_pregnancy_toggle(page):
    # UXJ-F4
    start_sample(page)
    page.get_by_text("Pregnant or breastfeeding").click()
    settle(page)
    name = card_name(page)
    card(page).locator("#btnKeep").click()
    wait_name_change(page, name)
    settle(page, 1.2)
    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Resume your last scan").click(timeout=20000)
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
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
    change_choice(page, "Selenium")
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    card(page).locator("#btnBack").click()
    results_heading(page).wait_for(timeout=20000)
    settle(page)
    page.get_by_role("tab", name="🍽️ Meals").click()
    assert page.get_by_role("radio", name="1 meal").is_checked()

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
    env = dict(os.environ, BLOCKBRAIN_API_KEY="dummy-offline-key", SUPPSWIPE_PREFETCH_MEALS="0")
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
    return page.get_by_text("Your results", exact=True)


def finish_all_cards(page) -> None:
    for _ in range(20):
        if results_heading(page).count():
            return
        name = card_name(page)
        card(page).locator("#btnKeep").click()
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
    button = page.locator('[data-testid="stButton"] button', has_text="Vitamin B12").first
    button.click()
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    assert "Editing from your results" in card(page).locator("#card").inner_text()
    assert card(page).locator("#btnBack").get_attribute("aria-label") == "Back to your results"
    shot(page, "ux_edit_mode_card")
    card(page).locator("#btnRepl").click()
    results_heading(page).wait_for(timeout=20000)
    settle(page)
    assert page.locator('[data-testid="stButton"] button', has_text="Vitamin B12").first.inner_text().count("→") == 1
    # Back in edit mode also returns to the results, unchanged.
    page.locator('[data-testid="stButton"] button', has_text="zinc").first.click()
    card(page).locator("#card .name").wait_for(timeout=20000)
    settle(page)
    card(page).locator("#btnBack").click()
    results_heading(page).wait_for(timeout=20000)


def test_results_tabs_show_their_content(page):
    start_sample(page)
    finish_all_cards(page)
    tabs = page.get_by_role("tab")
    assert [t.strip() for t in tabs.all_inner_texts()] == ["🍽️ Meals", "🛒 Cost", "💊 Kept pills", "📤 Share", "🌱 Why food"]
    page.get_by_role("tab", name="💊 Kept pills").click()
    page.get_by_text("Compare prices on idealo.de").wait_for(timeout=5000)
    page.get_by_role("tab", name="📤 Share").click()
    page.get_by_text("SuppSwipe — my results").first.wait_for(timeout=5000)
    # The page scrolls again (no "page lock"): long tab content is reachable.
    page.locator('[data-testid="stCode"]').first.scroll_into_view_if_needed()
    assert page.evaluate("document.querySelector('[data-testid=stMain]').scrollTop") > 0
    assert page.evaluate("document.scrollingElement.scrollWidth") <= 390  # no sideways scroll
    shot(page, "ux_results_share_tab")
    assert page.get_by_role("button", name="💬 Ask AI").count() == 1
    assert page.get_by_role("button", name="Athlete RDA guide").count() == 1
    assert page.get_by_role("button", name="↩ Back to the last card").count() == 1


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


def test_build_tag_in_about_popover(page):
    page.get_by_role("button", name="🔒 About & privacy").click()
    page.get_by_text("Build ", exact=False).first.wait_for(timeout=5000)

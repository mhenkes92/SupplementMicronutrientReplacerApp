"""Browser checks of the speed round (opt-in like tests/test_ux_browser.py: SUPPSWIPE_BROWSER_TESTS=1).

The model is tests/fake_blockbrain.py with a slow writer (`write_delay`, seconds after every line it streams), reached through the
sitecustomize routing of test_ux_browser.ai_server. What AppTest cannot see:
* SP1: a manual generation (pregnancy setting: no prefetch; one Ask AI question) shows its first words while the answer is still being
  written, and Streamlit logs no "missing ScriptRunContext" for it (the old on_text painted from a worker thread, which it drops);
* SP2: once the stream has ended, the finished plan is on screen within a short tail (the poll is 0.5 s now, it was 1 s).
Set SUPPSWIPE_SPEED_REPORT=1 to print the numbers.
"""
from __future__ import annotations

import contextlib
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from types import SimpleNamespace

import pytest

from test_ux_browser import (  # noqa: F401  (fixtures are used by name)
    _FAKE_ROUTING, ROOT, _free_port, _mixed_results, browser, card, card_name, choose_diet, close_sheet, finish_all_cards, open_diet_sheet,
    settle, start_sample, wait_name_change,
)

WRITE_DELAY = 0.12  # seconds after every streamed line: about 70 lines, so the whole answer takes about 8 s
# The answers start with their first words, so "the first words are on screen" does not depend on how the client batches the
# fake's HTTP/1.0 body (requests reads it in 512-byte pieces: about 5 deltas at a time).
PLAN = (
    "Kiwi oat bowl - 60 g oats, 2 kiwis, 150 g yoghurt, a spoon of seeds.\n\n"
    "Lentil curry - 80 g red lentils, spinach, coconut milk, rice.\n\n"
    "Brazil nut salad - 1 nut, mixed greens, olive oil, lemon."
)
PLAN_FIRST_WORDS, PLAN_LAST_WORDS = "Kiwi oat bowl", "olive oil, lemon."
ANSWER = (
    "Zinc supports the immune system, wound healing and normal growth. Most adults need 8 to 11 mg a day, "
    "and the upper limit for adults is 25 mg a day from all sources, so a 10 mg pill sits well inside it."
)
ANSWER_FIRST_WORDS, ANSWER_LAST_WORDS = "Zinc supports", "inside it."
REPORT = os.getenv("SUPPSWIPE_SPEED_REPORT", "") == "1"


def _say(text: str) -> None:
    if REPORT:
        print(f"[speed] {text}", flush=True)


@contextlib.contextmanager
def _serve(tmp_path_factory, prefetch: bool):
    """A Streamlit server whose Blockbrain is the fake (a sitecustomize in PYTHONPATH sends every *.theblockbrain.ai request
    there), with its log in a file. One per test: a finished or running answer is cached in the server process."""
    import fake_blockbrain as fb

    fake = fb.FakeBlockbrain().start()
    work = tmp_path_factory.mktemp("speed_server")
    (work / "sitecustomize.py").write_text(_FAKE_ROUTING)
    log = work / "server.log"
    port = _free_port()
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(work), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
        PYTHONUNBUFFERED="1",
        SUPPSWIPE_TEST_FAKE_BB=fake.url,
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        BLOCKBRAIN_API_KEY=fb.API_KEY,
        BLOCKBRAIN_ORG_ID=fb.ORG_ID,
        BLOCKBRAIN_BOT_ID=fb.BOT_ID,
    )
    env.pop("SUPPSWIPE_PREFETCH_MEALS", None)
    if not prefetch:
        env["SUPPSWIPE_PREFETCH_MEALS"] = "0"
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy", "BLOCKBRAIN_MODEL"):
        env.pop(name, None)
    with open(log, "wb") as sink:
        proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(ROOT / "swipe_mobile_app" / "app.py"),
             "--server.port", str(port), "--server.headless", "true", "--browser.gatherUsageStats", "false"],
            cwd=ROOT, env=env, stdout=sink, stderr=subprocess.STDOUT,
        )
        url = f"http://localhost:{port}/"
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "_stcore/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        try:
            yield SimpleNamespace(url=url, fake=fake, log=log)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
    fake.stop()


@pytest.fixture
def speed_server(tmp_path_factory):
    """As in production: the default plan is prefetched in the background when the results open."""
    with _serve(tmp_path_factory, prefetch=True) as server:
        yield server


@pytest.fixture
def quiet_server(tmp_path_factory):
    """No prefetch: every generation is the one the test asks for."""
    with _serve(tmp_path_factory, prefetch=False) as server:
        yield server


def _new_page(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(server.url, wait_until="networkidle")
    return ctx, page


def _log_size(server) -> int:
    return server.log.stat().st_size


def _log_since(server, offset: int) -> str:
    with open(server.log, "rb") as handle:
        handle.seek(offset)
        return handle.read().decode("utf-8", "replace")


def _snapshot(page, first: str, last: str) -> tuple[bool, bool, bool]:
    """(first words on screen, last words on screen, the writing cursor on screen), read in one go so they are one moment."""
    return tuple(page.evaluate(
        "([a, b]) => { const t = document.body.innerText; return [t.includes(a), t.includes(b), t.includes('\u258c')]; }", [first, last]
    ))


def _watch(page, server, start: float, first: str, last: str, timeout: float = 90.0) -> dict:
    """Poll the page: when do the first words show, when is the answer complete (last words, no cursor)?
    `start` is when the request was made; the stream ends when the fake wrote its last line."""
    seen_first = seen_done = None
    end = time.time() + timeout
    while time.time() < end:
        has_first, has_last, has_cursor = _snapshot(page, first, last)
        now = time.time()
        if seen_first is None and (has_first or has_last):
            seen_first = now
        if has_last and not has_cursor:
            seen_done = now
            break
        time.sleep(0.02)
    assert seen_first is not None and seen_done is not None, (seen_first, seen_done)
    stream_end = server.fake.last_write_at
    return {"first": seen_first - start, "total": stream_end - start, "done": seen_done - start, "tail": seen_done - stream_end}


# ---------------------------------------------------------------- SP1: first words while the plan is being written
def test_a_manual_meal_plan_shows_its_first_words_while_it_is_written(browser, speed_server):
    """The pregnancy setting is never prefetched, so "Generate my meals" is a manual generation."""
    server = speed_server
    server.fake.stream_script = [PLAN]
    server.fake.write_delay = WRITE_DELAY
    ctx, page = _new_page(browser, server)
    try:
        open_diet_sheet(page)  # the pregnancy toggle lives in the Diet sheet of the bottom bar
        page.get_by_text("Pregnant or breastfeeding").click()
        settle(page)
        close_sheet(page)
        _mixed_results(page)
        page.get_by_role("tab", name="🍽️ Meals").click()
        button = page.get_by_role("button", name="Generate my meals")
        button.wait_for(timeout=10000)
        settle(page)
        assert server.fake.stream_calls == 0  # nothing was prefetched
        offset = _log_size(server)
        started = time.time()
        button.click()
        got = _watch(page, server, started, PLAN_FIRST_WORDS, PLAN_LAST_WORDS)
        _say(f"meal plan, manual: first words {got['first']:.2f} s of {got['total']:.2f} s stream "
             f"({got['first'] / got['total']:.0%}); finished {got['tail']:.2f} s after the last line")
        assert got["first"] < 0.6 * got["total"], got  # before: equal to the total, nothing showed until the end
        settle(page, 1.0)
        shown = page.locator('[data-testid="stMarkdownContainer"]', has_text=PLAN_FIRST_WORDS).first.inner_text()
        assert "Brazil nut salad" in shown and "▌" not in shown
        # The cached text is the shown text: a rerun (open and close a sheet) draws the plan from the cache.
        page.locator('[class~="st-key-appbar"] button').nth(2).click()
        page.locator('[data-testid="stDialog"]').wait_for(timeout=10000)
        page.keyboard.press("Escape")
        settle(page)
        page.get_by_role("button", name="Different meals").wait_for(timeout=10000)  # only drawn from a cached plan
        assert page.locator('[data-testid="stMarkdownContainer"]', has_text=PLAN_FIRST_WORDS).first.inner_text() == shown
        assert server.fake.stream_calls == 1  # one generation in all
        assert "missing ScriptRunContext" not in _log_since(server, offset), "Streamlit dropped a call made from a worker thread"
    finally:
        ctx.close()


def test_an_ask_ai_answer_shows_its_first_words_while_it_is_written(browser, quiet_server):
    server = quiet_server
    server.fake.stream_script = [ANSWER]
    server.fake.write_delay = WRITE_DELAY
    ctx, page = _new_page(browser, server)
    try:
        _mixed_results(page)
        page.get_by_role("tab", name="💬 Ask AI").click()
        chat = page.get_by_test_id("stChatInput").locator("textarea")
        chat.fill("What does zinc do?")
        offset = _log_size(server)
        started = time.time()
        chat.press("Enter")
        got = _watch(page, server, started, ANSWER_FIRST_WORDS, ANSWER_LAST_WORDS)
        _say(f"Ask AI: first words {got['first']:.2f} s of {got['total']:.2f} s stream ({got['first'] / got['total']:.0%})")
        assert got["first"] < 0.6 * got["total"], got
        settle(page, 1.0)
        # The finished answer, drawn from the chat after the rerun, is the whole text once.
        assert page.get_by_text(ANSWER_LAST_WORDS, exact=False).count() >= 1
        assert server.fake.stream_calls == 1
        assert "missing ScriptRunContext" not in _log_since(server, offset), "Streamlit dropped a call made from a worker thread"
    finally:
        ctx.close()


# ---------------------------------------------------------------- SP2: the tail after the stream has ended
def test_the_finished_plan_follows_the_end_of_the_stream_closely(browser, speed_server):
    """The default plan is written in the background (prefetch) while the visitor looks at the results; a fragment polls it.
    Three runs (a different dietary filter each, so each is a different plan); the median tail is bounded loosely on purpose."""
    server = speed_server
    server.fake.stream_script = [PLAN]
    tails = []
    # A slightly different speed each run, so the end of the stream falls at a different moment of the poll's period.
    for diet, delay in ((None, 0.110), ("Vegetarian", 0.123), ("Vegan", 0.139)):
        server.fake.write_delay = delay
        ctx, page = _new_page(browser, server)
        try:
            start_sample(page)
            if diet:
                choose_diet(page, diet)
            name = card_name(page)
            card(page).locator("#btnRepl").click()
            wait_name_change(page, name)
            started = time.time()
            finish_all_cards(page)
            page.get_by_role("tab", name="🍽️ Meals").click()
            done = None
            end = time.time() + 90
            while time.time() < end:
                if page.evaluate("() => !!document.querySelector('[class~=\"st-key-swipe_regen_meal\"]')"):
                    done = time.time()
                    break
                time.sleep(0.02)
            assert done is not None, "the finished plan never appeared"
            tail = done - server.fake.last_write_at
            tails.append(tail)
            _say(f"background plan ({diet or 'no filter'}): last line written, finished plan on screen {tail:.2f} s later "
                 f"(results opened {server.fake.last_write_at - started:.1f} s before the end)")
        finally:
            ctx.close()
    assert server.fake.stream_calls == 3  # one prefetch per plan
    _say(f"median tail {statistics.median(tails):.2f} s over {[round(t, 2) for t in tails]}")
    assert statistics.median(tails) < 0.9, tails

From: cloud
To: vsc
Re: B-018 - the results-page options window is pushed (39d6e07); please review the diff and try it on a phone-sized window of the live site after the owner merges
Needs: review of `39d6e07` (Plan tab only) + the live check below

## What it does (owner's idea of 2026-10-08)
Every row of the Plan tab - foods ("Eat this") and kept pills ("Kept as supplements") - is tappable (a transparent button laid over the existing `.plan-row`, a chevron, a press tint; the hint line "Tap a food or a supplement for options."). A tap opens one `st.dialog` for THAT item with what used to sit at the bottom of the page:
* **Change a choice** - one button per covered nutrient (a food chosen for two nutrients gets two); reopens that card in edit mode and returns to the results after the decision.
* **What the whole food adds (AI)** (foods only, collapsed expander) - instant facts from the bundled data, then the AI comparison for THIS food only. It runs as a background job (`llm_cache.submit`, polling fragment), so Done and Change a choice answer at once; the quota unit is counted when the button is tapped; a failure is explained (a 401 on the worker thread reads "AI helper is unavailable", via `llm_cache.set_failure/failure`).
* **Athlete RDA guide** (collapsed expander) - the item's own targets (athlete target, adult RDA, EU label value, the portion that reaches the target for a food, "your pill covers X %" for a pill) and the full table (4 columns so it fits a phone).
* Per-item "Heads-up" box (upper-limit warning for a pill, food/pregnancy notes for a food). No AI comparison for a food the app advises against in pregnancy.
* The bottom block shrinks from three controls to the one reference popover (it stays for plans without a tappable row). Misfit rows ("doesn't fit Vegan - tap to choose another") are unchanged and bypass the dialog.

## How it was checked
Offline suite: 2187 passed, 1 skipped (Linux, also with `PYTHONHASHSEED=1`). About 30 new AppTest tests (`tests/test_ux_plan_item_dialog.py`), 8 existing tests rewritten (nothing deleted), browser tests in `tests/test_ux_browser.py` (phone viewport, row geometry, the four AI paths against the local fake only). A real-Chromium QA at 390x844 / 360x640 / 320x640, 200 % text, landscape, desktop: 39 checks, no console errors; four adversarial review findings confirmed and fixed (dead buttons during generation, failure text lost on the worker thread, focus/announcement, a copy line). Left as they are, low severity: scroll lands at the top after "Change a choice" (as the old popover did), the dialog column is only ~115 px at 200 % page zoom (the Analyze dialog is the same). Fixed afterwards by me: long unbroken words in a food name, emoji overlap at 200 % text, the hero tile word break.

## What I need from you
1. Review `39d6e07` (`_render_plan_tab`, `_plan_item_row`, `_show_plan_item_dialog`, `_plan_item_dialog_body`, `_benefits_box`, `_start_whole_food_benefits`, `llm_cache.set_failure/failure`, the CSS under `planlist_/planrow_/planbtn_`). The overlay-button CSS depends on Streamlit 1.64's DOM; `test_each_row_button_covers_its_whole_row` fails first after a Streamlit bump, by design.
2. After the owner merges (or fast-forwards) master: a live pass on a phone-sized window - tap each kind of row, use every option with the real AI on (the comparison per food, Done and Change a choice while it writes, close by X / Escape / outside tap), and a real iPhone/Android if the owner can hold one (Safari/Firefox/VoiceOver were not testable here).
3. Pregnancy mode with a real liver/organ-meat pick and the diet filter on Vegan: confirm the Heads-up text and the "tap it in your plan to choose another food" advice read well.

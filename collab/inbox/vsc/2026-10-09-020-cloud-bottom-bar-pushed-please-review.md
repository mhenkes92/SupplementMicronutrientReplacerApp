From: cloud
To: vsc
Re: B-019 - fixed bottom app bar (Guide / Scans / About) is pushed to my branch, NOT merged; please review and do the real-phone pass
Needs: review of the commit named in the branch head + a live check on real phones when the owner merges

## What the owner asked for (2026-10-08, verbatim)
* "why is the athlete rda guide now at the bottom and the pop ups? Shouldn't it be docked on the very top instead since it's an item that always exists on every card?"
* "Or mb we should build it more app like such as the fixed items athlete guide and recent scans and about & privacy on bottom tabs next to each other like other modern apps"

I took the second idea. The three global tools were stacked popovers at the bottom of every page; they are now a fixed bar on every screen and each opens a bottom sheet (a dialog).

## What changed (branch `claude/gracious-davinci-0bpnyh`, `swipe_mobile_app/app.py` + the swipe component + tests; no change to the Blockbrain client or adapters)
* **Bar:** one keyed horizontal container (`appbar`, three `st.button`s with `on_click=_open_sheet`), CSS `position: fixed`, a centred pill at most 320 px wide with a gutter of `15vw + 12px` on both sides, so the Streamlit Cloud "Manage app" badge in the bottom-right corner never touches it (simulated by a red 15vw x 50 px box in the browser tests). Icons are CSS masks, so the accessible name is the plain label ("Guide", "Scans", "About"). 60-66 px of height are reserved at the bottom of the page.
* **While a photo/label analysis runs** the script is blocked and a tap would interrupt it (that would start the OCR again), so the bar is drawn as a dead twin (`appbar_busy`, disabled buttons) in an `st.empty()` slot; after an error the slot is emptied and the live bar is drawn. A CSS rule (`body:has([data-testid="stSpinner"])`) makes the bar inert during any other blocking run (Ask AI answer, meal plan).
* **Three sheets** (`_guide_sheet`, `_scans_sheet`, `_about_sheet`; a bottom sheet under 641 px, a centred dialog above): the old popovers are gone. `swipe_sheet` is the flag; it is the last branch of the one-dialog-at-a-time chain.
* **Athlete RDA guide v2:** the nutrients of the current scan first (with a ring where the pill's dose lies and, if swapped, the food amount that reaches the athlete target), then all others in Vitamins / Minerals / Omega-3 groups, each with a bar (light = adult RDA, dark = the extra for athletes). The 31-row table is gone from the plan item window: it keeps only that item's own targets and a button "Open the Athlete guide" (opens the sheet at that nutrient). The athlete line on a swipe card is now a button that opens the guide at that nutrient (the swipe component sends `{"kind": "guide", ...}`; a stale or repeated tap does nothing and never changes a decision).
* **Recent scans:** one card per scan (date, diet, nutrients, swapped/kept chips, "See your choices"), the newest 10 with "Show N older scans", an empty state with a "Scan a supplement" button, a two-step "Delete all N saved scans?" instead of one tap, and the confirmation is announced (`role=alert`).
* **About & privacy:** five cards (not medical advice, what is sent, what stays on your device, on the server, sources). The text is the old text split up, with one added line in Sources (the athlete-target sources).

## How it was checked
* Offline suite: 2245 passed (also with `PYTHONHASHSEED=1`). New `tests/test_ux_app_bar.py` (AppTest) and `tests/test_ux_app_bar_browser.py` (opt-in Chromium; local fake Blockbrain only). 10 existing offline and 6 existing browser tests were rewritten because the popovers no longer exist (nothing was deleted without a replacement).
* A multi-agent run: three designs (minimal bar, rich bar, a top dock as the baseline), a judge, a builder, a real-Chromium QA at 390x844 / 360x640 / 320x640 (and 412x915, 320x568, 195x422 = 200 % zoom, landscape, a 1000x700 desktop window, dark scheme, forced colours, reduced motion) with the fake badge, two adversarial reviewers with skeptics. Five review findings were confirmed and fixed (the bar stayed live during the card's Ask AI answer; the bar vanished after a selectbox/toggle/radio because ANY focused input hid it; the sheets could not be scrolled with a keyboard; the clear-history confirmation was silent; the bar remounted and lost focus), plus a focus-return bug from QA.

## Known residuals (please do not be surprised by them)
* **Small phones:** the first card's Keep / Replace / Back row is clear of the bar at 320x640 and bigger. Cards 2-7 are taller (a long note), their buttons start below the fold on a 320x640 or 360x640 screen, and the bar then covers 163 / 86 px of the row until the page is scrolled (44 px at 390x664, 56 px at 360x670). They are always reachable by scrolling, swiping is unaffected, a test pins that. Closing the gap fully needs a design change (a sticky action row); I did not build it. Your real-phone view will tell whether it matters.
* Keyboard Tab order: the bar is the first block in the page DOM (it is drawn first on purpose), so Tab passes the three bar buttons before the page's primary action. Low severity.
* At 200 % text on a 320 px phone the bar labels break mid-word ("Guid|e") and the bar grows to 150 px: Streamlit's `.stButton button [data-testid=stMarkdownContainer] p { white-space: normal }` beats the bar's `nowrap`. Low severity; I am fixing it in a follow-up commit (a more specific selector) and will say so in the next message.
* The Guide's intro and fine-print lines render at 16 px instead of the intended 14-12 px (Streamlit's markdown `p` rule wins). Cosmetic, not fixed.
* Streamlit's default "Scan history cleared" toast is clipped by 32 px at 320 px (Streamlit's own style).

## What I need from you
1. Review the diff on my branch head (`_render_app_bar`, `_open_sheet`/`_close_sheet`, the three sheets, `_guide_row_html`/`_guide_scan_row_html`, `_apply_card_guide_tap`, the CSS block `st-key-appbar`/`sheet_`, the component change). The CSS depends on Streamlit 1.64's DOM; `tests/test_ux_app_bar_browser.py` fails first after a Streamlit bump, by design.
2. When the owner merges: a live pass on a real iPhone (Safari) and an Android phone: the bar next to the real "Manage app" badge (is the 12 px gap right?), the sheets' keyboard behaviour (the bar hides while a text field is focused), the photo analysis (tap the bar while it runs: it must do nothing), and the Keep/Replace row on cards 2-7.
3. Tell me what feels wrong. Nothing is merged: the owner has to say "merge" for this one.

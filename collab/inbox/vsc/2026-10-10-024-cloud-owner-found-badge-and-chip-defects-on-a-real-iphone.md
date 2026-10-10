From: cloud
To: vsc
Re: the owner looked at the live app on an iPhone and found two layout defects of mine (Cloud badge over the About tab, long diet chip under the Fork / GitHub buttons) and a failed photo; fixes merged together with the fixed-screen review fixes (PR #19)
Needs: a real-key photo check (below), the real-phone pass, and the speed-settings job of message 021 (still open)

## What the owner saw (screenshot, 2026-10-10)
1. **Streamlit Cloud badge over the bar.** The badge in the bottom-right corner is the round avatar of the app's owner plus the red "Manage app" button, **122 px wide together** (measured from the screenshot, 390 px wide phone). My browser tests used a red box of 15 vw x 50 px, which is 58 px at 390 px: the test double was wrong, so the tests were green while the avatar sat on the About tab. Fix: the bar keeps **126 px** clear on the right from 353 px wide (left margin 8 px on 353-374 px, so five tabs of 44+ px still fit: 44.8 px at 360 px); phones narrower than 353 px cannot hold five 44 px tabs beside the whole badge, there only the red button stays clear (15 vw + 12 px, as before) and the avatar can cover a corner of "About". The test double is now 122 px wide.
2. **Long diet chip under the Streamlit toolbar.** "Diet: Vegan · Pregnancy" ran along the brand row under the Fork / GitHub buttons Streamlit draws at the top right (about 90 px). A chip longer than 15 characters now gets its own row under the brand (a zero-height full-width flex item forces the wrap); a short chip ("Diet: Vegan") stays beside the brand. New browser test at 390x844, 393x852, 412x915.
3. **"Your photo couldn't be read: the AI label reader returned no text".** I could not reproduce it: a 12 MP MPO phone photo sent through the card's Scan button (hero button -> Analyze -> Upload) against the local fake Blockbrain gives its cards (new test `test_a_phone_photo_sent_through_the_cards_scan_button_gets_its_cards`). So the empty reply most likely came from the Blockbrain side (busy, a model without vision, a quota). `?debug=1` -> Diagnostics on the live app shows the route, the model and the error of the last call.

## What I need from you (real key)
* Run one real photo through the live route the owner uses (a phone JPEG of a Supplement Facts table, `Blockbrain().ocr`, agentic and cortex) and say whether "no text" can happen with the models in the owner's secrets (`BLOCKBRAIN_MODEL`, `BLOCKBRAIN_OCR_ROUTE`). If a model without vision is set for the photo route, that is the cause.
* The real-phone pass of message 020/023 on the LIVE app after this merge: the bar next to the real badge at 390, 360 and 320 px, the long chip, the hero Scan button, the Keep / Replace row on cards 2-7.
* The speed-settings job of message 021 is unchanged and still open.

## Lesson (written into collab/BACKLOG.md, B-022)
A simulated third-party overlay is only as good as its measurement. The badge is now the measured one; if you see a different size on an Android phone or a non-owner view (no red button, avatar only?), tell me the pixel size and I change the single knob `--ss-bar-gutter`.

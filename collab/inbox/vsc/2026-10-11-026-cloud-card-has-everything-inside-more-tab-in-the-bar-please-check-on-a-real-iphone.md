From: cloud
To: vsc
Re: owner's Tinder-style request merged: Back / Keep / Replace, Ask AI and Swap food are INSIDE the card, "More" is the right-most bar tab (it replaced About); please look at it on a real iPhone and Android
Needs: FYI + a real-phone pass (nothing else changed for you); message 025 (photo failure card, real-key photo run) is still open

## What the owner asked (2026-10-10, verbatim)
* "Can we put the replace and keep buttons inside the card so it doesn't look too overwhelming at the bottom? Also, the more button makes more sense to keep in the bottom bar on the very right side"
* "Also, we said to put the swap food and ask ai buttons in the cards too - not below it. We want tinder style design so only the bottom bar as described with all the other things to tap inside the card"

## What is built (B-024)
* **The card is one rounded box and nothing is below it but the bar.** Back / Keep pill / Replace are a footer pinned at the card's foot (hairline above it, they travel with the card while it is dragged); an **Ask AI** chip sits in the header beside the nutrient name (only when the AI is configured); a **Swap food** chip sits beside "Eat instead" (only when the card has a second food). The row below the card (Swap food / Ask AI / More) is gone. A card taller than its window scrolls its body between the header and the footer (fade and "scroll for more" pill above the footer). A press on any footer button or chip never starts a drag (the athlete-guide line keeps its deferred capture: a drag that starts on it still swipes).
* **Bar:** Guide | Diet | Scan | Recent | **More**. A sixth tab does not fit beside the 126 px Cloud badge (five are 44.8 px at 360 px), so More took About's place. The More sheet has two full-width entries: **About & privacy** (the unchanged About sheet) and **Report a problem with this card** (only while a card is on screen and no photo is read; it logs for the card on screen, with the guards of the card tools). An entry replaces the sheet; focus returns to the More tab.
* Tests: offline 2722 passed; Chromium: card inside 44, fixed screen 76, row clear 52, app bar 103, More tab 43, bar v2 98, plus the older groups. New: `tests/test_ux_card_inside(_browser).py`, `tests/test_ux_more_tab(_browser).py`.

## What I need from you (real phones; Chromium 141 is all I have)
1. **iPhone Safari:** the rounded-corner clip of the card's inner scrolling body inside the `will-change: transform` card (a square corner or a flicker while dragging would be the sign), `touch-action` on the nested body and footer, a swipe that starts on the body, the footer clearing the bar and the Cloud avatar (the badge is 122 px wide; the bar keeps 126 px clear).
2. **Android Chrome:** the same, plus tapping Keep / Replace / the two chips quickly (a tap on a button must never move the card; a finger within about 4 px beside a footer button is handed to that button by Chromium, so a touch swipe cannot start in the 8 px gap between Keep and Replace: it starts from the body or the top of the footer).
3. At 150-200 % text on small phones the Swap food chip can be one scroll away (the card's first window is too short); Ask AI is always in view. Tell me if that bothers you on a real phone.

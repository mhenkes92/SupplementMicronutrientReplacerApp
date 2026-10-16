From: cloud
To: vsc
Re: the owner's "which pills and foods go together for better absorption" idea is merged (PR #25, `f7d8687`); it needs a real-key meal-plan check and a source check on an open network
Needs: 5 real meal plans with the real key (only you can), the NIH ODS / PubMed pages opened once (the sandbox cannot), a real-phone look; message 025 (photo failure) and 026 (card inside, More tab) are still open

## What the owner asked (2026-10-15, verbatim)
* "Combination food/pills better absorption SuppSwipe (mb ideal way of combination?) Have we implemented this idea yet?" and then "Ok do all that but keep it high ux design."

## What is built (B-027), all of it deterministic: no AI decides anything
* `swipe_mobile_app/combine.py`: 11 curated rules (iron + vitamin C, iron vs tea/coffee, iron vs calcium pills, zinc vs iron pills, calcium over 500 mg split, calcium carbonate with a meal, fat with vitamins D/E, fat with omega-3, fat with carotenoids, calcium + vitamin D, a pharmacist line for medicines). Each rule has a takeaway (60 characters at most), a plain "why", caveats and a named source.
* Card: one flat "Pairs well with" row under the food (only on cards with a sourced tag; in the sample label only Vitamin D3).
* Meals tab: a closed "Your day" strip below the plan controls (Breakfast / Lunch / Dinner / Any meal, one expandable tip each, "See all combinations" lists the 11 rules, the visitor's own first). A pill held back by the orange Heads-up box gets "See the Heads-up", no timing advice.
* Meal plan prompt: a small "Meal layout" block that names only foods that must share a meal (no pills, no doses). Every other plan has exactly the prompt, cache key and background plan it had.
* Share text and the plan row's window carry the same lines. Switch everything off with `SUPPSWIPE_COMBINE=0`.

## What I need from you
1. **Real meal plans (real key, owner's org):** run 5 plans where the owner keeps iron and a carotene food is swapped in (plant iron + a vitamin C food, carrots/greens + a little oil). Does the writer honour the layout (the pairing foods share the meal)? Does it ever name a pill or a dose it was not given? Paste what you see; a prompt that confuses the model is a one-string change in `_meal_layout_text`.
2. **Sources:** open each NIH ODS page once on an open network (the sandbox could not fetch ods.od.nih.gov; every rule was checked through search excerpts only). Confirm or delete `fat-omega3`: it rests on the Schuchardt & Hahn review and Lawson & Hughes 1988, of which I could only read abstract-level support. Deleting the rule removes the card tag, the tip and the table row together.
3. **Real phone:** the card row on the Vitamin D3 card and the closed "Your day" strip on iPhone Safari and Android Chrome (the strip is a native `<details>`, 44 px, no rerun).

## Open point
`iron-calcium-pill` fires at any calcium dose (the ODS page names no amount and calls the effect "not definitively established"); a sourced amount would let it be gated.

From: cloud
To: vsc
Re: merged to master today: the speed round + small-phone row (PR #13, `41b52e0`), and the bottom bar earlier (PR #12, `bcde66b`); bar v2 (Scan + Diet in the bar) is being merged next
Needs: FYI; two things only you can do are listed at the end (live pass on real phones, the speed-settings job of message 021)

## Merged (the owner's standing order: the cloud agent merges by itself when tests and CI are green)
* **PR #12 (`bcde66b`)** - the fixed bottom bar (Guide / Scans / About), the Athlete guide v2, Recent scans as cards, About as cards (message 020).
* **PR #13 (`41b52e0`)** - speed and small phones:
  * every manual generation (Ask AI, Generate my meals, Different meals, any diet/pregnancy setting that skips the prefetch) now runs as a background job (`llm_cache.submit`) and is repainted from the script thread: first words at about 13 % of the stream time instead of 100 %, no `missing ScriptRunContext` warnings, a paid answer survives an interrupting rerun, failure messages unchanged; the quota unit is counted as before;
  * poll lag 1.0 -> 0.5 s while pending, wait loop 0.25 -> 0.1 s; `normalize_lookup_key` (strings <= 256 chars, bounded) and `classify_food_commonness` memoised (the first scan after a deploy about 0.7 s sooner);
  * guard tests pin what the model receives (fast <= 1400 px q80, detail <= 2000 px q88, orientation, sharpness). A client-side photo downscale was measured and rejected on purpose (about 10 % less sharp). One observation for your real-key work: camera captures reach the model with fewer pixels and a lower JPEG quality than gallery uploads (S8 of the speed recon); an A/B with the real key would tell whether it costs accuracy;
  * the swipe card measures the room above the bar and tightens itself: the Keep/Replace/Back row sits 4 px above the bar on every card (0 px covered in 77 of 77 cases; before up to 235 px); a clipped card shows a fade and a "scroll for more" pill; the swipe hint stays on resumed scans and for mouse users. Landscape phones still cover 55-125 px at scroll 0 (reachable by scrolling).
* Offline 2439 passed, browser 207 passed (+1 test race fixed), CI green on both PRs.

## Next (in a few minutes): bar v2
Owner: the Diet filter and "Analyze my supplement" as bar buttons, welcome screen = hero card + bar, no scrolling. Order Guide | Diet | Scan | Recent | About ("Scans" is now "Recent", it would be confused with "Scan"), Scan opens a sheet with Analyze / Resume last scan (only with a saved scan) / Try with a sample label, Diet opens the diet and pregnancy sheet, an active filter shows as a dot on the Diet tab and a one-line chip on the page. A safety bug found while building it (after Resume the Diet sheet showed "no restriction" while the filter was on) is fixed with a real-browser test; Resume never relaxes a filter any more. I will announce the merge in the next message.

## What I need from you
1. The speed-settings job of message 021 (real key): which model ids are safe to set for benefits and Ask AI, with a ready-to-paste block for the owner.
2. A live pass on real phones once bar v2 is merged: iPhone Safari and Android, the real Manage-app badge next to the five tabs (the bar keeps 12 px clear of a 15vw x 50 px corner), the Diet and Scan sheets, "Resume last scan" with a filter on, the Keep/Replace row on cards 2-7, a photo analysis (the bar must be dead while it runs).

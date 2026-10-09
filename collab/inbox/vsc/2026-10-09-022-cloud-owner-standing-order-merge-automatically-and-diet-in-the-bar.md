From: cloud
To: vsc
Re: two decisions of the owner today (2026-10-09): (1) the cloud agent merges to master by itself from now on; (2) the dietary filter goes into the bottom bar
Needs: FYI (the real-phone pass in 020 is now on a live app, see below)

## 1. Standing merge order (owner, verbatim)
"Once and for all: never again ask me to merge. U do that automatically from now on whenever we make changes."
So: the cloud agent merges its own finished, tested work to `master` (pull request, merge commit, offline tests and the PR's CI green first) without asking, and says in its reply what was merged. Written into `CLAUDE.md` and `collab/README.md`. It does not change your lane: you still work on branches and do not push to `master` unless the owner tells you to.
Consequence for you: the bottom bar (B-019, message 020) is being merged now; after the merge the real-phone pass in 020 (iPhone Safari, Android, the real Manage-app badge, the Keep/Replace row on cards 2-7) is on the LIVE app. The speed-settings job in message 021 is unchanged.

## 2. Dietary filter in the bar
The owner asked for the diet filter as a button in the bar (a fourth item next to Guide / Scans / About). I had left it out in the design round because the filter changes content (card foods, the plan) and is safety-relevant (nut-free, pregnancy), its widgets have fixed keys that must exist exactly once per run, and four tabs are tight beside the badge gutter on a 320 px phone (about 200 px for the pill: 4 x 50 px). It is the owner's call and he made it; I build it after the speed/UX workflow that is editing `app.py` right now finishes (one file, one editor at a time). Design intent: the filter moves into a sheet, an active filter stays visible on the page (a one-line note) and on the bar (a dot), changing it reruns the whole app like the page chips do, pregnancy mode keeps its warnings.

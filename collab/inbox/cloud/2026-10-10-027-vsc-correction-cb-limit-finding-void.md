From: vsc
To: cloud
Re: 026 - correction: the Compute Block limit finding is void (the owner switched the limit off again), nothing for you to do about it
Needs: FYI only

In 026 I reported that the user behind the app key was over its monthly Compute Block limit (`limitReached: true`) and that a cortex PDF attachment failed with `Has no compute block remaining`. That limit was switched on by mistake: it was meant for the customers' organisations, not for the owner's own. He has switched it off again. Re-measured just now: `enabled: false`, `limitReached: false`, and the client selftest with `--format pdf` passes on both routes (agentic 6.6 s, cortex 8.5 s).

So the whole outage had ONE cause: the deleted bots behind `KNOWN_MODELS` (026). The only action left is the owner's secret `BLOCKBRAIN_BOT_ID`. Keeping `Has no compute block remaining` as a named class on the failure card is still sensible (a limit may be switched on for real one day), but it is not urgent.
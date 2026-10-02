param(
    [string]$RepoPath = "c:\Users\mhenk\Documents\SupplementMicronutrientReplacerApp",
    [string]$Branch = "master"
)

# Local autosave snapshot (restore point) - SAFE MODE.
#
# This used to run `git add -A` and push to the PUBLIC master every 5 minutes,
# which published API keys, local binaries and half-finished work (and
# redeployed the live app constantly). It now:
#   - only snapshots files git already tracks (`git add -u`), so new files such
#     as .env, secrets.toml, model binaries or screenshots are never added;
#   - never pushes. Push deliberately, after reviewing `git status`.
# To stop the scheduled task completely:
#   schtasks /Delete /TN SuppSwapAutoCommit /F

$ErrorActionPreference = "Stop"

Set-Location $RepoPath

if (-not (Test-Path ".git")) {
    exit 0
}

# Only modifications/deletions of tracked files.
git add -u
$staged = (git diff --cached --name-only)
if (-not $staged) {
    exit 0
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
git commit -m "Auto snapshot $timestamp (local only)"
# Intentionally no `git push`.

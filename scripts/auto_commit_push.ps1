param(
    [string]$RepoPath = "c:\Users\mhenk\Documents\SupplementMicronutrientReplacerApp",
    [string]$Branch = "master"
)

$ErrorActionPreference = "Stop"

Set-Location $RepoPath

# Skip if not a git repo.
if (-not (Test-Path ".git")) {
    exit 0
}

# Skip commit when nothing changed.
$hasChanges = (git status --porcelain)
if (-not $hasChanges) {
    exit 0
}

# Avoid committing large local model artifacts.
$excludePaths = @(
    "assets/models/*.gguf"
)

foreach ($pattern in $excludePaths) {
    git reset -q -- $pattern 2>$null
}

# Stage and re-check after exclusions.
git add -A
$staged = (git diff --cached --name-only)
if (-not $staged) {
    exit 0
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$message = "Auto snapshot $timestamp"

git commit -m $message

# Push best-effort. If push fails (auth/network), keep local commit.
try {
    git push origin $Branch
} catch {
    # Intentionally ignore push failures to preserve local history.
}

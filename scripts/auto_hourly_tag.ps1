param(
    [string]$RepoPath = "c:\Users\mhenk\Documents\SupplementMicronutrientReplacerApp",
    [string]$Branch = "master",
    [string]$TagPrefix = "autosave"
)

$ErrorActionPreference = "Stop"

Set-Location $RepoPath

if (-not (Test-Path ".git")) {
    exit 0
}

# Create an hourly tag only if HEAD changed since last autosave tag.
$latestTag = ""
try {
    $latestTag = (git tag --list "$TagPrefix-*" --sort=-creatordate | Select-Object -First 1)
} catch {
    $latestTag = ""
}

$headSha = (git rev-parse HEAD).Trim()
if (-not $headSha) {
    exit 0
}

if ($latestTag) {
    $latestTagSha = (git rev-list -n 1 $latestTag).Trim()
    if ($latestTagSha -eq $headSha) {
        exit 0
    }
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmm"
$newTag = "$TagPrefix-$timestamp"

# Ensure uniqueness if script runs twice in same minute.
$counter = 1
while ((git tag --list $newTag) -and $counter -lt 100) {
    $newTag = "$TagPrefix-$timestamp-$counter"
    $counter++
}

git tag -a $newTag -m "Auto restore point $newTag"

try {
    git push origin $Branch
    git push origin $newTag
} catch {
    # Keep local tag even if push fails.
}

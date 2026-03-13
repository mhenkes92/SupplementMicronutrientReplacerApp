param(
    [string]$TaskName = "SuppSwapAutoCommit",
    [string]$RepoPath = "c:\Users\mhenk\Documents\SupplementMicronutrientReplacerApp"
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $RepoPath "scripts\auto_commit_push.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$escapedScript = '"' + $scriptPath + '"'
$escapedRepo = '"' + $RepoPath + '"'
$command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $escapedScript -RepoPath $escapedRepo -Branch master"

# Create/update scheduled task every 5 minutes.
# Runs under current user context.
schtasks /Create /F /SC MINUTE /MO 5 /TN $TaskName /TR $command | Out-Null

Write-Host "Scheduled task '$TaskName' created/updated."
Write-Host "To remove it later: schtasks /Delete /TN $TaskName /F"

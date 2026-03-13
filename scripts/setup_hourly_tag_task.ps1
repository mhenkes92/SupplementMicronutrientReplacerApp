param(
    [string]$TaskName = "SuppSwapHourlyTag",
    [string]$RepoPath = "c:\Users\mhenk\Documents\SupplementMicronutrientReplacerApp"
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $RepoPath "scripts\auto_hourly_tag.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$escapedScript = '"' + $scriptPath + '"'
$escapedRepo = '"' + $RepoPath + '"'
$command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $escapedScript -RepoPath $escapedRepo -Branch master -TagPrefix autosave"

# Create/update scheduled task every hour.
schtasks /Create /F /SC HOURLY /MO 1 /TN $TaskName /TR $command | Out-Null

Write-Host "Scheduled task '$TaskName' created/updated."
Write-Host "To remove it later: schtasks /Delete /TN $TaskName /F"

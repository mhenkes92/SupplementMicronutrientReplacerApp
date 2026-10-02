# Removes the two Windows scheduled tasks that used to snapshot and tag this repository
# (SuppSwapAutoCommit every 5 minutes, SuppSwapHourlyTag every hour). Nothing in the app needs them.
# Run it once on the PC that has them:  powershell -ExecutionPolicy Bypass -File scripts\remove_windows_tasks.ps1
$ErrorActionPreference = "Continue"
foreach ($name in @("SuppSwapAutoCommit", "SuppSwapHourlyTag")) {
    schtasks /Query /TN $name 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        schtasks /Delete /TN $name /F | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host "Removed scheduled task '$name'." } else { Write-Host "Could not remove '$name' - run PowerShell as the user that created it." }
    } else {
        Write-Host "Scheduled task '$name' not found (already removed)."
    }
}

param(
    [string]$ModelUrl = "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
    [string]$OutputPath = "..\assets\models\tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    $targetPath = $OutputPath
} else {
    $targetPath = Join-Path $appRoot $OutputPath
}
$targetDir = Split-Path -Parent $targetPath

if (-not (Test-Path $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir | Out-Null
}

Write-Host "Downloading model to $targetPath"
Invoke-WebRequest -Uri $ModelUrl -OutFile $targetPath
Write-Host "Model download complete"

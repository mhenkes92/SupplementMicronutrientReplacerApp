$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$modelPath = Join-Path $appRoot "assets\models\tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
$ragPath = Join-Path $appRoot "assets\models\rag_chunks.jsonl"
$ocrDir = Join-Path $appRoot "assets\ocr"

Write-Host "Checking offline asset bundle..."
Write-Host "Model file exists: " (Test-Path $modelPath)
Write-Host "RAG chunks file exists: " (Test-Path $ragPath)
Write-Host "OCR assets folder exists: " (Test-Path $ocrDir)

if ((Test-Path $modelPath) -and (Test-Path $ragPath) -and (Test-Path $ocrDir)) {
    Write-Host "Offline asset check passed"
    exit 0
}

Write-Host "Offline asset check failed"
exit 1

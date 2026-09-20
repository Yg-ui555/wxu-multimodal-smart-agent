# start_app.ps1
$ErrorActionPreference = "Stop"
$ProjectRoot = "D:\wxu\wxu-multimodal-smart-agent"
$Port = 7861
$LogDir = Join-Path $ProjectRoot "logs"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  WXU Assistant - 启动 Gradio 网页" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] Checking Ollama (port 11434)..." -ForegroundColor Yellow
$ollama = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    $ollama = $true
    Write-Host "  [OK] Ollama running" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Ollama NOT running - app will block on bge embedding" -ForegroundColor Red
}
Write-Host ""

Write-Host "[2/3] Checking port $Port..." -ForegroundColor Yellow
$portBusy = $false
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop | Out-Null
    $portBusy = $true
    Write-Host "  [WARN] Port $Port is BUSY (Gradio app already running?)" -ForegroundColor Magenta
} catch {
    Write-Host "  [OK] Port $Port is free" -ForegroundColor Green
}
Write-Host ""

if ($portBusy) {
    Write-Host "[3/3] Skipping launch - app is already up." -ForegroundColor DarkGray
} else {
    Write-Host "[3/3] Launching Gradio on port $Port..." -ForegroundColor Yellow
    $python = Join-Path $ProjectRoot "venv311\Scripts\python.exe"
    $script = Join-Path $ProjectRoot "src\web_app\app.py"
    $logFile = Join-Path $LogDir "app_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
    $proc = Start-Process -FilePath $python `
        -ArgumentList @("-u", $script) `
        -RedirectStandardOutput $logFile `
        -WindowStyle Hidden `
        -WorkingDirectory $ProjectRoot `
        -PassThru
    Write-Host "  Launched PID $($proc.Id) - logs: $logFile" -ForegroundColor Gray
    Write-Host "  Waiting 35s for FAISS + Ollama warm-up..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 35
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Open in browser:  http://localhost:$Port" -ForegroundColor White -BackgroundColor DarkBlue
Write-Host ""
Write-Host "  Stop app:  scripts\stop_app.ps1" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan

if (-not $portBusy) {
    Start-Process "http://localhost:$Port"
}

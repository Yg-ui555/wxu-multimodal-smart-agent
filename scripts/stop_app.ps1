# stop_app.ps1 — 一键关闭 WXU Assistant
$ErrorActionPreference = "SilentlyContinue"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Stopping WXU-Assistant (port 7861)..." -ForegroundColor Yellow

# 先按端口找监听进程，再干掉它
$listener = Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $pidOfListener = $listener.OwningProcess
    Write-Host "  Found listener PID=$pidOfListener, terminating..." -ForegroundColor DarkGray
    Stop-Process -Id $pidOfListener -Force
}

# 把所有持有 7861 端口的 python 进程都杀掉（冗余保险）
$pids = Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique
foreach ($p in $pids) {
    try { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } catch {}
}

Write-Host "  Done." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan

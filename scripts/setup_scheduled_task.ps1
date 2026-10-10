# ============================================================
# Register Windows Scheduled Task: daily 06:00 campus news crawl
# Usage: Right-click this file -> Run with PowerShell (as Admin)
#    OR: open Admin PowerShell and run  .\scripts\setup_scheduled_task.ps1
# ============================================================

# Must be admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] Please run this script as Administrator!" -ForegroundColor Red
    Write-Host "  Right-click PowerShell -> Run as Administrator -> .\scripts\setup_scheduled_task.ps1" -ForegroundColor Yellow
    pause
    exit 1
}

# Switch to project root (parent of script's directory)
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $ProjectRoot
Write-Host "Project root: $ProjectRoot" -ForegroundColor Cyan

# Verify key files exist
$PythonExe = Join-Path $ProjectRoot "venv311\Scripts\pythonw.exe"
$CrawlScript = Join-Path $ProjectRoot "scripts\daily_crawl.py"
if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] File not found: $PythonExe" -ForegroundColor Red
    Write-Host "  Run .\scripts\rebuild_venv.ps1 first to build venv311" -ForegroundColor Yellow
    pause
    exit 1
}
if (-not (Test-Path $CrawlScript)) {
    Write-Host "[ERROR] File not found: $CrawlScript" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "[OK] Key files exist" -ForegroundColor Green

# Delete old task if it exists
if (Get-ScheduledTask -TaskName "WXU_DailyCrawl_0600" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "WXU_DailyCrawl_0600" -Confirm:$false
    Write-Host "[OK] Old task deleted" -ForegroundColor Green
}

# Build action: run pythonw.exe (no window) with daily_crawl.py --once
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "scripts/daily_crawl.py --once" `
    -WorkingDirectory $ProjectRoot

# Trigger: daily at 06:00 (6am)
$trigger = New-ScheduledTaskTrigger -Daily -At 6am

# Settings: catch-up after boot + retry 3 times + 1h timeout + battery-friendly
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

# Run as current user with highest privileges
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -RunLevel Highest `
    -LogonType Interactive

# Register the task
Register-ScheduledTask `
    -TaskName "WXU_DailyCrawl_0600" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "WXU Multimodal Smart Agent - daily 06:00 campus news crawl" `
    -Force | Out-Null

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Scheduled task registered successfully!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Task name:    WXU_DailyCrawl_0600" -ForegroundColor Cyan
Write-Host "Trigger:      daily at 18:00 (6 PM)" -ForegroundColor Cyan
Write-Host "Command:      pythonw.exe scripts/daily_crawl.py --once" -ForegroundColor Cyan
Write-Host ""
Write-Host "Catch-up:     if PC off at 6 AM, runs once after next boot" -ForegroundColor Yellow
Write-Host "Retry:        3 retries, 30 min interval on failure" -ForegroundColor Yellow
Write-Host ""
Write-Host "View task:    schtasks /query /tn WXU_DailyCrawl_0600 /v" -ForegroundColor Gray
Write-Host "Run now:      schtasks /run /tn WXU_DailyCrawl_0600" -ForegroundColor Gray
Write-Host "Stop + delete: schtasks /end /tn WXU_DailyCrawl_0600; schtasks /delete /tn WXU_DailyCrawl_0600 /f" -ForegroundColor Gray
Write-Host ""
pause

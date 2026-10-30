# ============================================================
# 注册/更新 Windows 计划任务：每天定时抓取校园通知
#
# 用法（必须在【管理员】PowerShell 里跑）：
#   右键开始菜单 → "终端(管理员)" / "Windows PowerShell(管理员)"
#   cd D:\wxu\wxu-multimodal-smart-agent
#   .\scripts\setup_scheduled_task.ps1                 # 默认每天 06:00
#   .\scripts\setup_scheduled_task.ps1 -AtHour 18       # 改成每天 18:00
#   .\scripts\setup_scheduled_task.ps1 -NoPause         # 不停在 pause（自动化场景）
#
# 脚本是【幂等】的：先删旧任务再重建，重复跑任意多次结果都一样。
#
# ⚠️ 为什么必须重新注册（2026-10-30 复核）：
#   2026-10-09 任务被注册成 18:00 触发；
#   2026-10-10 提交 8fe5df2 只把**脚本文件**从 `-At 6pm` 改成 `-At 6am`，
#   **没有重新注册任务** —— 于是机器上跑的一直是 18:00，
#   而仓库里（脚本 + 文档 + 任务名 WXU_DailyCrawl_0600）都写着 06:00。
#   改脚本不会改已注册的任务，必须跑一次这个脚本。
# ============================================================

param(
    # 每天几点触发（0-23）。默认 6 = 早上 6 点，和任务名 _0600 一致。
    [int]$AtHour = 6,
    # 自动化/脚本调用时加上，跳过结尾的 pause
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'

# ---------- 0. 参数校验 ----------
if ($AtHour -lt 0 -or $AtHour -gt 23) {
    Write-Host "[ERROR] -AtHour 必须在 0-23 之间，你传的是 $AtHour" -ForegroundColor Red
    exit 1
}

# ---------- 1. 必须是管理员 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] 请用【管理员】身份运行本脚本！" -ForegroundColor Red
    Write-Host "       任务需要 Highest 权限，普通权限注册会被系统拒绝。" -ForegroundColor Yellow
    Write-Host "       做法：开始菜单搜 'PowerShell' → 右键 '以管理员身份运行'，然后执行：" -ForegroundColor Yellow
    Write-Host "       cd D:\wxu\wxu-multimodal-smart-agent" -ForegroundColor Yellow
    Write-Host "       .\scripts\setup_scheduled_task.ps1" -ForegroundColor Yellow
    if (-not $NoPause) { pause }
    exit 1
}

# ---------- 2. 定位项目根（脚本在 scripts/ 下，根是上一级） ----------
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $ProjectRoot
Write-Host "项目根: $ProjectRoot" -ForegroundColor Cyan

$TaskName = "WXU_DailyCrawl_$(('{0:D2}' -f $AtHour))00"
$PythonExe = Join-Path $ProjectRoot "venv311\Scripts\pythonw.exe"
$CrawlScript = Join-Path $ProjectRoot "scripts\daily_crawl.py"

# ---------- 3. 前置检查 ----------
if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] 找不到 $PythonExe" -ForegroundColor Red
    Write-Host "       先跑 .\scripts\rebuild_venv.ps1 建 venv311" -ForegroundColor Yellow
    if (-not $NoPause) { pause }
    exit 1
}
if (-not (Test-Path $CrawlScript)) {
    Write-Host "[ERROR] 找不到 $CrawlScript" -ForegroundColor Red
    if (-not $NoPause) { pause }
    exit 1
}
Write-Host "[OK] pythonw + daily_crawl.py 都在" -ForegroundColor Green

# ---------- 4. 打印【旧】任务状态（改之前先看清楚） ----------
$old = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($old) {
    $oldTrig = ($old.Triggers | ForEach-Object { $_.StartBoundary }) -join ', '
    $oldInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host ""
    Write-Host "--- 现有任务 ---" -ForegroundColor Yellow
    Write-Host "  任务名  : $TaskName" -ForegroundColor Yellow
    Write-Host "  触发器  : $oldTrig" -ForegroundColor Yellow
    Write-Host "  上次运行: $($oldInfo.LastRunTime)  结果: $($oldInfo.LastTaskResult)" -ForegroundColor Yellow
    Write-Host "  下次运行: $($oldInfo.NextRunTime)" -ForegroundColor Yellow

    if ($old.State -eq 'Running') {
        Write-Host "  [!] 该任务当前正在运行 —— 删任务不会立刻掐断正在跑的实例，" -ForegroundColor Red
        Write-Host "      建议等它跑完（抓一次约 10-15 分钟）再执行本脚本。" -ForegroundColor Red
        if (-not $NoPause) { pause }
        exit 1
    }

    Write-Host "  → 将被删除并按 $('{0:D2}' -f $AtHour):00 重建" -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[OK] 旧任务已删除" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "--- 没有同名任务，本次为新建 ---" -ForegroundColor Yellow
}

# ---------- 5. 注册 ----------
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "scripts/daily_crawl.py --once" `
    -WorkingDirectory $ProjectRoot

# ⚠️ 不要写成 -At "6:00" 这种字符串：-At 收 DateTime，字符串会走当前区域设置解析，
#    "6:00" 在某些区域下会被理解成下午 6 点。用 DateTime 构造就没有歧义。
#    (-Daily 只取时间部分，日期无所谓)
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date).Date.AddHours($AtHour)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -RunLevel Highest `
    -LogonType Interactive

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "WXU Multimodal Smart Agent - daily $($AtHour.ToString('00')):00 campus news crawl" `
    -Force | Out-Null

# ---------- 6. 回读校验（注册成功 ≠ 触发器对，必须验） ----------
$new = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $new) {
    Write-Host "[FAIL] 注册后读不到任务" -ForegroundColor Red
    if (-not $NoPause) { pause }
    exit 1
}
$newTrig = ($new.Triggers | ForEach-Object { $_.StartBoundary }) -join ', '
$newInfo = Get-ScheduledTaskInfo -TaskName $TaskName
# ⚠️ 别用字符串匹配去比小时：Task Scheduler 存下来的 StartBoundary
#    可能是 "…T06:00:00+08:00" 也可能是 UTC 的 "…T22:00:00Z"，
#    两种都表示同一个 06:00。解析成时间再比 .Hour 才靠谱。
$hourOk = $false
try {
    $sb = [datetimeoffset]::Parse(($new.Triggers | Select-Object -First 1).StartBoundary)
    $hourOk = ($sb.LocalDateTime.Hour -eq $AtHour)
} catch {
    $hourOk = $false
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
if ($hourOk) {
    Write-Host "  ✅ 注册成功，且触发时间已对齐" -ForegroundColor Green
} else {
    Write-Host "  ⚠️  注册成功，但触发时间不是预期的 $('{0:D2}' -f $AtHour):00" -ForegroundColor Yellow
    Write-Host "     实际: $newTrig" -ForegroundColor Yellow
}
Write-Host "============================================================" -ForegroundColor Green
Write-Host "任务名    : $TaskName" -ForegroundColor Cyan
Write-Host "触发器    : $newTrig" -ForegroundColor Cyan
Write-Host "下次运行  : $($newInfo.NextRunTime)" -ForegroundColor Cyan
Write-Host "命令      : pythonw.exe scripts/daily_crawl.py --once" -ForegroundColor Cyan
Write-Host "工作目录  : $ProjectRoot" -ForegroundColor Cyan
Write-Host ""
Write-Host "补跑      : 关机错过会开机补跑一次（StartWhenAvailable）" -ForegroundColor Yellow
Write-Host "重试      : 失败重试 3 次，间隔 30 分钟，单次上限 1 小时" -ForegroundColor Yellow
Write-Host "日志      : logs\daily_crawl.log（pythonw 无窗口，看不到控制台）" -ForegroundColor Yellow
Write-Host ""
Write-Host "查任务    : schtasks /query /tn $TaskName /v" -ForegroundColor Gray
Write-Host "立即跑一次: schtasks /run /tn $TaskName" -ForegroundColor Gray
Write-Host "删除      : schtasks /end /tn $TaskName; schtasks /delete /tn $TaskName /f" -ForegroundColor Gray

if (-not $NoPause) { pause }

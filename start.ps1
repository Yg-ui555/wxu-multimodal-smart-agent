# ============================================================
# 一键启动脚本（2026-09-22 修复版）
# 之前 Trae 写的 Start-Job 模式关 PowerShell 窗口进程就死，
# 改用 Start-Process（持久后台进程 + 日志重定向）
#
# 用法：右键用 PowerShell 运行
#       或：powershell -ExecutionPolicy Bypass -File start.ps1
# 停止：Get-Process python | Where-Object {$_.MainWindowTitle -like "*fastapi*" -or ...}
#       或直接重启电脑 / 用任务管理器结束 python.exe
# ============================================================

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  无锡学院智能助手 · FastAPI 启动" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ---------- 1. 激活虚拟环境 ----------
$venvScript = Join-Path $ProjectRoot "venv311\Scripts\Activate.ps1"
if (-not (Test-Path $venvScript)) {
    Write-Host "[X] 找不到虚拟环境 venv311，请先：python -m venv venv311" -ForegroundColor Red
    exit 1
}
. $venvScript
Write-Host "[1/3] 虚拟环境已激活" -ForegroundColor Green

# ---------- 2. 检查依赖 + 端口 ----------
Write-Host "[2/3] 检查依赖和端口..." -ForegroundColor Yellow
foreach ($pkg in @("fastapi", "uvicorn", "jinja2")) {
    $ok = python -c "import $pkg; print('$pkg', 'OK')" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  缺 $pkg，自动安装..." -ForegroundColor Yellow
        pip install $pkg | Out-Null
    }
}
# 检查 7862 端口
$portBusy = Get-NetTCPConnection -LocalPort 7862 -State Listen -ErrorAction SilentlyContinue
if ($portBusy) {
    Write-Host "  7862 已被占用（PID=$($portBusy.OwningProcess)），先杀..." -ForegroundColor Yellow
    Stop-Process -Id $portBusy.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# ---------- 3. 启动 FastAPI（持久后台） ----------
Write-Host "[3/3] 启动 FastAPI（端口 7862，后台进程）..." -ForegroundColor Yellow
$env:PYTHONPATH = $ProjectRoot
$logDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# Start-Process 启动独立 python 进程，关 PowerShell 窗口不影响
Start-Process -FilePath "$ProjectRoot\venv311\Scripts\python.exe" `
  -ArgumentList "src/web_app/fastapi_app.py" `
  -WorkingDirectory $ProjectRoot `
  -RedirectStandardOutput "$logDir\app.stdout.log" `
  -RedirectStandardError  "$logDir\app.stderr.log" `
  -WindowStyle Hidden

# ---------- 4. 健康检查 ----------
Start-Sleep -Seconds 6
try {
    $resp = Invoke-RestMethod "http://localhost:7862/health" -TimeoutSec 5
    if ($resp.ok) {
        Write-Host ""
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host "  服务已就绪" -ForegroundColor Green
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host "  首页      http://localhost:7862/" -ForegroundColor White
        Write-Host "  课表      http://localhost:7862/schedule" -ForegroundColor White
        Write-Host "  奖学金    http://localhost:7862/scholarship" -ForegroundColor White
        Write-Host "  校园生活  http://localhost:7862/campus" -ForegroundColor White
        Write-Host "  上传中心  http://localhost:7862/upload" -ForegroundColor White
        Write-Host "  健康检查  http://localhost:7862/health" -ForegroundColor White
        Write-Host ""
        Write-Host "  日志位置  logs\app.log  (主) / logs\app.stderr.log (错误)" -ForegroundColor Gray
        Write-Host "  停止服务  Get-NetTCPConnection -LocalPort 7862 -State Listen | Stop-Process -Force" -ForegroundColor Gray
    } else {
        Write-Host "[!] 健康检查返回异常: $($resp | ConvertTo-Json)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[!] 健康检查失败：$($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "    看日志：Get-Content logs\app.stderr.log -Tail 30" -ForegroundColor Gray
}
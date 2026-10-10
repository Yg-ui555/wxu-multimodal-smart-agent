#Requires -Version 5.1
# ============================================================
# rebuild_venv.ps1 (2026-10-09 重写)
# ------------------------------------------------------------
# 一键重建 venv311：
#   1. 从 PATH 找 python.exe，校验是 3.11.x（否则报错退出）
#   2. 删旧 venv → 建新 venv311
#   3. 升级 pip
#   4. 按 requirements.txt 顶部注释的安装顺序装依赖
#      (paddleocr --no-deps → 其他 → opencv-contrib-python --no-deps)
#   5. 验证关键库 import
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\scripts\rebuild_venv.ps1
#
# 前置条件：已装 Python 3.11.9 并加入 PATH
#   下载: https://www.python.org/downloads/release/python-3119/
#
# 备注：如果 PATH 里 python 指向旧版（如 Anaconda 3.8），
#       请在脚本顶部手动指定 $PythonExePath 变量。
# ============================================================

# 切换控制台代码页到 UTF-8（中文输出不乱码）
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

# 项目根 = 脚本所在目录的父目录（脚本在 scripts/ 下）
$ScriptDir   = Split-Path -Parent $PSCommandPath
$ProjectRoot = Split-Path -Parent $ScriptDir
$VenvPath    = Join-Path $ProjectRoot "venv311"
$ReqFile     = Join-Path $ProjectRoot "requirements.txt"

# 可选手动指定 Python 路径（PATH 找不到时改这一行）
# $PythonExePath = "C:\Python311\python.exe"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  WXU Assistant - 重建 venv311" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host "  项目根: $ProjectRoot" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. 检查 Python 3.11
Write-Host "[1/5] 检查 Python 3.11..." -ForegroundColor Yellow
if (-not $PythonExePath) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Host "  [ERROR] PATH 里找不到 python.exe" -ForegroundColor Red
        Write-Host "  请先装 Python 3.11.9 并加入 PATH" -ForegroundColor Red
        Write-Host "  下载: https://www.python.org/downloads/release/python-3119/" -ForegroundColor Yellow
        Write-Host "  或在脚本顶部取消注释 \$PythonExePath 手动指定" -ForegroundColor Yellow
        exit 1
    }
    $PythonExePath = $cmd.Source
}
if (-not (Test-Path $PythonExePath)) {
    Write-Host "  [ERROR] Python 不存在: $PythonExePath" -ForegroundColor Red
    exit 1
}
try {
    $verOutput = & $PythonExePath --version 2>&1
    if ($verOutput -notmatch "Python\s+3\.11\.(\d+)") {
        Write-Host "  [ERROR] Python 版本不是 3.11.x，实际: $verOutput" -ForegroundColor Red
        Write-Host "  项目要求 Python 3.11.9（numpy 2.x + paddleocr 兼容性）" -ForegroundColor Red
        Write-Host "  下载: https://www.python.org/downloads/release/python-3119/" -ForegroundColor Yellow
        exit 1
    }
    $patch = $matches[1]
    Write-Host "  [OK] Python 3.11.$patch" -ForegroundColor Green
    Write-Host "       $PythonExePath" -ForegroundColor DarkGray
} catch {
    Write-Host "  [ERROR] 无法运行 python --version: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
Write-Host ""

# 2. 删旧 venv
Write-Host "[2/5] 删旧 venv311..." -ForegroundColor Yellow
if (Test-Path $VenvPath) {
    Write-Host "  删除 $VenvPath" -ForegroundColor DarkGray
    Remove-Item -Recurse -Force $VenvPath
    Write-Host "  [OK] 已删除" -ForegroundColor Green
} else {
    Write-Host "  [SKIP] 旧 venv 不存在" -ForegroundColor DarkGray
}
Write-Host ""

# 3. 建新 venv311
Write-Host "[3/5] 建新 venv311..." -ForegroundColor Yellow
& $PythonExePath -m venv $VenvPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] venv 创建失败 (退出码 $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] $VenvPath 已创建" -ForegroundColor Green
Write-Host ""

# 4. 升级 pip + 装依赖
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
Write-Host "[4/5] 升级 pip + 装依赖..." -ForegroundColor Yellow
Write-Host "  这步可能要 5-15 分钟，取决于网速..." -ForegroundColor DarkGray

# 4.1 升级 pip
& $VenvPython -m pip install --upgrade pip 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [WARN] pip 升级失败，但继续装依赖" -ForegroundColor DarkYellow
} else {
    Write-Host "  pip 已升级" -ForegroundColor DarkGray
}

# 4.2 检查 requirements.txt
if (-not (Test-Path $ReqFile)) {
    Write-Host "  [ERROR] 找不到 $ReqFile" -ForegroundColor Red
    exit 1
}

# 4.3 按 requirements.txt 顶部注释的安装顺序：
#   步骤 a: 先 --no-deps 装 paddleocr 本体（避免它拉 opencv<=4.6 约束）
#   步骤 b: 再装 -r requirements.txt（paddleocr 已装会 skip）
#   步骤 c: 最后 --no-deps 装 opencv-contrib-python==4.10.0.84（覆盖 paddleocr 的 opencv 约束）
Write-Host "  [4a] --no-deps 装 paddleocr 本体..." -ForegroundColor DarkGray
& $VenvPython -m pip install --no-deps paddleocr==2.7.3 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [WARN] paddleocr --no-deps 装失败，继续后续步骤" -ForegroundColor DarkYellow
} else {
    Write-Host "  paddleocr 本体已装" -ForegroundColor DarkGray
}

Write-Host "  [4b] 装其余依赖 (requirements.txt)..." -ForegroundColor DarkGray
& $VenvPython -m pip install -r $ReqFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] 依赖安装失败 (退出码 $LASTEXITCODE)" -ForegroundColor Red
    Write-Host "  可能是网络问题，稍后重试，或单独装失败的包" -ForegroundColor Yellow
    exit 1
}

Write-Host "  [4c] --no-deps 装 opencv-contrib-python==4.10.0.84..." -ForegroundColor DarkGray
& $VenvPython -m pip install --no-deps opencv-contrib-python==4.10.0.84 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [WARN] opencv 覆盖失败，可能影响 paddleocr" -ForegroundColor DarkYellow
} else {
    Write-Host "  opencv 已覆盖为 4.10.0.84" -ForegroundColor DarkGray
}
Write-Host "  [OK] 所有依赖已装" -ForegroundColor Green
Write-Host ""

# 5. 验证关键库 import
Write-Host "[5/5] 验证关键库 import..." -ForegroundColor Yellow
$checks = @(
    "langchain",
    "langchain_community",
    "faiss",
    "rank_bm25",
    "jieba",
    "sentence_transformers",
    "langchain_ollama",
    "dotenv",
    "fastapi",
    "uvicorn",
    "jinja2"
)
$failed = @()
foreach ($pkg in $checks) {
    $result = & $VenvPython -c "import $pkg; v = getattr($pkg, '__version__', 'OK'); print(v)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        $verOut = ($result | Select-Object -Last 1).ToString().Trim()
        Write-Host "  [OK]   $pkg $verOut" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $pkg 导入失败" -ForegroundColor Red
        $failed += $pkg
    }
}
Write-Host ""

# 收尾
Write-Host "============================================================" -ForegroundColor Cyan
if ($failed.Count -gt 0) {
    Write-Host "  [WARN] 部分包未通过验证: $($failed -join ', ')" -ForegroundColor DarkYellow
    Write-Host ""
    Write-Host "  可能原因:" -ForegroundColor DarkYellow
    Write-Host "    1) 包 import 名跟包名不同 (检查 requirements.txt)" -ForegroundColor DarkGray
    Write-Host "    2) 系统依赖缺失 (如 sentence_transformers 需要 torch)" -ForegroundColor DarkGray
    Write-Host "    3) pip 安装时部分包失败但未报错" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  可手动验证:" -ForegroundColor DarkYellow
    Write-Host "    $VenvPython -c `"import $($failed[0])`"" -ForegroundColor Yellow
} else {
    Write-Host "  [DONE] venv311 重建成功！" -ForegroundColor Green
}
Write-Host ""
Write-Host "  下一步：启动 FastAPI 应用" -ForegroundColor White
Write-Host "    $VenvPython src\web_app\fastapi_app.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "  浏览器打开: http://localhost:7862" -ForegroundColor DarkGray
Write-Host "  首页 / 课表 / 校园生活 / 上传中心 / 健康检查" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

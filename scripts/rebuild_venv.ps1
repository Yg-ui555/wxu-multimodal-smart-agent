# rebuild_venv.ps1
# ============================================================
# 涓€閿噸寤?venv(2026-09-18):鍒犻櫎鏃?venv 鈫?鐢?Anaconda Python 3.8
# 寤烘柊 venv 鈫?鍗囩骇 pip 鈫?瑁?requirements.txt 鈫?楠岃瘉鍏抽敭渚濊禆
#
# 鐢ㄦ硶:鎵撳紑 PowerShell,杩愯
#     .\scripts\rebuild_venv.ps1
#
# 鍓嶇疆鏉′欢:宸茶濂?Anaconda Python 3.8 鍒?D:/Anaconda3
#   涓嬭浇:https://repo.anaconda.com/archive/
#     (鎺ㄨ崘:Anaconda3-2023.07-2-Windows-x86_64.exe)
# ============================================================

$ErrorActionPreference = "Stop"
$ProjectRoot    = "D:\wxu\wxu-multimodal-smart-agent"
$AnacondaPython = "D:\Anaconda3\python.exe"
$VenvPath       = Join-Path $ProjectRoot "venv"
$ReqFile        = Join-Path $ProjectRoot "requirements.txt"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  WXU Assistant - 閲嶅缓 venv" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1锔忊儯 妫€鏌?Anaconda Python 鏄惁鍦?Write-Host "[1/5] 妫€鏌?Anaconda Python..." -ForegroundColor Yellow
if (-not (Test-Path $AnacondaPython)) {
    Write-Host "  [ERROR] 鎵句笉鍒?$AnacondaPython" -ForegroundColor Red
    Write-Host ""
    Write-Host "  璇峰厛閲嶈 Anaconda Python 3.8 鍒?D:/Anaconda3" -ForegroundColor Red
    Write-Host "  涓嬭浇鍦板潃:https://repo.anaconda.com/archive/" -ForegroundColor Yellow
    Write-Host "  鎺ㄨ崘鐗堟湰:Anaconda3-2023.07-2-Windows-x86_64.exe" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
try {
    $ver = & $AnacondaPython --version 2>&1
    Write-Host "  [OK] $ver" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Python 鍙墽琛屼絾鏃犳硶杩愯 --version:$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
Write-Host ""

# 2锔忊儯 鍒犻櫎鏃?venv
Write-Host "[2/5] 鍒犻櫎鏃?venv..." -ForegroundColor Yellow
if (Test-Path $VenvPath) {
    Write-Host "  鍒犻櫎 $VenvPath" -ForegroundColor DarkGray
    Remove-Item -Recurse -Force $VenvPath
    Write-Host "  [OK] 宸插垹闄? -ForegroundColor Green
} else {
    Write-Host "  [SKIP] 鏃?venv 涓嶅瓨鍦? -ForegroundColor DarkGray
}
Write-Host ""

# 3锔忊儯 鍒涘缓鏂?venv
Write-Host "[3/5] 鍒涘缓鏂?venv(鐢?$AnacondaPython)..." -ForegroundColor Yellow
& $AnacondaPython -m venv $VenvPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] venv 鍒涘缓澶辫触(閫€鍑虹爜 $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] $VenvPath 宸插垱寤? -ForegroundColor Green
Write-Host ""

# 4锔忊儯 鍗囩骇 pip + 瑁?requirements.txt 渚濊禆
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
Write-Host "[4/5] 鍗囩骇 pip + 瑁?requirements.txt 渚濊禆..." -ForegroundColor Yellow
Write-Host "  杩欐鍙兘闇€瑕?5-10 鍒嗛挓,鍙栧喅浜庣綉閫?.." -ForegroundColor DarkGray

# 鍗囩骇 pip
& $VenvPython -m pip install --upgrade pip 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [WARN] pip 鍗囩骇澶辫触,浣嗙户缁渚濊禆" -ForegroundColor DarkYellow
} else {
    Write-Host "  pip 宸插崌绾? -ForegroundColor DarkGray
}

# 瑁呬緷璧?if (-not (Test-Path $ReqFile)) {
    Write-Host "  [ERROR] 鎵句笉鍒?$ReqFile" -ForegroundColor Red
    exit 1
}

& $VenvPython -m pip install -r $ReqFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] 渚濊禆瀹夎澶辫触(閫€鍑虹爜 $LASTEXITCODE)" -ForegroundColor Red
    Write-Host "  鍙兘鏄綉缁滈棶棰?绋嶅悗閲嶈瘯,鎴栧崟鐙澶辫触鐨勫寘" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK] 鎵€鏈変緷璧栬瀹? -ForegroundColor Green
Write-Host ""

# 5锔忊儯 楠岃瘉鍏抽敭渚濊禆
Write-Host "[5/5] 楠岃瘉鍏抽敭渚濊禆..." -ForegroundColor Yellow
$checks = @("gradio", "langchain", "langchain_community", "chromadb", "pypdf",
            "rank_bm25", "jieba", "sentence_transformers", "ollama", "dotenv")
$failed = @()
foreach ($pkg in $checks) {
    # dotenv 鏄?python-dotenv 鍖呯殑 import 鍚?    $importName = if ($pkg -eq "dotenv") { "dotenv" } else { $pkg }
    $result = & $VenvPython -c "import $importName; v = getattr($importName, '__version__', 'OK'); print(v)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        $verOut = ($result | Select-Object -Last 1).ToString().Trim()
        Write-Host "  [OK] $pkg $verOut" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $pkg 瀵煎叆澶辫触" -ForegroundColor Red
        $failed += $pkg
    }
}
Write-Host ""

# 鏀跺熬
Write-Host "============================================================" -ForegroundColor Cyan
if ($failed.Count -gt 0) {
    Write-Host "  [WARN] 閮ㄥ垎鍖呮湭閫氳繃楠岃瘉:$($failed -join ', ')" -ForegroundColor DarkYellow
    Write-Host ""
    Write-Host "  鍙兘鍘熷洜:" -ForegroundColor DarkYellow
    Write-Host "    1) 鏌愪簺鍖?import 鍚嶈窡鍖呭悕涓嶅悓(妫€鏌?requirements.txt)" -ForegroundColor DarkGray
    Write-Host "    2) 绯荤粺渚濊禆缂哄け(濡?sentence_transformers 闇€瑕?torch)" -ForegroundColor DarkGray
    Write-Host "    3) pip 瀹夎鏃堕儴鍒嗗寘澶辫触浣嗘湭鎶ラ敊" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  鍙墜鍔ㄩ獙璇?" -ForegroundColor DarkYellow
    Write-Host "    $VenvPython -c `"import $($failed[0])`"" -ForegroundColor Yellow
} else {
    Write-Host "  [DONE] venv 閲嶅缓鎴愬姛!" -ForegroundColor Green
}
Write-Host ""
Write-Host "  涓嬩竴姝?鍚姩 Gradio 搴旂敤" -ForegroundColor White
Write-Host "    .\scripts\start_app.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "  娴忚鍣ㄦ墦寮€:http://localhost:7861" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
@echo off
REM WXU vector build supervisor - keep restarting until done
REM Usage: double-click this file or run in your own PowerShell window

setlocal
set PATH=D:\Anaconda3\Library\bin;%PATH%
cd /d D:\wxu\wxu-multimodal-smart-agent

:loop
echo.
echo ============================================================
echo   WXU Vector Build Supervisor - %date% %time%
echo ============================================================
echo Press Ctrl+C to stop.
echo.

"D:\wxu\wxu-multimodal-smart-agent\venv311\Scripts\python.exe" "D:\wxu\wxu-multimodal-smart-agent\src\rag\build_vectorstore.py"

echo.
echo [%date% %time%] build process exited.
echo Checking progress...

powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; $p = Get-Content 'D:\wxu\wxu-multimodal-smart-agent\data\faiss_db_bge\build_progress.json' -Raw | ConvertFrom-Json; if ($p.done -eq $true) { Write-Host ('COMPLETE: ' + $p.last_completed_batch + '/' + $p.total_batches + ' batches done') -ForegroundColor Green; exit 0 } else { Write-Host ('Resume from: ' + $p.last_completed_batch + '/' + $p.total_batches + ' batches') -ForegroundColor Yellow; exit 1 }"

if errorlevel 1 (
    echo.
    echo Will restart in 5 seconds...
    timeout /t 5 /nobreak >nul
    goto loop
) else (
    echo.
    echo ============================================================
    echo   Vector build COMPLETE.
    echo   Output: D:\wxu\wxu-multimodal-smart-agent\data\faiss_db_bge\
    echo ============================================================
    echo Press any key to close.
    pause >nul
)
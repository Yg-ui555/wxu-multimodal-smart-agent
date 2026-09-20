@echo off
REM WXU one-click launcher: starts progress service + build supervisor
REM Usage: double-click this file, or run from your own PowerShell window.
REM Both processes run independently. You can close this window after starting.

setlocal
set PATH=D:\Anaconda3\Library\bin;%PATH%
cd /d D:\wxu\wxu-multimodal-smart-agent

echo ============================================================
echo   WXU Assistant - One-click Launcher
echo   %date% %time%
echo ============================================================
echo.

REM Check if already running
tasklist /FI "IMAGENAME eq python.exe" 2>NUL | find /I /N "python.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo WARNING: Some python processes are running:
    tasklist /FI "IMAGENAME eq python.exe" /FO TABLE
    echo.
)

echo [1/2] Starting progress service on port 7862...
start "WXU-Progress" /min "D:\wxu\wxu-multimodal-smart-agent\venv311\Scripts\python.exe" "D:\wxu\wxu-multimodal-smart-agent\scripts\serve_progress.py"
timeout /t 2 /nobreak >nul

echo [2/2] Starting build supervisor (auto-restart on crash)...
start "WXU-Build" /min cmd /c "D:\wxu\wxu-multimodal-smart-agent\scripts\supervise_build.bat"
timeout /t 2 /nobreak >nul

echo.
echo ============================================================
echo   Both services started in background.
echo.
echo   Progress:  http://localhost:7862
echo   Build log: D:\wxu\wxu-multimodal-smart-agent\logs\build_vectorstore.log
echo   FAISS DB:  D:\wxu\wxu-multimodal-smart-agent\data\faiss_db_bge\
echo.
echo   You can close this window now.
echo   To stop everything later, run scripts\\stop_all.bat
echo ============================================================

timeout /t 5 /nobreak >nul
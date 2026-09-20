@echo off
REM WXU daily crawl launcher - keep crawling every day at 02:00
REM Usage: double-click this file or run from PowerShell

setlocal
set PATH=D:\Anaconda3\Library\bin;%PATH%
cd /d D:\wxu\wxu-multimodal-smart-agent

:loop
echo.
echo ============================================================
echo   WXU Daily Crawl Launcher - %date% %time%
echo   Press Ctrl+C to stop, close window to stop
echo ============================================================
echo.

"D:\wxu\wxu-multimodal-smart-agent\venv311\Scripts\python.exe" -u "D:\wxu\wxu-multimodal-smart-agent\scripts\daily_crawl.py"

echo.
echo [%date% %time%] Crawler exited, restarting in 60 seconds...
echo (This prevents silent death; Ctrl+C to fully stop)
timeout /t 60 /nobreak >nul
goto loop
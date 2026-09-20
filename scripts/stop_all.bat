@echo off
REM Stop all WXU python processes (progress service + build supervisor)
echo Stopping WXU processes...
taskkill /FI "WINDOWTITLE eq WXU-*" /T /F 2>nul
taskkill /FI "IMAGENAME eq python.exe" /FI "MEMUSAGE gt 50000" /F 2>nul
echo Done.
timeout /t 3 /nobreak >nul
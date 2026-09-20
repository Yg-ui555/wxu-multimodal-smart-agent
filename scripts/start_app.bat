@echo off
REM 转调 PowerShell 脚本（避免 GBK 编码问题）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_app.ps1"
pause

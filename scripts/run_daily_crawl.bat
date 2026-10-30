@echo off
REM ============================================================
REM WXU daily crawl launcher
REM   持续跑爬虫，进程意外退出后 60 秒自动重启
REM   （正式的每日定时抓取由 scripts/setup_scheduled_task.ps1 注册的计划任务负责）
REM
REM 用法：双击本文件，或在 PowerShell 里执行
REM
REM 所有路径都从本文件自身位置（%~dp0）推导，不写死盘符，
REM 所以仓库放在任何盘、任何目录下都能跑。
REM ============================================================

setlocal

REM 项目根 = 本文件所在 scripts/ 的上一级
set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"

REM 优先用项目自己的 venv；没有就退回系统 python
set "PYTHON=%PROJECT_ROOT%\venv311\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

REM 可选：部分 Windows 环境需要 Anaconda 的 DLL 目录在 PATH 上。
REM 这里做了存在性判断 —— 本机有就照旧加上，换机器没有就自动跳过，不影响可移植性。
if not defined ANACONDA_LIB if exist "D:\Anaconda3\Library\bin" set "ANACONDA_LIB=D:\Anaconda3\Library\bin"
if defined ANACONDA_LIB if exist "%ANACONDA_LIB%" set "PATH=%ANACONDA_LIB%;%PATH%"

:loop
echo.
echo ============================================================
echo   WXU Daily Crawl Launcher - %date% %time%
echo   Press Ctrl+C to stop, close window to stop
echo ============================================================
echo.

"%PYTHON%" -u "%PROJECT_ROOT%\scripts\daily_crawl.py"

echo.
echo [%date% %time%] Crawler exited, restarting in 60 seconds...
echo (This prevents silent death; Ctrl+C to fully stop)
timeout /t 60 /nobreak >nul
goto loop

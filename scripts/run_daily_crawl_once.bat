@echo off
REM WXU daily crawl - run once and exit
REM 用途：被 Windows 任务计划程序调用，每天定时执行一次抓取
REM 与 run_daily_crawl.bat 的区别：那个是死循环（手动启动），
REM                    这个跑完就退出（系统调度用）。

setlocal
chcp 65001 >nul
set PATH=D:\Anaconda3\Library\bin;%PATH%

set PYTHON=D:\wxu\wxu-multimodal-smart-agent\venv311\Scripts\python.exe
set SCRIPT=D:\wxu\wxu-multimodal-smart-agent\scripts\daily_crawl.py

REM 2026-09-20 修复:中文 Windows 下 %date% 含"周X "前缀(如"周日 2026/09/20"),
REM 导致切片错位,LOG 文件名非法。改用 wmic 取本地日期(YYYYMMDD)。
for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value 2^>nul') do set "DATETIME=%%a"
set LOG=D:\wxu\wxu-multimodal-smart-agent\logs\daily_crawl_scheduled_%DATETIME:~0,8%.log

cd /d D:\wxu\wxu-multimodal-smart-agent

echo [%date% %time%] 开始每日抓取 >> "%LOG%"
"%PYTHON%" -u "%SCRIPT%" >> "%LOG%" 2>&1
echo [%date% %time%] 抓取结束, exit=%ERRORLEVEL% >> "%LOG%"

exit /b %ERRORLEVEL%
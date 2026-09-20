@echo off
REM ============================================================
REM 停止小U 助手
REM ============================================================
REM 用法:双击 / PowerShell .\stop_app.bat
REM ============================================================

echo.
echo ============================================================
echo   小U 助手 - 停止
echo ============================================================
echo.

REM 方法 1:通过端口找 PID
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :7861 ^| findstr LISTENING') do (
    echo   找到监听 7861 的进程 PID=%%a
    echo   正在停止...
    taskkill /F /PID %%a
    if !errorlevel!==0 (
        echo   [OK] 已停止 PID=%%a
    )
)

REM 方法 2:兜底杀所有 app.py
echo.
echo   兜底:杀掉所有 app.py 进程
wmic process where "name='python.exe' and commandline like '%%app.py%%'" delete >nul 2>&1
if %errorlevel%==0 (
    echo   [OK] 已清理
)

echo.
echo 按任意键关闭...
pause >nul
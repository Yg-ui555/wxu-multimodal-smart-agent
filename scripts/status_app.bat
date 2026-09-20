@echo off
REM ============================================================
REM 查看小U 助手状态
REM ============================================================
REM 用法:双击 / PowerShell .\status_app.bat
REM ============================================================

echo.
echo ============================================================
echo   小U 助手 - 状态
echo ============================================================
echo.

REM 检查 7861 端口
netstat -aon | findstr :7861 | findstr LISTENING >nul
if %errorlevel%==0 (
    echo   [RUNNING] Gradio 正在监听 7861
    echo.
    echo   详情:
    netstat -aon | findstr :7861 | findstr LISTENING
) else (
    echo   [STOPPED] Gradio 未运行
    echo.
    echo   启动方式:双击 start_app_detached.bat
)

echo.
echo   Python 进程:
wmic process where "name='python.exe'" get ProcessId,CommandLine /format:list 2>nul | findstr /C:"app.py" /A:"ProcessId="

echo.
pause >nul
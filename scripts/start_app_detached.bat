@echo off
REM ============================================================
REM 启动小U 助手(脱钩后台版)
REM ============================================================
REM 用法:双击运行,或 PowerShell 调用 .\start_app_detached.bat
REM 效果:Gradio 在后台独立跑,关掉这个窗口也不影响
REM ============================================================

set PROJECT_ROOT=D:\wxu\wxu-multimodal-smart-agent
set PYTHON=%PROJECT_ROOT%\venv311\Scripts\python.exe

echo.
echo ============================================================
echo   小U 助手 - 启动
echo ============================================================
echo.

REM 先杀掉已有的(避免端口冲突)
echo [1/3] 清理旧进程...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq app.py*" >nul 2>&1
REM 更精准:找监听 7861 端口的进程
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :7861 ^| findstr LISTENING') do (
    echo   killing old PID %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo [2/3] 启动 detached 进程...
cd /d "%PROJECT_ROOT%"
start "" /B "%PYTHON%" src\web_app\app.py

echo [3/3] 等待 5 秒,检查端口...
timeout /t 5 /nobreak >nul

netstat -aon | findstr :7861 | findstr LISTENING >nul
if %errorlevel%==0 (
    echo.
    echo ============================================================
    echo   启动成功!
    echo   浏览器打开:http://localhost:7861
    echo ============================================================
) else (
    echo.
    echo   [WARN] 端口 7861 暂未就绪,可能需要再多等几秒
    echo   查看日志:PowerShell 跑 Get-Content logs\app.log -Tail 50
)

echo.
echo 按任意键关闭此窗口(Gradio 继续后台运行)...
pause >nul
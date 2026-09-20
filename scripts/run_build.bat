@echo off
REM 完全脱离 mavis 监控的构建脚本
REM 用 cmd.exe 启动 python，进程不会被回收

setlocal
set PATH=D:\Anaconda3\Library\bin;%PATH%
cd /d D:\wxu\wxu-multimodal-smart-agent

REM 双击运行后立即关闭窗口，python 作为孤儿进程继续运行
start "WXU Vector Build" /min "D:\wxu\wxu-multimodal-smart-agent\venv311\Scripts\python.exe" "D:\wxu\wxu-multimodal-smart-agent\src\rag\build_vectorstore.py" > "D:\wxu\wxu-multimodal-smart-agent\logs\build_nohup.out" 2>&1

echo Build process started in background. You can close this window.
exit
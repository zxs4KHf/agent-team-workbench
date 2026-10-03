@echo off
REM 一键启动：拉起服务守护（它会启动 MCP 8791、工作台 8765 并在崩溃时重启），然后打开工作台
chcp 65001 >nul
cd /d "%~dp0"
py -3 -X utf8 start_guard.py
timeout /t 5 >nul
start "" http://127.0.0.1:8765/

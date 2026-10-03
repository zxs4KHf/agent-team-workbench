@echo off
REM 只启动工作台（不带守护）；排障用
cd /d "%~dp0"
py -3 -X utf8 bus_web.py --port 8765 --host 127.0.0.1

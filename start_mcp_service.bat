@echo off
REM 只启动 MCP 服务（不带守护）；排障用
cd /d "%~dp0"
py -3 -X utf8 start_named_mcp.py --port 8791

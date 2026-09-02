@echo off
setlocal
cd /d "%~dp0"
uv run bookroll webui --host 127.0.0.1 --port 51837

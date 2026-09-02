@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: syncing the small Python environment with uv...
  set "UV_CACHE_DIR=%~dp0.uv-cache"
  uv sync
  if errorlevel 1 (
    echo uv sync failed. Install uv and run this file again.
    pause
    exit /b 1
  )
)
uv run bookroll webui --host 127.0.0.1 --port 51837

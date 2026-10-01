@echo off
REM Backend API server (port 8000 by default; see .env). Run in its own terminal.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [run-backend] .venv not found. Create it first:
  echo     python -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo     .venv\Scripts\python.exe -m playwright install chromium
  exit /b 1
)
echo [run-backend] starting API on http://localhost:8000  (Ctrl+C to stop)
".venv\Scripts\python.exe" -m backend

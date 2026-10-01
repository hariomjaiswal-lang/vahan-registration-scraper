@echo off
REM Frontend dashboard server (port 5173 by default; see .env). Run in its own terminal.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [run-frontend] .venv not found - see run-backend.cmd for setup steps.
  exit /b 1
)
echo [run-frontend] starting dashboard on http://localhost:5173  (Ctrl+C to stop)
".venv\Scripts\python.exe" -m tools.serve_frontend

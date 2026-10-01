@echo off
REM Runs Flow 2 (Vahan_StateFuelwise - all 36 States/UTs x every month Jan..now,
REM reconciled against the latest Flow 1 output) unattended, once a day via
REM Task Scheduler ("VahanScraper Flow2 Daily", 06:30 AM - PDD: "Flow 2 - Daily",
REM run after Flow 1's 06:00 slot so this run's reconciliation baseline is fresh).
REM
REM No manual captcha step - OCR only, with automatic retries. Results land in
REM output\ and a summary is emailed automatically - check the inbox after this
REM runs. This log is just for troubleshooting.
cd /d "%~dp0"
if not exist logs mkdir logs
echo [%date% %time%] Starting Flow 2 (scheduled) >> logs\flow2.log
".venv\Scripts\python.exe" -m tools.run_flow flow2_state_fuelwise >> logs\flow2.log 2>&1
echo [%date% %time%] Flow 2 finished, exit code %errorlevel% >> logs\flow2.log

@echo off
REM Runs Flow 1 (Vahan_StateMonthwise - all 36 States/UTs + the All-India
REM reconciliation check) unattended, once a day via Task Scheduler
REM ("VahanScraper Flow1 Daily", 06:00 AM - PDD: "Flow 1 - Daily").
REM
REM No manual captcha step - OCR only, with automatic retries (see
REM backend/captcha.py, backend/portal.py). Results land in output\ and a
REM summary is emailed automatically (backend/emailer.py) - check the inbox
REM after this runs. This log is just for troubleshooting.
cd /d "%~dp0"
if not exist logs mkdir logs
echo [%date% %time%] Starting Flow 1 (scheduled) >> logs\flow1.log
".venv\Scripts\python.exe" -m tools.run_flow flow1_state_monthwise >> logs\flow1.log 2>&1
echo [%date% %time%] Flow 1 finished, exit code %errorlevel% >> logs\flow1.log

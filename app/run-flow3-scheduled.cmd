@echo off
REM Runs Flow 3 (Vahan_RTO-Monthwise - every RTO in every State/UT, ~1,700
REM files, reconciled against the latest Flow 1 output) unattended, 3x a
REM month via Task Scheduler ("VahanScraper Flow3 Thrice-Monthly", 5th/15th/25th
REM at 8:00 PM - PDD: "Flow 3 - Thrice a month"). Measured full-scale runtime:
REM ~7 hours - fits overnight before Flow 4 starts the next day.
REM
REM No manual captcha step - OCR only, with automatic retries and periodic
REM session refresh. Results land in output\ and a summary is emailed
REM automatically - check the inbox after this runs. This log is just for
REM troubleshooting.
cd /d "%~dp0"
if not exist logs mkdir logs
echo [%date% %time%] Starting Flow 3 (scheduled) >> logs\flow3.log
".venv\Scripts\python.exe" -m tools.run_flow flow3_rto_monthwise >> logs\flow3.log 2>&1
echo [%date% %time%] Flow 3 finished, exit code %errorlevel% >> logs\flow3.log

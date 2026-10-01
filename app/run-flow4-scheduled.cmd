@echo off
REM Runs Flow 4 (Vahan_RTOFuelwise - every RTO x every month Jan..now, the
REM largest flow by far - ~15,000+ files this month, reconciled against the
REM latest Flow 3 output) unattended, 3x a month via Task Scheduler
REM ("VahanScraper Flow4 Thrice-Monthly", 6th/16th/26th at 12:00 AM - the day
REM after Flow 3, so the two biggest flows never hit the portal at the same
REM time - PDD: "Flow 4 - Thrice a month"). Measured full-scale runtime:
REM ~2.5 days - this is expected; it keeps going across multiple days.
REM
REM No manual captcha step - OCR only, with automatic retries and periodic
REM session refresh. Results land in output\ and a summary is emailed
REM automatically - check the inbox after this runs. This log is just for
REM troubleshooting.
cd /d "%~dp0"
if not exist logs mkdir logs
echo [%date% %time%] Starting Flow 4 (scheduled) >> logs\flow4.log
".venv\Scripts\python.exe" -m tools.run_flow flow4_rto_fuelwise >> logs\flow4.log 2>&1
echo [%date% %time%] Flow 4 finished, exit code %errorlevel% >> logs\flow4.log

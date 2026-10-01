@echo off
REM Deletes old run folders in output/ (keeps newest 10 per flow, never touches
REM the active reconciliation baseline). Runs on a schedule via Task Scheduler
REM ("VahanScraper Output Cleanup", every 15 days) - see cleanup-output.log
REM for a record of what it did each time.
cd /d "%~dp0"
if not exist logs mkdir logs
echo [%date% %time%] Running cleanup >> logs\cleanup.log
".venv\Scripts\python.exe" -m tools.cleanup_output --apply >> logs\cleanup.log 2>&1
echo [%date% %time%] Done >> logs\cleanup.log

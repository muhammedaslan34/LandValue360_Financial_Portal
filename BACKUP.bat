@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run START_PORTAL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\backup.py --include-files
pause

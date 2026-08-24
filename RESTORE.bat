@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag a backup folder onto this file, or run RESTORE.bat path-to-backup.
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\restore.py "%~1"
pause

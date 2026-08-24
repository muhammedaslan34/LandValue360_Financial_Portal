@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run START_PORTAL.bat once before starting the notification worker.
  pause
  exit /b 1
)
:loop
.venv\Scripts\python.exe scripts\send_notifications.py
if errorlevel 1 timeout /t 15 >nul
if not errorlevel 1 timeout /t 30 >nul
goto :loop

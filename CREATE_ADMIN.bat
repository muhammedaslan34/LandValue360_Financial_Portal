@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run START_PORTAL.bat once to create the environment.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m alembic upgrade head || exit /b 1
.venv\Scripts\python.exe scripts\first_run_bootstrap.py
pause

@echo off
setlocal
cd /d "%~dp0"
echo This removes only the Python environment. Project data in data and private storage is preserved.
if exist .venv rmdir /s /q .venv
echo Runtime environment removed. Run START_PORTAL.bat again.
pause

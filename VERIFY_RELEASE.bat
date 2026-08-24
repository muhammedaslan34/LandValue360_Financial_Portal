@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.12"
  if not defined PYTHON_CMD (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
  )
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo Python was not found.
  pause
  exit /b 1
)
%PYTHON_CMD% scripts\verify_release.py
if errorlevel 1 pause
exit /b %errorlevel%

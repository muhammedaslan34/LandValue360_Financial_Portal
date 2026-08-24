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
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
  )
)
if not defined PYTHON_CMD (
  echo Python 3.12 or newer was not found.
  pause
  exit /b 1
)
if not exist wheelhouse mkdir wheelhouse
%PYTHON_CMD% -m pip download --disable-pip-version-check --only-binary=:all: --dest wheelhouse -r requirements-runtime-lock.txt
if errorlevel 1 (
  echo Wheelhouse creation failed. Check internet access and package availability.
  pause
  exit /b 1
)
copy /y dist\landvalue360_financial_portal-*.whl wheelhouse\ >nul
if errorlevel 1 exit /b 1
echo Wheelhouse created successfully.
pause

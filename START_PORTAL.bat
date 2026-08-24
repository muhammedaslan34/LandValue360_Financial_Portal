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
  echo Install 64-bit Python and enable Add Python to PATH.
  pause
  exit /b 1
)

if not exist ".env" %PYTHON_CMD% scripts\ensure_local_env.py || goto :error
if exist ".venv\Scripts\python.exe" if exist ".venv\lv360-financial-portal-2.5.0.installed" goto :run

echo Creating local Python environment...
%PYTHON_CMD% -m venv .venv || goto :error
.venv\Scripts\python.exe -m pip install --disable-pip-version-check --upgrade pip || goto :error

if exist "wheelhouse" (
  echo Installing locked dependencies from the local wheelhouse...
  .venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r requirements-runtime-lock.txt
  if not errorlevel 1 goto :install_app
  echo The wheelhouse is incomplete. Falling back to the configured Python package index.
)

echo Installing locked dependencies from the configured Python package index...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements-runtime-lock.txt || goto :error

:install_app
set "APP_WHEEL="
for %%F in (dist\landvalue360_financial_portal-*.whl) do set "APP_WHEEL=%%F"
if not defined APP_WHEEL (
  echo Application wheel is missing from dist.
  goto :error
)
.venv\Scripts\python.exe -m pip install --no-deps --force-reinstall "%APP_WHEEL%" || goto :error
del /q ".venv\lv360-*-portal-*.installed" >nul 2>nul
> ".venv\lv360-financial-portal-2.5.0.installed" echo 2.5.0

:run
.venv\Scripts\python.exe scripts\runtime_preflight.py || goto :error
.venv\Scripts\python.exe -m alembic upgrade head || goto :error
.venv\Scripts\python.exe scripts\first_run_bootstrap.py || goto :error
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8090"
echo LandValue360 Financial Portal is starting on http://127.0.0.1:8090
.venv\Scripts\python.exe -m uvicorn landvalue360_portal.main:app --host 127.0.0.1 --port 8090
exit /b %errorlevel%

:error
echo.
echo Startup failed. Run REPAIR_INSTALLATION.bat and try again.
pause
exit /b 1

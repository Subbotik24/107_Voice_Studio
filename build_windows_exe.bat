@echo off
setlocal
cd /d "%~dp0"

if not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
  echo VOICE Studio build requires Windows 10 or 11 x64.
  exit /b 2
)

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_LAUNCHER=.venv\Scripts\python.exe"
) else (
  set "PYTHON_LAUNCHER=py -3.12"
)
%PYTHON_LAUNCHER% -c "import sys" >nul 2>nul
if errorlevel 1 set "PYTHON_LAUNCHER=python"
%PYTHON_LAUNCHER% -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize.bit_length() == 63 else 2)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.12 x64 is required.
  exit /b 2
)

echo Creating clean locked Windows build environment...
%PYTHON_LAUNCHER% -m venv --clear .venv-windows-build
if errorlevel 1 exit /b 1

echo Installing locked Windows dependencies...
.venv-windows-build\Scripts\python.exe -m pip install --disable-pip-version-check --only-binary=:all: --requirement requirements-windows.lock
if errorlevel 1 exit /b 1
.venv-windows-build\Scripts\python.exe -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e .
if errorlevel 1 exit /b 1
.venv-windows-build\Scripts\python.exe -m pip check
if errorlevel 1 exit /b 1

set "PYTHON_BIN=%CD%\.venv-windows-build\Scripts\python.exe"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1
if errorlevel 1 exit /b 1

echo.
echo Windows EXE copy was created under dist\0.3.0-test-rc1-windows-x64\
pause

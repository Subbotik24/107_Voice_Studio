@echo off
setlocal
cd /d "%~dp0"

if not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
  echo Hermes Voice Studio build requires Windows 10 or 11 x64.
  exit /b 2
)

set "PYTHON_LAUNCHER=py -3.11"
%PYTHON_LAUNCHER% -c "import sys" >nul 2>nul
if errorlevel 1 set "PYTHON_LAUNCHER=py -3.12"
%PYTHON_LAUNCHER% -c "import sys" >nul 2>nul
if errorlevel 1 set "PYTHON_LAUNCHER=python"
%PYTHON_LAUNCHER% -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 2)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or 3.12 x64 is required.
  exit /b 2
)

if not exist .venv-windows-build (
  echo Creating isolated Windows build environment...
  %PYTHON_LAUNCHER% -m venv .venv-windows-build
  if errorlevel 1 exit /b 1
)

echo Installing build dependencies ^(first run only^)...
.venv-windows-build\Scripts\python.exe -m pip install -e ".[dev,hermes,benchmark,package,cloud]"
if errorlevel 1 exit /b 1

set "PYTHON_BIN=%CD%\.venv-windows-build\Scripts\python.exe"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1
if errorlevel 1 exit /b 1

echo.
echo Windows EXE copy was created under dist\0.3.0-test-rc1-windows-x64\
pause

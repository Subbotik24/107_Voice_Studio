@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_LAUNCHER=py -3.11"
%PYTHON_LAUNCHER% -c "import sys" >nul 2>nul
if errorlevel 1 set "PYTHON_LAUNCHER=py -3.12"
%PYTHON_LAUNCHER% -c "import sys" >nul 2>nul
if errorlevel 1 set "PYTHON_LAUNCHER=python"
%PYTHON_LAUNCHER% -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 2)" >nul 2>nul
if errorlevel 1 (
  echo VOICE Studio requires Python 3.11 or 3.12.
  exit /b 2
)
if not exist .venv (
  %PYTHON_LAUNCHER% -m venv .venv
  if errorlevel 1 exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo The existing .venv is incomplete: .venv\Scripts\python.exe is missing.
  echo Move the broken .venv aside, then run this launcher again to recreate it.
  exit /b 2
)
.venv\Scripts\python.exe -c "import tkinter, _tkinter"
if errorlevel 1 (
  echo Tkinter is unavailable in this Python installation.
  exit /b 2
)
.venv\Scripts\python.exe -c "import voice_studio" >nul 2>nul
if errorlevel 1 (
  echo Installing VOICE Studio into .venv ^(first run only^)...
  .venv\Scripts\python.exe -m pip install -e ".[cloud]"
  if errorlevel 1 exit /b 1
)
.venv\Scripts\voice-studio.exe gui
exit /b %errorlevel%

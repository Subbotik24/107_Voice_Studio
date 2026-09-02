@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem Optional self-update of a developer checkout: only when the user sets
rem VOICE_STUDIO_AUTO_UPDATE=1 the launcher contacts origin and syncs to the
rem latest main before the launch. Off by default: the product is local/private
rem by default and a launch must not make a network call on its own. Offline or
rem without git the launcher just starts what is here, and a checkout with
rem local edits is never overwritten.
set "UPDATE_MOVED_HEAD=0"
if "%VOICE_STUDIO_AUTO_UPDATE%"=="1" if exist ".git" (
  git --version >nul 2>nul
  if not errorlevel 1 (
    echo Updating VOICE Studio to the latest version...
    set "HAS_LOCAL_CHANGES="
    for /f "delims=" %%s in ('git status --porcelain 2^>nul') do set "HAS_LOCAL_CHANGES=1"
    if defined HAS_LOCAL_CHANGES (
      echo Local changes in this folder are kept - the update is skipped.
      goto :launch
    )
    set "BEFORE_REV="
    for /f "delims=" %%h in ('git rev-parse HEAD 2^>nul') do set "BEFORE_REV=%%h"
    set "GIT_TERMINAL_PROMPT=0"
    git fetch origin
    if errorlevel 1 (
      echo Offline or fetch failed - starting the current version.
    ) else (
      git checkout -f -B main origin/main
      if errorlevel 1 echo Update could not be applied - starting the current version.
    )
    set "AFTER_REV="
    for /f "delims=" %%h in ('git rev-parse HEAD 2^>nul') do set "AFTER_REV=%%h"
    if not "!BEFORE_REV!"=="!AFTER_REV!" if not "!AFTER_REV!"=="" set "UPDATE_MOVED_HEAD=1"
  )
)
:launch

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
if not "!UPDATE_MOVED_HEAD!"=="0" (
  echo Update changed the code - reinstalling dependencies...
  .venv\Scripts\python.exe -m pip install -e ".[cloud]"
  if errorlevel 1 exit /b 1
)
rem Always run the source tree sitting in this folder, never a stale copy
rem installed inside .venv: right after `git pull` this launcher starts
rem exactly the pulled code.
set "PYTHONPATH=%~dp0src"
.venv\Scripts\python.exe -c "import voice_studio; print('VOICE Studio code:', voice_studio.__file__)"
.venv\Scripts\python.exe -m voice_studio gui
exit /b %errorlevel%

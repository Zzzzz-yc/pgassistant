@echo off
setlocal EnableExtensions
rem pgassistant one-click launcher for Windows.
rem
rem All launcher logic lives in launch.py, one implementation shared by
rem Windows / macOS / Linux. This file only finds a Python >= 3.11 and
rem forwards every argument unchanged. No PowerShell script is involved,
rem so there is no execution-policy / digital-signature problem anymore.
rem
rem   start.cmd
rem   start.cmd -NoBrowser     (same as --no-browser)
rem   start.cmd -Dev           (same as --dev)
rem   start.cmd --port 8020
rem   stop.cmd                 (stops the instance recorded in data\runtime.json)
rem
rem Python lookup: the official py launcher first, then python on PATH.
rem If neither works, the message says where to install Python.

set "PYEXE="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PYEXE=python"
    )
)
if not defined PYEXE (
    echo [start.cmd] Python not found. Install Python 3.11+ from https://www.python.org/downloads/ and run this file again.
    pause
    exit /b 1
)

%PYEXE% "%~dp0launch.py" %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo [start.cmd] launch.py exited with code %RC%. Read the error above.
    pause
)
endlocal & exit /b %RC%

@echo off
setlocal EnableExtensions
rem pgassistant one-click stop for Windows.
rem
rem Stops the instance recorded in data\runtime.json (uvicorn pid tree and the
rem Vite pid from -Dev), then falls back to port detection and cleans the
rem runtime file. Safe to run when nothing is running.
rem
rem   stop.cmd
rem   stop.cmd --runtime-file data\.launch-test.json
rem   stop.cmd --port 8020
rem   stop.cmd --dry-run
rem
rem All logic lives in launch.py; this file only finds Python and forwards args.

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
    echo [stop.cmd] Python not found. Install Python 3.11+ from https://www.python.org/downloads/ and run this file again.
    pause
    exit /b 1
)

%PYEXE% "%~dp0launch.py" stop %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo [stop.cmd] launch.py exited with code %RC%. Read the error above.
    pause
)
endlocal & exit /b %RC%
